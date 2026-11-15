"""[AI] Request and response models of the burning-activity calendar (`POST /api/v1/calendar`).

[AI] The calendar's unit is the "harmonized fire cell-day": one 0.05° grid cell with at least one
detection on one solar day, scaled to the S-NPP satellite (see `src/utils/harmonize.py`).
"""

from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from src.models.aoi import PolygonAoi

YearMonth = Annotated[str, Field(pattern=r"^\d{4}-(0[1-9]|1[0-2])$")]  # [AI] "YYYY-MM"
# [AI] Where a value falls against the baseline quartiles (see `src.utils.stats.level`).
Level = Literal["below", "normal", "above", "no_baseline"]


class CalendarRequest(BaseModel):
    """[AI] Area, month range and baseline years to compare against.

    [AI] Defaults: from 2001-01 (the first month with data) to the current month, with the baseline
    running from 2001 to last year when `baseline_to` is not given.
    """

    model_config = ConfigDict(extra="forbid")

    aoi: PolygonAoi
    start: YearMonth = "2001-01"
    end: YearMonth | None = None
    baseline_from: int = Field(2001, ge=2001)
    baseline_to: int | None = None

    @model_validator(mode="after")
    def _start_before_end(self) -> "CalendarRequest":
        """[AI] Reject inverted ranges; "YYYY-MM" strings sort chronologically, so plain comparison works."""
        if self.end is not None and self.start > self.end:
            raise ValueError("start must not be after end")
        return self


class CalendarYear(BaseModel):
    """[AI] One row of the calendar: twelve monthly values and their quality flags."""

    year: int
    months: list[float | None]  # 12 values; None for months outside the range or in the future
    flags: list[list[str]]  # per month: estimated, filled, provisional


class MonthStat(BaseModel):
    """[AI] What is normal for one calendar month over the baseline years; `critical` marks peak-season months."""

    month: int
    median: float | None
    p25: float | None
    p75: float | None
    critical: bool


class CurrentMonth(BaseModel):
    """[AI] The month in progress, compared day-for-day with the same days in the baseline years."""

    year: int
    month: int
    day: int
    value_to_date: float
    median_to_date: float | None
    pct: float | None
    level: Level


class Kpis(BaseModel):
    """[AI] Headline numbers for the area (see `src.utils.stats.kpis`)."""

    peak_month: int | None
    peak_year: int | None
    last_full_year: int
    last_full_year_total: float
    last_full_year_vs_median_pct: float | None
    record: tuple[int, int]  # [AI] (first year, last year) of the record the statistics use


class CalendarData(BaseModel):
    """[AI] Response payload of the calendar endpoint."""

    metric: Literal["harmonized_fire_cell_days"] = "harmonized_fire_cell_days"
    years: list[CalendarYear]
    monthly: list[MonthStat]
    current: CurrentMonth
    kpis: Kpis
