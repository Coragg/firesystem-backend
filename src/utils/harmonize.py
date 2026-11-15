"""[AI] Harmonize MODIS and VIIRS fire counts onto one scale: VIIRS S-NPP cell-days.

[AI] How it fits together: for each solar day the database picks one "reference" satellite
(`daily_reference`; S-NPP first, then NOAA-20, NOAA-21, MODIS). Each reference's cell-days are
multiplied by a per-month factor k (`calibration_factor`) that converts them to S-NPP-equivalent
cell-days, then summed per month. Flags record which months rely on substitutes.
"""

from datetime import date
from statistics import mean
from typing import NamedTuple, TypedDict

from src.models.calendar import CalendarYear

# [AI] Months that use any of these references are flagged "estimated" (MODIS scaled to the VIIRS scale).
MODIS_REFERENCES = {"modis", "aqua", "terra"}
FILLED_REFERENCES = {"noaa20", "noaa21"}  # stand-ins for S-NPP on days it saw nothing


class CountRow(TypedDict):
    """One row of the calendar_counts SQL function."""

    year: int
    month: int
    reference: str
    cell_days: int
    cell_days_mtd: int
    provisional: bool


class MonthValue(NamedTuple):
    """[AI] Harmonized value of one month: the full month, the month up to today's day number, and its flags."""

    value: float
    value_to_date: float
    flags: list[str]


def _factor(reference: str, month: int, k: dict[tuple[str, int], float], warnings: list[str]) -> float:
    """Scale factor to S-NPP: exact month, else the reference's mean, else 1.0 with a warning."""
    if reference == "snpp":
        return 1.0
    if (reference, month) in k:
        return k[(reference, month)]
    same_reference = [value for (ref, _), value in k.items() if ref == reference]
    if same_reference:
        return mean(same_reference)
    # [AI] Decision visible in the code: unscaled data is returned with a warning instead of an error,
    # so the calendar still renders before calibration has been computed.
    if "CALIBRATION_MISSING" not in warnings:
        warnings.append("CALIBRATION_MISSING")
    return 1.0


def harmonize(
    rows: list[CountRow], k: dict[tuple[str, int], float]
) -> tuple[dict[tuple[int, int], MonthValue], list[str]]:
    """Scale each reference's cell-days to S-NPP and add them up per (year, month)."""
    warnings: list[str] = []
    totals: dict[tuple[int, int], list[float]] = {}
    flags: dict[tuple[int, int], set[str]] = {}
    for r in rows:
        key = (r["year"], r["month"])
        factor = _factor(r["reference"], r["month"], k, warnings)
        total = totals.setdefault(key, [0.0, 0.0])
        total[0] += r["cell_days"] * factor
        total[1] += r["cell_days_mtd"] * factor
        month_flags = flags.setdefault(key, set())
        if r["reference"] in MODIS_REFERENCES:
            month_flags.add("estimated")
        if r["reference"] in FILLED_REFERENCES:
            month_flags.add("filled")
        if r["provisional"]:
            month_flags.add("provisional")
    values = {
        key: MonthValue(round(v, 1), round(mtd, 1), sorted(flags[key])) for key, (v, mtd) in totals.items()
    }
    return values, warnings


def validation_error(checks: list[dict], k: dict[tuple[str, int], float]) -> float | None:
    """Mean percent gap between S-NPP and the scaled group, over months with overlap; None without data."""
    errors = [
        abs(c["n_grp"] * k[(c["reference"], c["month"])] - c["n_ref"]) / c["n_ref"] * 100
        for c in checks
        if c["n_ref"] > 0 and (c["reference"], c["month"]) in k
    ]
    return round(mean(errors), 1) if errors else None


def build_years(
    values: dict[tuple[int, int], MonthValue], start: tuple[int, int], end: tuple[int, int], today: date
) -> list[CalendarYear]:
    """Lay values out as years x 12 months; months outside [start, end] or after today are None."""
    last = min(end, (today.year, today.month))
    years = []
    for year in range(start[0], end[0] + 1):
        months: list[float | None] = []
        flags: list[list[str]] = []
        for month in range(1, 13):
            if start <= (year, month) <= last:
                found = values.get((year, month))
                months.append(found.value if found else 0.0)
                flags.append(found.flags if found else [])
            else:
                months.append(None)
                flags.append([])
        years.append(CalendarYear(year=year, months=months, flags=flags))
    return years
