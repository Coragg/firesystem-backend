import csv
import io
import logging
from calendar import monthrange
from datetime import UTC, date, datetime, timedelta
from time import perf_counter
from typing import Annotated
from uuid import uuid4

from fastapi import Depends, FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.responses import JSONResponse, StreamingResponse

from src.config import CORS_ORIGINS, LOG_LEVEL
from src.models.aoi import SWAP_HINT
from src.models.calendar import CalendarData, CalendarRequest
from src.models.hotspot import CONFIDENCE_NAMES, ExportRequest, HotspotPage, HotspotsRequest, hotspot_from_row
from src.models.response import ApiError, ApiResponse, ErrorBody, Meta
from src.models.source import (
    CalibrationInfo, Coverage, HealthData, ProductInfo, SatelliteInfo, SourcesData, Validation,
)
from src.routers.api_nasa import fires, health, quota, sources
from src.utils import db
from src.utils.coverage import AREAS, intersects_coverage
from src.utils.errors import AoiInvalid, AppError, ExportTooLarge, InvalidRequest, RangeTooLong
from src.utils.harmonize import build_years, harmonize, validation_error
from src.utils.pyproject_variables import name, version, description
from src.utils.stats import current_month, kpis, monthly_stats
from src.utils.supabase import SupabaseDep

logging.basicConfig(level=LOG_LEVEL)
logger = logging.getLogger("firesystem")
# These log every request URL at INFO, and FIRMS puts the map key in the URL.
for _noisy in ("httpx", "httpx2", "httpcore"):
    logging.getLogger(_noisy).setLevel(logging.WARNING)

app = FastAPI()
app.include_router(health.router)
app.include_router(quota.router)
app.include_router(sources.router)
app.include_router(fires.router)

# Added first = innermost. request_log is declared last so it wraps everything.
app.add_middleware(GZipMiddleware, minimum_size=1000)
app.add_middleware(
    CORSMiddleware,
    allow_origins=CORS_ORIGINS,
    allow_methods=["GET", "POST"],
    allow_headers=["*"],
    expose_headers=["X-Request-ID"],
)


@app.middleware("http")
async def request_log(request: Request, call_next):
    """Tag each request with an id, return it in X-Request-ID and log its duration."""
    request.state.request_id = request_id = uuid4().hex[:12]
    start = perf_counter()
    status = 500
    try:
        response = await call_next(request)
        status = response.status_code
        response.headers["X-Request-ID"] = request_id
        return response
    finally:
        logger.info(
            "%s %s %s %s %.0fms", request_id, request.method, request.url.path, status,
            (perf_counter() - start) * 1000,
        )


def error_response(
    request: Request, status: int, code: str, message: str, details: list[dict] | None = None
) -> JSONResponse:
    request_id = getattr(request.state, "request_id", None)
    body = ApiError(error=ErrorBody(code=code, message=message, details=details, request_id=request_id))
    headers = {"X-Request-ID": request_id} if request_id else None
    return JSONResponse(status_code=status, content=body.model_dump(), headers=headers)


@app.exception_handler(AppError)
async def app_error_handler(request: Request, exc: AppError) -> JSONResponse:
    return error_response(request, exc.status_code, exc.code, exc.message, exc.details)


@app.exception_handler(RequestValidationError)
async def validation_error_handler(request: Request, exc: RequestValidationError) -> JSONResponse:
    errors = exc.errors()
    details = [
        {"field": ".".join(str(part) for part in e["loc"]), "problem": e["msg"].removeprefix("Value error, ")}
        for e in errors
    ]
    code = "AOI_INVALID" if any("aoi" in e["loc"] for e in errors) else "INVALID_REQUEST"
    message = details[0]["problem"] if details else "The request is not valid."
    return error_response(request, 422, code, message, details)


@app.exception_handler(Exception)
async def unhandled_error_handler(request: Request, exc: Exception) -> JSONResponse:
    logger.exception("unhandled error id=%s", getattr(request.state, "request_id", None))
    return error_response(request, 500, "INTERNAL", "Something went wrong on our side.")


STALE_AFTER_HOURS = 36
FIRST_MONTH = (2001, 1)


def today_utc() -> date:
    return datetime.now(UTC).date()


def _year_month(text: str) -> tuple[int, int]:
    year, month = text.split("-")
    return int(year), int(month)


def _month_start(ym: tuple[int, int]) -> date:
    return date(ym[0], ym[1], 1)


def _month_end(ym: tuple[int, int]) -> date:
    return date(ym[0], ym[1], monthrange(*ym)[1])


MAX_RANGE_DAYS = 366
EXPORT_MAX_ROWS = 1_000_000
EXPORT_PAGE_SIZE = 1000
EXPORT_COLUMNS = [
    "id", "acq_time_utc", "solar_date", "lat", "lon", "satellite", "instrument", "confidence", "frp", "source",
    "excluded",
]


class CSVStreamingResponse(StreamingResponse):
    media_type = "text/csv"

    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        # Set here because FastAPI does not copy headers from a `response` parameter into streams.
        self.headers["Content-Disposition"] = 'attachment; filename="hotspots.csv"'


@app.post("/v1/hotspots")
def hotspots_view(body: HotspotsRequest, client: SupabaseDep) -> ApiResponse[HotspotPage]:
    """Detections in an area for the map, one page at a time."""
    if (body.end_date - body.start_date).days + 1 > MAX_RANGE_DAYS:
        raise RangeTooLong(f"The range is limited to {MAX_RANGE_DAYS} days; use the export for longer ones.")
    rows = db.hotspots_page(client, body.aoi.model_dump(), body.start_date, body.end_date, body.after_id, body.limit)
    return ApiResponse(
        data=HotspotPage(
            items=[hotspot_from_row(r) for r in rows],
            next_after_id=rows[-1]["id"] if len(rows) == body.limit else None,
        )
    )


