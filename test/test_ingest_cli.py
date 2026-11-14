from datetime import date

import httpx
import pytest
from fakes import FakeClient

from src.ingest import run
from src.ingest.normalize import FirmsResponseError
from src.utils import firms as firms_util

TODAY = date(2026, 11, 14)
AVAILABILITY = "data_id,min_date,max_date\nVIIRS_NOAA20_NRT,2026-08-01,2026-11-14\nVIIRS_SNPP_NRT,2026-08-01,2026-11-02\n"
VIIRS_CSV = (
    "latitude,longitude,bright_ti4,scan,track,acq_date,acq_time,satellite,instrument,confidence,version,"
    "bright_ti5,frp,daynight\n"
    "-37.4,-72.3,300.1,0.4,0.5,2026-11-13,1840,N20,VIIRS,n,2.0NRT,280.2,4.1,N\n"
)
NRT_SOURCES = [
    {"code": "VIIRS_SNPP_NRT", "kind": "NRT", "min_date": "2026-08-01", "max_date": "2026-11-02"},
    {"code": "VIIRS_NOAA20_NRT", "kind": "NRT", "min_date": "2026-08-01", "max_date": "2026-11-14"},
]
SP_SOURCES = [{"code": "VIIRS_SNPP_SP", "kind": "SP", "min_date": "2025-12-30", "max_date": "2026-01-03"}]


@pytest.fixture(autouse=True)
def firms_config(monkeypatch):
    monkeypatch.setattr(firms_util, "FIRMS_BASE_URL", "https://x/api")
    monkeypatch.setattr(firms_util, "FIRMS_MAP_KEY", "KEY123")


def http_client(firms_status: int = 200, availability: str = AVAILABILITY) -> tuple[httpx.Client, list[str]]:
    urls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        urls.append(str(request.url))
        if "data_availability" in str(request.url):
            return httpx.Response(200, text=availability)
        return httpx.Response(firms_status, text=VIIRS_CSV)

    return httpx.Client(transport=httpx.MockTransport(handler)), urls


def batches(already_loaded: bool = False):
    """ingest_batch rows: inserts return an id; selects find a loaded chunk or nothing."""

    def data(ops: list) -> list[dict]:
        if ops[0][0] == "insert":
            return [{"id": 7}]
        return [{"id": 1}] if already_loaded else []

    return data


def updates(client: FakeClient, table: str) -> list[dict]:
    return [ops[0][1][0] for name, ops in client.executed if name == table and ops[0][0] == "update"]


def test_chunks_are_five_days():
    assert run.chunks(date(2025, 9, 1), date(2025, 9, 12)) == [
        (date(2025, 9, 1), 5), (date(2025, 9, 6), 5), (date(2025, 9, 11), 2),
    ]


def test_active_nrt_sources_skip_a_satellite_that_stopped():
    assert run.active_nrt_sources(NRT_SOURCES, TODAY) == ["VIIRS_NOAA20_NRT"]


def test_daily_loads_only_active_sources_and_rebuilds_recent_days():
    client = FakeClient(tables={"firms_source": NRT_SOURCES, "ingest_batch": batches()})
    http, urls = http_client()

    code = run.daily(client, http, TODAY, sleep=lambda _s: None)

    firms_urls = [u for u in urls if "data_availability" not in u]
    assert code == 0
    assert len(firms_urls) == 3  # one per coverage area
    assert all("VIIRS_NOAA20_NRT" in u and u.endswith("/5/2026-11-10") for u in firms_urls)
    upserts = [ops for name, ops in client.executed if name == "fire_detection"]
    assert len(upserts) == 3
    assert upserts[0][0][1][0][0]["batch_id"] == 7
    assert upserts[0][0][2] == {"on_conflict": "satellite_id,source_code,acq_time,lat,lon", "ignore_duplicates": True}
    assert client.calls[-1] == ("rebuild_cell_days", {"d_from": "2026-11-09", "d_to": "2026-11-14"})
    assert {u["status"] for u in updates(client, "ingest_batch")} == {"ok"}


def test_daily_failed_download_exits_1_and_never_stores_the_key():
    client = FakeClient(tables={"firms_source": NRT_SOURCES, "ingest_batch": batches()})
    http, _ = http_client(firms_status=404)

    code = run.daily(client, http, TODAY, sleep=lambda _s: None)

    failed = [u for u in updates(client, "ingest_batch") if u["status"] == "failed"]
    assert code == 1
    assert len(failed) == 3
    assert all("KEY123" not in u["error"] for u in failed)


