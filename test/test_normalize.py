import pytest

from src.ingest.normalize import FirmsResponseError, normalize_row, parse_csv

# Rows copied from the real FIRMS samples in C:\NASA\data-samples.
MODIS_SP = {
    "latitude": "-22.6891", "longitude": "-61.5949", "brightness": "302.6", "scan": "2.4", "track": "1.5",
    "acq_date": "2025-09-01", "acq_time": "106", "satellite": "Terra", "instrument": "MODIS",
    "confidence": "50", "version": "61.03", "bright_t31": "290.7", "frp": "14.6", "daynight": "N", "type": "0",
}
MODIS_NRT = {
    "latitude": "-22.48152", "longitude": "-58.34565", "brightness": "309.06", "scan": "1.58", "track": "1.24",
    "acq_date": "2026-11-13", "acq_time": "20", "satellite": "Terra", "instrument": "MODIS",
    "confidence": "71", "version": "6.1NRT", "bright_t31": "293.2", "frp": "11.99", "daynight": "N",
}
VIIRS_SNPP = {
    "latitude": "-18.09095", "longitude": "-58.86467", "bright_ti4": "311.74", "scan": "0.75", "track": "0.77",
    "acq_date": "2025-09-01", "acq_time": "413", "satellite": "N", "instrument": "VIIRS",
    "confidence": "n", "version": "2", "bright_ti5": "289.58", "frp": "1.89", "daynight": "N", "type": "0",
}

HEADER = ",".join(MODIS_SP)


def modis_line(**changes: str) -> str:
    return ",".join({**MODIS_SP, **changes}.values())


def test_modis_sp_row():
    row = normalize_row(MODIS_SP, "MODIS_SP")

    assert row["acq_time"] == "2025-09-01T01:06:00+00:00"
    assert row["satellite_id"] == 5
    assert row["source_code"] == "MODIS_SP"
    assert row["confidence_level"] == 1
    assert row["confidence_raw"] == "50"
    assert row["brightness"] == 302.6
    assert row["brightness_2"] == 290.7
    assert row["fire_type"] == 0
    assert row["lat"] == -22.6891
    # 01:06 UTC at longitude -61.59 is 20:59 the evening before, local solar time.
    assert row["solar_date"] == "2025-08-31"
    assert (row["cell_x"], row["cell_y"]) == (-1232, -454)


def test_modis_nrt_row_without_type_pads_time():
    row = normalize_row(MODIS_NRT, "MODIS_NRT")

    assert row["acq_time"] == "2026-11-13T00:20:00+00:00"
    assert row["fire_type"] is None
    assert row["version"] == "6.1NRT"
    assert row["solar_date"] == "2026-11-12"


def test_viirs_snpp_row_uses_ti4_ti5():
    row = normalize_row(VIIRS_SNPP, "VIIRS_SNPP_SP")

    assert row["satellite_id"] == 1
    assert row["confidence_level"] == 1
    assert row["brightness"] == 311.74
    assert row["brightness_2"] == 289.58
    assert row["solar_date"] == "2025-09-01"
    assert (row["cell_x"], row["cell_y"]) == (-1178, -362)


@pytest.mark.parametrize("raw, level", [("0", 0), ("29", 0), ("30", 1), ("79", 1), ("80", 2), ("100", 2)])
def test_modis_confidence_thresholds(raw, level):
    assert normalize_row({**MODIS_SP, "confidence": raw}, "MODIS_SP")["confidence_level"] == level


@pytest.mark.parametrize("raw, level", [("l", 0), ("n", 1), ("h", 2)])
def test_viirs_confidence_letters(raw, level):
    assert normalize_row({**VIIRS_SNPP, "confidence": raw}, "VIIRS_SNPP_SP")["confidence_level"] == level


@pytest.mark.parametrize(
    "changes",
    [
        {"latitude": "abc"}, {"satellite": "Landsat"}, {"acq_time": "2400"}, {"confidence": "x"},
        {"latitude": "95.0"}, {"longitude": "-181.0"},  # would fail the database CHECK and abort a whole batch
    ],
)
def test_broken_row_returns_none(changes):
    assert normalize_row({**MODIS_SP, **changes}, "MODIS_SP") is None


def test_parse_csv_counts_rejected_rows():
    text = "\n".join([HEADER, modis_line(), modis_line(acq_time="107"), modis_line(latitude="abc")])

    detections, rejected = parse_csv(text, "MODIS_SP")

    assert len(detections) == 2
    assert rejected == 1


def test_parse_csv_header_only_is_valid():
    assert parse_csv(HEADER + "\n", "MODIS_SP") == ([], 0)


def test_parse_csv_invalid_key_raises():
    with pytest.raises(FirmsResponseError, match="Invalid MAP_KEY"):
        parse_csv("Invalid MAP_KEY.", "MODIS_SP")
