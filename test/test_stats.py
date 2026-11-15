from datetime import date

import pytest

from src.models.calendar import CalendarYear
from src.utils.harmonize import MonthValue
from src.utils.stats import current_month, kpis, level, monthly_stats, pct, quartiles


def year(number: int, months: list[float | None]) -> CalendarYear:
    return CalendarYear(year=number, months=months, flags=[[] for _ in months])


def flat(value: float) -> list[float | None]:
    return [value] * 12


def test_quartiles_need_three_values():
    assert quartiles([1.0, 2.0]) is None


def test_quartiles_inclusive():
    assert quartiles([1.0, 2.0, 3.0, 4.0, 5.0]) == (2.0, 3.0, 4.0)


def test_level_bands():
    q = (10.0, 20.0, 30.0)

    assert level(5, q) == "below"
    assert level(20, q) == "normal"
    assert level(31, q) == "above"
    assert level(5, None) == "no_baseline"


def test_pct_over_median():
    assert pct(150, 100) == 50.0


@pytest.mark.parametrize("median", [0, None])
def test_pct_without_base_is_none(median):
    assert pct(10, median) is None


def test_monthly_stats_ignore_years_outside_baseline():
    years = [year(2001, flat(10)), year(2002, flat(20)), year(2003, flat(30)), year(2026, flat(999))]

    stats = monthly_stats(years, range(2001, 2026))

    assert stats[0].median == 20.0
    assert stats[0].p25 == 15.0
    assert stats[0].p75 == 25.0


def test_monthly_stats_flag_critical_months():
    january_peak = [90.0] + [1.0] * 11
    years = [year(2001, january_peak), year(2002, january_peak), year(2003, january_peak)]

    stats = monthly_stats(years, range(2001, 2004))

    assert [s.month for s in stats if s.critical] == [1]


def test_monthly_stats_zero_median_is_not_critical():
    years = [year(2001, flat(0.0)), year(2002, flat(0.0)), year(2003, flat(0.0))]

    assert not any(s.critical for s in monthly_stats(years, range(2001, 2004)))


def test_current_month_compares_month_to_date_with_baseline():
    values = {
        (2023, 11): MonthValue(100, 4, []),
        (2024, 11): MonthValue(100, 6, []),
        (2025, 11): MonthValue(100, 8, []),
        (2026, 11): MonthValue(50, 9, []),
    }

    current = current_month(values, date(2026, 11, 14), range(2023, 2026))

    assert (current.year, current.month, current.day) == (2026, 11, 14)
    assert current.value_to_date == 9.0
    assert current.median_to_date == 6.0
    assert current.pct == 50.0
    assert current.level == "above"


def test_current_month_baseline_years_without_fire_count_as_zero():
    values = {(2026, 11): MonthValue(5, 5, [])}

    current = current_month(values, date(2026, 11, 14), range(2023, 2026))

    assert current.median_to_date == 0.0
    assert current.pct is None


def test_kpis_peak_and_last_full_year_vs_median():
    january_peak = [90.0] + [1.0] * 11
    years = [
        year(2022, january_peak),
        year(2023, january_peak),
        year(2024, january_peak),
        year(2025, flat(20.0)),
    ]
    today = date(2026, 11, 14)

    result = kpis(years, monthly_stats(years, range(2022, 2026)), today, range(2022, 2026))

    assert result.peak_month == 1
    assert result.peak_year == 2025
    assert result.last_full_year == 2025
    assert result.last_full_year_total == 240.0
    assert result.last_full_year_vs_median_pct == pytest.approx(240.0 / 101.0 * 100 - 100, abs=0.1)
    assert result.record == (2022, 2025)
