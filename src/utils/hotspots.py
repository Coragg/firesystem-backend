"""FIRMS active fire hotspots (MODIS, VIIRS S-NPP) as monthly labels on a grid, and a sampled pixel-month panel.

Source: FIRMS yearly country archives (no map key needed), e.g.
https://firms.modaps.eosdis.nasa.gov/data/country/viirs-snpp/2020/viirs-snpp_2020_Chile.csv
Columns used: latitude, longitude, acq_date, confidence, type (0 = presumed vegetation fire,
1 = active volcano, 2 = other static land source, 3 = offshore).
Months after the last yearly file come from the FIRMS area API (map key needed; `src.utils.firms`),
standard processing (SP) first and near-real-time (NRT) after it. API rows have no `type`, so
`static_source_mask` drops them in pixels where the archive recorded non-vegetation sources.
"""

import time
import warnings
from datetime import date, timedelta

from collections.abc import Sequence
from pathlib import Path

import numpy as np
import pandas as pd
import requests
import xarray as xr
from shapely.geometry.base import BaseGeometry

from src.utils import fire_datasets as fd

COUNTRY_URL = "https://firms.modaps.eosdis.nasa.gov/data/country/{source}/{year}/{source}_{year}_{country}.csv"
SOURCES = {"MODIS": "modis", "VIIRS": "viirs-snpp"}
FIRST_YEAR = {"MODIS": 2000, "VIIRS": 2012}
VEGETATION_FIRE_TYPE = 0
MODIS_MIN_CONFIDENCE = 30           # 0-100; FIRMS classes: < 30 low, 30-79 nominal, >= 80 high
VIIRS_CONFIDENCE = ("n", "h")       # l = low, n = nominal, h = high


def download_country_year(
    sensor: str, year: int, directory: str | Path, country: str = "Chile", timeout: float = 300
) -> Path | None:
    """Download one FIRMS yearly country CSV (skipped if present); None if FIRMS has no file for that year."""
    source = SOURCES[sensor]
    path = Path(directory) / f"{source}_{year}_{country}.csv"
    if path.exists():
        return path
    path.parent.mkdir(parents=True, exist_ok=True)
    with requests.get(COUNTRY_URL.format(source=source, year=year, country=country), stream=True, timeout=timeout) as response:
        if response.status_code == 404:
            return None
        response.raise_for_status()
        partial = path.with_name(path.name + ".part")
        with partial.open("wb") as out:
            for chunk in response.iter_content(chunk_size=1024 * 1024):
                out.write(chunk)
        partial.replace(path)
    return path


def download_hotspots(
    directory: str | Path, start_year: int, stop_year: int, sensors: Sequence[str] = tuple(SOURCES), country: str = "Chile"
) -> list[Path]:
    """Download every available yearly file for the sensors and years; returns the files found."""
    paths = []
    for sensor in sensors:
        for year in range(max(start_year, FIRST_YEAR[sensor]), stop_year + 1):
            path = download_country_year(sensor, year, directory, country)
            if path is not None:
                paths.append(path)
    return paths


API_SOURCES = {"MODIS": ("MODIS_SP", "MODIS_NRT"), "VIIRS": ("VIIRS_SNPP_SP", "VIIRS_SNPP_NRT")}
API_MAX_DAYS = 5


def api_chunks(start: date, stop: date, days: int = API_MAX_DAYS) -> list[tuple[date, int]]:
    """Split [start, stop] into (first day, number of days <= `days`) API requests."""
    chunks, day = [], start
    while day <= stop:
        n = min(days, (stop - day).days + 1)
        chunks.append((day, n))
        day += timedelta(days=n)
    return chunks