def check_export_size(body: ExportRequest, client: SupabaseDep) -> ExportRequest:
    """Runs before streaming starts, so a too-large export is a real 413 and not a broken file."""
    total = db.hotspots_count(client, body.aoi.model_dump(), body.start_date, body.end_date)
    if total > EXPORT_MAX_ROWS:
        raise ExportTooLarge(f"{total} detections is over the {EXPORT_MAX_ROWS} limit. Narrow the area or the dates.")
    return body


ExportGuard = Annotated[ExportRequest, Depends(check_export_size)]


@app.post("/v1/hotspots/export", response_class=CSVStreamingResponse, status_code=200)
def export_hotspots(request: Request, body: ExportGuard, client: SupabaseDep):
    """Every detection in an area and range as a CSV, streamed page by page."""

    def line(values: list) -> str:
        buffer = io.StringIO()
        csv.writer(buffer, delimiter=body.separator, lineterminator="\n").writerow(values)
        return buffer.getvalue()

    yield "﻿" + line(EXPORT_COLUMNS)  # BOM so Excel reads accents correctly
    after_id = None
    try:
        while True:
            rows = db.hotspots_page(
                client, body.aoi.model_dump(), body.start_date, body.end_date, after_id, EXPORT_PAGE_SIZE
            )
            for r in rows:
                yield line([
                    r["id"], r["acq_time"], r["solar_date"], r["lat"], r["lon"], r["satellite"], r["instrument"],
                    CONFIDENCE_NAMES[r["confidence_level"]], r["frp"], r["source_kind"], str(r["excluded"]).lower(),
                ])
            if len(rows) < EXPORT_PAGE_SIZE:
                return
            after_id = rows[-1]["id"]
    except Exception:
        # The 200 status is already sent, so the file itself must say it is cut short.
        logger.exception("export failed id=%s", request.state.request_id)
        yield f"# EXPORT_INCOMPLETE request_id={request.state.request_id}\n"


@app.get("/v1/sources")
def sources_view(client: SupabaseDep) -> ApiResponse[SourcesData]:
    """Where the data comes from, how each sensor is scaled and how well the scaling holds."""
    coverage = {r["satellite"]: r for r in db.rpc(client, "satellite_coverage")}
    factors = db.select(client, "calibration_factor", "reference,month,k,overlap_cell_days,is_estimated")
    k = {(f["reference"], f["month"]): f["k"] for f in factors}
    error = validation_error(db.rpc(client, "calibration_check"), k)

    def role(s: dict) -> str:
        return "reference" if s["priority"] == 1 else "scaled" if s["instrument"] == "MODIS" else "fallback"

    return ApiResponse(
        data=SourcesData(
            satellites=[
                SatelliteInfo(
                    code=s["code"], instrument=s["instrument"], pixel_m=s["pixel_m"], role=role(s),
                    first_date=coverage.get(s["code"], {}).get("first_date"),
                    last_date=coverage.get(s["code"], {}).get("last_date"),
                )
                for s in db.select(client, "satellite", "code,instrument,pixel_m,priority")
            ],
            products=[ProductInfo(**p) for p in db.select(client, "firms_source", "code,kind,min_date,max_date")],
            calibration=[
                CalibrationInfo(reference=f["reference"], month=f["month"], k=f["k"],
                                overlap_cell_days=f["overlap_cell_days"], estimated=f["is_estimated"])
                for f in factors
            ],
            validation=Validation(reference="modis", mean_abs_error_pct=error) if error is not None else None,
            coverage=Coverage(name="Chile", bbox=AREAS["mainland"]),
        ),
        meta=Meta(warnings=[] if factors else ["CALIBRATION_MISSING"]),
    )


@app.post("/v1/calendar")
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
        raise InvalidRequest("start is after the latest month with data")

    west, south, east, north = body.aoi.bbox()
    covered = intersects_coverage((west, south, east, north))
    if not covered:
        if intersects_coverage((south, west, north, east)):
            raise AoiInvalid(f"The area is outside Chile but fits it with latitude and longitude swapped. {SWAP_HINT}.")
        warnings.append("AOI_OUTSIDE_COVERAGE")

    baseline = range(body.baseline_from, (body.baseline_to or today.year - 1) + 1)
    if len(baseline) < 3:
        warnings.append("BASELINE_TOO_SHORT")

    # Statistics need the baseline years and the current month even when the view is narrower.
    fetch_start, fetch_end = min(start, (body.baseline_from, 1)), max(end, current)
    rows, k = [], {}
    if covered:
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
        meta=Meta(data_updated_at=last, warnings=list(dict.fromkeys(warnings))),
    )


@app.get("/v1/health")
def health_view(client: SupabaseDep) -> ApiResponse[HealthData]:
    """Report that the API and database answer, and how fresh the loaded data is."""
    last = db.last_ingest_at(client)
    hours = None if last is None else (datetime.now(UTC) - last).total_seconds() / 3600
    status = "empty" if hours is None else "stale" if hours > STALE_AFTER_HOURS else "fresh"
    return ApiResponse(
        data=HealthData(
            api="ok",
            database="ok",
            data_status=status,
            last_ingest_at=last,
            hours_since_last_ingest=None if hours is None else round(hours, 1),
        )
    )


@app.get("/")
async def root() -> ApiResponse[dict]:
    return ApiResponse(
        data={
            "message": f"Welcome to {name}",
            "version": version,
            "description": description,
        }
    )
