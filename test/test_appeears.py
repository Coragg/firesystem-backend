from datetime import datetime

import pytest

from src.utils import appeears


class FakeResponse:
    def __init__(self, json_data=None, status_code: int = 200, chunks: list[bytes] | None = None):
        self._json = json_data
        self.status_code = status_code
        self._chunks = chunks or []

    def json(self):
        return self._json

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")

    def iter_content(self, chunk_size: int):
        yield from self._chunks

    def __enter__(self):
        return self

    def __exit__(self, *exc) -> None:
        return None


BBOX = (-95.0, 20.0, -75.0, 35.0)
HEADERS = {"Authorization": "Bearer abc"}


def test_credentials_from_netrc_reads_earthdata_entry(tmp_path):
    netrc_file = tmp_path / ".netrc"
    netrc_file.write_text("machine urs.earthdata.nasa.gov login alice password secret\n")
    netrc_file.chmod(0o600)

    assert appeears.credentials_from_netrc(netrc_path=str(netrc_file)) == ("alice", "secret")


def test_credentials_from_netrc_raises_when_host_missing(tmp_path):
    netrc_file = tmp_path / ".netrc"
    netrc_file.write_text("machine example.com login bob password pw\n")
    netrc_file.chmod(0o600)

    with pytest.raises(LookupError):
        appeears.credentials_from_netrc(netrc_path=str(netrc_file))


def test_login_returns_bearer_headers(monkeypatch):
    calls = {}

    def fake_post(url, auth=None, timeout=None):
        calls["url"], calls["auth"] = url, auth
        return FakeResponse({"token": "abc", "token_type": "Bearer"})

    monkeypatch.setattr(appeears.requests, "post", fake_post)

    assert appeears.login("alice", "secret") == HEADERS
    assert calls == {"url": f"{appeears.API_URL}/login", "auth": ("alice", "secret")}


def test_login_raises_on_http_error(monkeypatch):
    monkeypatch.setattr(appeears.requests, "post", lambda *a, **k: FakeResponse({}, status_code=401))

    with pytest.raises(RuntimeError):
        appeears.login("alice", "wrong")


def test_logout_posts_with_headers(monkeypatch):
    calls = {}

    def fake_post(url, headers=None, timeout=None):
        calls["url"], calls["headers"] = url, headers
        return FakeResponse(status_code=204)

    monkeypatch.setattr(appeears.requests, "post", fake_post)
    appeears.logout(HEADERS)

    assert calls == {"url": f"{appeears.API_URL}/logout", "headers": HEADERS}


def test_bbox_to_geojson_builds_closed_polygon():
    geo = appeears.bbox_to_geojson(BBOX)
    ring = geo["features"][0]["geometry"]["coordinates"][0]

    assert geo["type"] == "FeatureCollection"
    assert ring[0] == ring[-1] == [-95.0, 20.0]
    assert ring[2] == [-75.0, 35.0]


@pytest.mark.parametrize("bbox", [(-75, 20, -95, 35), (-95, 35, -75, 20)])
def test_bbox_to_geojson_rejects_inverted_bbox(bbox):
    with pytest.raises(ValueError):
        appeears.bbox_to_geojson(bbox)


def test_build_area_task_formats_dates_and_output():
    layers = [{"product": "MOD13A1.061", "layer": "_500m_16_days_NDVI"}]
    task = appeears.build_area_task("t", BBOX, datetime(2005, 8, 1), datetime(2005, 9, 30), layers)

    assert task["task_type"] == "area"
    assert task["params"]["dates"] == [{"startDate": "08-01-2005", "endDate": "09-30-2005"}]
    assert task["params"]["layers"] == layers
    assert task["params"]["output"] == {"format": {"type": "netcdf4"}, "projection": "geographic"}


def test_build_area_task_validates_inputs():
    layers = [{"product": "p", "layer": "l"}]
    with pytest.raises(ValueError):
        appeears.build_area_task("t", BBOX, datetime(2005, 9, 1), datetime(2005, 8, 1), layers)
    with pytest.raises(ValueError):
        appeears.build_area_task("t", BBOX, datetime(2005, 8, 1), datetime(2005, 9, 1), [])


def test_submit_task_returns_task_id(monkeypatch):
    monkeypatch.setattr(appeears.requests, "post", lambda *a, **k: FakeResponse({"task_id": "t-1"}))

    assert appeears.submit_task(HEADERS, {"task_type": "area"}) == "t-1"


def test_wait_for_task_polls_until_done(monkeypatch):
    statuses = iter(["pending", "processing", "done"])
    monkeypatch.setattr(appeears.requests, "get", lambda *a, **k: FakeResponse({"status": next(statuses)}))
    monkeypatch.setattr(appeears.time, "sleep", lambda s: None)

    appeears.wait_for_task(HEADERS, "t-1", poll_seconds=0)

    assert next(statuses, None) is None


