import csv
import io

import httpx

from src.config import FIRMS_BASE_URL, FIRMS_MAP_KEY


def firms_url(source: str, area: str, days: int, date: str | None = None) -> str:
    """Build a FIRMS area CSV URL. area is 'west,south,east,north' or 'world'; days is 1-5."""
    url = f"{FIRMS_BASE_URL}/area/csv/{FIRMS_MAP_KEY}/{source}/{area}/{days}"
    return f"{url}/{date}" if date else url


def fetch_hotspots(
    source: str, area: str, days: int = 1, date: str | None = None
) -> list[dict[str, str]]:
    """Download hotspots for any FIRMS source (MODIS_NRT, VIIRS_SNPP_NRT, ...) as dict rows."""
    response = httpx.get(firms_url(source, area, days, date), timeout=60)
    response.raise_for_status()
    return list(csv.DictReader(io.StringIO(response.text)))
