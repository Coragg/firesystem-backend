"""Features, chronological splits and walk-forward validation for burn / no-burn forecasting.

Every feature of a row dated in month t is known at least `min(lags)` months before t (12 by default):
- burn lags: whether the row's pixel burned in month t - L (L = 12..24), read from the burned-area grids;
- burn counts: months the pixel burned, and burned pixels in its 3 x 3 neighbourhood, over t-23..t-12;
- vegetation lag: the pixel's NDVI / EVI as of the row's date minus 12 months;
- leaf area lag (optional): the pixel's leaf area index as of the row's date minus 24 months;
- terrain (optional): elevation and slope of the pixel (fixed in time);
- calendar: month and quarter of t (known in advance), and the sensor.
So the model forecasts 12 months ahead: features from month t - 12 and earlier predict month t.
"""

import json
from collections.abc import Callable, Iterable, Sequence
from pathlib import Path

import numpy as np
import pandas as pd
import xarray as xr
from shapely.geometry.base import BaseGeometry
from sklearn.metrics import average_precision_score, brier_score_loss, log_loss, roc_auc_score
from sklearn.model_selection import ParameterGrid

from src.utils import fire_datasets as fd
from src.utils import geotiff_layers as gl

SEASON_START_MONTH = 7          # fire seasons run July-June, so a summer fire season is never split
BURN_LAGS = tuple(range(12, 25))
VEG_LAG_MONTHS = 12
SENSOR_CODES = {"MODIS": 0, "VIIRS": 1}
METRICS = ("roc_auc", "average_precision", "log_loss", "brier")


# ---------------------------------------------------------------------------
# Loading
# ---------------------------------------------------------------------------
def load_training_data(path: str | Path) -> pd.DataFrame:
    """Load a training dataset saved by `fd.save_dataframe` (CSV or JSON) and add a `date` column."""
    path = Path(path)
    if path.suffix == ".json":
        content = json.loads(path.read_text(encoding="utf-8"))
        df = pd.DataFrame(content["data"])
        df.attrs["description"] = content.get("description", {})
    else:
        df = pd.read_csv(path)
        description_path = path.with_name(f"{path.stem}.description.json")
        if description_path.exists():
            df.attrs["description"] = json.loads(description_path.read_text(encoding="utf-8"))
    df["date"] = pd.to_datetime(dict(year=df["year"], month=df["month"], day=df["day"]))
    return df


def month_number(dates: pd.Series | pd.DatetimeIndex) -> np.ndarray:
    """Return a running month index (year * 12 + month - 1)."""
    dates = pd.DatetimeIndex(dates)
    return (dates.year * 12 + dates.month - 1).to_numpy()


# ---------------------------------------------------------------------------
# Burn history and lag features
# ---------------------------------------------------------------------------
def monthly_burn_history(ds: xr.Dataset, region: BaseGeometry | None = None) -> xr.DataArray:
    """Return a (month, lat, lon) grid: 1 = burned that month, 0 = not burned, NaN = no data / outside `region`.

    Months missing from the files are added as NaN, so the month axis is continuous.
    """
    burn_doy = fd.get_layer(ds, "Burn_Date")
    mask = fd.region_mask(ds, region)
    months = pd.DatetimeIndex(ds["time"].values).to_period("M").to_timestamp()
    full = pd.date_range(months.min(), months.max(), freq="MS")
    values = np.full((len(full), ds.sizes["lat"], ds.sizes["lon"]), np.nan, dtype="float32")
    position = {m: i for i, m in enumerate(full)}
    for t, month in enumerate(months):
        doy = burn_doy.isel(time=t).values
        slice_ = np.where(doy > 0, 1.0, np.where(doy == 0, 0.0, np.nan))  # -1 fill, -2 water -> NaN
        slice_[~mask] = np.nan
        current = values[position[month]]
        values[position[month]] = np.where(np.isnan(current), slice_, np.fmax(current, slice_))
    return xr.DataArray(
        values, dims=("month", "lat", "lon"),
        coords={"month": full, "lat": ds["lat"].values, "lon": ds["lon"].values}, name="burned",
    )