def test_wait_for_task_raises_on_error(monkeypatch):
    monkeypatch.setattr(appeears.requests, "get", lambda *a, **k: FakeResponse({"status": "error"}))

    with pytest.raises(RuntimeError):
        appeears.wait_for_task(HEADERS, "t-1")


def test_wait_for_task_times_out(monkeypatch):
    monkeypatch.setattr(appeears.requests, "get", lambda *a, **k: FakeResponse({"status": "pending"}))
    monkeypatch.setattr(appeears.time, "sleep", lambda s: None)

    with pytest.raises(TimeoutError):
        appeears.wait_for_task(HEADERS, "t-1", max_wait_seconds=-1)


def test_download_bundle_writes_only_matching_files(monkeypatch, tmp_path):
    bundle = {"files": [
        {"file_id": "f1", "file_name": "MOD13A1.061_500m_aid0001.nc"},
        {"file_id": "f2", "file_name": "request.json"},
        {"file_id": "f3", "file_name": "sub/MCD64A1.061_500m_aid0001.nc"},
    ]}
    requested = []

    def fake_get(url, headers=None, stream=False, timeout=None):
        requested.append(url)
        if url.endswith("/bundle/t-1"):
            return FakeResponse(bundle)
        return FakeResponse(chunks=[b"abc", b"def"])

    monkeypatch.setattr(appeears.requests, "get", fake_get)
    paths = appeears.download_bundle(HEADERS, "t-1", tmp_path / "out")

    assert [p.name for p in paths] == ["MOD13A1.061_500m_aid0001.nc", "MCD64A1.061_500m_aid0001.nc"]
    assert all(p.read_bytes() == b"abcdef" for p in paths)
    assert f"{appeears.API_URL}/bundle/t-1/f2" not in requested


def test_build_area_task_uses_custom_geo():
    geo = {"type": "FeatureCollection", "features": []}
    task = appeears.build_area_task("t", BBOX, datetime(2005, 8, 1), datetime(2005, 9, 30),
                                    [{"product": "p", "layer": "l"}], geo=geo)
    assert task["params"]["geo"] is geo


def test_download_bundle_filters_names_skips_existing_and_runs_in_parallel(monkeypatch, tmp_path):
    bundle = {"files": [
        {"file_id": "f1", "file_name": "MCD15A3H.061_2012/MCD15A3H.061_Lai_500m_doy2012061_aid0001.tif", "file_size": 6},
        {"file_id": "f2", "file_name": "MOD11A2.061_2012/MOD11A2.061_LST_Day_1km_doy2012061_aid0001.tif", "file_size": 6},
        {"file_id": "f3", "file_name": "SRTMGL3_NC.003/SRTMGL3_NC.003_SRTMGL3_DEM_doy2000042_aid0001.tif", "file_size": 6},
    ]}
    out = tmp_path / "out"
    out.mkdir()
    (out / "SRTMGL3_NC.003_SRTMGL3_DEM_doy2000042_aid0001.tif").write_bytes(b"abcdef")  # already complete
    requested = []

    def fake_get(url, headers=None, stream=False, timeout=None):
        requested.append(url)
        if url.endswith("/bundle/t-1"):
            return FakeResponse(bundle)
        return FakeResponse(chunks=[b"abc", b"def"])

    monkeypatch.setattr(appeears.requests, "get", fake_get)
    paths = appeears.download_bundle(
        HEADERS, "t-1", out, extensions=(".tif",), name_contains=("_Lai_500m_", "_SRTMGL3_DEM_"), workers=2,
    )

    assert [p.name for p in paths] == [
        "MCD15A3H.061_Lai_500m_doy2012061_aid0001.tif", "SRTMGL3_NC.003_SRTMGL3_DEM_doy2000042_aid0001.tif",
    ]
    assert all(p.read_bytes() == b"abcdef" for p in paths)
    assert requested == [f"{appeears.API_URL}/bundle/t-1", f"{appeears.API_URL}/bundle/t-1/f1"]
    assert not list(out.glob("*.part"))


def test_download_bundle_retries_failed_files(monkeypatch, tmp_path):
    bundle = {"files": [{"file_id": "f1", "file_name": "a_Lai_500m_doy2012061_aid0001.tif"}]}
    failures = iter([True, True, False])

    def fake_get(url, headers=None, stream=False, timeout=None):
        if url.endswith("/bundle/t-1"):
            return FakeResponse(bundle)
        if next(failures):
            raise appeears.requests.ConnectionError("reset by peer")
        return FakeResponse(chunks=[b"ok"])

    monkeypatch.setattr(appeears.requests, "get", fake_get)
    monkeypatch.setattr(appeears.time, "sleep", lambda s: None)
    paths = appeears.download_bundle(HEADERS, "t-1", tmp_path, extensions=(".tif",), retries=2)
    assert paths[0].read_bytes() == b"ok"

    failures = iter([True, True])
    (tmp_path / "a_Lai_500m_doy2012061_aid0001.tif").unlink()
    with pytest.raises(appeears.requests.ConnectionError):
        appeears.download_bundle(HEADERS, "t-1", tmp_path, extensions=(".tif",), retries=1)
