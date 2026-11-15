"""[AI] `POST /api/v1/hotspots` and `/hotspots/export`: detections stored in the database, for the map and as CSV."""

import csv
import io
import logging
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import StreamingResponse

from src.models.hotspot import CONFIDENCE_NAMES, ExportRequest, HotspotPage, HotspotsRequest, hotspot_from_row
from src.models.response import ApiResponse
from src.utils import db
from src.utils.supabase import SupabaseDep

logger = logging.getLogger("firesystem")
router = APIRouter(prefix="/api/v1", tags=["Database"])

MAX_RANGE_DAYS = 366  # [AI] map view limit (one year, leap years included); longer ranges go to the export
EXPORT_MAX_ROWS = 1_000_000
EXPORT_PAGE_SIZE = 1000  # [AI] matches the cap inside the hotspots_page SQL function
EXPORT_COLUMNS = [
    "id", "acq_time_utc", "solar_date", "lat", "lon", "satellite", "instrument", "confidence", "frp", "source",
    "excluded",
]


class CSVStreamingResponse(StreamingResponse):
    """[AI] Streaming response with the text/csv media type, sent as a `hotspots.csv` attachment."""

    media_type = "text/csv"

    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        # Set here because FastAPI does not copy headers from a `response` parameter into streams.
        self.headers["Content-Disposition"] = 'attachment; filename="hotspots.csv"'


@router.post("/hotspots")
def hotspots_view(body: HotspotsRequest, client: SupabaseDep) -> ApiResponse[HotspotPage]:
    """Detections in an area for the map, one page at a time."""
    if (body.end_date - body.start_date).days + 1 > MAX_RANGE_DAYS:
        raise HTTPException(
            status_code=422, detail=f"The range is limited to {MAX_RANGE_DAYS} days; use the export for longer ones."
        )
    rows = db.hotspots_page(client, body.aoi.model_dump(), body.start_date, body.end_date, body.after_id, body.limit)
    return ApiResponse(
        data=HotspotPage(
            items=[hotspot_from_row(r) for r in rows],
            # [AI] A full page may have more after it; a short page is the last one.
            next_after_id=rows[-1]["id"] if len(rows) == body.limit else None,
        )
    )


def check_export_size(body: ExportRequest, client: SupabaseDep) -> ExportRequest:
    """Runs before streaming starts, so a too-large export is a real 413 and not a broken file."""
    total = db.hotspots_count(client, body.aoi.model_dump(), body.start_date, body.end_date)
    if total > EXPORT_MAX_ROWS:
        raise HTTPException(
            status_code=413, detail=f"{total} detections is over the {EXPORT_MAX_ROWS} limit. Narrow the area or the dates."
        )
    return body


# [AI] Decision visible in the code: the size check is a dependency because FastAPI runs dependencies
# before the generator below starts, which is the only time an error status can still be sent.
ExportGuard = Annotated[ExportRequest, Depends(check_export_size)]


@router.post("/hotspots/export", response_class=CSVStreamingResponse, status_code=200)
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
