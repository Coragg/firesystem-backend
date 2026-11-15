"""[AI] `GET /api/v1/data-model/terrain`: elevation and slope of a 500 m pixel (SRTMGL3 DEM)."""

import math

from fastapi import APIRouter, HTTPException, Query

from src.models.data_model import Terrain
from src.models.response import ApiResponse
from src.routers.api_data_model.common import pixel_center, read

router = APIRouter(prefix="/api/v1/data-model", tags=["Data model"])


@router.get(
    "/terrain",
    response_model=ApiResponse[Terrain],
    summary="Elevation and slope of a pixel",
    description="SRTMGL3 elevation (m) and slope (degrees) averaged over the 500 m model pixel containing (lat, lon).",
)
def get_terrain(
    lat: float = Query(ge=-90, le=90, description="Latitude in decimal degrees."),
    lon: float = Query(ge=-180, le=180, description="Longitude in decimal degrees."),
) -> ApiResponse[Terrain]:
    """[AI] A pixel without elevation (void or sea) is a 422: the model needs both numbers."""
    pixel_lat, pixel_lon = pixel_center(lat, lon)
    elevation, slope = read(lambda source: source.terrain(pixel_lat, pixel_lon))
    if math.isnan(elevation) or math.isnan(slope):
        raise HTTPException(status_code=422, detail=f"No terrain data for ({lat}, {lon})")
    return ApiResponse(data=Terrain(elevation_m=elevation, slope_deg=slope))
