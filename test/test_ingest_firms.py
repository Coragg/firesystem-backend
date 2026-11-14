from datetime import date

import httpx
import pytest

from src.ingest.firms import fetch_area_csv, fetch_availability
from src.utils import firms as firms_util

BBOX = (-75.8, -56.1, -66.4, -17.4)


@pytest.fixture(autouse=True)
def firms_config(monkeypatch):
    monkeypatch.setattr(firms_util, "FIRMS_BASE_URL", "https://x/api")
    monkeypatch.setattr(firms_util, "FIRMS_MAP_KEY", "KEY")


def http_client(responses: list) -> tuple[httpx.Client, list[str]]:
    """A client that answers with the given items in order; an Exception item is raised."""
    urls: list[str] = []
    queue = iter(responses)

    def handler(request: httpx.Request) -> httpx.Response:
        urls.append(str(request.url))
        item = next(queue)
        if isinstance(item, Exception):
            raise item
        status, text = item
        return httpx.Response(status, text=text)

    return httpx.Client(transport=httpx.MockTransport(handler)), urls


def test_fetch_area_csv_builds_area_url():
    http, urls = http_client([(200, "latitude\n")])

    text = fetch_area_csv(http, "MODIS_NRT", BBOX, date(2026, 11, 10), 5, sleep=lambda _s: None)

    assert text == "latitude\n"
    assert urls == ["https://x/api/area/csv/KEY/MODIS_NRT/-75.8,-56.1,-66.4,-17.4/5/2026-11-10"]


def test_fetch_retries_then_succeeds():
    http, urls = http_client([(429, ""), (429, ""), (200, "ok")])
    waits: list[float] = []

    text = fetch_area_csv(http, "MODIS_NRT", BBOX, date(2026, 11, 10), 5, sleep=waits.append)

    assert text == "ok"
    assert waits == [2, 4]
    assert len(urls) == 3


def test_fetch_gives_up_after_three_retries():
    http, urls = http_client([(503, "")] * 4)
    waits: list[float] = []

    with pytest.raises(httpx.HTTPStatusError):
        fetch_area_csv(http, "MODIS_NRT", BBOX, date(2026, 11, 10), 5, sleep=waits.append)

    assert waits == [2, 4, 8]
    assert len(urls) == 4


def test_fetch_does_not_retry_client_errors():
    http, urls = http_client([(404, "")])

    with pytest.raises(httpx.HTTPStatusError):
        fetch_area_csv(http, "MODIS_NRT", BBOX, date(2026, 11, 10), 5, sleep=lambda _s: None)

    assert len(urls) == 1


def test_fetch_retries_network_errors():
    http, urls = http_client([httpx.ConnectError("down"), (200, "ok")])

    assert fetch_area_csv(http, "MODIS_NRT", BBOX, date(2026, 11, 10), 5, sleep=lambda _s: None) == "ok"
    assert len(urls) == 2


def test_fetch_availability_returns_rows():
    text = "data_id,min_date,max_date\nMODIS_SP,2000-11-01,2026-08-31\n"
    http, urls = http_client([(200, text)])

    rows = fetch_availability(http, sleep=lambda _s: None)

    assert rows == [{"data_id": "MODIS_SP", "min_date": "2000-11-01", "max_date": "2026-08-31"}]
    assert urls == ["https://x/api/data_availability/csv/KEY/all"]
