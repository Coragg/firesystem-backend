"""Build burned-area, vegetation, LST and land-cover dataframes from AppEEARS NetCDF downloads.

Pipeline:
1. `build_dataframes` reads the downloaded NetCDF files and returns one dataframe per dataset.
   Burned-area dataframes contain observed burns (burned=1) plus generated no-burn samples
   (burned=0) with a random day and location; every other dataset is also sampled at those
   exact dates and locations (`<name>_at_burn_dates`).
2. `build_training_dataset` joins burned area (label) with vegetation (features).
"""

import json
from datetime import datetime
from pathlib import Path

import numpy as np
import pandas as pd
import xarray as xr

from shapely.geometry.base import BaseGeometry

from src.utils.appeears import BBox
from src.utils.chile_regions import points_in_geometry

SENSORS = ("MODIS", "VIIRS")

DATASETS: dict[str, dict] = {
    "MODIS_burned_area": {
        "product": "MCD64A1.061",
        "layers": ["Burn_Date", "Burn_Date_Uncertainty"],
        "first_date": datetime(2000, 11, 1),
        "description": {
            "title": "MODIS Terra+Aqua Burned Area (MCD64A1 v061)",
            "variables": {"burn_date_uncertainty": "days"},
            "update_frequency": "Monthly",
            "spatial_granularity": "500 m",
        },
    },
    "VIIRS_burned_area": {
        "product": "VNP64A1.002",
        "layers": ["Burn_Date", "Burn_Date_Uncertainty"],
        "first_date": datetime(2012, 3, 1),
        "description": {
            "title": "VIIRS S-NPP Burned Area (VNP64A1 v002)",
            "variables": {"burn_date_uncertainty": "days"},
            "update_frequency": "Monthly",
            "spatial_granularity": "500 m",
        },
    },
    "MODIS_vegetation": {
        "product": "MOD13A1.061",
        "max_age_days": 128,
        "burn_source": "MODIS_burned_area",
        "period_days": 16,
        "layers": ["_500m_16_days_NDVI", "_500m_16_days_EVI", "_500m_16_days_pixel_reliability"],
        "first_date": datetime(2000, 2, 18),
        "good_reliability": [0, 1],
        "description": {
            "title": "MODIS Terra Vegetation Indices (MOD13A1 v061)",
            "variables": {"ndvi": "unitless (-0.2 to 1)", "evi": "unitless (-0.2 to 1)"},
            "update_frequency": "16-day composite",
            "spatial_granularity": "500 m",
        },
    },
    "VIIRS_vegetation": {
        "product": "VNP13A1.002",
        "max_age_days": 128,
        "burn_source": "VIIRS_burned_area",
        "period_days": 16,
        "layers": ["500_m_16_days_NDVI", "500_m_16_days_EVI", "500_m_16_days_pixel_reliability"],
        "first_date": datetime(2012, 1, 17),
        "good_reliability": [0, 1, 2],
        "description": {
            "title": "VIIRS S-NPP Vegetation Indices (VNP13A1 v002)",
            "variables": {"ndvi": "unitless (-1 to 1)", "evi": "unitless (-1 to 1)"},
            "update_frequency": "16-day composite",
            "spatial_granularity": "500 m",
        },
    },
    "MODIS_lst": {
        "product": "MOD11A1.061",
        "max_age_days": 7,
        "burn_source": "MODIS_burned_area",
        "period_days": 1,
        "layers": ["LST_Day_1km", "LST_Night_1km"],
        "first_date": datetime(2000, 2, 24),
        "description": {
            "title": "MODIS Terra Land Surface Temperature (MOD11A1 v061)",
            "variables": {"lst_day_c": "°C", "lst_night_c": "°C"},
            "update_frequency": "Daily",
            "spatial_granularity": "1 km",
        },
    },
    "VIIRS_lst": {
        "product": "VNP21A1D.002",
        "max_age_days": 7,
        "burn_source": "VIIRS_burned_area",
        "period_days": 1,
        "layers": ["LST_1KM"],
        "first_date": datetime(2012, 1, 17),
        "description": {
            "title": "VIIRS S-NPP Daytime Land Surface Temperature (VNP21A1D v002)",
            "variables": {"lst_day_c": "°C"},
            "update_frequency": "Daily",
            "spatial_granularity": "1 km",
        },
    },
    "MODIS_land_cover": {
        "product": "MCD12Q1.061",
        "max_age_days": 731,
        "burn_source": "MODIS_burned_area",
        "period_days": 366,
        "layers": ["LC_Type1"],
        "first_date": datetime(2001, 1, 1),
        "description": {
            "title": "MODIS Terra+Aqua Land Cover Type (MCD12Q1 v061)",
            "variables": {"land_cover_igbp": "IGBP class code (1-17)"},
            "update_frequency": "Yearly",
            "spatial_granularity": "500 m",
        },
    },
}

LST_LAYERS = {
    "MODIS_lst": {"lst_day_c": "LST_Day_1km", "lst_night_c": "LST_Night_1km"},
    "VIIRS_lst": {"lst_day_c": "LST_1KM"},
}

DATE_COLS = ["year", "quarter", "month", "day"]
PIXEL_BOUND_COLS = ["lat_min", "lat_max", "lon_min", "lon_max"]


# ---------------------------------------------------------------------------
# Catalogue helpers
# ---------------------------------------------------------------------------
def active_datasets(stop: datetime, datasets: dict[str, dict] = DATASETS) -> dict[str, dict]:
    """Return the datasets whose record starts on or before `stop`."""
    return {name: d for name, d in datasets.items() if stop >= d["first_date"]}


def request_layers(datasets: dict[str, dict]) -> list[dict[str, str]]:
    """Return the AppEEARS product/layer list for the given datasets."""
    return [{"product": d["product"], "layer": layer} for d in datasets.values() for layer in d["layers"]]


def dataset_context(bbox: BBox, start: datetime, stop: datetime, region_name: str | None = None) -> dict:
    """Return the request metadata added to every dataframe description."""
    return {
        **({"region": region_name} if region_name else {}),
        "bbox": dict(zip(["west", "south", "east", "north"], bbox)),
        "time_range": {"start": f"{start:%Y-%m-%d}", "stop": f"{stop:%Y-%m-%d}"},
        "source": "NASA AppEEARS",
    }


# ---------------------------------------------------------------------------
# NetCDF helpers
# ---------------------------------------------------------------------------
def get_layer(ds: xr.Dataset, layer: str) -> xr.DataArray:
    """Return the data variable whose name ends with the given layer name."""
    matches = [v for v in ds.data_vars if str(v).endswith(layer)]
    if len(matches) != 1:
        raise KeyError(f"Expected one variable ending in {layer!r}, found {matches} in {list(ds.data_vars)}")
    return ds[matches[0]]


def open_product(product: str, directory: str | Path) -> xr.Dataset:
    """Open all downloaded NetCDF files of one AppEEARS product."""
    paths = sorted(Path(directory).glob(f"{product}*.nc"))
    if not paths:
        raise FileNotFoundError(f"No NetCDF files for {product} in {directory}")
    return xr.open_mfdataset(paths, engine="h5netcdf")