def nearest_pixel(grid: xr.DataArray | xr.Dataset, lat: Iterable[float], lon: Iterable[float]) -> tuple[np.ndarray, np.ndarray]:
    """Return the (lat, lon) indices of the nearest grid cell, -1 when farther than half a pixel."""
    def index(coord: str, values: Iterable[float]) -> np.ndarray:
        axis = grid.indexes[coord]
        half_pixel = fd.grid_spacing(axis.values) / 2
        return axis.get_indexer(np.asarray(values, dtype=float), method="nearest", tolerance=half_pixel * 1.01 or None)

    return index("lat", lat), index("lon", lon)


def add_burn_lags(
    rows: pd.DataFrame,
    history: xr.DataArray,
    lags: Sequence[int] = BURN_LAGS,
    window: Sequence[int] | None = None,
    neighbourhood: int = 1,
) -> pd.DataFrame:
    """Return burn-lag features of each row's pixel (rows need `date`, `lat`, `lon`).

    - burned_lag_<L>: 1 / 0 if the pixel burned in month t - L; NaN before the history starts.
    - burned_months_<a>_<b>: months the pixel burned in t-b .. t-a (window, default min(lags) .. min(lags) + 11).
    - nbr_burned_<a>_<b>: burned pixel-months in the (2 * neighbourhood + 1)^2 block around it, same window.
    """
    lags = list(lags)
    window = list(window) if window is not None else list(range(min(lags), min(lags) + 12))
    values = history.values
    n_months, n_lat, n_lon = values.shape
    filled = np.nan_to_num(values, nan=0.0)
    lat_idx, lon_idx = nearest_pixel(history, rows["lat"], rows["lon"])
    in_grid = (lat_idx >= 0) & (lon_idx >= 0)
    month_idx = month_number(rows["date"]) - month_number(history.indexes["month"][:1])[0]

    def lookup(lag: int, array: np.ndarray, d_lat: int = 0, d_lon: int = 0) -> tuple[np.ndarray, np.ndarray]:
        t = month_idx - lag
        la, lo = lat_idx + d_lat, lon_idx + d_lon
        ok = in_grid & (t >= 0) & (t < n_months) & (la >= 0) & (la < n_lat) & (lo >= 0) & (lo < n_lon)
        out = np.full(len(rows), np.nan)
        out[ok] = array[t[ok], la[ok], lo[ok]]
        return out, ok

    features = {f"burned_lag_{lag}": lookup(lag, values)[0] for lag in lags}

    a, b = min(window), max(window)
    months_burned = np.zeros(len(rows))
    nbr_burned = np.zeros(len(rows))
    known = np.ones(len(rows), dtype=bool)
    for lag in window:
        own, ok = lookup(lag, values)
        months_burned += np.nan_to_num(own)
        known &= ok
        for d_lat in range(-neighbourhood, neighbourhood + 1):
            for d_lon in range(-neighbourhood, neighbourhood + 1):
                nbr_burned += np.nan_to_num(lookup(lag, filled, d_lat, d_lon)[0])
    features[f"burned_months_{a}_{b}"] = np.where(known, months_burned, np.nan)
    features[f"nbr_burned_{a}_{b}"] = np.where(known, nbr_burned, np.nan)
    return pd.DataFrame(features, index=rows.index)


