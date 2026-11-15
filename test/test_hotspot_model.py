import json

import httpx
import numpy as np
import pandas as pd
import pytest
import xgboost as xgb
from fastapi.testclient import TestClient

from src.main import app
from src.routers.api_model import hotspot as hotspot_router
from src.utils import hotspot_model as hm
from src.utils import hotspots as hs

LAGS = [24, 25]
FEATURES = ["lai_lag_24", "lai_lag_25", "lai_mean_24_25", "lai_min_24_25", "lai_max_24_25", "lai_std_24_25",
            "lai_valid_months_24_25", "elevation_m", "slope_deg", "land_cover"]
GRID = {"lat": [-35.0, -35.005, -35.01], "lon": [-71.0, -70.995, -70.99]}


class FakeSource:
    def __init__(self, lai: float | None = 2.0):
        self.lai, self.calls = lai, []

    def monthly_lai(self, lat, lon, months):
        self.calls.append(("lai", lat, lon, [f"{m:%Y-%m}" for m in months]))
        return {m: (np.nan if self.lai is None else self.lai + k) for k, m in enumerate(months)}

    def terrain(self, lat, lon):
        return 350.0, 12.5

    def land_cover(self, lat, lon, max_year):
        self.calls.append(("land_cover", max_year))
        return max_year, 8.0


@pytest.fixture
def model_dir(tmp_path):
    rng = np.random.default_rng(0)
    X = pd.DataFrame(rng.random((200, len(FEATURES))), columns=FEATURES)
    X["land_cover"] = pd.Categorical(rng.choice([8, 10], 200), categories=list(hs.IGBP_CLASSES))
    y = (X["lai_lag_24"] > 0.5).astype(int)
    xgb.XGBClassifier(n_estimators=5, max_depth=2, enable_categorical=True, tree_method="hist").fit(X, y).save_model(tmp_path / hm.MODEL_FILE)
    (tmp_path / hm.METADATA_FILE).write_text(json.dumps({
        "region": "Maule", "bbox": [-71.0, -35.01, -70.99, -35.0], "trained_months": ["2015-02", "2026-10"],
        "features": FEATURES, "lai_lags": LAGS, "use_terrain": True, "use_land_cover": True, "land_cover_lag_years": 2,
        "land_cover_categories": list(hs.IGBP_CLASSES), "negative_rate": 0.1, "horizon_months": 13, "grid": GRID,
    }))
    return tmp_path


def test_load_bundle_and_missing_files(model_dir, tmp_path_factory):
    bundle = hm.load_bundle(model_dir)
    assert bundle.features == FEATURES and bundle.lat.tolist() == GRID["lat"]
    with pytest.raises(FileNotFoundError):
        hm.load_bundle(tmp_path_factory.mktemp("empty"))


def test_snap_to_pixel_and_outside_area(model_dir):
    bundle = hm.load_bundle(model_dir)
    assert hm.snap_to_pixel(bundle, -35.0041, -70.9962) == (-35.005, -70.995)
    with pytest.raises(hm.FeatureError, match="outside the area"):
        hm.snap_to_pixel(bundle, -33.0, -71.0)


def test_build_features_matches_training_definitions(model_dir):
    bundle = hm.load_bundle(model_dir)
    source = FakeSource()
    features = hm.build_features(bundle, source, -35.0, -71.0, pd.Timestamp("2027-01-20"))

    assert source.calls[0][3] == ["2025-01", "2024-12"]       # 24 and 25 months before January 2027
    assert source.calls[1] == ("land_cover", 2025)             # latest year <= 2027 - 2
    assert features["lai_lag_24"] == 2.0 and features["lai_lag_25"] == 3.0
    assert features["lai_mean_24_25"] == 2.5 and features["lai_std_24_25"] == 0.5
    assert features["lai_valid_months_24_25"] == 2
    assert (features["elevation_m"], features["slope_deg"], features["land_cover"]) == (350.0, 12.5, 8.0)


def test_build_features_without_leaf_area_raises(model_dir):
    with pytest.raises(hm.FeatureError, match="No leaf area data"):
        hm.build_features(hm.load_bundle(model_dir), FakeSource(lai=None), -35.0, -71.0, pd.Timestamp("2027-01-01"))


def test_predict_returns_score_and_corrected_probability(model_dir):
    bundle = hm.load_bundle(model_dir)
    score, probability = hm.predict(bundle, hm.build_features(bundle, FakeSource(), -35.0, -71.0, pd.Timestamp("2027-01-01")))
    assert 0 < score < 1
    assert probability == pytest.approx(float(hs.corrected_probability(np.array([score]), 0.1)[0]))
    assert probability < score