def download_api_hotspots(
    directory: str | Path,
    bbox: tuple[float, float, float, float],
    start: date,
    stop: date,
    availability: dict[str, tuple[date, date]],
    sensors: Sequence[str] = tuple(SOURCES),
    pause_seconds: float = 0.2,
    refresh_days: int = 2,
) -> list[Path]:
    """Download FIRMS area-API CSVs for [start, stop], SP where available and NRT after it.

    availability: {data_id: (min_date, max_date)} from `firms.fetch_sources()`. Each request is saved as
    `api_<SOURCE>_<first day>_<days>.csv` and skipped if present, except near-real-time chunks that reach
    the last `refresh_days` days of the source's availability: FIRMS may still be adding detections there,
    so they are downloaded again on every run (standard-processing data is final once published).
    """
    from src.utils import firms  # needs FIRMS_MAP_KEY / FIRMS_BASE_URL in the environment

    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    west, south, east, north = bbox
    area = f"{west},{south},{east},{north}"
    paths = []
    for sensor in sensors:
        sensor_start = start
        for source in API_SOURCES[sensor]:  # SP first, then NRT from the day after the SP range
            if source not in availability:
                continue
            first, last = availability[source]
            lo, hi = max(sensor_start, first), min(stop, last)
            if lo > hi:
                continue
            for day, n in api_chunks(lo, hi):
                path = directory / f"api_{source}_{day:%Y%m%d}_{n}.csv"
                recent = source.endswith("_NRT") and day + timedelta(days=n - 1) > last - timedelta(days=refresh_days)
                if recent or not path.exists():
                    rows = firms.fetch_hotspots(source, area, n, day.isoformat())
                    pd.DataFrame(rows).to_csv(path, index=False)
                    time.sleep(pause_seconds)
                paths.append(path)
            sensor_start = hi + timedelta(days=1)
    return paths


def static_source_mask(
    paths: Sequence[str | Path], lat: np.ndarray, lon: np.ndarray, radius: int = 1
) -> np.ndarray:
    """Return a (lat, lon) mask of grid cells (and `radius` neighbours) with non-vegetation archive detections.

    Uses the yearly files' `type` column (1 volcano, 2 static land source, 3 offshore); API rows inside
    these cells are dropped since the API has no `type`.
    """
    mask = np.zeros((len(lat), len(lon)), dtype=bool)
    lat_axis, lon_axis = pd.Index(lat), pd.Index(lon)
    for path in paths:
        if Path(path).name.startswith("api_"):
            continue
        df = pd.read_csv(path, usecols=lambda c: c in ("latitude", "longitude", "type"))
        if "type" not in df:
            continue
        df = df[df["type"] != VEGETATION_FIRE_TYPE]
        i = lat_axis.get_indexer(df["latitude"].to_numpy(), method="nearest", tolerance=fd.grid_spacing(lat) / 2 * 1.01)
        j = lon_axis.get_indexer(df["longitude"].to_numpy(), method="nearest", tolerance=fd.grid_spacing(lon) / 2 * 1.01)
        ok = (i >= 0) & (j >= 0)
        mask[i[ok], j[ok]] = True
    if radius:
        padded = np.pad(mask, radius)
        rows, cols = mask.shape
        grown = np.zeros_like(mask)
        for di in range(2 * radius + 1):
            for dj in range(2 * radius + 1):
                grown |= padded[di:di + rows, dj:dj + cols]
        mask = grown
    return mask


def sensor_of(path: str | Path) -> str:
    """Return the sensor of a FIRMS file ('viirs-snpp_2020_Chile.csv' or 'api_VIIRS_SNPP_NRT_...csv' -> 'VIIRS')."""
    name = Path(path).name
    if name.startswith("api_"):
        source = name[4:]
        for sensor, ids in API_SOURCES.items():
            if any(source.startswith(i + "_") for i in ids):
                return sensor
    prefix = name.split("_")[0]
    for sensor, source in SOURCES.items():
        if prefix == source:
            return sensor
    raise ValueError(f"Unknown FIRMS source in {name}")


def confident(df: pd.DataFrame) -> pd.Series:
    """Return True for nominal/high-confidence detections (MODIS >= 30, VIIRS 'n' or 'h')."""
    conf = df["confidence"].astype(str).str.strip().str.lower()
    numeric = pd.to_numeric(conf, errors="coerce")
    return np.where(df["sensor"] == "MODIS", numeric >= MODIS_MIN_CONFIDENCE, conf.isin(VIIRS_CONFIDENCE))