def test_backfill_skips_chunks_already_loaded():
    client = FakeClient(tables={"firms_source": SP_SOURCES, "ingest_batch": batches(already_loaded=True)})
    http, urls = http_client()

    code = run.backfill(client, http, sleep=lambda _s: None)

    assert code == 0
    assert [u for u in urls if "data_availability" not in u] == []


def test_backfill_replaces_nrt_and_rebuilds_year_by_year_after_success():
    client = FakeClient(tables={"firms_source": SP_SOURCES, "ingest_batch": batches()})
    http, _ = http_client()

    code = run.backfill(client, http, sleep=lambda _s: None)

    names = [name for name, _ in client.calls]
    assert code == 0
    assert ("replace_nrt_with_sp", {"nrt_code": "VIIRS_SNPP_NRT", "d_from": "2025-12-30", "d_to": "2026-01-03"}) in client.calls
    assert [p for n, p in client.calls if n == "rebuild_cell_days"] == [
        {"d_from": "2025-12-30", "d_to": "2025-12-31"},
        {"d_from": "2026-01-01", "d_to": "2026-01-03"},
    ]
    assert names[-1] == "recompute_calibration"
    assert "refresh_excluded_cells" in names


def test_backfill_with_a_failed_chunk_does_not_delete_or_rebuild():
    client = FakeClient(tables={"firms_source": SP_SOURCES, "ingest_batch": batches()})
    http, _ = http_client(firms_status=404)

    code = run.backfill(client, http, sleep=lambda _s: None)

    assert code == 1
    assert client.calls == []  # replacing NRT with a hole in SP would lose data


def upserts_to(client: FakeClient, table: str) -> list[tuple]:
    return [ops[0] for name, ops in client.executed if name == table and ops[0][0] == "upsert"]


def test_refresh_creates_firms_source_rows_for_known_products_only():
    client = FakeClient(tables={"firms_source": NRT_SOURCES, "ingest_batch": batches()})
    availability = AVAILABILITY + "LANDSAT_NRT,2022-06-20,2026-11-14\nMODIS_SP,2000-11-01,2026-08-31\n"
    http, _ = http_client(availability=availability)

    run.daily(client, http, TODAY, sleep=lambda _s: None)

    (_, args, kwargs), = upserts_to(client, "firms_source")
    rows = {row["code"]: row for row in args[0]}
    assert kwargs == {"on_conflict": "code"}
    assert set(rows) == {"VIIRS_NOAA20_NRT", "VIIRS_SNPP_NRT", "MODIS_SP"}
    assert (rows["MODIS_SP"]["instrument"], rows["MODIS_SP"]["kind"]) == ("MODIS", "SP")
    assert (rows["VIIRS_NOAA20_NRT"]["instrument"], rows["VIIRS_NOAA20_NRT"]["kind"]) == ("VIIRS", "NRT")
    assert rows["VIIRS_NOAA20_NRT"]["max_date"] == "2026-11-14"
    assert rows["MODIS_SP"]["checked_at"]


@pytest.mark.parametrize("availability", ["Invalid MAP_KEY.", "data_id,min_date,max_date\nLANDSAT_NRT,2022-06-20,2026-11-14\n"])
def test_refresh_without_known_products_stops_instead_of_loading_nothing(availability):
    client = FakeClient(tables={"firms_source": NRT_SOURCES, "ingest_batch": batches()})
    http, urls = http_client(availability=availability)

    with pytest.raises(FirmsResponseError):
        run.daily(client, http, TODAY, sleep=lambda _s: None)

    assert [u for u in urls if "data_availability" not in u] == []


def test_backfill_limits_range_and_source():
    client = FakeClient(tables={"firms_source": SP_SOURCES, "ingest_batch": batches()})
    http, urls = http_client()

    run.backfill(client, http, only_source="MODIS_SP", sleep=lambda _s: None)
    assert [u for u in urls if "data_availability" not in u] == []

    run.backfill(client, http, d_from=date(2026, 1, 1), d_to=date(2026, 1, 2), sleep=lambda _s: None)
    firms_urls = [u for u in urls if "data_availability" not in u]
    assert len(firms_urls) == 3 and all(u.endswith("/2/2026-01-01") for u in firms_urls)
