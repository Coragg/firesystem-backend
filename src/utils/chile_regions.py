"""Chilean region (ADM1) boundaries for clipping requests and dataframes to Chile.

Boundaries: geoBoundaries gbOpen CHL ADM1 (2020; source BCN / OCHA ROLAC, CC BY 3.0 IGO),
simplified release: https://www.geoboundaries.org/api/current/gbOpen/CHL/ADM1/
"""

import math
import re
import unicodedata
from pathlib import Path

import geopandas as gpd
import numpy as np
import shapely
from shapely import affinity
from shapely.geometry import Polygon, box, mapping
from shapely.geometry.base import BaseGeometry

from src.utils.appeears import BBox

BOUNDARIES_FILE = "geoBoundaries-CHL-ADM1_simplified.geojson"

# Short region name -> official name (geoBoundaries `shapeName`, after encoding repair)
REGIONS = {
    "Arica y Parinacota": "Región de Arica y Parinacota",
    "Tarapacá": "Región de Tarapacá",
    "Antofagasta": "Región de Antofagasta",
    "Atacama": "Región de Atacama",
    "Coquimbo": "Región de Coquimbo",
    "Valparaíso": "Región de Valparaíso",
    "Metropolitana": "Región Metropolitana de Santiago",
    "O'Higgins": "Región del Libertador Bernardo O'Higgins",
    "Maule": "Región del Maule",
    "Ñuble": "Región de Ñuble",
    "Biobío": "Región del Bío-Bío",
    "La Araucanía": "Región de La Araucanía",
    "Los Ríos": "Región de Los Ríos",
    "Los Lagos": "Región de Los Lagos",
    "Aysén": "Región de Aysén del Gral.Ibañez del Campo",
    "Magallanes": "Región de Magallanes y Antártica Chilena",
}

# Reduced regions for small runs: name -> (parent region, share of its area kept around its centre)
REDUCED_REGIONS = {
    "Maule2": ("Maule", 0.1),
    "Maule3": ("Maule", 0.2),
    "Maule4": ("Maule", 0.05),
}

# Continental Chile and its coastal islands; excludes Rapa Nui, Juan Fernández and Desventuradas
MAINLAND = box(-76.5, -60.0, -66.0, -17.0)


def region_slug(name: str) -> str:
    """Return an ASCII folder/file name for a region ('O'Higgins' -> 'OHiggins', 'Ñuble' -> 'Nuble')."""
    ascii_name = unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode()
    return re.sub(r"[^A-Za-z0-9]+", "_", ascii_name.replace("'", "")).strip("_")


def repair_name(name: str) -> str:
    """Fix names stored as UTF-8 bytes decoded as Latin-1 (e.g. 'RegiÃ³n' -> 'Región')."""
    try:
        return name.encode("latin-1").decode("utf-8")
    except (UnicodeEncodeError, UnicodeDecodeError):
        return name


def load_regions(path: str | Path) -> gpd.GeoDataFrame:
    """Load the region boundaries with a `region` column holding the short names of `REGIONS`."""
    gdf = gpd.read_file(path).to_crs(4326)
    official = gdf["shapeName"].map(repair_name)
    short_by_official = {v: k for k, v in REGIONS.items()}
    missing = sorted(set(official) - set(short_by_official))
    if missing:
        raise ValueError(f"Unknown region names in {path}: {missing}")
    gdf.insert(0, "region", official.map(short_by_official))
    gdf["official_name"] = official
    return gdf[["region", "official_name", "geometry"]]


def region_geometry(regions: gpd.GeoDataFrame, name: str, mainland_only: bool = True) -> BaseGeometry:
    """Return the polygon of one region (by short name), optionally without the Pacific islands."""
    if name not in REGIONS:
        raise KeyError(f"Unknown region {name!r}; expected one of {list(REGIONS)}")
    geom = regions.loc[regions["region"] == name, "geometry"].union_all()
    return geom.intersection(MAINLAND) if mainland_only else geom


def named_region_geometry(
    regions: gpd.GeoDataFrame, name: str, reduced: dict[str, tuple[str, float]] = REDUCED_REGIONS
) -> BaseGeometry:
    """Return the mainland polygon of a region or of a reduced region (e.g. 'Maule2')."""
    if name in reduced:
        parent, fraction = reduced[name]
        return shrink_geometry(region_geometry(regions, parent), fraction)
    return region_geometry(regions, name)