def processing_of(path: str | Path) -> str:
    """Return 'SP' (standard processing: yearly files and API *_SP chunks) or 'NRT' for a FIRMS file."""
    name = Path(path).name
    return "NRT" if name.startswith("api_") and "_NRT_" in name else "SP"


def covered_until(path: str | Path) -> date:
    """Return the last day a FIRMS file covers (31 Dec for yearly files; first day + days - 1 for API chunks)."""
    name = Path(path).name
    if name.startswith("api_"):
        first, days = name.removesuffix(".csv").split("_")[-2:]
        return date(int(first[:4]), int(first[4:6]), int(first[6:])) + timedelta(days=int(days) - 1)
    return date(int(name.split("_")[1]), 12, 31)


def load_hotspots(
    paths: Sequence[str | Path],
    region: BaseGeometry | None = None,
    vegetation_only: bool = True,
    confident_only: bool = True,
) -> pd.DataFrame:
    """Load FIRMS country files into one dataframe: sensor, date, lat, lon, confidence, frp, daynight.

    Keeps vegetation fires (type 0, when the column exists) with nominal/high confidence, inside `region`.
    Removes overlaps between files: near-real-time (NRT) rows on days that standard-processing (SP) files
    of the same sensor already cover, and exact duplicates (same sensor, date, time and location), which
    appear when the same days were downloaded in different chunks.
    """
    sp_until: dict[str, date] = {}
    for path in paths:
        if processing_of(path) == "SP":
            sensor = sensor_of(path)
            sp_until[sensor] = max(sp_until.get(sensor, date.min), covered_until(path))
    parts = []
    for path in paths:
        try:
            df = pd.read_csv(path, dtype={"confidence": str, "acq_time": str})
        except pd.errors.EmptyDataError:  # API chunk without detections
            continue
        if df.empty:
            continue
        sensor = sensor_of(path)
        df.insert(0, "sensor", sensor)
        df["api"] = Path(path).name.startswith("api_")
        df["processing"] = processing_of(path)
        if df["processing"].iloc[0] == "NRT" and sensor in sp_until:
            df = df[pd.to_datetime(df["acq_date"]).dt.date > sp_until[sensor]]
        parts.append(df)
    if not parts:
        return pd.DataFrame(columns=["sensor", "date", "lat", "lon", "confidence", "frp", "daynight", "api", "processing"])
    df = pd.concat(parts, ignore_index=True)
    time_col = df["acq_time"].astype(str).str.zfill(4) if "acq_time" in df else ""
    df = df.assign(_time=time_col).drop_duplicates(["sensor", "acq_date", "_time", "latitude", "longitude"])
    keep = np.ones(len(df), dtype=bool)
    if vegetation_only and "type" in df:
        keep &= df["type"].isna().to_numpy() | (df["type"].to_numpy() == VEGETATION_FIRE_TYPE)  # NaN: API rows
    if confident_only:
        keep &= confident(df)
    df = df[keep]
    if region is not None:
        df = df[fd.points_in_geometry(region, df["latitude"].to_numpy(), df["longitude"].to_numpy())]
    out = pd.DataFrame({
        "sensor": df["sensor"].to_numpy(),
        "date": pd.to_datetime(df["acq_date"]).to_numpy(),
        "lat": df["latitude"].to_numpy(dtype=float),
        "lon": df["longitude"].to_numpy(dtype=float),
        "confidence": df["confidence"].to_numpy(),
        "frp": pd.to_numeric(df.get("frp"), errors="coerce").to_numpy() if "frp" in df else np.nan,
        "daynight": df["daynight"].to_numpy() if "daynight" in df else None,
        "api": df["api"].to_numpy(),
        "processing": df["processing"].to_numpy(),
    })
    return out.sort_values(["date", "lat", "lon"], ignore_index=True)


