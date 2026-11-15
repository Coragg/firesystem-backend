"""[AI] Shared helpers of the data-model routers: the local data source and the pixel the model works on.

[AI] These endpoints always read the AppEEARS files on disk (never `ApiFeatureSource`), so a deployment whose
`APPEEARS_DATA_URL` points at this same API does not call itself.
"""

from collections.abc import Callable
from pathlib import Path
from typing import TypeVar

from fastapi import HTTPException

from src import config
from src.routers.api_model.hotspot import model_bundle
from src.utils import hotspot_model as hm

T = TypeVar("T")


def local_source() -> hm.LocalFeatureSource:
    """Return the shared source over the AppEEARS files in `APPEEARS_LOCAL_DIR`."""
    return hm.cached_local_source(str(Path(config.APPEEARS_LOCAL_DIR).resolve()))


def pixel_center(lat: float, lon: float) -> tuple[float, float]:
    """[AI] Snap the point to the model's 500 m grid; outside the trained area -> 422, model files missing -> 503.

    [AI] The model is only ever asked about pixel centres, so values are looked up at the centre too.
    """
    try:
        return hm.snap_to_pixel(model_bundle(), lat, lon)
    except hm.FeatureError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except FileNotFoundError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


def read(call: Callable[[hm.LocalFeatureSource], T]) -> T:
    """[AI] Run `call` on the local source; missing AppEEARS files -> 503 and unusable data -> 422."""
    try:
        return call(local_source())
    except FileNotFoundError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except hm.FeatureError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