def test_api_feature_source_follows_the_contract(monkeypatch):
    calls = []

    def fake_get(url, params=None, timeout=None):
        calls.append((url, params))
        data = {
            "lai": [{"month": "2024-12", "lai": 1.5}, {"month": "2025-01", "lai": None}],
            "terrain": {"elevation_m": 100, "slope_deg": 3},
            "land-cover": {"year": 2024, "igbp_class": 9},
        }[url.rsplit("/", 1)[1]]
        return httpx.Response(200, json={"data": data}, request=httpx.Request("GET", url))

    monkeypatch.setattr(hm.httpx, "get", fake_get)
    source = hm.ApiFeatureSource("https://data.example/appeears/")
    lai = source.monthly_lai(-35.0, -71.0, [pd.Timestamp("2025-01-01"), pd.Timestamp("2024-12-01")])
    assert lai[pd.Timestamp("2024-12-01")] == 1.5 and np.isnan(lai[pd.Timestamp("2025-01-01")])
    assert calls[0] == ("https://data.example/appeears/lai", {"lat": -35.0, "lon": -71.0, "start": "2024-12", "end": "2025-01"})
    assert source.terrain(-35.0, -71.0) == (100.0, 3.0)
    assert source.land_cover(-35.0, -71.0, 2025) == (2024, 9.0)
    with pytest.raises(ValueError):
        hm.ApiFeatureSource("")


@pytest.fixture
def client(model_dir, monkeypatch):
    hm.cached_bundle.cache_clear()
    monkeypatch.setattr(hotspot_router.config, "HOTSPOT_MODEL_DIR", str(model_dir))
    monkeypatch.setattr(hotspot_router, "feature_source", lambda _request: FakeSource())
    return TestClient(app)


def test_forecast_endpoint_returns_probability(client):
    response = client.get("/api/v1/hotspot/forecast", params={"date": "2027-01-20", "lat": -35.0041, "lon": -70.9962})
    assert response.status_code == 200
    data = response.json()["data"]
    assert data["month"] == "2027-01"
    assert (data["pixel_lat"], data["pixel_lon"]) == (-35.005, -70.995)
    assert 0 <= data["hotspot_probability"] <= data["hotspot_score"] <= 1
    assert data["features"]["land_cover"] == 8.0 and data["features"]["land_cover_year"] == 2025
    assert data["model"]["region"] == "Maule"


def test_forecast_endpoint_errors(client, monkeypatch):
    assert client.get("/api/v1/hotspot/forecast", params={"date": "2027-01-20", "lat": -33.0, "lon": -71.0}).status_code == 422
    assert client.get("/api/v1/hotspot/forecast", params={"date": "2027-13-01", "lat": -35.0, "lon": -71.0}).status_code == 422

    monkeypatch.setattr(hotspot_router, "feature_source", lambda _request: FakeSource(lai=None))
    assert client.get("/api/v1/hotspot/forecast", params={"date": "2027-01-20", "lat": -35.0, "lon": -71.0}).status_code == 422

    class Offline(FakeSource):
        def monthly_lai(self, *args):
            raise httpx.ConnectError("offline")

    monkeypatch.setattr(hotspot_router, "feature_source", lambda _request: Offline())
    assert client.get("/api/v1/hotspot/forecast", params={"date": "2027-01-20", "lat": -35.0, "lon": -71.0}).status_code == 502


def test_forecast_uses_only_the_data_api(monkeypatch):
    """[AI] Without APPEEARS_DATA_URL the model calls this server's own data-model API, never local files."""
    request = type("FakeRequest", (), {"base_url": "http://testserver/"})()
    monkeypatch.setattr(hotspot_router.config, "APPEEARS_DATA_URL", "")
    source = hotspot_router.feature_source(request)
    assert isinstance(source, hm.ApiFeatureSource)
    assert source.base_url == "http://testserver/api/v1/data-model"

    monkeypatch.setattr(hotspot_router.config, "APPEEARS_DATA_URL", "https://data.example/api/v1/data-model/")
    assert hotspot_router.feature_source(request).base_url == "https://data.example/api/v1/data-model"


@pytest.mark.parametrize("status", [422, 503])
def test_forecast_passes_on_data_api_422_and_503(client, monkeypatch, status):
    """[AI] A rejection by the data API keeps its status and message instead of becoming a generic 502."""

    class Rejecting(FakeSource):
        def terrain(self, *args):
            request = httpx.Request("GET", "http://data/terrain")
            response = httpx.Response(status, json={"detail": "No terrain data"}, request=request)
            raise httpx.HTTPStatusError("rejected", request=request, response=response)

    monkeypatch.setattr(hotspot_router, "feature_source", lambda _request: Rejecting())
    response = client.get("/api/v1/hotspot/forecast", params={"date": "2027-01-20", "lat": -35.0, "lon": -71.0})
    assert response.status_code == status
    assert response.json()["detail"] == "No terrain data"


def test_forecast_endpoint_without_model_is_503(monkeypatch, tmp_path):
    hm.cached_bundle.cache_clear()
    monkeypatch.setattr(hotspot_router.config, "HOTSPOT_MODEL_DIR", str(tmp_path))
    response = TestClient(app).get("/api/v1/hotspot/forecast", params={"date": "2027-01-20", "lat": -35.0, "lon": -71.0})
    assert response.status_code == 503
