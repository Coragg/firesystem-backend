"""[AI] `GET /api/v1/data-model/lai`: monthly leaf area index of a 500 m pixel (MCD15A3H Lai_500m + FparLai_QC)."""

import pandas as pd
from fastapi import APIRouter, HTTPException, Query

from src.models.data_model import LaiPoint
from src.models.response import ApiResponse
from src.routers.api_data_model.common import pixel_center, read

router = APIRouter(prefix="/api/v1/data-model", tags=["Data model"])

MAX_MONTHS = 120  # [AI] ten years; the model asks for 12 months, so this only guards against runaway ranges
MONTH_PATTERN = r"^\d{4}-(0[1-9]|1[0-2])$"


@router.get(
    "/lai",
    response_model=ApiResponse[list[LaiPoint]],
    summary="Monthly leaf area index of a pixel",
    description=(
        "Monthly mean of the good-quality MCD15A3H `Lai_500m` composites (`FparLai_QC` main algorithm) of the "
        "500 m model pixel containing (lat, lon). Months without a valid composite have `lai: null`."
    ),
)
def get_lai(
    lat: float = Query(ge=-90, le=90, description="Latitude in decimal degrees."),
    lon: float = Query(ge=-180, le=180, description="Longitude in decimal degrees."),
    start: str = Query(pattern=MONTH_PATTERN, description="First month, YYYY-MM."),
    end: str = Query(pattern=MONTH_PATTERN, description="Last month, YYYY-MM (inclusive)."),
) -> ApiResponse[list[LaiPoint]]:
    """[AI] Return one point per month from `start` to `end`, looked up at the centre of the pixel."""
    months = pd.date_range(f"{start}-01", f"{end}-01", freq="MS")
    if not len(months):
        raise HTTPException(status_code=422, detail="start is after end")
    if len(months) > MAX_MONTHS:
        raise HTTPException(status_code=422, detail=f"The range is limited to {MAX_MONTHS} months")
    pixel_lat, pixel_lon = pixel_center(lat, lon)
    values = read(lambda source: source.monthly_lai(pixel_lat, pixel_lon, list(months)))
    return ApiResponse(data=[
        LaiPoint(month=f"{m:%Y-%m}", lai=None if pd.isna(values[m]) else float(values[m])) for m in months
    ])