def geometry_bbox(geom: BaseGeometry, decimals: int = 2) -> BBox:
    """Return the (west, south, east, north) box of a geometry, rounded outward."""
    factor = 10 ** decimals
    west, south, east, north = geom.bounds
    return (
        math.floor(west * factor) / factor,
        math.floor(south * factor) / factor,
        math.ceil(east * factor) / factor,
        math.ceil(north * factor) / factor,
    )


def geometry_to_geojson(geom: BaseGeometry, name: str = "region") -> dict:
    """Wrap a geometry in a GeoJSON FeatureCollection (the `geo` parameter of AppEEARS tasks)."""
    return {
        "type": "FeatureCollection",
        "features": [{"type": "Feature", "properties": {"name": name}, "geometry": mapping(geom)}],
    }


def polygon_points(geom: BaseGeometry) -> int:
    """Return the number of coordinates in a polygon's outline (including the closing point)."""
    if not isinstance(geom, Polygon):
        raise TypeError(f"Expected a Polygon, got {geom.geom_type}")
    return len(geom.exterior.coords) + sum(len(ring.coords) for ring in geom.interiors)


def request_geometry(geom: BaseGeometry, max_points: int = 50, max_rounds: int = 40) -> Polygon:
    """Return a single polygon of at most `max_points` points that covers `geom`, for API requests.

    Starts from the outline pre-simplified by t/4 and grown by t (t ~ 1/800 of the region's extent),
    which covers `geom`. Each round simplifies the current outline by t; if the result is a single
    polygon (holes dropped) with at most `max_points` points that covers `geom`, it is returned.
    Otherwise the simplified outline is grown by 1.1 x t (so it still covers the previous one, and
    therefore `geom`), t grows by 1.5 and the next round starts. Results are cut to the region's bounding box. Growing the already-simplified
    outline keeps every round cheap, even for regions with thousands of islands (Aysén, Magallanes).
    Falls back to the minimum rotated rectangle (5 points). Dataframes are clipped to the exact
    `geom` afterwards, so the extra area only adds pixels that are dropped later.
    """
    if max_points < 5:
        raise ValueError("max_points must be at least 5")
    shapely.prepare(geom)
    west, south, east, north = geom.bounds
    bounds = box(west, south, east, north).buffer(1e-6, join_style="mitre")  # never request beyond the bbox
    tol = max(east - west, north - south) / 800
    current = geom.simplify(tol / 4, preserve_topology=True).buffer(tol, quad_segs=1, join_style="mitre")

    for _ in range(max_rounds):
        simplified = current.simplify(tol, preserve_topology=True)
        if isinstance(simplified, Polygon):
            candidate = Polygon(simplified.exterior).intersection(bounds)
            if (
                isinstance(candidate, Polygon)
                and polygon_points(candidate) <= max_points
                and candidate.covers(geom)
            ):
                return candidate
        current = simplified.buffer(1.1 * tol, quad_segs=1, join_style="mitre")
        tol *= 1.5

    # Tiny mitred margin so points on the rectangle's edge are covered despite float rounding (still 5 points)
    return geom.minimum_rotated_rectangle.buffer(1e-6, join_style="mitre")


def shrink_geometry(geom: BaseGeometry, fraction: float, tolerance: float = 1e-3) -> BaseGeometry:
    """Return the part of `geom` covered by `geom` scaled down around its centre, with `fraction` of its area.

    The outline is scaled about a point inside `geom` (so the result keeps its shape and stays inside it),
    and the scale factor is found by bisection so that area(result) / area(geom) = fraction (± tolerance).
    """
    if not 0 < fraction <= 1:
        raise ValueError("fraction must be in (0, 1]")
    if fraction == 1:
        return geom
    centre = geom.representative_point()
    target = fraction * geom.area
    low, high = 0.0, 1.0
    result = geom
    for _ in range(60):
        factor = (low + high) / 2
        result = affinity.scale(geom, factor, factor, origin=centre).intersection(geom)
        if abs(result.area - target) <= tolerance * target:
            break
        low, high = (factor, high) if result.area < target else (low, factor)
    return result


def geometry_area_km2(geom: BaseGeometry) -> float:
    """Return the area of a lon/lat geometry in km² (World Cylindrical Equal Area, EPSG:6933)."""
    return float(gpd.GeoSeries([geom], crs=4326).to_crs(6933).area.iloc[0]) / 1e6


def points_in_geometry(geom: BaseGeometry, lat: np.ndarray, lon: np.ndarray) -> np.ndarray:
    """Return a boolean mask of the (lat, lon) points that fall inside (or on the edge of) `geom`."""
    shapely.prepare(geom)
    return shapely.intersects_xy(geom, np.asarray(lon, dtype=float), np.asarray(lat, dtype=float))
