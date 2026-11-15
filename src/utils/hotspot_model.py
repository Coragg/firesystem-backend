"""Run the hotspot forecast model for one pixel and month.

The model and its metadata are saved by `src/notebooks/XGBoost_Hotspot_Forecast.ipynb` as `model.json` and
`metadata.json`. Features are rebuilt exactly as in training: monthly leaf area 24-35 months before the
month, elevation and slope of the 500 m pixel, and the land cover class of the latest year <= year - 2.

Feature values come from a `FeatureSource`:
- `ApiFeatureSource`: HTTP endpoints for AppEEARS-derived values (served by src/routers/api_data_model; the
  contract is documented below). [AI] The forecast endpoint uses only this source.
- `LocalFeatureSource`: the AppEEARS files already downloaded under `data/AppEEARS/<Region>/`. [AI] Used only
  by the data-model API to serve those endpoints, never by the forecast directly.
"""

import json
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path
from typing import Protocol

import httpx
import numpy as np
import pandas as pd

from src.utils import hotspots as hs

MODEL_FILE = "model.json"
METADATA_FILE = "metadata.json"


class FeatureError(ValueError):
    """The requested pixel or month cannot be forecast (outside the model's area, or data missing)."""


class FeatureSource(Protocol):
    """[AI] Where model inputs come from; both implementations below satisfy it, so the router can switch by config."""

    def monthly_lai(self, lat: float, lon: float, months: list[pd.Timestamp]) -> dict[pd.Timestamp, float]:
        """Monthly mean leaf area index (m²/m², good-quality composites only) of the 500 m pixel; NaN if none."""

    def terrain(self, lat: float, lon: float) -> tuple[float, float]:
        """(elevation_m, slope_deg) averaged over the 500 m pixel."""

    def land_cover(self, lat: float, lon: float, max_year: int) -> tuple[int | None, float]:
        """(year, IGBP class) of the latest land cover year <= max_year; (None, NaN) if there is none."""


@dataclass
class ModelBundle:
    """[AI] A trained XGBoost model with its training metadata (grid, features, lags, negative sampling rate)."""

    model: object
    metadata: dict
    lat: np.ndarray = field(init=False)
    lon: np.ndarray = field(init=False)

    def __post_init__(self) -> None:
        self.lat = np.asarray(self.metadata["grid"]["lat"], dtype=float)
        self.lon = np.asarray(self.metadata["grid"]["lon"], dtype=float)

    @property
    def features(self) -> list[str]:
        return list(self.metadata["features"])


def load_bundle(model_dir: str | Path) -> ModelBundle:
    """Load `model.json` and `metadata.json` from `model_dir`."""
    import xgboost as xgb  # [AI] deferred import: xgboost is only loaded when a forecast is requested

    model_dir = Path(model_dir)
    model_path, metadata_path = model_dir / MODEL_FILE, model_dir / METADATA_FILE
    if not model_path.exists() or not metadata_path.exists():
        raise FileNotFoundError(f"No {MODEL_FILE} / {METADATA_FILE} in {model_dir}: run XGBoost_Hotspot_Forecast.ipynb")
    model = xgb.XGBClassifier()
    model.load_model(model_path)
    return ModelBundle(model, json.loads(metadata_path.read_text(encoding="utf-8")))


@lru_cache(maxsize=4)
def cached_bundle(model_dir: str) -> ModelBundle:
    """[AI] `load_bundle`, cached per directory so the model is read from disk once per process."""
    return load_bundle(model_dir)


def snap_to_pixel(bundle: ModelBundle, lat: float, lon: float) -> tuple[float, float]:
    """Return the centre of the model grid's 500 m pixel containing (lat, lon); FeatureError outside the grid."""
    half_lat = abs(np.diff(bundle.lat).mean()) / 2
    half_lon = abs(np.diff(bundle.lon).mean()) / 2
    # [AI] Nearest grid centre, accepted only within half a pixel (+1 % for float rounding).
    i, j = int(np.abs(bundle.lat - lat).argmin()), int(np.abs(bundle.lon - lon).argmin())
    if abs(bundle.lat[i] - lat) > half_lat * 1.01 or abs(bundle.lon[j] - lon) > half_lon * 1.01:
        west, south, east, north = bundle.metadata["bbox"]
        raise FeatureError(
            f"({lat}, {lon}) is outside the area the model was trained on ({bundle.metadata['region']}: "
            f"lon {west}..{east}, lat {south}..{north})"
        )
    return float(bundle.lat[i]), float(bundle.lon[j])