def monthly_hotspot_grid(
    hotspots: pd.DataFrame, lat: np.ndarray, lon: np.ndarray, months: pd.DatetimeIndex,
    api_exclude: np.ndarray | None = None,
) -> xr.DataArray:
    """Count hotspots per (month, lat, lon) cell of a grid (nearest cell centre, within half a pixel).

    api_exclude: (lat, lon) mask of cells where API rows (no `type`) are dropped, e.g. `static_source_mask`.
    """
    months = pd.DatetimeIndex(months).to_period("M").to_timestamp()
    counts = np.zeros((len(months), len(lat), len(lon)), dtype="int32")
    grid = xr.DataArray(counts, dims=("month", "lat", "lon"), coords={"month": months, "lat": lat, "lon": lon}, name="hotspots")
    if hotspots.empty:
        return grid
    lat_axis, lon_axis = grid.indexes["lat"], grid.indexes["lon"]
    lat_idx = lat_axis.get_indexer(hotspots["lat"].to_numpy(), method="nearest", tolerance=fd.grid_spacing(lat) / 2 * 1.01)
    lon_idx = lon_axis.get_indexer(hotspots["lon"].to_numpy(), method="nearest", tolerance=fd.grid_spacing(lon) / 2 * 1.01)
    month_idx = grid.indexes["month"].get_indexer(pd.DatetimeIndex(hotspots["date"]).to_period("M").to_timestamp())
    ok = (lat_idx >= 0) & (lon_idx >= 0) & (month_idx >= 0)
    if api_exclude is not None and "api" in hotspots:
        api = hotspots["api"].to_numpy(dtype=bool)
        ok[ok & api] &= ~api_exclude[lat_idx[ok & api], lon_idx[ok & api]]
    np.add.at(counts, (month_idx[ok], lat_idx[ok], lon_idx[ok]), 1)
    return grid


def sample_panel(
    hotspot_grid: xr.DataArray,
    valid: np.ndarray,
    first_month: pd.Timestamp,
    negatives_per_positive: float = 11,
    seed: int = 42,
) -> tuple[pd.DataFrame, float]:
    """Return a sampled pixel-month panel and the negative sampling rate.

    Keeps every (month, pixel) with a hotspot from `first_month` on (label 1) and samples pixel-months
    without one (label 0) at the same rate in every month, so the negatives keep the real time distribution:
    rate = negatives_per_positive x positives / all negatives (capped at 1). Only pixels where `valid`
    is True (inside the region, with data) are used. Columns: date (1st of month), month_idx, lat_idx,
    lon_idx, lat, lon, hotspot (label), hotspot_count.
    """
    rng = np.random.default_rng(seed)
    months = hotspot_grid.indexes["month"]
    use = np.flatnonzero(months >= first_month)
    counts = hotspot_grid.values
    valid_flat = np.flatnonzero(valid.ravel())

    positives = [(t, counts[t].ravel()[valid_flat] > 0) for t in use]
    n_pos = sum(int(p.sum()) for _, p in positives)
    n_neg = sum(int((~p).sum()) for _, p in positives)
    rate = min(1.0, negatives_per_positive * n_pos / n_neg) if n_neg else 0.0

    rows = []
    for t, is_pos in positives:
        pos_cells = valid_flat[is_pos]
        neg_cells = valid_flat[~is_pos]
        n_take = int(rng.binomial(len(neg_cells), rate)) if rate < 1 else len(neg_cells)
        neg_take = rng.choice(neg_cells, size=n_take, replace=False) if n_take else np.empty(0, dtype=int)
        cells = np.concatenate([pos_cells, neg_take])
        lat_idx, lon_idx = np.unravel_index(cells, valid.shape)
        rows.append(pd.DataFrame({
            "date": months[t], "month_idx": t, "lat_idx": lat_idx, "lon_idx": lon_idx,
            "hotspot": np.r_[np.ones(len(pos_cells), int), np.zeros(n_take, int)],
            "hotspot_count": counts[t].ravel()[cells],
        }))
    panel = pd.concat(rows, ignore_index=True) if rows else pd.DataFrame(
        columns=["date", "month_idx", "lat_idx", "lon_idx", "hotspot", "hotspot_count"])
    panel.insert(4, "lat", hotspot_grid["lat"].values[panel["lat_idx"].to_numpy(dtype=int)])
    panel.insert(5, "lon", hotspot_grid["lon"].values[panel["lon_idx"].to_numpy(dtype=int)])
    return panel, rate


