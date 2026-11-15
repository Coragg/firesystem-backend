from datetime import date
from pathlib import Path

import httpx
import numpy as np
from fastapi import APIRouter, HTTPException, Query

from src import config
from src.models.hotspot import HotspotForecast, HotspotModelInfo
from src.models.response import ApiResponse
from src.utils import hotspot_model as hm

router = APIRouter(prefix="/api/v1/hotspot", tags=["Hotspot forecast"])


def feature_source() -> hm.FeatureSource:
    """Return the configured source of the model's input data."""
    if config.HOTSPOT_FEATURE_SOURCE == "local":
        return hm.cached_local_source(str(Path(config.APPEEARS_LOCAL_DIR).resolve()))
    if config.HOTSPOT_FEATURE_SOURCE == "api":
        return hm.ApiFeatureSource(config.APPEEARS_DATA_URL)
    raise ValueError(f"HOTSPOT_FEATURE_SOURCE must be 'api' or 'local', got {config.HOTSPOT_FEATURE_SOURCE!r}")


def model_bundle() -> hm.ModelBundle:
    return hm.cached_bundle(str(Path(config.HOTSPOT_MODEL_DIR).resolve()))


def json_value(value: object) -> float | int | None:
    if value is None or (isinstance(value, float) and np.isnan(value)):
        return None
    return value.item() if hasattr(value, "item") else value


@router.get(
    "/forecast",
    response_model=ApiResponse[HotspotForecast],
    summary="Forecast the hotspot probability of a pixel and month",
    description=(
        "Runs the XGBoost hotspot model for the 500 m pixel containing (lat, lon) and the month containing "
        "`date`. Inputs: leaf area index 24-35 months before the month, elevation and slope, and the land "
        "cover class of two years before. Returns the estimated probability of at least one FIRMS "
        "vegetation-fire hotspot in the pixel during that month."
    ),
)
def get_hotspot_forecast(
    date_: date = Query(alias="date", description="Any day of the month to forecast (YYYY-MM-DD)."),
    lat: float = Query(ge=-90, le=90, description="Latitude in decimal degrees."),
    lon: float = Query(ge=-180, le=180, description="Longitude in decimal degrees."),
) -> ApiResponse[HotspotForecast]:
    try:
        bundle = model_bundle()
    except FileNotFoundError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    try:
        source = feature_source()
    except ValueError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc

    month = date_.replace(day=1)
    try:
        pixel_lat, pixel_lon = hm.snap_to_pixel(bundle, lat, lon)
        features = hm.build_features(bundle, source, pixel_lat, pixel_lon, month)
    except hm.FeatureError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except httpx.HTTPError as exc:
        raise HTTPException(status_code=502, detail="Data API request failed") from exc

    score, probability = hm.predict(bundle, features)
    meta = bundle.metadata
    return ApiResponse(data=HotspotForecast(
        month=f"{month:%Y-%m}", lat=lat, lon=lon, pixel_lat=pixel_lat, pixel_lon=pixel_lon,
        hotspot_probability=probability, hotspot_score=score,
        features={name: json_value(value) for name, value in features.items()},
        model=HotspotModelInfo(region=meta["region"], trained_months=meta["trained_months"], features=bundle.features),
    ))
