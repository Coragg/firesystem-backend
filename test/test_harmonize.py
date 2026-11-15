from datetime import date

from src.utils.harmonize import CountRow, build_years, harmonize


def row(year: int, month: int, reference: str, cell_days: int, mtd: int = 0, provisional: bool = False) -> CountRow:
    return CountRow(
        year=year, month=month, reference=reference, cell_days=cell_days, cell_days_mtd=mtd, provisional=provisional
    )


def test_harmonize_applies_k_to_modis():
    values, warnings = harmonize([row(2005, 2, "modis", 100, 40)], {("modis", 2): 2.24})

    month = values[(2005, 2)]
    assert month.value == 224.0
    assert month.value_to_date == 89.6
    assert month.flags == ["estimated"]
    assert warnings == []


def test_harmonize_snpp_needs_no_k():
    values, warnings = harmonize([row(2020, 2, "snpp", 100)], {})

    assert values[(2020, 2)].value == 100.0
    assert values[(2020, 2)].flags == []
    assert warnings == []


def test_harmonize_sums_references_in_the_same_month():
    rows = [row(2012, 1, "modis", 10), row(2012, 1, "snpp", 20)]

    values, _ = harmonize(rows, {("modis", 1): 2.0})

    assert values[(2012, 1)].value == 40.0


def test_harmonize_missing_month_uses_reference_mean():
    values, warnings = harmonize([row(2005, 2, "modis", 10)], {("modis", 1): 2.0, ("modis", 3): 3.0})

    assert values[(2005, 2)].value == 25.0
    assert warnings == []


def test_harmonize_missing_reference_warns():
    values, warnings = harmonize([row(2025, 6, "noaa21", 10)], {})

    assert values[(2025, 6)].value == 10.0
    assert warnings == ["CALIBRATION_MISSING"]


def test_harmonize_flags_provisional_and_filled():
    values, _ = harmonize([row(2026, 11, "noaa20", 5, provisional=True)], {("noaa20", 11): 1.03})

    assert values[(2026, 11)].flags == ["filled", "provisional"]


def test_build_years_future_months_are_null():
    values, _ = harmonize([row(2026, 11, "snpp", 9)], {})

    years = build_years(values, (2026, 1), (2026, 12), date(2026, 11, 14))

    months = years[0].months
    assert months[10] == 9.0
    assert months[11] is None
    assert months[0] == 0.0


def test_build_years_before_start_is_null():
    years = build_years({}, (2001, 3), (2001, 12), date(2026, 11, 14))

    assert years[0].months[:2] == [None, None]
    assert years[0].months[2] == 0.0
    assert years[0].flags[0] == []
