import json
from pathlib import Path

import numpy as np
import pytest
from shapely.geometry import MultiPolygon, Point, Polygon, box, shape

from src.utils import chile_regions as cr

REAL_FILE = Path(__file__).resolve().parent.parent / "data" / "boundaries" / cr.BOUNDARIES_FILE


def mojibake(text: str) -> str:
    """Encode text the way the geoBoundaries file stores it (UTF-8 bytes read as Latin-1)."""
    return text.encode("utf-8").decode("latin-1")


@pytest.fixture
def regions_file(tmp_path: Path) -> Path:
    features = []
    for i, official in enumerate(cr.REGIONS.values()):
        south = -18.0 - 2 * i
        geom = box(-72.0, south - 1.5, -70.0, south)
        if official == "Región de Valparaíso":
            geom = MultiPolygon([geom, box(-109.5, -27.3, -109.2, -27.0)])  # mainland + Rapa Nui
        features.append({
            "type": "Feature",
            "properties": {"shapeName": mojibake(official)},
            "geometry": json.loads(json.dumps(geom.__geo_interface__)),
        })
    path = tmp_path / "regions.geojson"
    path.write_text(json.dumps({"type": "FeatureCollection", "features": features}), encoding="utf-8")
    return path


def test_repair_name_fixes_mojibake_and_keeps_clean_names():
    assert cr.repair_name(mojibake("Región de Ñuble")) == "Región de Ñuble"
    assert cr.repair_name("Región del Maule") == "Región del Maule"


def test_load_regions_maps_short_names(regions_file):
    gdf = cr.load_regions(regions_file)
    assert sorted(gdf["region"]) == sorted(cr.REGIONS)
    assert gdf.loc[gdf.region == "Ñuble", "official_name"].item() == "Región de Ñuble"


def test_load_regions_rejects_unknown_names(tmp_path):
    path = tmp_path / "bad.geojson"
    path.write_text(json.dumps({"type": "FeatureCollection", "features": [{
        "type": "Feature", "properties": {"shapeName": "Provincia X"},
        "geometry": box(0, 0, 1, 1).__geo_interface__,
    }]}))
    with pytest.raises(ValueError, match="Unknown region names"):
        cr.load_regions(path)


def test_region_geometry_drops_pacific_islands(regions_file):
    gdf = cr.load_regions(regions_file)
    mainland = cr.region_geometry(gdf, "Valparaíso")
    full = cr.region_geometry(gdf, "Valparaíso", mainland_only=False)
    assert mainland.bounds[0] == pytest.approx(-72.0)
    assert full.bounds[0] == pytest.approx(-109.5)
    with pytest.raises(KeyError):
        cr.region_geometry(gdf, "Patagonia")


def test_geometry_bbox_rounds_outward():
    assert cr.geometry_bbox(box(-72.791, -36.541, -70.312, -34.684)) == (-72.80, -36.55, -70.31, -34.68)


def test_geometry_to_geojson_wraps_feature():
    geo = cr.geometry_to_geojson(box(0, 0, 1, 1), name="Maule")
    assert geo["type"] == "FeatureCollection"
    assert geo["features"][0]["properties"] == {"name": "Maule"}
    assert shape(geo["features"][0]["geometry"]).equals(box(0, 0, 1, 1))


def test_polygon_points_counts_rings():
    assert cr.polygon_points(box(0, 0, 1, 1)) == 5
    with pytest.raises(TypeError):
        cr.polygon_points(Point(0, 0))


def test_request_geometry_is_small_single_polygon_covering_region():
    # Jagged coastline with fjords and offshore islands
    angles = np.linspace(0, 2 * np.pi, 2000, endpoint=False)
    radius = 1 + 0.15 * np.sin(angles * 80)
    coast = Polygon(np.c_[-72 + radius * np.cos(angles), -40 + radius * np.sin(angles)])
    islands = [Point(-73.3 + 0.05 * i, -40).buffer(0.02) for i in range(5)]
    region = MultiPolygon([coast, *islands])

    req = cr.request_geometry(region, max_points=50)
    assert isinstance(req, Polygon)
    assert cr.polygon_points(req) <= 50
    assert req.covers(region)
    assert box(*region.bounds).buffer(1e-5).covers(req)  # never larger than the bounding box

    tiny = cr.request_geometry(region, max_points=5, max_rounds=1)  # forces the rectangle fallback
    assert cr.polygon_points(tiny) == 5 and tiny.covers(region)
    with pytest.raises(ValueError):
        cr.request_geometry(region, max_points=4)


def test_points_in_geometry():
    mask = cr.points_in_geometry(box(-72, -36, -71, -35), np.array([-35.5, -30.0, -36.0]), np.array([-71.5, -71.5, -71.0]))
    assert mask.tolist() == [True, False, True]


@pytest.mark.skipif(not REAL_FILE.exists(), reason="Chile boundaries not downloaded to data/boundaries")
def test_real_boundaries_load_and_request_outlines():
    gdf = cr.load_regions(REAL_FILE)
    assert len(gdf) == 16
    maule = cr.region_geometry(gdf, "Maule")
    assert cr.geometry_bbox(maule) == (-72.79, -36.55, -70.31, -34.68)
    req = cr.request_geometry(maule)
    assert cr.polygon_points(req) <= 50 and req.covers(maule)


def test_region_slug_is_ascii():
    assert cr.region_slug("O'Higgins") == "OHiggins"
    assert cr.region_slug("Ñuble") == "Nuble"
    assert cr.region_slug("Arica y Parinacota") == "Arica_y_Parinacota"
    assert cr.region_slug("Los Ríos") == "Los_Rios"


def test_geometry_area_km2():
    # 1° x 1° at the equator is about 111.3 km x 110.6 km
    assert cr.geometry_area_km2(box(0, 0, 1, 1)) == pytest.approx(12_300, rel=0.01)


@pytest.mark.parametrize("fraction", [0.1, 0.5])
def test_shrink_geometry_keeps_fraction_of_area_inside(fraction):
    # Concave L-shaped region: a plain scaled copy could leave it
    region = Polygon([(0, 0), (4, 0), (4, 1), (1, 1), (1, 4), (0, 4)])
    small = cr.shrink_geometry(region, fraction)
    assert small.area == pytest.approx(fraction * region.area, rel=1e-3)
    assert region.buffer(1e-9).covers(small)


def test_shrink_geometry_validates_fraction():
    region = box(0, 0, 1, 1)
    assert cr.shrink_geometry(region, 1) is region
    for bad in (0, -0.1, 1.5):
        with pytest.raises(ValueError):
            cr.shrink_geometry(region, bad)


def test_named_region_geometry_resolves_reduced_regions(regions_file):
    gdf = cr.load_regions(regions_file)
    full = cr.named_region_geometry(gdf, "Maule")
    small = cr.named_region_geometry(gdf, "Maule2", reduced={"Maule2": ("Maule", 0.1)})
    assert full.equals(cr.region_geometry(gdf, "Maule"))
    assert small.area == pytest.approx(0.1 * full.area, rel=1e-3)
    assert set(cr.REDUCED_REGIONS) >= {"Maule2", "Maule3", "Maule4"}
