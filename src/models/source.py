from datetime import date, datetime
from typing import Literal

from pydantic import BaseModel


class HealthData(BaseModel):
    api: Literal["ok"]
    database: Literal["ok"]
    data_status: Literal["fresh", "stale", "empty"]
    last_ingest_at: datetime | None
    hours_since_last_ingest: float | None


class SatelliteInfo(BaseModel):
    code: str
    instrument: str
    pixel_m: int
    first_date: date | None
    last_date: date | None
    role: Literal["reference", "fallback", "scaled"]


class ProductInfo(BaseModel):
    code: str
    kind: Literal["SP", "NRT"]
    min_date: date | None
    max_date: date | None


class CalibrationInfo(BaseModel):
    reference: str
    month: int
    k: float
    overlap_cell_days: int
    estimated: bool


class Validation(BaseModel):
    reference: str
    mean_abs_error_pct: float


class Coverage(BaseModel):
    name: str
    bbox: tuple[float, float, float, float]


class SourcesData(BaseModel):
    satellites: list[SatelliteInfo]
    products: list[ProductInfo]
    calibration: list[CalibrationInfo]
    validation: Validation | None
    coverage: Coverage