def check_grid_in_bbox(ds: xr.Dataset, bbox: BBox, product: str) -> None:
    """Raise if the grid's pixel centres are not inside `bbox` (allowing one pixel of margin).

    Catches NetCDF files downloaded for a different bounding box than the one being processed.
    """
    west, south, east, north = bbox
    lat, lon = ds["lat"].values, ds["lon"].values
    margin_lat, margin_lon = grid_spacing(lat), grid_spacing(lon)
    inside = (
        lat.min() >= south - margin_lat and lat.max() <= north + margin_lat
        and lon.min() >= west - margin_lon and lon.max() <= east + margin_lon
    )
    if not inside:
        raise ValueError(
            f"{product} grid (lat {lat.min():.2f}..{lat.max():.2f}, lon {lon.min():.2f}..{lon.max():.2f}) "
            f"is outside bbox {bbox}. Were the files in the data directory downloaded for another bounding box?"
        )


def grid_spacing(values: np.ndarray) -> float:
    """Return the mean absolute spacing of a 1-D coordinate (0 for a single value)."""
    return float(np.abs(np.diff(values)).mean()) if len(values) > 1 else 0.0


def to_dataframe(
    variables: dict[str, xr.DataArray], description: dict, region: BaseGeometry | None = None
) -> pd.DataFrame:
    """Flatten gridded variables into a date/lat/lon dataframe with pixel bounding boxes.

    region: keep only pixels whose centre is inside this polygon.
    """
    ds = xr.Dataset(variables)
    if region is not None:
        lat2d, lon2d = np.meshgrid(ds["lat"].values, ds["lon"].values, indexing="ij")
        inside = points_in_geometry(region, lat2d.ravel(), lon2d.ravel()).reshape(lat2d.shape)
        ds = ds.where(xr.DataArray(inside, dims=("lat", "lon"), coords={"lat": ds["lat"], "lon": ds["lon"]}))
    df = ds.to_dataframe().reset_index().dropna(subset=list(variables), how="all")
    df = df.rename(columns={"time": "date"})[["date", "lat", "lon", *variables]]

    dlat = grid_spacing(ds["lat"].values)
    dlon = grid_spacing(ds["lon"].values)
    df["lat_min"] = df["lat"] - dlat / 2
    df["lat_max"] = df["lat"] + dlat / 2
    df["lon_min"] = df["lon"] - dlon / 2
    df["lon_max"] = df["lon"] + dlon / 2
    df["date"] = pd.to_datetime(df["date"]).dt.normalize()

    df = df.reset_index(drop=True)
    df.attrs["description"] = {
        **description,
        "grid_spacing_deg": {"lat": round(dlat, 6), "lon": round(dlon, 6)},
    }
    return df


# ---------------------------------------------------------------------------
# Burned area
# ---------------------------------------------------------------------------
def region_mask(ds: xr.Dataset, region: BaseGeometry | None) -> np.ndarray:
    """Return a (lat, lon) boolean mask of the grid cells whose centre is inside `region` (all True if None)."""
    lat, lon = ds["lat"].values, ds["lon"].values
    if region is None:
        return np.ones((len(lat), len(lon)), dtype=bool)
    lat2d, lon2d = np.meshgrid(lat, lon, indexing="ij")
    return points_in_geometry(region, lat2d.ravel(), lon2d.ravel()).reshape(lat2d.shape)


def build_burned_area(ds: xr.Dataset, description: dict, region: BaseGeometry | None = None) -> pd.DataFrame:
    """Keep only burned pixels (inside `region` if given) and split the day of burn into year, quarter, month, day.

    Reads one monthly composite at a time and keeps only burned cells, so memory grows with the number
    of burned pixels, not with the size of the grid times the number of months.
    """
    burn_doy = get_layer(ds, "Burn_Date")
    uncertainty = get_layer(ds, "Burn_Date_Uncertainty")
    lat, lon = ds["lat"].values, ds["lon"].values
    mask = region_mask(ds, region)
    times = pd.DatetimeIndex(ds["time"].values).normalize()

    parts = []
    for t in range(len(times)):
        doy = burn_doy.isel(time=t).values
        burned = (doy > 0) & mask  # 0 = unburned, -1 = fill, -2 = water
        rows, cols = np.nonzero(burned)
        if len(rows):
            parts.append(pd.DataFrame({
                "date": times[t],
                "lat": lat[rows],
                "lon": lon[cols],
                "burn_doy": doy[rows, cols].astype(float),
                "burn_date_uncertainty": uncertainty.isel(time=t).values[rows, cols].astype(float),
            }))
    df = pd.concat(parts, ignore_index=True) if parts else pd.DataFrame(
        {"date": pd.Series(dtype="datetime64[ns]"), "lat": [], "lon": [], "burn_doy": [], "burn_date_uncertainty": []}
    )

    dlat, dlon = grid_spacing(lat), grid_spacing(lon)
    df["lat_min"], df["lat_max"] = df["lat"] - dlat / 2, df["lat"] + dlat / 2
    df["lon_min"], df["lon_max"] = df["lon"] - dlon / 2, df["lon"] + dlon / 2
    df.attrs["description"] = {**description, "grid_spacing_deg": {"lat": round(dlat, 6), "lon": round(dlon, 6)}}

    # Burn_Date is the day of year within the monthly composite's year
    burn_date = pd.to_datetime(df["date"].dt.year.astype(str), format="%Y") + pd.to_timedelta(
        df["burn_doy"].astype(int) - 1, unit="D"
    )
    df["year"] = burn_date.dt.year
    df["quarter"] = burn_date.dt.quarter
    df["month"] = burn_date.dt.month
    df["day"] = burn_date.dt.day

    attrs = df.attrs
    df = df.sort_values(["year", "month", "day", "lat", "lon"]).reset_index(drop=True)
    df = df[[*DATE_COLS, "lat", "lon", "burn_date_uncertainty", *PIXEL_BOUND_COLS]]
    df.attrs = attrs
    df.attrs["description"]["variables"] = {
        "year": "year of burn",
        "quarter": "quarter of burn (1-4)",
        "month": "month of burn (1-12)",
        "day": "day of month of burn (1-31)",
        "burn_date_uncertainty": "days",
    }
    df.attrs["description"]["update_frequency"] = "Monthly composite; rows dated by day of burn"
    return df


def burn_dates(burns: pd.DataFrame) -> pd.Series:
    """Return the day of burn of each row as a datetime."""
    return pd.to_datetime(dict(year=burns["year"], month=burns["month"], day=burns["day"]))


def label_fire_events(burns: pd.DataFrame, max_gap_days: int = 5) -> np.ndarray:
    """Group burned pixels into fire events and return one event ID per row.

    Two burn rows belong to the same event when their pixels are the same or 8-neighbours
    and their burn dates are at most `max_gap_days` apart; events are the connected groups.
    """
    n = len(burns)
    if n == 0:
        return np.empty(0, dtype=int)

    dlat = float((burns["lat_max"] - burns["lat_min"]).median())
    dlon = float((burns["lon_max"] - burns["lon_min"]).median())
    cells = pd.DataFrame({
        "i": np.arange(n),
        "row": np.rint((burns["lat"].to_numpy() - burns["lat"].min()) / dlat).astype(int) if dlat else 0,
        "col": np.rint((burns["lon"].to_numpy() - burns["lon"].min()) / dlon).astype(int) if dlon else 0,
        "t": burn_dates(burns).to_numpy(),
    })

    # Candidate pairs: same or neighbouring cell, burned within max_gap_days
    pairs = []
    gap = np.timedelta64(max_gap_days, "D")
    for dr in (-1, 0, 1):
        for dc in (-1, 0, 1):
            shifted = cells.assign(row=cells["row"] + dr, col=cells["col"] + dc)
            m = cells.merge(shifted, on=["row", "col"], suffixes=("", "_n"))
            m = m[(m["i"] < m["i_n"]) & (np.abs(m["t"] - m["t_n"]) <= gap)]
            pairs.append(m[["i", "i_n"]].to_numpy())
    edges = np.concatenate(pairs) if pairs else np.empty((0, 2), dtype=int)

    # Connected components by minimum-label propagation with pointer jumping
    labels = np.arange(n)
    while len(edges):
        before = labels.copy()
        lo = np.minimum(labels[edges[:, 0]], labels[edges[:, 1]])
        np.minimum.at(labels, edges[:, 0], lo)
        np.minimum.at(labels, edges[:, 1], lo)
        labels = labels[labels]
        if np.array_equal(labels, before):
            break
    while not np.array_equal(labels, labels[labels]):
        labels = labels[labels]

    return np.unique(labels, return_inverse=True)[1]


