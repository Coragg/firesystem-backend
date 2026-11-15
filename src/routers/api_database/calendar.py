"""[AI] `POST /api/v1/calendar`: the harmonized fire calendar for an area, built from the loaded database."""

from calendar import monthrange
from datetime import UTC, date, datetime, timedelta

from fastapi import APIRouter, HTTPException

from src.config import STALE_AFTER_HOURS
from src.models.aoi import SWAP_HINT
from src.models.calendar import CalendarData, CalendarRequest
from src.models.response import ApiResponse, Meta
from src.utils import db
from src.utils.coverage import intersects_coverage
from src.utils.harmonize import build_years, harmonize
from src.utils.stats import current_month, kpis, monthly_stats
from src.utils.supabase import SupabaseDep

router = APIRouter(prefix="/api/v1", tags=["Database"])

FIRST_MONTH = (2001, 1)  # [AI] earliest month the calendar serves; requests before it are clipped


def today_utc() -> date:
    """[AI] Today's date in UTC; a separate function so tests can pin "today"."""
    return datetime.now(UTC).date()


def _year_month(text: str) -> tuple[int, int]:
    """[AI] "YYYY-MM" -> (year, month); tuples compare chronologically."""
    year, month = text.split("-")
    return int(year), int(month)


def _month_start(ym: tuple[int, int]) -> date:
    """[AI] First day of a (year, month)."""
    return date(ym[0], ym[1], 1)


def _month_end(ym: tuple[int, int]) -> date:
    """[AI] Last day of a (year, month), accounting for month length and leap years."""
    return date(ym[0], ym[1], monthrange(*ym)[1])


@router.post("/calendar")
def calendar_view(body: CalendarRequest, client: SupabaseDep) -> ApiResponse[CalendarData]:
    """Harmonized fire calendar for an area, with what is normal for each month."""
    today = today_utc()
    current = (today.year, today.month)
    warnings: list[str] = []

    start = _year_month(body.start)
    end = _year_month(body.end) if body.end else current
    if start < FIRST_MONTH:
        start = FIRST_MONTH
        warnings.append("RANGE_CLIPPED")
    if end > current:
        end = current
        warnings.append("RANGE_CLIPPED")
    if start > end:
        raise HTTPException(status_code=422, detail="start is after the latest month with data")

    west, south, east, north = body.aoi.bbox()
    covered = intersects_coverage((west, south, east, north))
    if not covered:
        # [AI] An area outside Chile that would fit Chile with lat/lon swapped is almost certainly a client
        # mistake, so it fails with a hint. Any other outside area still answers, with a warning and zeros.
        if intersects_coverage((south, west, north, east)):
            raise HTTPException(
                status_code=422,
                detail=f"The area is outside Chile but fits it with latitude and longitude swapped. {SWAP_HINT}.",
            )
        warnings.append("AOI_OUTSIDE_COVERAGE")

    baseline = range(body.baseline_from, (body.baseline_to or today.year - 1) + 1)
    if len(baseline) < 3:
        warnings.append("BASELINE_TOO_SHORT")

    # Statistics need the baseline years and the current month even when the view is narrower.
    fetch_start, fetch_end = min(start, (body.baseline_from, 1)), max(end, current)
    rows, k = [], {}
    if covered:  # [AI] skip the database entirely when the area has no data
        rows = db.calendar_counts(client, body.aoi.model_dump(), _month_start(fetch_start), _month_end(fetch_end), today.day)
        k = db.calibration(client)
    values, calibration_warnings = harmonize(rows, k)
    warnings += calibration_warnings

    all_years = build_years(values, fetch_start, fetch_end, today)
    monthly = monthly_stats(all_years, baseline)

    last = db.last_ingest_at(client)
    if last is None or datetime.now(UTC) - last > timedelta(hours=STALE_AFTER_HOURS):
        warnings.append("DATA_STALE")

    return ApiResponse(
        data=CalendarData(
            years=build_years(values, start, end, today),
            monthly=monthly,
            current=current_month(values, today, baseline),
            kpis=kpis(all_years, monthly, today, baseline),
        ),
        meta=Meta(data_updated_at=last, warnings=list(dict.fromkeys(warnings))),  # [AI] de-duplicate, keep order
    )

