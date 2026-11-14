import logging

import httpx
import pytest

import src.main  # noqa: F401  (importing the app configures logging)
from src.utils import firms


def test_api_does_not_log_the_firms_key(monkeypatch, caplog):
    """httpx logs the full request URL at INFO, and FIRMS puts the map key in the URL."""
    monkeypatch.setattr(firms, "FIRMS_MAP_KEY", "SECRETKEY123")
    transport = httpx.MockTransport(lambda request: httpx.Response(200, text="ok"))

    with caplog.at_level(logging.INFO), httpx.Client(transport=transport) as http:
        http.get(f"https://firms.example/api/area/csv/{firms.FIRMS_MAP_KEY}/MODIS_NRT/world/1")

    assert "SECRETKEY123" not in caplog.text


@pytest.mark.parametrize("name", ["httpx", "httpx2", "httpcore"])
def test_http_client_loggers_are_quiet(name):
    assert logging.getLogger(name).getEffectiveLevel() >= logging.WARNING
