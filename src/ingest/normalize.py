import csv
import io
from datetime import UTC, datetime
from typing import TypedDict

SATELLITE_IDS = {"N": 1, "N20": 2, "N21": 3, "Aqua": 4, "Terra": 5}
VIIRS_CONFIDENCE = {"l": 0, "n": 1, "h": 2}


class FirmsResponseError(Exception):
    """FIRMS answered with something that is not a hotspot CSV (bad key, quota, outage page)."""


class Detection(TypedDict):
    satellite_id: int
    source_code: str
    acq_time: str
    lat: float
    lon: float
    confidence_level: int
    confidence_raw: str
    brightness: float | None
    brightness_2: float | None
    frp: float | None
    scan: float | None
    track: float | None
    daynight: str | None
    fire_type: int | None
    version: str | None


def _float(value: str | None) -> float | None:
    return float(value) if value not in (None, "") else None


def _confidence_level(raw: str) -> int:
    """MODIS sends 0-100 (below 30 low, 80 and up high); VIIRS sends l, n or h."""
    if raw.isdigit():
        number = int(raw)
        return 0 if number < 30 else 1 if number < 80 else 2
    return VIIRS_CONFIDENCE[raw.lower()]


def normalize_row(row: dict[str, str], source_code: str) -> Detection | None:
    """Turn one MODIS or VIIRS CSV row into the common columns; None if the row is broken."""
    try:
        padded_time = row["acq_time"].zfill(4)  # FIRMS drops leading zeros: "106" is 01:06
        acquired = datetime.fromisoformat(row["acq_date"]).replace(
            hour=int(padded_time[:2]), minute=int(padded_time[2:]), tzinfo=UTC
        )
        is_modis = row["instrument"] == "MODIS"
        fire_type = row.get("type")
        return Detection(
            satellite_id=SATELLITE_IDS[row["satellite"]],
            source_code=source_code,
            acq_time=acquired.isoformat(),
            lat=float(row["latitude"]),
            lon=float(row["longitude"]),
            confidence_level=_confidence_level(row["confidence"]),
            confidence_raw=row["confidence"],
            brightness=_float(row.get("brightness" if is_modis else "bright_ti4")),
            brightness_2=_float(row.get("bright_t31" if is_modis else "bright_ti5")),
            frp=_float(row.get("frp")),
            scan=_float(row.get("scan")),
            track=_float(row.get("track")),
            daynight=row.get("daynight") or None,
            fire_type=int(fire_type) if fire_type not in (None, "") else None,
            version=row.get("version") or None,
        )
    except (KeyError, ValueError):
        return None


def parse_csv(text: str, source_code: str) -> tuple[list[Detection], int]:
    """Parse a FIRMS CSV into detections plus the number of rejected rows."""
    reader = csv.DictReader(io.StringIO(text))
    if not reader.fieldnames or "latitude" not in reader.fieldnames:
        raise FirmsResponseError(text.strip()[:200] or "empty response")
    detections: list[Detection] = []
    rejected = 0
    for row in reader:
        detection = normalize_row(row, source_code)
        if detection is None:
            rejected += 1
        else:
            detections.append(detection)
    return detections, rejected