def corrected_probability(p: np.ndarray, negative_rate: float) -> np.ndarray:
    """Undo negative subsampling: real odds = sampled odds x negative_rate."""
    p = np.clip(np.asarray(p, dtype=float), 1e-9, 1 - 1e-9)
    odds = p / (1 - p) * negative_rate
    return odds / (1 + odds)


def lag_lookup(panel: pd.DataFrame, grid: xr.DataArray, lag: int, month_offset: int = 0) -> np.ndarray:
    """Return grid[month_idx - lag, lat_idx, lon_idx] for each panel row (NaN before the grid starts).

    month_offset: the panel's month 0 minus the grid's month 0, in months (0 when both start together).
    """
    t = panel["month_idx"].to_numpy(dtype=int) + month_offset - lag
    ok = (t >= 0) & (t < grid.sizes["month"])
    out = np.full(len(panel), np.nan)
    out[ok] = grid.values[t[ok], panel["lat_idx"].to_numpy(dtype=int)[ok], panel["lon_idx"].to_numpy(dtype=int)[ok]]
    return out


def month_offset(panel_months: pd.DatetimeIndex, grid: xr.DataArray) -> int:
    """Return the months between the grid's first month and the panel's first month."""
    first_panel, first_grid = pd.Timestamp(panel_months[0]), pd.Timestamp(grid.indexes["month"][0])
    return (first_panel.year - first_grid.year) * 12 + first_panel.month - first_grid.month


def grid_lag_features(
    panel: pd.DataFrame, monthly: xr.DataArray, lags: Sequence[int], prefix: str, offset: int = 0
) -> pd.DataFrame:
    """Lagged features of a monthly (month, lat, lon) grid on the panel's grid indices.

    <prefix>_lag_<L> for each lag, plus <prefix>_mean/min/max/std_<a>_<b> and <prefix>_valid_months_<a>_<b>
    over the lag window. With lags 24..35 every value is from two to three years before the row's month.
    """
    lags = list(lags)
    values = {f"{prefix}_lag_{lag}": lag_lookup(panel, monthly, lag, offset) for lag in lags}
    stack = np.column_stack(list(values.values()))
    a, b = min(lags), max(lags)
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", RuntimeWarning)  # all-NaN rows -> NaN
        values[f"{prefix}_mean_{a}_{b}"] = np.nanmean(stack, axis=1)
        values[f"{prefix}_min_{a}_{b}"] = np.nanmin(stack, axis=1)
        values[f"{prefix}_max_{a}_{b}"] = np.nanmax(stack, axis=1)
        values[f"{prefix}_std_{a}_{b}"] = np.nanstd(stack, axis=1)
    values[f"{prefix}_valid_months_{a}_{b}"] = np.isfinite(stack).sum(axis=1)
    return pd.DataFrame(values, index=panel.index)


def lai_features(panel: pd.DataFrame, monthly_lai: xr.DataArray, lags: Sequence[int], offset: int = 0) -> pd.DataFrame:
    """Leaf area features: `grid_lag_features` with the `lai` prefix."""
    return grid_lag_features(panel, monthly_lai, lags, "lai", offset)


def hotspot_lag_features(panel: pd.DataFrame, hotspot_grid: xr.DataArray, lags: Sequence[int]) -> pd.DataFrame:
    """hotspot_lag_<L>: 1 if the pixel had a hotspot in month t - L (NaN before the data starts)."""
    out = {}
    for lag in lags:
        counts = lag_lookup(panel, hotspot_grid, lag)
        out[f"hotspot_lag_{lag}"] = np.where(np.isnan(counts), np.nan, counts > 0)
    return pd.DataFrame(out, index=panel.index)


