import pytest
from pydantic import ValidationError

from src.models.aoi import PolygonAoi
from src.utils.coverage import intersects_coverage

BIOBIO = [[-73.2, -37.9], [-71.5, -37.9], [-71.5, -36.5], [-73.2, -36.5]]


def polygon(ring: list[list[float]]) -> dict:
    return {"type": "Polygon", "coordinates": [ring]}


def test_aoi_closes_ring():
    aoi = PolygonAoi.model_validate(polygon(BIOBIO))

    ring = aoi.coordinates[0]
    assert len(ring) == 5
    assert ring[-1] == ring[0]


def test_aoi_bbox_is_west_south_east_north():
    aoi = PolygonAoi.model_validate(polygon(BIOBIO))

    assert aoi.bbox() == (-73.2, -37.9, -71.5, -36.5)


def test_aoi_swapped_coords_hint():
    san_francisco_as_lat_lon = [[37.7, -122.4], [37.8, -122.4], [37.8, -122.3], [37.7, -122.3]]

    with pytest.raises(ValidationError, match=r"GeoJSON uses \[lon, lat\]"):
        PolygonAoi.model_validate(polygon(san_francisco_as_lat_lon))


def test_aoi_too_many_vertices():
    ring = [[-72.0 + i * 0.0001, -37.0 + (i % 2) * 0.01] for i in range(1001)]

    with pytest.raises(ValidationError, match="1000"):
        PolygonAoi.model_validate(polygon(ring))


def test_aoi_needs_three_distinct_points():
    with pytest.raises(ValidationError, match="3 distinct"):
        PolygonAoi.model_validate(polygon([[-72.0, -37.0], [-71.0, -37.0]]))


def test_aoi_rejects_extra_fields():
    with pytest.raises(ValidationError):
        PolygonAoi.model_validate({**polygon(BIOBIO), "crs": "x"})


def test_intersects_coverage():
    assert intersects_coverage((-73.2, -37.9, -71.5, -36.5)) is True
    assert intersects_coverage((0.0, 0.0, 1.0, 1.0)) is False
