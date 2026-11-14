from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from src.models.aoi import PolygonAoi

YearMonth = Annotated[str, Field(pattern=r"^\d{4}-(0[1-9]|1[0-2])$")]
Level = Literal["below", "normal", "above", "no_baseline"]


class CalendarRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    aoi: PolygonAoi
    start: YearMonth = "2001-01"
    end: YearMonth | None = None
    baseline_from: int = Field(2001, ge=2001)
    baseline_to: int | None = None

    @model_validator(mode="after")
    def _start_before_end(self) -> "CalendarRequest":
        if self.end is not None and self.start > self.end:
            raise ValueError("start must not be after end")
        return self


class CalendarYear(BaseModel):
    year: int
    months: list[float | None]  # 12 values; None for months outside the range or in the future
    flags: list[list[str]]  # per month: estimated, filled, provisional


class MonthStat(BaseModel):
    month: int
    median: float | None
    p25: float | None
    p75: float | None
    critical: bool


class CurrentMonth(BaseModel):
    year: int
    month: int
    day: int
    value_to_date: float
    median_to_date: float | None
    pct: float | None
    level: Level


class Kpis(BaseModel):
    peak_month: int | None
    peak_year: int | None
    last_full_year: int
    last_full_year_total: float
    last_full_year_vs_median_pct: float | None
    record: tuple[int, int]


class CalendarData(BaseModel):
    metric: Literal["harmonized_fire_cell_days"] = "harmonized_fire_cell_days"
    years: list[CalendarYear]
    monthly: list[MonthStat]
    current: CurrentMonth
    kpis: Kpis