def fire_summary(
    burns: pd.DataFrame,
    by: tuple[str, ...] = ("year", "month"),
    fire_count: str = "events",
    max_gap_days: int = 5,
) -> pd.DataFrame:
    """Count fires per period (default: per year and month) and each period's share (ratio) of all fires.

    fire_count="events": fire events from `label_fire_events`, assigned to the period of their first burn day.
    fire_count="pixels": burned pixel rows per period.
    """
    if fire_count not in ("events", "pixels"):
        raise ValueError(f"fire_count must be 'events' or 'pixels', got {fire_count!r}")
    by = list(by)

    pixels = burns.groupby(by).size().rename("burned_pixels")
    if fire_count == "events":
        dates = burn_dates(burns).to_numpy()
        events = pd.DataFrame({"event": label_fire_events(burns, max_gap_days), "date": dates})
        first = pd.DatetimeIndex(events.groupby("event")["date"].min())
        starts = pd.DataFrame({
            "year": first.year, "quarter": first.quarter, "month": first.month, "day": first.day,
        })[by]
        fires = starts.groupby(by).size().rename("fires")
    else:
        fires = pixels.rename("fires")

    summary = pd.concat([fires, pixels], axis=1).fillna(0).astype(int).sort_index()
    total = summary["fires"].sum()
    summary["ratio"] = summary["fires"] / total if total else 0.0
    return summary.reset_index()[[*by, "fires", "ratio", "burned_pixels"]]


def allocate_samples(
    ratios: pd.Series, available: pd.Series, max_samples: int, redistribute: bool = True
) -> pd.Series:
    """Split `max_samples` across groups proportionally to `ratios`, capped by `available` per group.

    redistribute=True: capacity left over by capped groups goes to the others, so the result sums to
    min(max_samples, available.sum()). redistribute=False: strict proportions; capped groups keep
    their cap and the total can be lower. Integer rounding uses the largest-remainder method.
    """
    if max_samples < 0:
        raise ValueError("max_samples must be >= 0")
    available = available.astype(int)
    target = min(int(max_samples), int(available.sum()))
    alloc = pd.Series(0, index=available.index, dtype=int)
    open_groups = available.index[available > 0]

    while target - alloc.sum() > 0 and len(open_groups):
        remaining = target - alloc.sum()
        weights = ratios.loc[open_groups].clip(lower=0)
        weights = weights / weights.sum() if weights.sum() > 0 else pd.Series(1 / len(open_groups), index=open_groups)
        share = weights * remaining
        add = np.floor(share).astype(int)
        leftover = remaining - add.sum()
        if leftover:
            add.loc[(share - add).sort_values(ascending=False).index[:leftover]] += 1
        alloc.loc[open_groups] += np.minimum(add, available.loc[open_groups] - alloc.loc[open_groups])
        if not redistribute:
            break
        open_groups = open_groups[alloc.loc[open_groups] < available.loc[open_groups]]
    return alloc


def draw_burns(
    burns: pd.DataFrame, alloc: pd.Series, seed: int | None = 42, by: tuple[str, ...] = ("year", "month")
) -> pd.DataFrame:
    """Randomly draw `alloc[period]` burn rows from each period (without replacement)."""
    rng = np.random.default_rng(seed)
    by = list(by)
    keys = burns[by].to_numpy()
    picked = []
    for key, n in alloc.items():
        if n:
            rows = np.flatnonzero((keys == np.atleast_1d(key)).all(axis=1))
            picked.append(rng.choice(rows, size=min(int(n), len(rows)), replace=False))
    index = np.sort(np.concatenate(picked)) if picked else np.empty(0, dtype=int)

    sampled = burns.iloc[index].reset_index(drop=True)
    sampled.attrs = {k: dict(v) if isinstance(v, dict) else v for k, v in burns.attrs.items()}
    return sampled


