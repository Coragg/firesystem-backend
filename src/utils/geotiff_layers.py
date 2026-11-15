"""AppEEARS GeoTIFF layers used as extra forecast features: leaf area index (MCD15A3H) and elevation (SRTM).

AppEEARS GeoTIFFs in geographic projection are plain lat/lon grids: the georeferencing is the top-left
corner (ModelTiepointTag) and the pixel size (ModelPixelScaleTag), WGS 84, pixel-is-area. File names
carry the date as `_doy<YYYY><DDD>`, the start of the composite period.
"""

import re
from datetime import datetime, timedelta
from pathlib import Path

import numpy as np
import pandas as pd
import tifffile
import xarray as xr

LAI_PRODUCT = "MCD15A3H.061"
LAI_PERIOD_DAYS = 4
LAI_MAX_AGE_DAYS = 32           # skip back over cloudy composites for at most a month
LAI_LAG_MONTHS = 24
LAI_FILL = 255
LAI_VALID_MAX = 100             # 0-100 (x 0.1 m²/m²); 248-254 are non-vegetated / fill classes
LAI_GOOD_ALGORITHM = (0, 1)     # FparLai_QC bits 5-7 (SCF_QC): main algorithm, with or without saturation
LAI_NETCDF = f"{LAI_PRODUCT}_Lai_500m.nc"

LAI_DESCRIPTION = {
    "title": "MODIS Terra+Aqua Leaf Area Index (MCD15A3H v061)",
    "variables": {"lai": "m²/m² (0-10), main-algorithm retrievals only"},
    "update_frequency": "4-day composite",
    "spatial_granularity": "500 m",
}

_DOY = re.compile(r"_doy(\d{4})(\d{3})")


def file_date(path: str | Path) -> datetime:
    """Return the date in an AppEEARS file name (`..._doy2012061..._aid0001.tif` -> 2012-03-01)."""
    match = _DOY.search(Path(path).name)
    if match is None:
        raise ValueError(f"No _doy<YYYY><DDD> date in {Path(path).name}")
    year, doy = int(match.group(1)), int(match.group(2))
    return datetime(year, 1, 1) + timedelta(days=doy - 1)


def read_geotiff(path: str | Path) -> tuple[np.ndarray, np.ndarray, np.ndarray, float | None]:
    """Return (values, lat, lon, nodata) of a north-up lat/lon GeoTIFF; lat/lon are pixel centres."""
    with tifffile.TiffFile(path) as tif:
        page = tif.pages[0]
        values = page.asarray()
        scale_x, scale_y, _ = page.tags["ModelPixelScaleTag"].value
        _, _, _, west, north, _ = page.tags["ModelTiepointTag"].value
        nodata_tag = page.tags.get("GDAL_NODATA")
        nodata = float(str(nodata_tag.value).strip("\x00 ")) if nodata_tag else None
    rows, cols = values.shape[:2]
    lat = north - (np.arange(rows) + 0.5) * scale_y
    lon = west + (np.arange(cols) + 0.5) * scale_x
    return values, lat, lon, nodata


def layer_files(directory: str | Path, layer: str) -> list[Path]:
    """Return the GeoTIFFs of one layer (e.g. 'Lai_500m') in `directory`, sorted by date."""
    return sorted(Path(directory).glob(f"*_{layer}_doy*.tif"), key=file_date)


def lai_quality_mask(qc: np.ndarray) -> np.ndarray:
    """Return True where FparLai_QC says the main algorithm produced the retrieval (SCF_QC 0 or 1)."""
    return np.isin(qc >> 5, LAI_GOOD_ALGORITHM) & (qc != LAI_FILL)