def sample_lagged(
    rows: pd.DataFrame,
    grid: xr.Dataset,
    period_days: int,
    description: dict,
    months: int,
    max_age_days: int | None = None,
) -> pd.DataFrame:
    """Sample a time-varying grid as of each row's date minus `months` (rows need `date`, `lat`, `lon`).

    Uses the leakage-free "before" matching of `fd.sample_at_burn_dates` on the shifted dates: the
    pixel's last valid composite ending before (date - months), at most `max_age_days` old.
    Returns the grid's variables plus `source_age_days`, indexed like `rows`.
    """
    shifted = pd.DatetimeIndex(rows["date"]) - pd.DateOffset(months=months)
    frame = pd.DataFrame({
        "year": shifted.year, "quarter": shifted.quarter, "month": shifted.month, "day": shifted.day,
        "lat": rows["lat"].to_numpy(), "lon": rows["lon"].to_numpy(), "burned": 0,
    })
    for col, source in zip(fd.PIXEL_BOUND_COLS, ("lat", "lat", "lon", "lon")):
        frame[col] = frame[source]
    sampled = fd.sample_at_burn_dates(grid, frame, period_days, description, timing="before", max_age_days=max_age_days)
    sampled.index = rows.index
    return sampled[[*grid.data_vars, "source_age_days"]]


def add_lagged_vegetation(
    rows: pd.DataFrame,
    grid: xr.Dataset,
    period_days: int,
    description: dict,
    months: int = VEG_LAG_MONTHS,
    max_age_days: int | None = None,
) -> pd.DataFrame:
    """Return each row's NDVI / EVI as of its date minus `months`.

    Columns: ndvi_lag_<m>, evi_lag_<m>, veg_lag_<m>_age_days.
    """
    sampled = sample_lagged(rows, grid, period_days, description, months, max_age_days)
    return pd.DataFrame({
        f"ndvi_lag_{months}": sampled["ndvi"].to_numpy(),
        f"evi_lag_{months}": sampled["evi"].to_numpy(),
        f"veg_lag_{months}_age_days": sampled["source_age_days"].to_numpy(),
    }, index=rows.index)


def add_lagged_lai(
    rows: pd.DataFrame,
    lai: xr.Dataset,
    months: int = gl.LAI_LAG_MONTHS,
    max_age_days: int | None = gl.LAI_MAX_AGE_DAYS,
) -> pd.DataFrame:
    """Return each row's leaf area index as of its date minus `months` (default 24).

    Columns: lai_lag_<m>, lai_lag_<m>_age_days.
    """
    sampled = sample_lagged(rows, lai[["lai"]], gl.LAI_PERIOD_DAYS, gl.LAI_DESCRIPTION, months, max_age_days)
    return pd.DataFrame({
        f"lai_lag_{months}": sampled["lai"].to_numpy(),
        f"lai_lag_{months}_age_days": sampled["source_age_days"].to_numpy(),
    }, index=rows.index)


def add_terrain(rows: pd.DataFrame, terrain: xr.Dataset, lat: Iterable[float], lon: Iterable[float]) -> pd.DataFrame:
    """Return elevation_m and slope_deg at the given points (the rows' pixel centres), NaN outside the DEM."""
    lat_idx, lon_idx = nearest_pixel(terrain, lat, lon)
    ok = (lat_idx >= 0) & (lon_idx >= 0)
    out = {}
    for var in ("elevation_m", "slope_deg"):
        values = np.full(len(rows), np.nan)
        values[ok] = terrain[var].values[lat_idx[ok], lon_idx[ok]]
        out[var] = values
    return pd.DataFrame(out, index=rows.index)


def pixel_centres(rows: pd.DataFrame, history: xr.DataArray) -> pd.DataFrame:
    """Return pixel_lat / pixel_lon: the centre of the 500 m burned-area pixel holding each row (NaN outside).

    Burn rows sit on pixel centres while generated no-burn rows are spread inside the pixel, so
    location and terrain features use the pixel centre to describe both the same way.
    """
    lat_idx, lon_idx = nearest_pixel(history, rows["lat"], rows["lon"])
    inside = (lat_idx >= 0) & (lon_idx >= 0)
    lat = np.where(inside, history["lat"].values[np.clip(lat_idx, 0, None)], np.nan)
    lon = np.where(inside, history["lon"].values[np.clip(lon_idx, 0, None)], np.nan)
    return pd.DataFrame({"pixel_lat": lat, "pixel_lon": lon}, index=rows.index)