def build_features(bundle: ModelBundle, source: FeatureSource, lat: float, lon: float, month: pd.Timestamp) -> dict:
    """Return the model features of one pixel and month, named and ordered as in training."""
    meta = bundle.metadata
    month = pd.Timestamp(month).to_period("M").to_timestamp()
    lags = list(meta["lai_lags"])
    lag_months = [month - pd.DateOffset(months=lag) for lag in lags]
    lai = source.monthly_lai(lat, lon, lag_months)
    values = np.array([lai.get(m, np.nan) for m in lag_months], dtype=float)
    a, b = min(lags), max(lags)

    features: dict = {f"lai_lag_{lag}": v for lag, v in zip(lags, values)}
    valid = np.isfinite(values)
    features[f"lai_mean_{a}_{b}"] = float(np.mean(values[valid])) if valid.any() else np.nan
    features[f"lai_min_{a}_{b}"] = float(np.min(values[valid])) if valid.any() else np.nan
    features[f"lai_max_{a}_{b}"] = float(np.max(values[valid])) if valid.any() else np.nan
    features[f"lai_std_{a}_{b}"] = float(np.std(values[valid])) if valid.any() else np.nan
    features[f"lai_valid_months_{a}_{b}"] = int(valid.sum())
    if not valid.any():
        raise FeatureError(
            f"No leaf area data for ({lat}, {lon}) between {lag_months[-1]:%Y-%m} and {lag_months[0]:%Y-%m}: "
            "the pixel is not vegetated, or the month is too far from the available data"
        )

    if meta.get("use_terrain"):
        features["elevation_m"], features["slope_deg"] = source.terrain(lat, lon)
    if meta.get("use_land_cover"):
        year, igbp = source.land_cover(lat, lon, month.year - int(meta["land_cover_lag_years"]))
        features["land_cover"] = igbp if igbp in hs.IGBP_CLASSES else np.nan
        features["land_cover_year"] = year
    return features


def predict(bundle: ModelBundle, features: dict) -> tuple[float, float]:
    """Return (hotspot_score, hotspot_probability) for one feature row.

    hotspot_score is the model output on the sampled scale (1 hotspot : 11 no-hotspot rows);
    hotspot_probability undoes the negative sampling: the estimated real chance of at least one hotspot.
    """
    row = pd.DataFrame([{name: features.get(name, np.nan) for name in bundle.features}])
    # [AI] Same categories as in training, otherwise XGBoost would encode the single row's class differently.
    if "land_cover" in row:
        row["land_cover"] = pd.Categorical(row["land_cover"], categories=bundle.metadata["land_cover_categories"])
    score = float(bundle.model.predict_proba(row)[:, 1][0])
    return score, float(hs.corrected_probability(np.array([score]), bundle.metadata["negative_rate"])[0])


