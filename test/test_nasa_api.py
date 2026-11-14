import httpx
from fastapi.testclient import TestClient

from src.main import app
from src.routers.api_nasa import fires, health, quota, sources

client = TestClient(app)


def test_fires_returns_common_and_original_fields(monkeypatch):
    monkeypatch.setattr(fires, "fetch_hotspots", lambda *args: [{
        "latitude": "-33.4", "longitude": "-70.6", "acq_date": "2026-11-14", "bright_ti4": "321.5"
    }])

    response = client.get("/api/v1/fire", params={"source": "VIIRS_SNPP_NRT", "area": "-71,-34,-70,-33"})

    assert response.status_code == 200
    detection = response.json()["data"]["detections"][0]
    assert detection["latitude"] == -33.4
    assert detection["bright_ti4"] == "321.5"


def test_fires_rejects_invalid_area():
    response = client.get("/api/v1/fire", params={"source": "MODIS_NRT", "area": "-200,-90,200,90"})
    assert response.status_code == 422


def test_fires_converts_upstream_failure_to_502(monkeypatch):
    def fail(*args):
        raise httpx.ConnectError("offline")

    monkeypatch.setattr(fires, "fetch_hotspots", fail)
    assert client.get("/api/v1/fire", params={"source": "MODIS_NRT"}).status_code == 502


def test_sources_returns_availability(monkeypatch):
    monkeypatch.setattr(sources, "fetch_sources", lambda: [{"data_id": "MODIS_NRT", "min_date": "2026-01-01", "max_date": "2026-11-14"}])
    response = client.get("/api/v1/nasa/sources")
    assert response.status_code == 200
    assert response.json()["data"][0]["data_id"] == "MODIS_NRT"


def test_quota_returns_usage(monkeypatch):
    monkeypatch.setattr(quota, "fetch_quota", lambda: {
        "transaction_limit": 5000, "current_transactions": 22, "transaction_interval": "10 minutes"
    })
    response = client.get("/api/v1/nasa/quota")
    assert response.status_code == 200
    assert response.json()["data"]["current_transactions"] == 22


def test_health_reports_firms_status(monkeypatch):
    monkeypatch.setattr(health, "fetch_quota", lambda: {
        "transaction_limit": 5000, "current_transactions": 22, "transaction_interval": "10 minutes"
    })
    response = client.get("/api/v1/nasa/health")
    assert response.status_code == 200
    assert response.json()["data"]["available"] is True
    assert response.json()["data"]["key_valid"] is True
