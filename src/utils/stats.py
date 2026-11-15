"""[AI] Baseline statistics of the calendar: quartiles per month, the current month, and the headline KPIs.

[AI] "Normal" means between the 25th and 75th percentile of the baseline years. The quartiles use
the "inclusive" method, and fewer than 3 values count as no baseline at all.
"""

from datetime import date
from statistics import median, quantiles

from src.models.calendar import CalendarYear, CurrentMonth, Kpis, Level, MonthStat
from src.utils.harmonize import MonthValue

Quartiles = tuple[float, float, float]  # p25, median, p75


def quartiles(values: list[float]) -> Quartiles | None:
    """p25, median and p75; None when there are too few values to mean anything."""
    if len(values) < 3:
        return None
    p25, mid, p75 = quantiles(values, n=4, method="inclusive")
    return p25, mid, p75


def level(value: float, q: Quartiles | None) -> Level:
    """[AI] Classify a value against the baseline: below p25, above p75, otherwise normal (edges count as normal)."""
    if q is None:
        return "no_baseline"
    if value < q[0]:
        return "below"
    return "above" if value > q[2] else "normal"


def pct(value: float, base: float | None) -> float | None:
    """Percent over the base; None when the base is 0 or unknown (never infinity)."""
    if not base:
        return None
    return round((value - base) / base * 100, 1)


def monthly_stats(years: list[CalendarYear], baseline: range) -> list[MonthStat]:
    """Median and quartiles of each calendar month over the baseline years."""
    stats = []
    for month in range(1, 13):
        values = [y.months[month - 1] for y in years if y.year in baseline and y.months[month - 1] is not None]
        q = quartiles(values)
        stats.append(MonthStat(month=month, median=q[1] if q else None, p25=q[0] if q else None,
                               p75=q[2] if q else None, critical=False))
    # [AI] A month is "critical" when its median is above the 75th percentile of the 12 monthly medians
    # (and not 0), i.e. it is one of the area's peak fire months.
    medians = [s.median for s in stats if s.median is not None]
    threshold = quartiles(medians)
    if threshold:
        for s in stats:
            s.critical = s.median is not None and s.median > 0 and s.median > threshold[2]
    return stats


def current_month(values: dict[tuple[int, int], MonthValue], today: date, baseline: range) -> CurrentMonth:
    """Month-to-date against the same days of the baseline years (a missing month means no fire)."""
    now = values.get((today.year, today.month))
    value_to_date = now.value_to_date if now else 0.0
    history = [
        (values[(y, today.month)].value_to_date if (y, today.month) in values else 0.0) for y in baseline
    ]
    q = quartiles(history)
    base = q[1] if q else None
    return CurrentMonth(
        year=today.year, month=today.month, day=today.day, value_to_date=value_to_date,
        median_to_date=base, pct=pct(value_to_date, base), level=level(value_to_date, q),
    )


def _total(year: CalendarYear) -> float:
    """[AI] Sum of a year's months, skipping months outside the range (None)."""
    return sum(m for m in year.months if m is not None)


def kpis(years: list[CalendarYear], monthly: list[MonthStat], today: date, baseline: range) -> Kpis:
    """Headline numbers; the last full year is compared with the other baseline years."""
    last_full = today.year - 1
    totals = {y.year: _total(y) for y in years}
    # [AI] The year being judged is left out of its own baseline so it is not compared with itself.
    others = [totals[y] for y in baseline if y in totals and y != last_full]
    q = quartiles(others)
    last_total = totals.get(last_full, 0.0)
    peak_month = max(monthly, key=lambda s: s.median or 0.0)
    peak_year = max(totals, key=totals.get) if totals else None
    return Kpis(
        peak_month=peak_month.month if peak_month.median else None,
        peak_year=peak_year if peak_year is not None and totals[peak_year] > 0 else None,
        last_full_year=last_full,
        last_full_year_total=last_total,
        last_full_year_vs_median_pct=pct(last_total, q[1] if q else None),
        record=(years[0].year, years[-1].year),
    )
