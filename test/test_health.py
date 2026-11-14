from datetime import UTC, datetime, timedelta

import httpx
import pytest
from fakes import FakeClient
from fastapi.testclient import TestClient

from src.main import app

client = TestClient(app, raise_server_exceptions=False)


def ingest_rows(hours_ago: float) -> dict:
    finished = datetime.now(UTC) - timedelta(hours=hours_ago)
    return {"ingest_batch": [{"finished_at": finished.isoformat()}]}


def test_health_fresh(use_fake_db):
    use_fake_db(FakeClient(tables=ingest_rows(2)))

    data = client.get("/v1/health").json()["data"]

    assert data["api"] == "ok"
    assert data["database"] == "ok"
    assert data["data_status"] == "fresh"
    assert data["hours_since_last_ingest"] == pytest.approx(2.0, abs=0.1)


def test_health_stale_after_36_hours(use_fake_db):
    use_fake_db(FakeClient(tables=ingest_rows(40)))

    assert client.get("/v1/health").json()["data"]["data_status"] == "stale"


def test_health_empty_without_ingests(use_fake_db):
    use_fake_db(FakeClient())

    data = client.get("/v1/health").json()["data"]

    assert data["data_status"] == "empty"
    assert data["last_ingest_at"] is None


def test_health_database_down_returns_503(use_fake_db):
    use_fake_db(FakeClient(error=httpx.ConnectError("no route")))

    response = client.get("/v1/health")

    assert response.status_code == 503
    assert response.json()["error"]["code"] == "DATA_STORE_UNAVAILABLE"
