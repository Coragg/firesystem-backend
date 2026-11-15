import csv
import io
from typing import Any

import httpx

from src.config import FIRMS_BASE_URL, FIRMS_MAP_KEY

FIRMS_SOURCES_URL = f"{FIRMS_BASE_URL}/data_availability/csv/{FIRMS_MAP_KEY}/ALL"
FIRMS_QUOTA_URL = f"{FIRMS_BASE_URL.rstrip('/').removesuffix('/api')}/mapserver/mapkey_status/"


def _get(url: str) -> httpx.Response:
    response = httpx.get(url, timeout=60)
    response.raise_for_status()
    return response


def _parse_csv(response: httpx.Response) -> list[dict[str, str]]:
    return list(csv.DictReader(io.StringIO(response.text)))


def firms_url(source: str, area: str, days: int, date: str | None = None) -> str:
    """Build a FIRMS area CSV URL. area is 'west,south,east,north' or 'world'; days is 1-5."""
    url = f"{FIRMS_BASE_URL}/area/csv/{FIRMS_MAP_KEY}/{source}/{area}/{days}"
    return f"{url}/{date}" if date else url


def fetch_hotspots(
    source: str, area: str, days: int = 1, date: str | None = None
) -> list[dict[str, str]]:
    """Download hotspots for any FIRMS source (MODIS_NRT, VIIRS_SNPP_NRT, ...) as dict rows."""
    return _parse_csv(_get(firms_url(source, area, days, date)))


def fetch_sources() -> list[dict[str, str]]:
    """Return available FIRMS products and their date ranges."""
    return _parse_csv(_get(FIRMS_SOURCES_URL))


def fetch_quota() -> dict[str, Any]:
    """Return current transaction usage for the configured FIRMS map key."""
    response = _get(f"{FIRMS_QUOTA_URL}?MAP_KEY={FIRMS_MAP_KEY}")
    payload = response.json()
    if not isinstance(payload, dict):
        raise ValueError("FIRMS returned an invalid quota response")
    return payload
