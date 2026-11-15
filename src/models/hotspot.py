"""[AI] Models of the hotspot endpoints: the XGBoost forecast (`/api/v1/hotspot/forecast`) and the map /
export of stored detections (`/api/v1/hotspots`, `/api/v1/hotspots/export`).

[AI] Merge resolution: feature/model_training and feature/dev-1 each added different models to this
file; both sets are kept unchanged, under one shared import block.
"""

from datetime import date, datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from src.models.aoi import PolygonAoi


# [AI] Forecast models (from feature/model_training)
class HotspotModelInfo(BaseModel):
    region: str = Field(description="Region the model was trained on.")
    trained_months: list[str] = Field(description="First and last month (YYYY-MM) of the training rows.")
    features: list[str] = Field(description="Model input features.")


class HotspotForecast(BaseModel):
    month: str = Field(description="Forecast month (YYYY-MM) containing the requested date.")
    lat: float = Field(description="Requested latitude.")
    lon: float = Field(description="Requested longitude.")
    pixel_lat: float = Field(description="Centre latitude of the 500 m pixel used.")
    pixel_lon: float = Field(description="Centre longitude of the 500 m pixel used.")
    hotspot_probability: float = Field(description="Estimated probability of at least one FIRMS vegetation-fire hotspot in the pixel during the month.")
    hotspot_score: float = Field(description="Raw model output on the training sample scale (1 hotspot : 11 no-hotspot rows); use for ranking only.")
    features: dict[str, float | int | None] = Field(description="Feature values the model received.")
    model: HotspotModelInfo


# [AI] Stored-detection models (from feature/dev-1)
CONFIDENCE_NAMES = {0: "low", 1: "nominal", 2: "high"}


class _DateRange(BaseModel):
    model_config = ConfigDict(extra="forbid")

    aoi: PolygonAoi
    start_date: date
    end_date: date

    @model_validator(mode="after")
    def _start_before_end(self) -> "_DateRange":
        if self.start_date > self.end_date:
            raise ValueError("start_date must not be after end_date")
        return self


class HotspotsRequest(_DateRange):
    after_id: int | None = Field(None, ge=0)
    limit: int = Field(500, ge=1, le=1000)


class ExportRequest(_DateRange):
    separator: Literal[",", ";"] = ","


class Hotspot(BaseModel):
    id: int
    lat: float
    lon: float
    acq_time: datetime
    solar_date: date
    satellite: str
    instrument: str
    confidence: Literal["low", "nominal", "high"]
    frp: float | None
    provisional: bool
    excluded: bool


class HotspotPage(BaseModel):
    items: list[Hotspot]
    next_after_id: int | None


def hotspot_from_row(row: dict) -> Hotspot:
    """Build a Hotspot from one row of the hotspots_page SQL function."""
    return Hotspot(
        id=row["id"], lat=row["lat"], lon=row["lon"], acq_time=row["acq_time"], solar_date=row["solar_date"],
        satellite=row["satellite"], instrument=row["instrument"],
        confidence=CONFIDENCE_NAMES[row["confidence_level"]], frp=row["frp"],
        provisional=row["source_kind"] == "NRT", excluded=row["excluded"],
    )