def add_calendar_features(rows: pd.DataFrame) -> pd.DataFrame:
    """Return month, quarter, cyclic month encoding and the sensor code (all known in advance)."""
    month = pd.DatetimeIndex(rows["date"]).month.to_numpy()
    features = pd.DataFrame({
        "month": month,
        "quarter": (month - 1) // 3 + 1,
        "month_sin": np.sin(2 * np.pi * month / 12),
        "month_cos": np.cos(2 * np.pi * month / 12),
    }, index=rows.index)
    if "sensor" in rows:
        features["sensor_code"] = rows["sensor"].map(SENSOR_CODES).to_numpy()
    return features


def build_forecast_features(
    rows: pd.DataFrame,
    directory: str | Path,
    datasets: dict[str, dict] = fd.DATASETS,
    lags: Sequence[int] = BURN_LAGS,
    veg_lag_months: int = VEG_LAG_MONTHS,
    region: BaseGeometry | None = None,
    histories: dict[str, xr.DataArray] | None = None,
    extra_dir: str | Path | None = None,
    lai_lag_months: int = gl.LAI_LAG_MONTHS,
) -> pd.DataFrame:
    """Add lagged features to the rows (date, lat, lon, sensor) from the NetCDF files in `directory`.

    Each sensor's rows get the lags of that sensor's burned area and vegetation products.
    extra_dir: folder with the AppEEARS GeoTIFFs of leaf area (MCD15A3H Lai_500m + FparLai_QC) and/or
    elevation (SRTMGL3 DEM); adds lai_lag_<lai_lag_months> and elevation_m / slope_deg when present.
    Keeps the original columns needed to identify the row (date, lat, lon, sensor, burned if present).
    `histories` caches the monthly burn grids per sensor (filled in place).
    """
    histories = {} if histories is None else histories
    keep = [c for c in ("region", "sensor", "date", "lat", "lon", "burned") if c in rows]
    lai = terrain = None
    if extra_dir is not None:
        extra_dir = Path(extra_dir)
        if gl.layer_files(extra_dir, "Lai_500m") or (extra_dir / gl.LAI_NETCDF).exists():
            lai = gl.open_lai(extra_dir)
        if list(extra_dir.glob("*_SRTMGL3_DEM_*.tif")):
            terrain = gl.open_elevation(extra_dir)

    parts = []
    for sensor, sensor_rows in rows.groupby("sensor", sort=False):
        burn_spec, veg_spec = datasets[f"{sensor}_burned_area"], datasets[f"{sensor}_vegetation"]
        if sensor not in histories:
            histories[sensor] = monthly_burn_history(fd.open_product(burn_spec["product"], directory), region)
        veg = fd.vegetation_grid(fd.open_product(veg_spec["product"], directory), veg_spec["good_reliability"])
        centres = pixel_centres(sensor_rows, histories[sensor])
        blocks = [
            sensor_rows[keep],
            centres,
            add_calendar_features(sensor_rows),
            add_burn_lags(sensor_rows, histories[sensor], lags),
            add_lagged_vegetation(sensor_rows, veg, veg_spec["period_days"], veg_spec["description"],
                                  veg_lag_months, veg_spec.get("max_age_days")),
        ]
        if lai is not None:
            blocks.append(add_lagged_lai(sensor_rows, lai, lai_lag_months))
        if terrain is not None:
            blocks.append(add_terrain(sensor_rows, terrain, centres["pixel_lat"], centres["pixel_lon"]))
        parts.append(pd.concat(blocks, axis=1))
    return pd.concat(parts).loc[rows.index]


def forecast_horizon(lags: Sequence[int] = BURN_LAGS, veg_lag_months: int = VEG_LAG_MONTHS) -> int:
    """Return how many months ahead the features allow forecasting (the shortest lag)."""
    return min(min(lags), veg_lag_months)


