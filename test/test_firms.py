import httpx

from src.utils import firms


def test_firms_url_with_and_without_date(monkeypatch):
    monkeypatch.setattr(firms, "FIRMS_BASE_URL", "https://x/api")
    monkeypatch.setattr(firms, "FIRMS_MAP_KEY", "KEY")

    assert firms.firms_url("MODIS_NRT", "world", 1) == "https://x/api/area/csv/KEY/MODIS_NRT/world/1"
    assert firms.firms_url("VIIRS_SNPP_SP", "1,2,3,4", 5, "2024-01-01").endswith(
        "/VIIRS_SNPP_SP/1,2,3,4/5/2024-01-01"
    )


def test_fetch_hotspots_parses_csv(monkeypatch):
    csv_text = "latitude,longitude,acq_date\n-1.5,-60.2,2024-01-01\n"
    monkeypatch.setattr(
        firms.httpx, "get", lambda *a, **k: httpx.Response(200, text=csv_text, request=httpx.Request("GET", "http://x"))
    )

    assert firms.fetch_hotspots("MODIS_NRT", "world") == [
        {"latitude": "-1.5", "longitude": "-60.2", "acq_date": "2024-01-01"}
    ]
