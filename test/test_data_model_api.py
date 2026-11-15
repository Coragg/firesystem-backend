import httpx
import numpy as np
import pandas as pd
import pytest
from fastapi.testclient import TestClient

from src.main import app
from src.routers.api_data_model import common
from src.utils import hotspot_model as hm

GRID = {"lat": [-35.0, -35.005, -35.01], "lon": [-71.0, -70.995, -70.99]}
BASE = "/api/v1/data-model"


class FakeLocal:
    """Stands in for LocalFeatureSource; records the pixel it is asked about."""

    def __init__(self):
        self.calls = []

    def monthly_lai(self, lat, lon, months):
        self.calls.append(("lai", lat, lon))
        return {m: (np.nan if m.month == 2 else 1.5 + m.month) for m in months}

    def terrain(self, lat, lon):
        self.calls.append(("terrain", lat, lon))
        return (350.0, 12.5) if lat > -35.009 else (np.nan, np.nan)

    def land_cover(self, lat, lon, max_year):
        return (None, np.nan) if max_year < 2001 else (max_year, 8.0)


@pytest.fixture
def local(monkeypatch):
    bundle = hm.ModelBundle(None, {"grid": GRID, "bbox": [-71.0, -35.01, -70.99, -35.0], "region": "Test"})
    fake = FakeLocal()
    monkeypatch.setattr(common, "model_bundle", lambda: bundle)
    monkeypatch.setattr(common, "local_source", lambda: fake)
    return fake


@pytest.fixture
def client(local):
    return TestClient(app)


def test_lai_returns_one_point_per_month_with_null_for_missing(client, local):
    response = client.get(f"{BASE}/lai", params={"lat": -35.0041, "lon": -70.9962, "start": "2024-01", "end": "2024-03"})
    assert response.status_code == 200
    assert response.json()["data"] == [
        {"month": "2024-01", "lai": 2.5}, {"month": "2024-02", "lai": None}, {"month": "2024-03", "lai": 4.5},
    ]
    assert local.calls == [("lai", -35.005, -70.995)]  # looked up at the pixel centre


@pytest.mark.parametrize("start,end,status", [
    ("2024-03", "2024-01", 422), ("2024-1", "2024-02", 422), ("2024-13", "2024-14", 422), ("2000-01", "2012-01", 422),
])
def test_lai_rejects_bad_ranges(client, start, end, status):
    response = client.get(f"{BASE}/lai", params={"lat": -35.0, "lon": -71.0, "start": start, "end": end})
    assert response.status_code == status


def test_terrain_returns_elevation_and_slope(client):
    response = client.get(f"{BASE}/terrain", params={"lat": -35.0, "lon": -71.0})
    assert response.status_code == 200
    assert response.json()["data"] == {"elevation_m": 350.0, "slope_deg": 12.5}


def test_terrain_without_elevation_is_422(client):
    response = client.get(f"{BASE}/terrain", params={"lat": -35.01, "lon": -70.99})
    assert response.status_code == 422


def test_land_cover_returns_year_and_class(client):
    response = client.get(f"{BASE}/land-cover", params={"lat": -35.0, "lon": -71.0, "max_year": 2025})
    assert response.json()["data"] == {"year": 2025, "igbp_class": 8}


def test_land_cover_without_a_year_is_null(client):
    response = client.get(f"{BASE}/land-cover", params={"lat": -35.0, "lon": -71.0, "max_year": 1999})
    assert response.status_code == 200
    assert response.json()["data"] == {"year": None, "igbp_class": None}


@pytest.mark.parametrize("path,extra", [("lai", {"start": "2024-01", "end": "2024-02"}), ("terrain", {}),
                                        ("land-cover", {"max_year": 2025})])
def test_point_outside_the_trained_area_is_422(client, path, extra):
    response = client.get(f"{BASE}/{path}", params={"lat": 10.0, "lon": 10.0, **extra})
    assert response.status_code == 422
    assert "outside the area" in response.json()["detail"]


def test_missing_appeears_files_is_503(monkeypatch, client):
    class Missing(FakeLocal):
        def terrain(self, lat, lon):
            raise FileNotFoundError("No *_SRTMGL3_DEM_*.tif file in data")

    monkeypatch.setattr(common, "local_source", lambda: Missing())
    assert client.get(f"{BASE}/terrain", params={"lat": -35.0, "lon": -71.0}).status_code == 503


def test_missing_model_files_is_503(monkeypatch, client):
    def missing():
        raise FileNotFoundError("No model.json")

    monkeypatch.setattr(common, "model_bundle", missing)
    assert client.get(f"{BASE}/terrain", params={"lat": -35.0, "lon": -71.0}).status_code == 503


def test_api_feature_source_reads_the_endpoints(client, monkeypatch):
    """The model's HTTP client and these endpoints agree on paths, parameters and shapes."""

    def through_app(url, params=None, timeout=None):
        response = client.get(url.removeprefix("http://testserver"), params=params)
        return httpx.Response(response.status_code, json=response.json(), request=httpx.Request("GET", url))

    monkeypatch.setattr(hm.httpx, "get", through_app)
    source = hm.ApiFeatureSource(f"http://testserver{BASE}")
    lai = source.monthly_lai(-35.0, -71.0, [pd.Timestamp("2024-01-01"), pd.Timestamp("2024-02-01")])
    assert lai[pd.Timestamp("2024-01-01")] == 2.5 and np.isnan(lai[pd.Timestamp("2024-02-01")])
    assert source.terrain(-35.0, -71.0) == (350.0, 12.5)
    assert source.land_cover(-35.0, -71.0, 2025) == (2025, 8.0)
