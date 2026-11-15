"""[AI] `GET /api/v1/data-model/land-cover`: IGBP land cover class of a 500 m pixel (MCD12Q1 LC_Type1)."""

import math

from fastapi import APIRouter, Query

from src.models.data_model import LandCover
from src.models.response import ApiResponse
from src.routers.api_data_model.common import pixel_center, read

router = APIRouter(prefix="/api/v1/data-model", tags=["Data model"])


@router.get(
    "/land-cover",
    response_model=ApiResponse[LandCover],
    summary="Land cover class of a pixel",
    description=(
        "MCD12Q1 `LC_Type1` (IGBP class) of the 500 m model pixel containing (lat, lon), for the latest year "
        "that is not after `max_year`. `year` and `igbp_class` are null when no such year exists."
    ),
)
def get_land_cover(
    lat: float = Query(ge=-90, le=90, description="Latitude in decimal degrees."),
    lon: float = Query(ge=-180, le=180, description="Longitude in decimal degrees."),
    max_year: int = Query(ge=1900, le=2200, description="Latest year allowed, YYYY."),
) -> ApiResponse[LandCover]:
    """[AI] The source returns a float class (NaN if the pixel has none); the API reports it as an integer or null."""
    pixel_lat, pixel_lon = pixel_center(lat, lon)
    year, igbp = read(lambda source: source.land_cover(pixel_lat, pixel_lon, max_year))
    return ApiResponse(data=LandCover(year=year, igbp_class=None if math.isnan(igbp) else int(igbp)))
