from datetime import date, timedelta

import pytest
from fakes import FakeClient
from fastapi.testclient import TestClient

from src import main
from src.main import app

client = TestClient(app, raise_server_exceptions=False)

BIOBIO = [[-73.2, -37.9], [-71.5, -37.9], [-71.5, -36.5], [-73.2, -36.5]]
HEADER = "id;acq_time_utc;solar_date;lat;lon;satellite;instrument;confidence;frp;source;excluded"


def row(id: int, **changes) -> dict:
    return {
        "id": id, "acq_time": "2026-11-13T18:40:00+00:00", "solar_date": "2026-11-13", "lat": -37.41,
        "lon": -72.35, "satellite": "noaa20", "instrument": "VIIRS", "confidence_level": 1, "frp": 4.1,
        "source_kind": "NRT", "excluded": False, **changes,
    }


def body(start: str = "2026-11-01", end: str = "2026-11-14", **extra) -> dict:
    return {"aoi": {"type": "Polygon", "coordinates": [BIOBIO]}, "start_date": start, "end_date": end, **extra}


def test_hotspots_page_maps_rows_and_next_cursor(use_fake_db):
    use_fake_db(FakeClient(rpcs={"hotspots_page": [row(7), row(9, confidence_level=0, source_kind="SP", excluded=True)]}))

    data = client.post("/v1/hotspots", json=body(limit=2)).json()["data"]

    assert data["next_after_id"] == 9
    first, second = data["items"]
    assert (first["confidence"], first["provisional"], first["excluded"]) == ("nominal", True, False)
    assert (second["confidence"], second["provisional"], second["excluded"]) == ("low", False, True)
    assert first["satellite"] == "noaa20"


def test_hotspots_last_page_has_no_cursor(use_fake_db):
    use_fake_db(FakeClient(rpcs={"hotspots_page": [row(7)]}))

    assert client.post("/v1/hotspots", json=body(limit=5)).json()["data"]["next_after_id"] is None


def test_hotspots_passes_cursor_and_limit_to_rpc(use_fake_db):
    fake = FakeClient()
    use_fake_db(fake)

    client.post("/v1/hotspots", json=body(after_id=41, limit=10))

    assert fake.calls[0][1]["after_id"] == 41
    assert fake.calls[0][1]["page_size"] == 10


def test_hotspots_366_days_is_allowed(use_fake_db):
    use_fake_db(FakeClient())
    start = date(2025, 1, 1)

    response = client.post("/v1/hotspots", json=body(str(start), str(start + timedelta(days=365))))

    assert response.status_code == 200


def test_hotspots_367_days_is_range_too_long(use_fake_db):
    use_fake_db(FakeClient())
    start = date(2025, 1, 1)

    response = client.post("/v1/hotspots", json=body(str(start), str(start + timedelta(days=366))))

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "RANGE_TOO_LONG"


@pytest.mark.parametrize("extra", [{"limit": 1001}, {"limit": 0}, {"after_id": -1}])
def test_hotspots_rejects_bad_paging(use_fake_db, extra):
    use_fake_db(FakeClient())

    response = client.post("/v1/hotspots", json=body(**extra))

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "INVALID_REQUEST"


def test_hotspots_end_before_start_is_invalid(use_fake_db):
    use_fake_db(FakeClient())

    response = client.post("/v1/hotspots", json=body("2026-11-14", "2026-11-01"))

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "INVALID_REQUEST"


def test_export_semicolon_with_bom_and_download_name(use_fake_db):
    use_fake_db(FakeClient(rpcs={"hotspots_count": 2, "hotspots_page": [row(1), row(2)]}))

    response = client.post("/v1/hotspots/export", json=body(separator=";"))

    lines = response.text.splitlines()
    assert response.status_code == 200
    assert lines[0] == "﻿" + HEADER
    assert lines[1] == "1;2026-11-13T18:40:00+00:00;2026-11-13;-37.41;-72.35;noaa20;VIIRS;nominal;4.1;NRT;false"
    assert 'filename="hotspots.csv"' in response.headers["content-disposition"]
    assert response.headers["content-type"].startswith("text/csv")


def test_export_over_one_million_rows_is_413(use_fake_db):
    use_fake_db(FakeClient(rpcs={"hotspots_count": 1_000_001}))

    response = client.post("/v1/hotspots/export", json=body())

    assert response.status_code == 413
    assert response.json()["error"]["code"] == "EXPORT_TOO_LARGE"


def test_export_has_no_date_limit(use_fake_db):
    use_fake_db(FakeClient(rpcs={"hotspots_count": 0}))

    response = client.post("/v1/hotspots/export", json=body("2001-01-01", "2026-11-14"))

    assert response.status_code == 200


def test_export_marks_incomplete_file_when_database_fails_midway(use_fake_db, monkeypatch):
    monkeypatch.setattr(main, "EXPORT_PAGE_SIZE", 2)
    pages = iter([[row(1), row(2)]])

    def page(_params):
        try:
            return next(pages)
        except StopIteration:
            raise RuntimeError("database went away")

    use_fake_db(FakeClient(rpcs={"hotspots_count": 4, "hotspots_page": page}))

    response = client.post("/v1/hotspots/export", json=body(separator=";"))

    lines = response.text.splitlines()
    assert response.status_code == 200
    assert len(lines) == 4  # header, two rows, marker
    assert lines[-1] == f"# EXPORT_INCOMPLETE request_id={response.headers['X-Request-ID']}"