def build_lai_netcdf(directory: str | Path, out_path: str | Path | None = None) -> Path:
    """Stack the LAI GeoTIFFs (masked by their QC files) into one compressed NetCDF and return its path.

    Values stay 8-bit (x 0.1 m²/m²) with 255 for missing or poor-quality pixels, so the file is small and
    `open_lai` reads one date at a time. Dates without a QC file keep only the valid-range check.
    """
    directory = Path(directory)
    out_path = Path(out_path) if out_path else directory / LAI_NETCDF
    lai_files = layer_files(directory, "Lai_500m")
    if not lai_files:
        raise FileNotFoundError(f"No *_Lai_500m_doy*.tif files in {directory}")
    qc_by_date = {file_date(p): p for p in layer_files(directory, "FparLai_QC")}

    _, lat, lon, _ = read_geotiff(lai_files[0])
    stack = np.full((len(lai_files), len(lat), len(lon)), LAI_FILL, dtype="uint8")
    dates = []
    for t, path in enumerate(lai_files):
        values, file_lat, file_lon, _ = read_geotiff(path)
        if values.shape != stack.shape[1:] or not (np.allclose(file_lat, lat) and np.allclose(file_lon, lon)):
            raise ValueError(f"{path.name} is on a different grid than {lai_files[0].name}")
        good = values <= LAI_VALID_MAX
        date = file_date(path)
        if date in qc_by_date:
            good &= lai_quality_mask(read_geotiff(qc_by_date[date])[0])
        stack[t] = np.where(good, values, LAI_FILL)
        dates.append(date)

    ds = xr.Dataset(
        {"lai": (("time", "lat", "lon"), stack, {"scale_factor": 0.1, "units": "m2/m2"})},
        coords={"time": pd.to_datetime(dates), "lat": lat, "lon": lon},
        attrs={"source": f"{LAI_PRODUCT} Lai_500m + FparLai_QC (AppEEARS GeoTIFF)"},
    )
    encoding = {"lai": {"_FillValue": LAI_FILL, "dtype": "uint8", "zlib": True, "complevel": 4,
                        "chunksizes": (1, len(lat), len(lon))}}
    out_path.parent.mkdir(parents=True, exist_ok=True)
    ds.to_netcdf(out_path, engine="h5netcdf", encoding=encoding)
    return out_path


def open_lai(directory: str | Path) -> xr.Dataset:
    """Open the LAI stack of `directory` (building it from the GeoTIFFs on first use), in m²/m², NaN = missing."""
    path = Path(directory) / LAI_NETCDF
    if not path.exists():
        build_lai_netcdf(directory, path)
    return xr.open_dataset(path, engine="h5netcdf", chunks={"time": 1})


def open_elevation(directory: str | Path) -> xr.Dataset:
    """Return SRTM elevation (m) and slope (degrees) on the DEM grid of `directory`."""
    files = sorted(Path(directory).glob("*_SRTMGL3_DEM_*.tif"))
    if not files:
        raise FileNotFoundError(f"No *_SRTMGL3_DEM_*.tif file in {directory}")
    values, lat, lon, nodata = read_geotiff(files[0])
    elevation = values.astype("float32")
    if nodata is not None:
        elevation[values == nodata] = np.nan
    elevation[elevation < -500] = np.nan  # SRTM voids / fill
    return xr.Dataset(
        {"elevation_m": (("lat", "lon"), elevation), "slope_deg": (("lat", "lon"), slope_degrees(elevation, lat, lon))},
        coords={"lat": lat, "lon": lon},
    )


def slope_degrees(elevation: np.ndarray, lat: np.ndarray, lon: np.ndarray) -> np.ndarray:
    """Return the terrain slope (degrees) of a lat/lon elevation grid."""
    metres_per_deg = 111_320.0
    dy = np.abs(np.diff(lat).mean()) * metres_per_deg
    dx = np.abs(np.diff(lon).mean()) * metres_per_deg * np.cos(np.deg2rad(lat))[:, None]
    grad_y, grad_x = np.gradient(elevation, axis=(0, 1))
    return np.degrees(np.arctan(np.hypot(grad_x / dx, grad_y / dy))).astype("float32")