def future_panel(valid: np.ndarray, grid_months: pd.DatetimeIndex, lat: np.ndarray, lon: np.ndarray,
                 months_ahead: int, stride: int = 1) -> pd.DataFrame:
    """Return every `stride`-th valid pixel x the `months_ahead` months after `grid_months`.

    month_idx continues the grid's month index, so lags look back into the same grids.
    """
    sub = np.zeros_like(valid)
    sub[::stride, ::stride] = valid[::stride, ::stride]
    lat_idx, lon_idx = np.nonzero(sub)
    last = pd.Timestamp(grid_months[-1])
    months = pd.date_range(last + pd.DateOffset(months=1), periods=months_ahead, freq="MS")
    n = len(lat_idx)
    return pd.DataFrame({
        "date": np.repeat(months.to_numpy(), n),
        "month_idx": np.repeat(np.arange(len(grid_months), len(grid_months) + months_ahead), n),
        "lat_idx": np.tile(lat_idx, months_ahead),
        "lon_idx": np.tile(lon_idx, months_ahead),
        "lat": np.tile(lat[lat_idx], months_ahead),
        "lon": np.tile(lon[lon_idx], months_ahead),
    })


IGBP_CLASSES = {
    1: "Evergreen needleleaf forest", 2: "Evergreen broadleaf forest", 3: "Deciduous needleleaf forest",
    4: "Deciduous broadleaf forest", 5: "Mixed forest", 6: "Closed shrublands", 7: "Open shrublands",
    8: "Woody savannas", 9: "Savannas", 10: "Grasslands", 11: "Permanent wetlands", 12: "Croplands",
    13: "Urban and built-up", 14: "Cropland / natural vegetation mosaic", 15: "Permanent snow and ice",
    16: "Barren", 17: "Water bodies",
}


def land_cover_features(panel: pd.DataFrame, land_cover: xr.DataArray, lag_years: int = 2) -> pd.DataFrame:
    """Return `land_cover` (IGBP class 1-17, categorical) and `land_cover_year` for each pixel-month row.

    A row in year Y gets the class of the latest land cover year <= Y - lag_years (a fire in year Y can
    change that year's class, so the same year would leak). Pixels are matched by the rows' lat / lon to
    the nearest land cover cell within half a pixel; missing or fill (255) values are NaN.
    land_cover: (time, lat, lon) from `fd.land_cover_grid(...)["land_cover_igbp"]`.
    """
    years = pd.DatetimeIndex(land_cover["time"].values).year.to_numpy()
    order = np.argsort(years)
    years = years[order]
    lat_idx = land_cover.indexes["lat"].get_indexer(
        panel["lat"].to_numpy(dtype=float), method="nearest", tolerance=fd.grid_spacing(land_cover["lat"].values) / 2 * 1.01)
    lon_idx = land_cover.indexes["lon"].get_indexer(
        panel["lon"].to_numpy(dtype=float), method="nearest", tolerance=fd.grid_spacing(land_cover["lon"].values) / 2 * 1.01)
    target_year = pd.DatetimeIndex(panel["date"]).year.to_numpy() - lag_years
    pos = np.searchsorted(years, target_year, side="right") - 1   # latest year <= target
    ok = (pos >= 0) & (lat_idx >= 0) & (lon_idx >= 0)

    classes = np.full(len(panel), np.nan)
    values = land_cover.values
    classes[ok] = values[order[pos[ok]], lat_idx[ok], lon_idx[ok]]
    classes[~np.isin(classes, list(IGBP_CLASSES))] = np.nan
    year = np.where(ok, years[np.clip(pos, 0, None)], np.nan)
    return pd.DataFrame({
        "land_cover": pd.Categorical(classes, categories=list(IGBP_CLASSES)),
        "land_cover_year": year,
    }, index=panel.index)