def feature_columns(df: pd.DataFrame) -> list[str]:
    """Return the model input columns created by `build_forecast_features`."""
    fixed = ["pixel_lat", "pixel_lon", "elevation_m", "slope_deg", "month", "quarter", "month_sin", "month_cos", "sensor_code"]
    lagged = [c for c in df.columns if c.startswith(
        ("burned_lag_", "burned_months_", "nbr_burned_", "ndvi_lag_", "evi_lag_", "veg_lag_", "lai_lag_"))]
    return [c for c in fixed if c in df] + lagged


def future_rows(
    history: xr.DataArray,
    sensor: str,
    months_ahead: int = 12,
    stride: int = 1,
    day: int = 15,
) -> pd.DataFrame:
    """Return rows to forecast: every `stride`-th pixel with data x the `months_ahead` months after the history.

    The pixels are those with at least one valid month in `history` (inside the region).
    `months_ahead` must not exceed `forecast_horizon(...)` of the features used.
    """
    has_data = np.isfinite(history.values).any(axis=0)
    lat_idx, lon_idx = np.nonzero(has_data[::stride, ::stride])
    lat = history["lat"].values[::stride][lat_idx]
    lon = history["lon"].values[::stride][lon_idx]
    last = history.indexes["month"].max()
    months = pd.date_range(last + pd.DateOffset(months=1), periods=months_ahead, freq="MS") + pd.Timedelta(days=day - 1)
    return pd.DataFrame({
        "sensor": sensor,
        "date": np.repeat(months.to_numpy(), len(lat)),
        "lat": np.tile(lat, len(months)),
        "lon": np.tile(lon, len(months)),
    })


# ---------------------------------------------------------------------------
# Chronological splits and walk-forward validation
# ---------------------------------------------------------------------------
def fire_season(dates: pd.Series, start_month: int = SEASON_START_MONTH) -> pd.Series:
    """Return the fire season of each date, named by the year it starts (July 2019 - June 2020 -> 2019)."""
    dates = pd.to_datetime(dates)
    return (dates.dt.year - (dates.dt.month < start_month)).astype(int)


def chronological_split(
    df: pd.DataFrame,
    val_seasons: int = 2,
    test_seasons: int = 2,
    start_month: int = SEASON_START_MONTH,
) -> tuple[pd.DataFrame, dict[str, list[int]]]:
    """Add `season` and `split` (train / val / test) columns: the last seasons are test, the ones before val.

    Returns the dataframe and the seasons of each split.
    """
    out = df.copy()
    out["season"] = fire_season(out["date"], start_month)
    seasons = sorted(out["season"].unique())
    if len(seasons) < val_seasons + test_seasons + 1:
        raise ValueError(f"Need at least {val_seasons + test_seasons + 1} seasons, found {len(seasons)}: {seasons}")
    split_seasons = {
        "train": seasons[: len(seasons) - val_seasons - test_seasons],
        "val": seasons[len(seasons) - val_seasons - test_seasons: len(seasons) - test_seasons],
        "test": seasons[len(seasons) - test_seasons:],
    }
    out["split"] = out["season"].map({s: name for name, ss in split_seasons.items() for s in ss})
    return out, split_seasons


def classification_metrics(y: np.ndarray, p: np.ndarray) -> dict[str, float]:
    """Return ROC AUC, average precision (PR AUC), log loss and Brier score (NaN when undefined)."""
    y, p = np.asarray(y, dtype=int), np.clip(np.asarray(p, dtype=float), 1e-6, 1 - 1e-6)
    both = len(np.unique(y)) == 2
    return {
        "rows": len(y),
        "burn_share": float(y.mean()) if len(y) else np.nan,
        "roc_auc": roc_auc_score(y, p) if both else np.nan,
        "average_precision": average_precision_score(y, p) if both else np.nan,
        "log_loss": log_loss(y, p, labels=[0, 1]) if len(y) else np.nan,
        "brier": brier_score_loss(y, p) if len(y) else np.nan,
    }