class ApiFeatureSource:
    """AppEEARS-derived values from HTTP endpoints; `{base}` is e.g. `https://host/api/v1/data-model` (see src/routers/api_data_model/).

    GET {base}/lai?lat=&lon=&start=YYYY-MM&end=YYYY-MM -> {"data": [{"month": "YYYY-MM", "lai": float | null}, ...]}
        monthly mean of good-quality MCD15A3H Lai_500m composites of the 500 m pixel
    GET {base}/terrain?lat=&lon= -> {"data": {"elevation_m": float, "slope_deg": float}}
        SRTMGL3 elevation and slope averaged over the 500 m pixel
    GET {base}/land-cover?lat=&lon=&max_year=YYYY -> {"data": {"year": int | null, "igbp_class": int | null}}
        MCD12Q1 LC_Type1 of the latest year <= max_year
    """

    def __init__(self, base_url: str, timeout: float = 60):
        if not base_url:
            raise ValueError("APPEEARS_DATA_URL is not set")
        self.base_url, self.timeout = base_url.rstrip("/"), timeout

    def _get(self, path: str, params: dict) -> dict | list:
        """[AI] GET `{base_url}/{path}` and return the `data` field of the ApiResponse-style envelope."""
        response = httpx.get(f"{self.base_url}/{path}", params=params, timeout=self.timeout)
        response.raise_for_status()
        return response.json()["data"]

    def monthly_lai(self, lat: float, lon: float, months: list[pd.Timestamp]) -> dict[pd.Timestamp, float]:
        rows = self._get("lai", {"lat": lat, "lon": lon, "start": f"{min(months):%Y-%m}", "end": f"{max(months):%Y-%m}"})
        return {pd.Timestamp(r["month"]): (np.nan if r["lai"] is None else float(r["lai"])) for r in rows}

    def terrain(self, lat: float, lon: float) -> tuple[float, float]:
        data = self._get("terrain", {"lat": lat, "lon": lon})
        return float(data["elevation_m"]), float(data["slope_deg"])

    def land_cover(self, lat: float, lon: float, max_year: int) -> tuple[int | None, float]:
        data = self._get("land-cover", {"lat": lat, "lon": lon, "max_year": max_year})
        return data["year"], (np.nan if data["igbp_class"] is None else float(data["igbp_class"]))


class LocalFeatureSource:
    """Values from the AppEEARS files already downloaded (`data/AppEEARS/<Region>/` and its `extra/` folder)."""

    def __init__(self, appeears_dir: str | Path):
        self.appeears_dir = Path(appeears_dir)
        # [AI] Each grid is loaded lazily on first use and kept in memory for later requests.
        self._monthly_lai = None
        self._terrain = None
        self._land_cover = None

    def monthly_lai(self, lat: float, lon: float, months: list[pd.Timestamp]) -> dict[pd.Timestamp, float]:
        from src.utils import geotiff_layers as gl

        if self._monthly_lai is None:  # built once (~20 s), then every request is a lookup
            lai = gl.open_lai(self.appeears_dir / "extra")
            times = pd.DatetimeIndex(lai["time"].values)
            all_months = pd.date_range(times.min().to_period("M").to_timestamp(), times.max(), freq="MS")
            self._monthly_lai = gl.monthly_mean(lai, "lai", all_months)
        grid = self._monthly_lai
        i = int(np.abs(grid["lat"].values - lat).argmin())
        j = int(np.abs(grid["lon"].values - lon).argmin())
        index = grid.indexes["month"]
        return {m: float(grid.values[index.get_loc(m), i, j]) if m in index else np.nan for m in months}

    def terrain(self, lat: float, lon: float) -> tuple[float, float]:
        from src.utils import geotiff_layers as gl

        if self._terrain is None:
            lai = gl.open_lai(self.appeears_dir / "extra")
            self._terrain = gl.terrain_on_grid(gl.open_elevation(self.appeears_dir / "extra"), lai["lat"].values, lai["lon"].values)
        cell = self._terrain.sel(lat=lat, lon=lon, method="nearest")
        return float(cell["elevation_m"]), float(cell["slope_deg"])

    def land_cover(self, lat: float, lon: float, max_year: int) -> tuple[int | None, float]:
        from src.utils import fire_datasets as fd

        if self._land_cover is None:
            self._land_cover = fd.land_cover_grid(fd.open_product("MCD12Q1.061", self.appeears_dir))["land_cover_igbp"].load()
        years = pd.DatetimeIndex(self._land_cover["time"].values).year
        usable = np.flatnonzero(years <= max_year)
        if not len(usable):
            return None, np.nan
        t = int(usable[np.argmax(years[usable])])
        value = float(self._land_cover.isel(time=t).sel(lat=lat, lon=lon, method="nearest"))
        return int(years[t]), value


@lru_cache(maxsize=4)
def cached_local_source(appeears_dir: str) -> LocalFeatureSource:
    """[AI] One LocalFeatureSource per directory, so its in-memory grids are shared across requests."""
    return LocalFeatureSource(appeears_dir)
