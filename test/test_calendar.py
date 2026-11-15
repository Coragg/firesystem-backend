from datetime import UTC, datetime, timedelta
from datetime import date

import pytest
from fakes import FakeClient
from fastapi.testclient import TestClient

from src.routers.api_database import calendar as calendar_router
from src.main import app

client = TestClient(app, raise_server_exceptions=False)

BIOBIO = [[-73.2, -37.9], [-71.5, -37.9], [-71.5, -36.5], [-73.2, -36.5]]
BIOBIO_SWAPPED = [[-37.9, -73.2], [-36.5, -73.2], [-36.5, -71.5], [-37.9, -71.5]]
NULL_ISLAND = [[0.0, 0.0], [1.0, 0.0], [1.0, 1.0], [0.0, 1.0]]


def body(ring: list[list[float]] = BIOBIO, **extra) -> dict:
    return {"aoi": {"type": "Polygon", "coordinates": [ring]}, **extra}


def count(year: int, month: int, reference: str, cell_days: int) -> dict:
    return {"year": year, "month": month, "reference": reference, "cell_days": cell_days,
            "cell_days_mtd": cell_days, "provisional": False}


def fresh_ingest() -> dict:
    return {"ingest_batch": [{"finished_at": (datetime.now(UTC) - timedelta(hours=1)).isoformat()}]}


@pytest.fixture(autouse=True)
def fixed_today(monkeypatch):
    monkeypatch.setattr(calendar_router, "today_utc", lambda: date(2026, 11, 14))


def test_calendar_shape_matches_front(use_fake_db):
    use_fake_db(FakeClient(rpcs={"calendar_counts": [count(2024, 2, "snpp", 5)]}, tables=fresh_ingest()))

    data = client.post("/api/v1/calendar", json=body(start="2024-01", end="2024-12")).json()["data"]

    assert data["metric"] == "harmonized_fire_cell_days"
    assert [y["year"] for y in data["years"]] == [2024]
    assert len(data["years"][0]["months"]) == 12
    assert len(data["years"][0]["flags"]) == 12
    assert data["years"][0]["months"][1] == 5.0


def test_calendar_scales_modis_months_by_k(use_fake_db):
    use_fake_db(FakeClient(
        rpcs={"calendar_counts": [count(2005, 2, "modis", 100)]},
        tables={**fresh_ingest(), "calibration_factor": [{"reference": "modis", "month": 2, "k": 2.0}]},
    ))

    data = client.post("/api/v1/calendar", json=body(start="2005-01", end="2005-12")).json()["data"]

    assert data["years"][0]["months"][1] == 200.0
    assert data["years"][0]["flags"][1] == ["estimated"]


def test_calendar_outside_coverage_returns_zeros_and_warning(use_fake_db):
    fake = FakeClient(tables=fresh_ingest())
    use_fake_db(fake)

    result = client.post("/api/v1/calendar", json=body(NULL_ISLAND, start="2024-01", end="2024-12")).json()

    assert result["meta"]["warnings"] == ["AOI_OUTSIDE_COVERAGE"]
    assert set(result["data"]["years"][0]["months"]) == {0.0}
    assert fake.calls == []


def test_calendar_swapped_coords_inside_valid_range_is_422(use_fake_db):
    use_fake_db(FakeClient())

    response = client.post("/api/v1/calendar", json=body(BIOBIO_SWAPPED))

    assert response.status_code == 422
    assert "GeoJSON uses [lon, lat]" in response.json()["detail"]


def test_calendar_start_after_end_is_422(use_fake_db):
    use_fake_db(FakeClient())

    response = client.post("/api/v1/calendar", json=body(start="2024-05", end="2024-01"))

    assert response.status_code == 422
    assert "start must not be after end" in str(response.json()["detail"])


def test_calendar_start_in_the_future_is_422(use_fake_db):
    use_fake_db(FakeClient())

    response = client.post("/api/v1/calendar", json=body(start="2030-01"))

    assert response.status_code == 422
    assert "after the latest month" in response.json()["detail"]


def test_calendar_queries_baseline_and_current_month(use_fake_db):
    fake = FakeClient(tables=fresh_ingest())
    use_fake_db(fake)

    client.post("/api/v1/calendar", json=body(start="2024-01", end="2024-12"))

    name, params = fake.calls[0]
    assert name == "calendar_counts"
    assert params["d_from"] == "2001-01-01"
    assert params["d_to"] == "2026-11-30"
    assert params["mtd_day"] == 14


def test_calendar_clips_start_before_2001(use_fake_db):
    use_fake_db(FakeClient(tables=fresh_ingest()))

    result = client.post("/api/v1/calendar", json=body(start="1999-05", end="2001-12")).json()

    assert result["data"]["years"][0]["year"] == 2001
    assert "RANGE_CLIPPED" in result["meta"]["warnings"]


def test_calendar_short_baseline_warns(use_fake_db):
    use_fake_db(FakeClient(tables=fresh_ingest()))

    result = client.post("/api/v1/calendar", json=body(baseline_from=2024, baseline_to=2025)).json()

    assert "BASELINE_TOO_SHORT" in result["meta"]["warnings"]


def test_calendar_warns_when_data_is_stale(use_fake_db):
    use_fake_db(FakeClient())

    result = client.post("/api/v1/calendar", json=body()).json()

    assert "DATA_STALE" in result["meta"]["warnings"]
