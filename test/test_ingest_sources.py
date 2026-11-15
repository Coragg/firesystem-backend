from typing import get_args

from src.ingest.run import KNOWN_SOURCES
from src.models.nasa import FirmsSource


def test_ingest_loads_exactly_the_sources_the_api_accepts():
    assert KNOWN_SOURCES == set(get_args(FirmsSource))
    assert "VIIRS_NOAA21_NRT" in KNOWN_SOURCES
