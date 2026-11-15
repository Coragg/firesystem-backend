"""[AI] `GET /api/v1/hotspot/forecast`: XGBoost hotspot probability for one 500 m pixel and month.

[AI] The model files come from `src/notebooks/XGBoost_Hotspot_Forecast.ipynb`; the logic lives in
`src/utils/hotspot_model.py`. This router only maps settings and errors to HTTP.

[AI] The model's input data (leaf area, terrain, land cover) is only ever fetched over HTTP from the
data-model API (`/api/v1/data-model`); this router never reads AppEEARS files. By default it calls the
data-model endpoints of this same server; set APPEEARS_DATA_URL to use another deployment's.
"""

from datetime import date
from pathlib import Path

import httpx
import numpy as np
from fastapi import APIRouter, HTTPException, Query, Request

from src import config
from src.models.hotspot import HotspotForecast, HotspotModelInfo
from src.models.response import ApiResponse
from src.utils import hotspot_model as hm

router = APIRouter(prefix="/api/v1/hotspot", tags=["Hotspot forecast"])


DATA_MODEL_PATH = "api/v1/data-model"


def data_api_url(request: Request) -> str:
    """[AI] Base URL of the data-model API: APPEEARS_DATA_URL if set, else this server's own endpoints."""
    return config.APPEEARS_DATA_URL or f"{request.base_url}{DATA_MODEL_PATH}"


def feature_source(request: Request) -> hm.FeatureSource:
    """[AI] Return the source of the model's input data: always the data-model API, over HTTP."""
    return hm.ApiFeatureSource(data_api_url(request))


def data_api_error(exc: httpx.HTTPError) -> HTTPException:
    """[AI] Map a failed data-model API call to the forecast's own HTTP error.

    [AI] 422 (point or data unusable) and 503 (data files missing on the data server) are passed on with
    their message, so the answer is the same as if the data were read here; anything else is a 502.
    """
    if isinstance(exc, httpx.HTTPStatusError) and exc.response.status_code in (422, 503):
        try:
            detail = exc.response.json().get("detail", "Data API rejected the request")
        except ValueError:
            detail = "Data API rejected the request"
        return HTTPException(status_code=exc.response.status_code, detail=detail)
    return HTTPException(status_code=502, detail="Data API request failed")


def model_bundle() -> hm.ModelBundle:
    """[AI] Return the trained model and its metadata, loaded once per directory and then cached.

    [AI] The path is resolved to an absolute string so the cache key does not depend on the working directory.
    """
    return hm.cached_bundle(str(Path(config.HOTSPOT_MODEL_DIR).resolve()))


def json_value(value: object) -> float | int | None:
    """[AI] Make a feature value JSON-safe: NaN becomes None and numpy scalars become Python numbers."""
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
    request: Request,
    date_: date = Query(alias="date", description="Any day of the month to forecast (YYYY-MM-DD)."),
    lat: float = Query(ge=-90, le=90, description="Latitude in decimal degrees."),
    lon: float = Query(ge=-180, le=180, description="Longitude in decimal degrees."),
) -> ApiResponse[HotspotForecast]:
    """[AI] Snap the point to the model grid, build the features, and return the probability.

    [AI] Error mapping: missing model files -> 503 (the server is not ready); point outside the trained
    area or no input data -> 422; the data API's own 422 / 503 are passed on; data API down -> 502.
    """
    try:
        bundle = model_bundle()
    except FileNotFoundError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    source = feature_source(request)

    month = date_.replace(day=1)  # [AI] the model works per month, so any day selects its month
    try:
        pixel_lat, pixel_lon = hm.snap_to_pixel(bundle, lat, lon)
        features = hm.build_features(bundle, source, pixel_lat, pixel_lon, month)
    except hm.FeatureError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except httpx.HTTPError as exc:
        raise data_api_error(exc) from exc

    score, probability = hm.predict(bundle, features)
    meta = bundle.metadata
    return ApiResponse(data=HotspotForecast(
        month=f"{month:%Y-%m}", lat=lat, lon=lon, pixel_lat=pixel_lat, pixel_lon=pixel_lon,
        hotspot_probability=probability, hotspot_score=score,
        features={name: json_value(value) for name, value in features.items()},
        model=HotspotModelInfo(region=meta["region"], trained_months=meta["trained_months"], features=bundle.features),
    ))
