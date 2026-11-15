"""[AI] FIRMS downloads for the ingest job, with retries (the API routers use `src.utils.firms` without retries).

[AI] Decision visible in the code: `sleep` is a parameter so tests can run the retry logic without waiting.
"""

import csv
import io
import time
from collections.abc import Callable
from datetime import date

import httpx

from src.utils import firms as firms_util

RETRY_WAITS = (2, 4, 8)  # seconds before each retry; one try plus three retries


def _retriable(exc: Exception) -> bool:
    """[AI] Retry rate limits (429), server errors (5xx) and network errors; other 4xx errors will not fix themselves."""
    if isinstance(exc, httpx.HTTPStatusError):
        return exc.response.status_code == 429 or exc.response.status_code >= 500
    return True  # network error


def _get_text(http: httpx.Client, url: str, sleep: Callable[[float], None]) -> str:
    """[AI] Return the response body, retrying with the RETRY_WAITS backoff; re-raise after the last attempt."""
    for attempt in range(len(RETRY_WAITS) + 1):
        try:
            response = http.get(url)
            response.raise_for_status()
            return response.text
        except (httpx.TransportError, httpx.HTTPStatusError) as exc:
            if not _retriable(exc) or attempt == len(RETRY_WAITS):
                raise
            sleep(RETRY_WAITS[attempt])
    raise AssertionError("unreachable")  # [AI] the loop always returns or raises; this satisfies type checkers


def fetch_area_csv(
    http: httpx.Client, source: str, bbox: tuple[float, float, float, float], start: date, days: int,
    sleep: Callable[[float], None] = time.sleep,
) -> str:
    """Download hotspots of one FIRMS source for a bounding box; days is 1-5 (FIRMS limit)."""
    area = ",".join(str(value) for value in bbox)
    return _get_text(http, firms_util.firms_url(source, area, days, start.isoformat()), sleep)


def fetch_availability(http: httpx.Client, sleep: Callable[[float], None] = time.sleep) -> list[dict[str, str]]:
    """First and last date FIRMS has for every product."""
    url = f"{firms_util.FIRMS_BASE_URL}/data_availability/csv/{firms_util.FIRMS_MAP_KEY}/all"
    return list(csv.DictReader(io.StringIO(_get_text(http, url, sleep))))