def sample_burns_by_period(
    burns: pd.DataFrame,
    summary: pd.DataFrame,
    max_burn_samples: int,
    seed: int | None = 42,
    by: tuple[str, ...] = ("year", "month"),
    redistribute: bool = True,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Randomly draw burn rows per period in proportion to each period's fire ratio, up to `max_burn_samples`.

    Returns the sampled burn rows and `summary` with a `burn_samples` column (rows drawn per period).
    """
    by = list(by)
    summary = summary.set_index(by)
    available = burns.groupby(by).size().reindex(summary.index, fill_value=0)
    alloc = allocate_samples(summary["ratio"], available, max_burn_samples, redistribute=redistribute)
    sampled = draw_burns(burns, alloc, seed=seed, by=by)
    summary["burn_samples"] = alloc
    return sampled, summary.reset_index()


def sample_burns_with_allocation(
    burns: pd.DataFrame,
    summary: pd.DataFrame,
    allocation: pd.DataFrame,
    seed: int | None = 42,
    by: tuple[str, ...] = ("year", "month"),
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Draw burn rows per period using a precomputed `allocation` (columns `by` + `burn_samples`).

    Used when the samples were allocated across several regions (see `allocate_across_regions`).
    Counts are capped by the burn rows available in each period.
    """
    by = list(by)
    summary = summary.set_index(by)
    available = burns.groupby(by).size().reindex(summary.index, fill_value=0)
    wanted = allocation.set_index(by)["burn_samples"].reindex(summary.index, fill_value=0).astype(int)
    alloc = np.minimum(wanted, available)
    sampled = draw_burns(burns, alloc, seed=seed, by=by)
    summary["burn_samples"] = alloc
    return sampled, summary.reset_index()


def allocate_across_regions(
    summaries: dict[str, pd.DataFrame],
    max_burn_samples: int,
    by: tuple[str, ...] = ("year", "month"),
    redistribute: bool = True,
) -> tuple[dict[str, pd.DataFrame], pd.DataFrame]:
    """Split `max_burn_samples` across regions and periods in proportion to their number of fires.

    summaries: per region, a `fire_summary` dataframe (columns `by`, fires, burned_pixels).
    Every (region, period) gets max_burn_samples x its share of all fires in all regions (capped by its
    burned pixels, see `allocate_samples`), so each region's total is proportional to its fires and,
    within a region, each period's to its own fires.
    Returns the per-region allocations (columns `by` + burn_samples) and a per-region summary.
    """
    by = list(by)
    frames = [s.assign(region=name) for name, s in summaries.items()]
    combined = (
        pd.concat(frames, ignore_index=True) if frames
        else pd.DataFrame(columns=["region", *by, "fires", "burned_pixels"])
    )
    combined = combined.set_index(["region", *by])
    total = combined["fires"].sum()
    ratios = combined["fires"] / total if total else combined["fires"].astype(float)
    combined["burn_samples"] = allocate_samples(
        ratios, combined["burned_pixels"], max_burn_samples, redistribute=redistribute
    )
    combined = combined.reset_index()

    allocations = {
        name: combined.loc[combined["region"] == name, [*by, "burn_samples"]].reset_index(drop=True)
        for name in summaries
    }
    per_region = (
        combined.groupby("region")[["fires", "burned_pixels", "burn_samples"]].sum()
        .reindex(list(summaries), fill_value=0)
    )
    per_region.insert(1, "ratio", per_region["fires"] / total if total else 0.0)
    return allocations, per_region.reset_index()


def add_unburned_samples(
    df: pd.DataFrame,
    bbox: BBox,
    seed: int | None = 42,
    extent: str = "pixel",
    burned_reference: pd.DataFrame | None = None,
    region: BaseGeometry | None = None,
    max_tries: int = 50,
) -> pd.DataFrame:
    """For each pixel and year, add one random date and location in every month without a burn (burned=0).

    extent="pixel": random lat/lon inside the burned pixel's bounding box, clipped to `bbox`.
    extent="study_area": random lat/lon anywhere inside `bbox`.
    burned_reference: all burn rows (before sampling); months in which a pixel burned there are never
    used for no-burn samples, even if that burn row was not sampled. Defaults to `df`.
    region: random locations are redrawn (up to `max_tries` times) until they fall inside this polygon;
    remaining ones use the pixel centre (extent="pixel") or are dropped (extent="study_area").
    """
    if extent not in ("pixel", "study_area"):
        raise ValueError(f"extent must be 'pixel' or 'study_area', got {extent!r}")

    rng = np.random.default_rng(seed)
    pixel_cols = ["lat", "lon", *PIXEL_BOUND_COLS]
    burned = df.assign(burned=1)
    reference = df if burned_reference is None else burned_reference

    # Every pixel-year x every month, minus the months where the pixel burned
    pixel_years = burned[["year", *pixel_cols]].drop_duplicates()
    all_months = pixel_years.merge(pd.DataFrame({"month": range(1, 13)}), how="cross")
    burned_months = reference[["year", "month", "lat", "lon"]].drop_duplicates()
    unburned = (
        all_months.merge(burned_months, on=["year", "month", "lat", "lon"], how="left", indicator=True)
        .query("_merge == 'left_only'")
        .drop(columns="_merge")
        .reset_index(drop=True)
    )

    # Random day within each month
    month_start = pd.to_datetime(dict(year=unburned["year"], month=unburned["month"], day=1))
    unburned["day"] = rng.integers(1, month_start.dt.days_in_month.to_numpy() + 1)
    unburned["quarter"] = (unburned["month"] - 1) // 3 + 1

    # Random location (redrawn until inside the region, if one is given)
    west, south, east, north = bbox
    half_lat = (unburned["lat_max"] - unburned["lat_min"]).to_numpy() / 2
    half_lon = (unburned["lon_max"] - unburned["lon_min"]).to_numpy() / 2
    centre_lat = (unburned["lat_min"] + unburned["lat_max"]).to_numpy() / 2
    centre_lon = (unburned["lon_min"] + unburned["lon_max"]).to_numpy() / 2
    if extent == "pixel":
        low_lat, high_lat = np.maximum(centre_lat - half_lat, south), np.minimum(centre_lat + half_lat, north)
        low_lon, high_lon = np.maximum(centre_lon - half_lon, west), np.minimum(centre_lon + half_lon, east)
    else:
        n = len(unburned)
        low_lat, high_lat = np.full(n, float(south)), np.full(n, float(north))
        low_lon, high_lon = np.full(n, float(west)), np.full(n, float(east))

    lat = rng.uniform(low_lat, high_lat)
    lon = rng.uniform(low_lon, high_lon)
    if region is not None:
        for _ in range(max_tries):
            outside = ~points_in_geometry(region, lat, lon)
            if not outside.any():
                break
            lat[outside] = rng.uniform(low_lat[outside], high_lat[outside])
            lon[outside] = rng.uniform(low_lon[outside], high_lon[outside])
        outside = ~points_in_geometry(region, lat, lon)
        if extent == "pixel":
            lat[outside], lon[outside] = centre_lat[outside], centre_lon[outside]
            outside[:] = False
    unburned["lat"], unburned["lon"] = lat, lon
    if region is not None and outside.any():
        unburned = unburned.loc[~outside].reset_index(drop=True)
        half_lat, half_lon = half_lat[~outside], half_lon[~outside]
    if extent == "study_area":
        unburned["lat_min"] = unburned["lat"] - half_lat
        unburned["lat_max"] = unburned["lat"] + half_lat
        unburned["lon_min"] = unburned["lon"] - half_lon
        unburned["lon_max"] = unburned["lon"] + half_lon

    unburned["burn_date_uncertainty"] = np.nan
    unburned["burned"] = 0

    columns = [*DATE_COLS, "lat", "lon", "burned", "burn_date_uncertainty", *PIXEL_BOUND_COLS]
    out = (
        pd.concat([burned[columns], unburned[columns]], ignore_index=True)
        .sort_values(["lat", "lon", "year", "month", "day"])
        .reset_index(drop=True)
    )
    description = df.attrs.get("description", {})
    out.attrs["description"] = {
        **description,
        "variables": {
            **description.get("variables", {}),
            "burned": "1 = observed burn; 0 = random day in a month with no burn for that pixel",
        },
        "unburned_sampling": (
            "one uniformly random day per pixel, year and month without a burn; random lat/lon inside "
            + ("the burned pixel's bounding box" if extent == "pixel" else "the study bounding box")
        ),
    }
    return out


# ---------------------------------------------------------------------------
# Gridded features
# ---------------------------------------------------------------------------
def vegetation_grid(ds: xr.Dataset, good_reliability: list[int]) -> xr.Dataset:
    """Return NDVI/EVI masked by pixel reliability."""
    good = get_layer(ds, "_pixel_reliability").isin(good_reliability)
    return xr.Dataset({"ndvi": get_layer(ds, "_NDVI").where(good), "evi": get_layer(ds, "_EVI").where(good)})


def lst_grid(ds: xr.Dataset, layers: dict[str, str]) -> xr.Dataset:
    """Return land surface temperature layers converted from Kelvin to °C."""
    return xr.Dataset({col: get_layer(ds, layer) - 273.15 for col, layer in layers.items()})


def land_cover_grid(ds: xr.Dataset) -> xr.Dataset:
    """Return the IGBP land cover class."""
    return xr.Dataset({"land_cover_igbp": get_layer(ds, "LC_Type1")})


def sample_at_burn_dates(
    grid: xr.Dataset,
    burns: pd.DataFrame,
    period_days: int,
    description: dict,
    timing: str = "before",
    guard_days: int = 1,
    max_age_days: int | None = None,
) -> pd.DataFrame:
    """Sample gridded values at every burn-dataframe row (burned and generated dates/locations).

    timing="before" (default, no leakage): the pixel's last valid observation whose period
    [start, start + period_days) ends at least `burn_date_uncertainty` + `guard_days` days before the
    row's date, so nothing observed on or after the (uncertain) day of burn is used. The value is
    taken as constant up to the date; masked observations are skipped back to earlier ones, up to
    `max_age_days` between the end of the observation period and the date.
    timing="covering": the observation whose period contains the date (may include post-fire data).
    Space: the nearest grid cell, within half a pixel of the row's lat/lon.
    Adds `source_date` (start of the observation used) and `source_age_days` (days from the end of
    its period to the date). Rows without a matching observation keep NaN values.
    """
    if timing not in ("before", "covering"):
        raise ValueError(f"timing must be 'before' or 'covering', got {timing!r}")
    n = len(burns)
    dates = pd.to_datetime(dict(year=burns["year"], month=burns["month"], day=burns["day"])).to_numpy()
    times = pd.DatetimeIndex(grid["time"].values).normalize()
    order = np.argsort(times)
    starts = times[order].to_numpy()
    period = np.timedelta64(period_days, "D")

    def nearest(coord: str, values: pd.Series) -> np.ndarray:
        index = grid.indexes[coord]
        half_pixel = grid_spacing(index.values) / 2
        return index.get_indexer(values.to_numpy(), method="nearest", tolerance=half_pixel * 1.01 or None)

    lat_idx = nearest("lat", burns["lat"])
    lon_idx = nearest("lon", burns["lon"])
    in_grid = (lat_idx >= 0) & (lon_idx >= 0)
    rows_lat, rows_lon = np.clip(lat_idx, 0, None), np.clip(lon_idx, 0, None)

    if timing == "covering":
        pos = np.searchsorted(starts, dates, side="right") - 1
        candidate_ok = (pos >= 0) & (dates - starts[np.clip(pos, 0, None)] < period)
        max_back = 0
    else:
        uncertainty = burns["burn_date_uncertainty"].fillna(0).to_numpy() if "burn_date_uncertainty" in burns else np.zeros(n)
        cutoff = dates - (uncertainty + guard_days).astype("timedelta64[D]")
        pos = np.searchsorted(starts + period, cutoff, side="right") - 1   # last period ending on/before cutoff
        candidate_ok = pos >= 0
        max_back = len(starts)

    values = {var: np.full(n, np.nan) for var in grid.data_vars}
    used = np.full(n, -1)
    pending = in_grid & candidate_ok
    for k in range(max_back + 1):
        current = pos - k
        active = pending & (current >= 0)
        if max_age_days is not None and timing == "before":
            age_ok = (dates - (starts[np.clip(current, 0, None)] + period)) <= np.timedelta64(max_age_days, "D")
            active &= age_ok
        if not active.any():
            break
        for t in np.unique(current[active]):
            rows = np.flatnonzero(active & (current == t))
            sample = {var: grid[var].isel(time=int(order[t])).values[rows_lat[rows], rows_lon[rows]] for var in grid.data_vars}
            found = np.all([np.isfinite(v) for v in sample.values()], axis=0) if timing == "before" else np.ones(len(rows), bool)
            hit = rows[found]
            for var, v in sample.items():
                values[var][hit] = v[found]
            used[hit] = t
            pending[hit] = False

    out = burns[[*DATE_COLS, "lat", "lon", "burned"]].reset_index(drop=True).copy()
    for var in grid.data_vars:
        out[var] = values[var]
    has = used >= 0
    source = np.full(n, np.datetime64("NaT", "ns"), dtype="datetime64[ns]")
    source[has] = starts[used[has]].astype("datetime64[ns]")
    out["source_date"] = source
    out["source_age_days"] = np.where(has, (dates - (source + period)) / np.timedelta64(1, "D"), np.nan)
    out[PIXEL_BOUND_COLS] = burns[PIXEL_BOUND_COLS].to_numpy()

    timing_text = (
        f"last valid observation ending >= burn_date_uncertainty + {guard_days} day(s) before the date"
        + (f", at most {max_age_days} days old" if max_age_days is not None else "")
        + " (no data from the day of burn or later)"
        if timing == "before" else "observation whose period contains the date"
    )
    out.attrs["description"] = {
        **description,
        "variables": {
            **description["variables"],
            "burned": "1 = observed burn; 0 = generated date in a month with no burn",
            "source_date": "start date of the observation/composite the value was taken from",
            "source_age_days": "days from the end of that observation's period to the row's date",
        },
        "time_matching": timing_text,
        "update_frequency": f"Sampled on burn and generated dates (source: {description['update_frequency']})",
    }
    return out


# ---------------------------------------------------------------------------
# Pipeline
# ---------------------------------------------------------------------------
def build_dataframes(
    directory: str | Path,
    bbox: BBox,
    start: datetime,
    stop: datetime,
    seed: int | None = 42,
    extent: str = "pixel",
    datasets: dict[str, dict] | None = None,
    max_burn_samples: int | None = None,
    fire_count: str = "events",
    max_gap_days: int = 5,
    sample_by: tuple[str, ...] = ("year", "month"),
    redistribute: bool = True,
    region: BaseGeometry | None = None,
    region_name: str | None = None,
    burn_allocations: dict[str, pd.DataFrame] | None = None,
    full_grids: bool = True,
    feature_timing: str = "before",
) -> dict[str, pd.DataFrame]:
    """Build one dataframe per active dataset, plus each non-burn dataset sampled on the burn dates.

    max_burn_samples: maximum number of burn rows kept per burned-area dataset. They are drawn at
    random per period (`sample_by`, default year and month), in proportion to each period's share of
    fires (see `fire_summary`); no-burn samples are then generated for the drawn pixels only.
    None keeps every burn row. redistribute: see `allocate_samples`.
    region / region_name: clip every dataframe to this polygon (e.g. a Chilean region from
    `src.utils.chile_regions`) and record its name in the descriptions.
    burn_allocations: per burned-area dataset name, burn rows to draw per period (columns `sample_by`
    + burn_samples), e.g. from `allocate_across_regions`; overrides `max_burn_samples` for that dataset.
    feature_timing: "before" (default) uses each pixel's last valid observation before the date (see
    `sample_at_burn_dates`), avoiding leakage from post-fire observations; "covering" the one containing it.
    full_grids: False skips the full-grid dataframes (`MODIS_vegetation`, `MODIS_lst`, ...), which are the
    largest; the `_at_burn_dates` dataframes used for training are always built.
    Each burned-area dataset also gets a `<name>_fire_summary` dataframe.
    """
    datasets = active_datasets(stop) if datasets is None else datasets
    context = dataset_context(bbox, start, stop, region_name)
    dfs: dict[str, pd.DataFrame] = {}

    # Burned area first: its dates and locations drive the sampling of every other dataset
    for name, d in datasets.items():
        if name.endswith("burned_area"):
            ds = open_product(d["product"], directory)
            check_grid_in_bbox(ds, bbox, d["product"])
            burns = build_burned_area(ds, {**d["description"], **context}, region=region)
            summary = fire_summary(burns, by=sample_by, fire_count=fire_count, max_gap_days=max_gap_days)

            sampled = burns
            allocation = (burn_allocations or {}).get(name)
            if allocation is not None:
                sampled, summary = sample_burns_with_allocation(burns, summary, allocation, seed=seed, by=sample_by)
            elif max_burn_samples is not None:
                sampled, summary = sample_burns_by_period(
                    burns, summary, max_burn_samples, seed=seed, by=sample_by, redistribute=redistribute
                )
            else:
                summary["burn_samples"] = summary["burned_pixels"]

            dfs[name] = add_unburned_samples(
                sampled, bbox, seed=seed, extent=extent, burned_reference=burns, region=region
            )

            no_burn = dfs[name].query("burned == 0").groupby(list(sample_by)).size().rename("no_burn_samples")
            # Outer join: periods with only no-burn samples appear with 0 fires
            summary = summary.merge(no_burn.reset_index(), on=list(sample_by), how="outer")
            count_cols = ["fires", "burned_pixels", "burn_samples", "no_burn_samples"]
            summary[count_cols] = summary[count_cols].fillna(0).astype(int)
            summary["ratio"] = summary["ratio"].fillna(0.0)
            summary = summary.sort_values(list(sample_by)).reset_index(drop=True)
            period = " and ".join(sample_by)
            summary.attrs["description"] = {
                "title": f"Fires per {period} and burn samples drawn ({d['product']})",
                "variables": {
                    "fires": "fire events starting in the period" if fire_count == "events"
                    else "burned pixels in the period",
                    "ratio": "share of all fires in the time range",
                    "burned_pixels": "burned pixel rows available in the period",
                    "burn_samples": "burn rows drawn (proportional to ratio, capped by burned_pixels)",
                    "no_burn_samples": "generated no-burn rows (for the drawn pixels) dated in the period",
                },
                "fire_count": fire_count,
                "max_gap_days": max_gap_days,
                "max_burn_samples": max_burn_samples,
                "redistribute": redistribute,
                **context,
            }
            dfs[f"{name}_fire_summary"] = summary
            if allocation is not None:
                sampling = f"burn rows drawn per region and {period} in proportion to fires (allocated across regions)"
            elif max_burn_samples is not None:
                sampling = f"burn rows drawn per {period} in proportion to fires per {period}, max {max_burn_samples}"
            else:
                sampling = "all burn rows kept"
            dfs[name].attrs["description"]["burn_sampling"] = sampling
            summary.attrs["description"]["burn_sampling"] = sampling

    for name, d in datasets.items():
        if name.endswith("burned_area"):
            continue
        ds = open_product(d["product"], directory)
        check_grid_in_bbox(ds, bbox, d["product"])
        if name.endswith("vegetation"):
            grid = vegetation_grid(ds, d["good_reliability"])
        elif name in LST_LAYERS:
            grid = lst_grid(ds, LST_LAYERS[name])
        elif name.endswith("land_cover"):
            grid = land_cover_grid(ds)
        else:
            continue

        description = {**d["description"], **context}
        if full_grids:
            dfs[name] = to_dataframe(dict(grid.data_vars), description, region=region)

        burns = dfs.get(d.get("burn_source", ""))
        if burns is not None:
            dfs[f"{name}_at_burn_dates"] = sample_at_burn_dates(
                grid, burns, d["period_days"], description, timing=feature_timing, max_age_days=d.get("max_age_days")
            )

        if name.endswith("land_cover"):
            for key in (name, f"{name}_at_burn_dates"):
                if key in dfs:
                    dfs[key]["land_cover_igbp"] = dfs[key]["land_cover_igbp"].round().astype("Int64")
    return dfs


def build_training_dataset(dfs: dict[str, pd.DataFrame], drop_missing: bool = True) -> pd.DataFrame:
    """Combine burned area (label) with vegetation sampled at the same date and location (features).

    Features: year, quarter, month, day, lat, lon, ndvi, evi. Label: burned (1 = burn, 0 = no burn).
    One block per sensor (MODIS, VIIRS) that has both datasets, identified by the `sensor` column.
    """
    feature_cols = [*DATE_COLS, "lat", "lon", "ndvi", "evi", "veg_age_days"]
    key_cols = ["year", "month", "day", "lat", "lon", "burned"]
    parts = []
    context = {}
    time_matching = ""

    for sensor in SENSORS:
        burns = dfs.get(f"{sensor}_burned_area")
        veg = dfs.get(f"{sensor}_vegetation_at_burn_dates")
        if burns is None or veg is None:
            continue

        # Vegetation rows are sampled 1:1 from the burn rows, in the same order
        burns = burns.reset_index(drop=True)
        veg = veg.reset_index(drop=True)
        if len(burns) != len(veg) or not np.allclose(
            burns[key_cols].to_numpy(float), veg[key_cols].to_numpy(float)
        ):
            raise ValueError(f"{sensor}: burn and vegetation rows are not aligned")

        part = veg.rename(columns={"source_age_days": "veg_age_days"})[feature_cols].copy()
        time_matching = veg.attrs.get("description", {}).get("time_matching", time_matching)
        part.insert(0, "sensor", sensor)
        region_name = burns.attrs.get("description", {}).get("region")
        if region_name:
            part.insert(0, "region", region_name)
        part["burned"] = burns["burned"].astype(int)
        parts.append(part)
        context = {
            k: v for k, v in burns.attrs.get("description", {}).items()
            if k in ("region", "bbox", "time_range", "source")
        }

    if not parts:
        raise ValueError("No sensor has both burned area and vegetation dataframes")

    data = pd.concat(parts, ignore_index=True)
    n_total = len(data)
    if drop_missing:
        data = data.dropna(subset=["ndvi", "evi"], how="all").reset_index(drop=True)

    data.attrs["description"] = {
        "title": "Burn / no-burn training dataset with vegetation features",
        "features": {
            "year": "year", "quarter": "quarter (1-4)", "month": "month (1-12)", "day": "day of month",
            "lat": "latitude (deg)", "lon": "longitude (deg)",
            "ndvi": "NDVI: the pixel's last valid 16-day composite before the date (unitless)",
            "evi": "EVI: the pixel's last valid 16-day composite before the date (unitless)",
            "veg_age_days": "days between the end of that composite and the date",
        },
        "time_matching": time_matching,
        "label": {"burned": "1 = observed burn (MCD64A1 / VNP64A1); 0 = generated no-burn sample"},
        "sensor": "MODIS (MCD64A1 + MOD13A1) or VIIRS (VNP64A1 + VNP13A1)",
        "update_frequency": "Daily (burn dates); vegetation from 16-day composites",
        "spatial_granularity": "500 m",
        **context,
        "rows": {"total": n_total, "kept": len(data), "dropped_missing_vegetation": n_total - len(data)},
        "class_balance": {int(k): int(v) for k, v in data["burned"].value_counts().sort_index().items()},
    }
    return data


def build_multi_region_dataset(
    regions: dict[str, dict],
    start: datetime,
    stop: datetime,
    max_burn_samples: int | None = None,
    seed: int | None = 42,
    extent: str = "pixel",
    datasets: dict[str, dict] | None = None,
    fire_count: str = "events",
    max_gap_days: int = 5,
    sample_by: tuple[str, ...] = ("year", "month"),
    redistribute: bool = True,
    drop_missing: bool = True,
    keep_dataframes: bool = False,
    feature_timing: str = "before",
) -> dict:
    """Build one training dataset from several regions, each contributing in proportion to its fires.

    regions: {name: {"directory": NetCDF folder, "bbox": (w, s, e, n), "geometry": region polygon}}.
    1. Burned area of every region is read and its fires counted per `sample_by` period.
    2. `max_burn_samples` (per burned-area dataset, i.e. per sensor) is split across all
       (region, period) groups by their share of all fires (`allocate_across_regions`).
    3. Each region's dataframes are built with its allocation and its training rows are combined.
    max_burn_samples=None keeps every burn row of every region.

    Returns {"training": combined training dataset, "region_summary": fires, ratio and samples per
    region and sensor, "fire_summary": per region and period, "dataframes": {region: dfs} if
    keep_dataframes else {}}.
    """
    datasets = active_datasets(stop) if datasets is None else datasets
    burn_names = [name for name in datasets if name.endswith("burned_area")]
    by = list(sample_by)

    # 1. Fires per region and period
    summaries: dict[str, dict[str, pd.DataFrame]] = {name: {} for name in burn_names}
    for region_name, spec in regions.items():
        context = dataset_context(spec["bbox"], start, stop, region_name)
        for name in burn_names:
            d = datasets[name]
            ds = open_product(d["product"], spec["directory"])
            check_grid_in_bbox(ds, spec["bbox"], d["product"])
            burns = build_burned_area(ds, {**d["description"], **context}, region=spec.get("geometry"))
            summaries[name][region_name] = fire_summary(burns, by=by, fire_count=fire_count, max_gap_days=max_gap_days)

    # 2. Allocation across regions
    allocations: dict[str, dict[str, pd.DataFrame]] = {}
    region_tables = []
    for name in burn_names:
        if max_burn_samples is not None:
            allocations[name], table = allocate_across_regions(summaries[name], max_burn_samples, by, redistribute)
        else:
            rows = [
                {"region": r, "fires": int(s["fires"].sum()), "burned_pixels": int(s["burned_pixels"].sum())}
                for r, s in summaries[name].items()
            ]
            table = pd.DataFrame(rows, columns=["region", "fires", "burned_pixels"])
            total = table["fires"].sum()
            table.insert(2, "ratio", table["fires"] / total if total else 0.0)
            table["burn_samples"] = table["burned_pixels"]
        table.insert(1, "sensor", name.split("_")[0])
        region_tables.append(table)

    # 3. Per-region dataframes and training rows
    trainings, fire_tables, kept = [], [], {}
    for i, (region_name, spec) in enumerate(regions.items()):
        region_alloc = {name: allocations[name][region_name] for name in allocations}
        dfs = build_dataframes(
            spec["directory"], spec["bbox"], start, stop,
            seed=None if seed is None else seed + i, extent=extent, datasets=datasets,
            fire_count=fire_count, max_gap_days=max_gap_days, sample_by=sample_by, redistribute=redistribute,
            region=spec.get("geometry"), region_name=region_name,
            burn_allocations=region_alloc or None, full_grids=keep_dataframes, feature_timing=feature_timing,
        )
        trainings.append(build_training_dataset(dfs, drop_missing=drop_missing))
        for name in burn_names:
            fire_tables.append(dfs[f"{name}_fire_summary"].assign(region=region_name, sensor=name.split("_")[0]))
        if keep_dataframes:
            kept[region_name] = dfs

    training = pd.concat(trainings, ignore_index=True)
    region_summary = pd.concat(region_tables, ignore_index=True)
    counts = training.groupby(["region", "sensor", "burned"]).size().unstack(fill_value=0)
    counts = counts.reindex(columns=[1, 0], fill_value=0).rename(columns={1: "training_burn_rows", 0: "training_no_burn_rows"})
    region_summary = region_summary.merge(counts.reset_index(), on=["region", "sensor"], how="left")
    region_summary[["training_burn_rows", "training_no_burn_rows"]] = (
        region_summary[["training_burn_rows", "training_no_burn_rows"]].fillna(0).astype(int)
    )
    region_summary.attrs["description"] = {
        "title": "Fires, ratio and samples per region and sensor",
        "variables": {
            "fires": "fire events (or burned pixels) in the time range",
            "ratio": "region's share of all fires of that sensor",
            "burned_pixels": "burned pixel rows available",
            "burn_samples": "burn rows allocated (proportional to fires, capped by burned pixels)",
            "training_burn_rows": "burn rows in the training dataset (after dropping missing vegetation)",
            "training_no_burn_rows": "no-burn rows in the training dataset",
        },
        "max_burn_samples": max_burn_samples,
    }

    fire_table = pd.concat(fire_tables, ignore_index=True) if fire_tables else pd.DataFrame()
    if len(fire_table):
        fire_table = fire_table[["region", "sensor", *[c for c in fire_table.columns if c not in ("region", "sensor")]]]

    first = trainings[0].attrs.get("description", {}) if trainings else {}
    training.attrs["description"] = {
        **{k: v for k, v in first.items() if k not in ("region", "bbox", "rows", "class_balance")},
        "title": "Burn / no-burn training dataset with vegetation features (Chilean regions)",
        "regions": {
            r: {"bbox": dict(zip(["west", "south", "east", "north"], spec["bbox"])),
                "rows": int((training["region"] == r).sum())}
            for r, spec in regions.items()
        },
        "region_sampling": (
            f"max {max_burn_samples} burn rows per sensor, allocated to regions and {' and '.join(by)} "
            "in proportion to their number of fires" if max_burn_samples is not None else "all burn rows kept"
        ),
        "rows": {"kept": len(training)},
        "class_balance": {int(k): int(v) for k, v in training["burned"].value_counts().sort_index().items()},
    }
    return {"training": training, "region_summary": region_summary, "fire_summary": fire_table, "dataframes": kept}


# ---------------------------------------------------------------------------
# Size budget
# ---------------------------------------------------------------------------
# Approximate MODIS/VIIRS pixel areas on the sinusoidal grid and raw bytes per pixel and time step
PIXEL_KM2 = {"500 m": 0.2147, "1 km": 0.8588}
RAW_BYTES = {
    "burned_area": 3,   # Burn_Date int16 + Burn_Date_Uncertainty uint8
    "vegetation": 5,    # NDVI int16 + EVI int16 + pixel_reliability int8
    "lst": 4,           # day + night uint16 (VIIRS day only: 2)
    "land_cover": 1,    # LC_Type1 uint8
}
STEPS_PER_YEAR = {"burned_area": 12, "vegetation": 365.25 / 16, "lst": 365.25, "land_cover": 1}
CSV_BYTES_PER_ROW = 100     # training CSV row (region, sensor, date parts, lat/lon, ndvi/evi, burned), rounded up
ROWS_PER_BURN_SAMPLE = 12   # a burn row plus up to 11 generated no-burn rows (one per other month of its year)


def dataset_kind(name: str) -> str:
    """Return the kind of a dataset name ('MODIS_vegetation' -> 'vegetation')."""
    return next(k for k in RAW_BYTES if name.endswith(k))


def estimate_download_bytes(request_km2: float, start: datetime, stop: datetime, datasets: dict[str, dict]) -> float:
    """Estimate the raw (uncompressed) size of an AppEEARS request covering `request_km2`."""
    years = ((stop - start).days + 1) / 365.25
    total = 0.0
    for name, d in datasets.items():
        kind = dataset_kind(name)
        pixels = request_km2 / PIXEL_KM2[d["description"]["spatial_granularity"]]
        bytes_per_pixel = 2 if name == "VIIRS_lst" else RAW_BYTES[kind]
        steps = max(1, round(years * STEPS_PER_YEAR[kind])) if kind != "land_cover" else max(1, round(years) + 1)
        total += pixels * steps * bytes_per_pixel
    return total


def max_burn_samples_for_bytes(
    budget_bytes: float,
    bytes_per_row: int = CSV_BYTES_PER_ROW,
    rows_per_burn: int = ROWS_PER_BURN_SAMPLE,
    sensors: int = 1,
) -> int:
    """Maximum burn samples per sensor so the training CSV stays within `budget_bytes`."""
    if budget_bytes <= 0:
        return 0
    return int(budget_bytes // (bytes_per_row * rows_per_burn * max(1, sensors)))


# ---------------------------------------------------------------------------
# Regional monthly time series
# ---------------------------------------------------------------------------
def composite_vegetation(grid: xr.Dataset, mask: np.ndarray, period_days: int = 16) -> pd.DataFrame:
    """Mean, spatial standard deviation and valid share of NDVI/EVI over the masked cells, per composite.

    Reads one composite at a time. Columns: start, end (start + period_days) and the statistics.
    """
    times = pd.DatetimeIndex(grid["time"].values).normalize()
    rows = []
    for t, when in enumerate(times):
        row = {"start": when, "end": when + pd.Timedelta(days=period_days)}
        for var in ("ndvi", "evi"):
            values = grid[var].isel(time=t).values[mask]
            values = values[np.isfinite(values)]
            row[f"{var}_mean"] = float(values.mean()) if len(values) else np.nan
            row[f"{var}_std"] = float(values.std()) if len(values) else np.nan
            row[f"{var}_valid_share"] = len(values) / mask.sum() if mask.sum() else np.nan
        rows.append(row)
    columns = ["start", "end", *[f"{v}_{s}" for v in ("ndvi", "evi") for s in ("mean", "std", "valid_share")]]
    return pd.DataFrame(rows, columns=columns).sort_values("start").reset_index(drop=True)


def vegetation_as_of(composites: pd.DataFrame, dates: pd.Series) -> pd.DataFrame:
    """For each date, the last composite (with a valid NDVI mean) whose period ended on or before it.

    Returns the composite statistics plus `veg_age_days` (days from the composite's end to the date).
    """
    valid = composites.dropna(subset=["ndvi_mean"]).sort_values("end")
    valid = valid.assign(start=valid["start"].astype("datetime64[ns]"), end=valid["end"].astype("datetime64[ns]"))
    query = pd.DataFrame({"date": pd.to_datetime(dates).to_numpy().astype("datetime64[ns]")}).reset_index()
    if valid.empty:
        result = query.assign(**{c: np.nan for c in composites.columns if c not in ("start", "end")})
        result["veg_age_days"] = np.nan
        return result.sort_values("index").drop(columns=["index", "date"]).reset_index(drop=True)
    merged = pd.merge_asof(query.sort_values("date"), valid, left_on="date", right_on="end", direction="backward")
    merged["veg_age_days"] = (merged["date"] - merged["end"]).dt.days
    return merged.sort_values("index").drop(columns=["index", "date", "start", "end"]).reset_index(drop=True)


def build_regional_monthly_series(
    regions: dict[str, dict],
    start: datetime,
    stop: datetime,
    datasets: dict[str, dict] | None = None,
    fire_count: str = "events",
    max_gap_days: int = 5,
) -> pd.DataFrame:
    """One row per region and month from `start` to `stop`: fires, burned pixels and NDVI/EVI per sensor.

    regions: {name: {"directory": NetCDF folder, "bbox": (w, s, e, n), "geometry": region polygon}}.
    Columns per sensor (e.g. MODIS_fires, MODIS_burned_pixels, MODIS_ndvi_mean, ...). Months without
    fires have 0 fires; months without vegetation composites have NaN vegetation values.
    """
    datasets = active_datasets(stop) if datasets is None else datasets
    months = pd.period_range(start, stop, freq="M")
    frames = []
    for region_name, spec in regions.items():
        frame = pd.DataFrame({"region": region_name, "year": months.year, "month": months.month})
        context = dataset_context(spec["bbox"], start, stop, region_name)
        for name, d in datasets.items():
            sensor = name.split("_")[0]
            ds = open_product(d["product"], spec["directory"])
            check_grid_in_bbox(ds, spec["bbox"], d["product"])
            if name.endswith("burned_area"):
                burns = build_burned_area(ds, {**d["description"], **context}, region=spec.get("geometry"))
                fires = fire_summary(burns, fire_count=fire_count, max_gap_days=max_gap_days)
                fires = fires.rename(columns={"fires": f"{sensor}_fires", "burned_pixels": f"{sensor}_burned_pixels"})
                frame = frame.merge(fires.drop(columns="ratio"), on=["year", "month"], how="left")
                frame[[f"{sensor}_fires", f"{sensor}_burned_pixels"]] = (
                    frame[[f"{sensor}_fires", f"{sensor}_burned_pixels"]].fillna(0).astype(int)
                )
            elif name.endswith("vegetation"):
                composites = composite_vegetation(
                    vegetation_grid(ds, d["good_reliability"]), region_mask(ds, spec.get("geometry")), d["period_days"]
                )
                month_start = pd.to_datetime(dict(year=frame["year"], month=frame["month"], day=1))
                veg = vegetation_as_of(composites, month_start)
                frame = pd.concat([frame, veg.add_prefix(f"{sensor}_")], axis=1)
        frames.append(frame)

    series = pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()
    series.insert(1, "date", pd.to_datetime(dict(year=series["year"], month=series["month"], day=1)))
    series.attrs["description"] = {
        "title": "Monthly fire and vegetation time series per Chilean region",
        "regions": list(regions),
        "time_range": {"start": f"{start:%Y-%m-%d}", "stop": f"{stop:%Y-%m-%d}"},
        "update_frequency": "Monthly (one row per region and month)",
        "spatial_granularity": "Region (aggregated from 500 m pixels inside the region outline)",
        "variables": {
            "<SENSOR>_fires": "fire events starting in the month" if fire_count == "events" else "burned pixels",
            "<SENSOR>_burned_pixels": "500 m pixels burned in the month (MCD64A1 / VNP64A1)",
            "<SENSOR>_ndvi_mean / _evi_mean": "mean NDVI / EVI over the region of the last 16-day composite ending on or before the 1st of the month (no data from the month itself)",
            "<SENSOR>_veg_age_days": "days from the end of that composite to the 1st of the month",
            "<SENSOR>_ndvi_std / _evi_std": "spatial standard deviation of NDVI / EVI",
            "<SENSOR>_ndvi_valid_share": "share of region pixels with reliable NDVI",
        },
        "fire_count": fire_count,
        "max_gap_days": max_gap_days,
        "source": "NASA AppEEARS",
    }
    return series


# ---------------------------------------------------------------------------
# Saving
# ---------------------------------------------------------------------------
def save_dataframe(df: pd.DataFrame, path: str | Path, output_format: str | None = None) -> list[Path]:
    """Save a dataframe and its description (`df.attrs["description"]`) as CSV or JSON.

    output_format: "csv" or "json" (default: taken from the file extension).
    - csv:  `<name>.csv` with the rows, plus `<name>.description.json` with the description.
    - json: one file `{"description": {...}, "data": [{column: value, ...}, ...]}`; dates are ISO
      strings and missing values are null.
    Returns the written paths.
    """
    path = Path(path)
    output_format = (output_format or path.suffix.lstrip(".")).lower()
    if output_format not in ("csv", "json"):
        raise ValueError(f"output_format must be 'csv' or 'json', got {output_format!r}")
    path = path.with_suffix(f".{output_format}")
    path.parent.mkdir(parents=True, exist_ok=True)
    description = df.attrs.get("description", {})

    if output_format == "csv":
        df.to_csv(path, index=False)
        description_path = path.with_name(f"{path.stem}.description.json")
        description_path.write_text(
            json.dumps(description, indent=2, ensure_ascii=False, default=str), encoding="utf-8"
        )
        return [path, description_path]

    records = json.loads(df.to_json(orient="records", date_format="iso", force_ascii=False))
    path.write_text(
        json.dumps({"description": description, "data": records}, indent=2, ensure_ascii=False, default=str),
        encoding="utf-8",
    )
    return [path]