def walk_forward(
    df: pd.DataFrame,
    features: list[str],
    target: str,
    make_model: Callable[[], object],
    eval_seasons: Sequence[int],
) -> pd.DataFrame:
    """Expanding-window walk-forward: for each season, fit on all earlier seasons and predict that season.

    Returns one row per predicted sample: its index, season, label and predicted probability.
    """
    predictions = []
    for season in eval_seasons:
        train, test = df[df["season"] < season], df[df["season"] == season]
        if test.empty:
            continue
        if train[target].nunique() < 2:
            raise ValueError(f"Season {season}: training data before it has a single class")
        model = make_model()
        model.fit(train[features], train[target])
        predictions.append(pd.DataFrame({
            "season": season, "y": test[target].to_numpy(), "p": model.predict_proba(test[features])[:, 1],
        }, index=test.index))
    return pd.concat(predictions) if predictions else pd.DataFrame(columns=["season", "y", "p"])


def score_predictions(predictions: pd.DataFrame) -> pd.DataFrame:
    """Return metrics per season plus an `all` row over every walk-forward prediction."""
    rows = {season: classification_metrics(g["y"], g["p"]) for season, g in predictions.groupby("season")}
    rows["all"] = classification_metrics(predictions["y"], predictions["p"])
    return pd.DataFrame(rows).T.rename_axis("season")


def tune_walk_forward(
    df: pd.DataFrame,
    features: list[str],
    target: str,
    make_model: Callable[[dict], object],
    param_grid: dict[str, list],
    eval_seasons: Sequence[int],
    metric: str = "average_precision",
) -> pd.DataFrame:
    """Score every parameter combination by walk-forward over `eval_seasons`; best first.

    `metric` is computed over all walk-forward predictions; log_loss and brier are minimised, others maximised.
    """
    results = []
    for params in ParameterGrid(param_grid):
        predictions = walk_forward(df, features, target, lambda: make_model(params), eval_seasons)
        scores = classification_metrics(predictions["y"], predictions["p"])
        results.append({**params, **{m: scores[m] for m in METRICS}})
    ascending = metric in ("log_loss", "brier")
    return pd.DataFrame(results).sort_values(metric, ascending=ascending, ignore_index=True)


class SeasonalNaive:
    """Baseline: probability = whether the pixel burned 12 months earlier (`column`), clipped."""

    def __init__(self, column: str = "burned_lag_12", low: float = 0.01, high: float = 0.99):
        self.column, self.low, self.high = column, low, high

    def fit(self, X: pd.DataFrame, y: pd.Series) -> "SeasonalNaive":
        return self

    def predict_proba(self, X: pd.DataFrame) -> np.ndarray:
        p = np.clip(X[self.column].fillna(0).to_numpy(dtype=float), self.low, self.high)
        return np.c_[1 - p, p]


class Climatology:
    """Baseline: probability = burn share of the training rows with the same calendar month (and sensor)."""

    def __init__(self, by: Sequence[str] = ("sensor_code", "month")):
        self.by = list(by)

    def fit(self, X: pd.DataFrame, y: pd.Series) -> "Climatology":
        by = [c for c in self.by if c in X]
        self.by_ = by
        self.rates_ = pd.Series(np.asarray(y, dtype=float), index=X.index).groupby([X[c] for c in by]).mean()
        self.overall_ = float(np.mean(y))
        return self

    def predict_proba(self, X: pd.DataFrame) -> np.ndarray:
        keys = pd.MultiIndex.from_frame(X[self.by_]) if len(self.by_) > 1 else pd.Index(X[self.by_[0]])
        p = self.rates_.reindex(keys).fillna(self.overall_).to_numpy()
        return np.c_[1 - p, p]
