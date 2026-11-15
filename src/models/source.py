"""[AI] Models of `GET /api/v1/health` and `GET /api/v1/sources` (data provenance and calibration)."""

from datetime import date, datetime
from typing import Literal

from pydantic import BaseModel


class HealthData(BaseModel):
    """[AI] API and database status plus how fresh the loaded data is."""

    api: Literal["ok"]
    database: Literal["ok"]
    data_status: Literal["fresh", "stale", "empty"]
    last_ingest_at: datetime | None
    hours_since_last_ingest: float | None


class SatelliteInfo(BaseModel):
    """[AI] One satellite and the part it plays in harmonization."""

    code: str
    instrument: str
    pixel_m: int
    first_date: date | None
    last_date: date | None
    # [AI] reference = S-NPP (the scale everything is expressed in); fallback = other VIIRS satellites
    # used on days S-NPP has nothing; scaled = MODIS, converted to the S-NPP scale with calibration factors.
    role: Literal["reference", "fallback", "scaled"]


class ProductInfo(BaseModel):
    """[AI] A FIRMS product the ingest knows about, with its available date range."""

    code: str
    kind: Literal["SP", "NRT"]
    min_date: date | None
    max_date: date | None


class CalibrationInfo(BaseModel):
    """[AI] Scale factor k that converts a reference's cell-days to S-NPP cell-days for one calendar month."""

    reference: str
    month: int
    k: float
    overlap_cell_days: int  # [AI] cell-days both sensors observed; the evidence behind k
    estimated: bool  # [AI] True when the overlap was too small (< 30) and the yearly k was used instead


class Validation(BaseModel):
    """[AI] How closely the scaled series matches S-NPP where both have data (mean absolute % error)."""

    reference: str
    mean_abs_error_pct: float


class Coverage(BaseModel):
    """[AI] The area the data covers, as a (west, south, east, north) box."""

    name: str
    bbox: tuple[float, float, float, float]


class SourcesData(BaseModel):
    """[AI] Response payload of `GET /api/v1/sources`."""

    satellites: list[SatelliteInfo]
    products: list[ProductInfo]
    calibration: list[CalibrationInfo]
    validation: Validation | None
    coverage: Coverage
