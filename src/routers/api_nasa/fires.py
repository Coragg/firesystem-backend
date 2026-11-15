"""[AI] `GET /api/v1/fire`: live MODIS/VIIRS detections from the FIRMS area API, converted from CSV to JSON.

[AI] Goes straight to FIRMS (not the database), so it needs a valid map key and counts against its quota.
"""

import re
from datetime import date

import httpx
from fastapi import APIRouter, HTTPException, Query

from src.models.nasa import FireDetection, FireQueryResult, FirmsSource
from src.models.response import ApiResponse
from src.utils.firms import fetch_hotspots

router = APIRouter(prefix="/api/v1", tags=["NASA FIRMS"])
# [AI] Four comma-separated decimal numbers with no spaces: west,south,east,north.
AREA_PATTERN = re.compile(r"^-?\d+(?:\.\d+)?,-?\d+(?:\.\d+)?,-?\d+(?:\.\d+)?,-?\d+(?:\.\d+)?$")


def validate_area(area: str) -> str:
    """[AI] Return `area` unchanged if it is 'world' or a valid bounding box; otherwise raise a 422.

    [AI] Checked here, before calling FIRMS, so a malformed area does not spend a FIRMS transaction.
    Boxes crossing the antimeridian (west > east) are rejected.
    """
    if area == "world":
        return area
    if not AREA_PATTERN.fullmatch(area):
        raise HTTPException(status_code=422, detail="area must be 'world' or west,south,east,north")
    west, south, east, north = map(float, area.split(","))
    if not (-180 <= west < east <= 180 and -90 <= south < north <= 90):
        raise HTTPException(status_code=422, detail="area coordinates are outside valid bounds or inverted")
    return area


@router.get(
    "/fire",
    response_model=ApiResponse[FireQueryResult],
    summary="Get active fire detections",
    description=(
        "Queries NASA FIRMS for MODIS or VIIRS active fire detections by source and area. "
        "FIRMS returns CSV upstream; this endpoint responds with JSON. A hotspot is a satellite "
        "thermal detection and should not be interpreted as a confirmed wildfire perimeter."
    ),
)
def get_fires(
    source: FirmsSource = Query(
        description="FIRMS product ID. Supports the MODIS and VIIRS active fire products."
    ),
    area: str = Query(
        "world",
        description="'world' or west,south,east,north bounding box coordinates.",
    ),
    days: int = Query(1, ge=1, le=5, description="Number of days to query (1–5)."),
    date_from: date | None = Query(
        None,
        alias="date",
        description="Optional start date (YYYY-MM-DD); defaults to the most recent data.",
    ),
) -> ApiResponse[FireQueryResult]:
    """[AI] Fetch the detections and return them with the query parameters echoed back."""
    area = validate_area(area)
    try:
        rows = fetch_hotspots(source, area, days, date_from.isoformat() if date_from else None)
    except httpx.HTTPError as exc:
        raise HTTPException(status_code=502, detail="NASA FIRMS request failed") from exc
    except ValueError as exc:
        raise HTTPException(status_code=502, detail="NASA FIRMS returned invalid data") from exc
    return ApiResponse(data=FireQueryResult(
        source=source,
        area=area,
        days=days,
        detections=[FireDetection.model_validate(row) for row in rows],
    ))
