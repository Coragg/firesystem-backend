import pytest
from fakes import FakeClient
from fastapi.testclient import TestClient

from src.main import app
from src.utils.harmonize import validation_error

client = TestClient(app, raise_server_exceptions=False)

SATELLITES = [
    {"code": "snpp", "instrument": "VIIRS", "pixel_m": 375, "priority": 1},
    {"code": "noaa20", "instrument": "VIIRS", "pixel_m": 375, "priority": 2},
    {"code": "terra", "instrument": "MODIS", "pixel_m": 1000, "priority": 5},
]
COVERAGE = [{"satellite": "snpp", "first_date": "2012-01-20", "last_date": "2026-11-02"}]
PRODUCTS = [{"code": "MODIS_SP", "kind": "SP", "min_date": "2000-11-01", "max_date": "2026-08-31"}]
FACTORS = [{"reference": "modis", "month": 3, "k": 2.0, "overlap_cell_days": 5120, "is_estimated": False}]
CHECKS = [{"reference": "modis", "year": 2020, "month": 3, "n_ref": 100, "n_grp": 50}]


def loaded_client() -> FakeClient:
    return FakeClient(
        tables={"satellite": SATELLITES, "firms_source": PRODUCTS, "calibration_factor": FACTORS},
        rpcs={"satellite_coverage": COVERAGE, "calibration_check": CHECKS},
    )


def test_sources_lists_satellites_with_roles_and_coverage(use_fake_db):
    use_fake_db(loaded_client())

    data = client.get("/api/v1/sources").json()["data"]

    roles = {s["code"]: s["role"] for s in data["satellites"]}
    assert roles == {"snpp": "reference", "noaa20": "fallback", "terra": "scaled"}
    assert data["satellites"][0]["last_date"] == "2026-11-02"
    assert data["satellites"][2]["first_date"] is None
    assert data["products"][0]["code"] == "MODIS_SP"
    assert data["calibration"][0] == {
        "reference": "modis", "month": 3, "k": 2.0, "overlap_cell_days": 5120, "estimated": False,
    }
    assert data["coverage"]["name"] == "Chile"


def test_sources_reports_validation_error(use_fake_db):
    use_fake_db(loaded_client())

    data = client.get("/api/v1/sources").json()["data"]

    assert data["validation"] == {"reference": "modis", "mean_abs_error_pct": 0.0}


def test_sources_without_calibration_warns(use_fake_db):
    use_fake_db(FakeClient(tables={"satellite": SATELLITES}))

    result = client.get("/api/v1/sources").json()

    assert result["meta"]["warnings"] == ["CALIBRATION_MISSING"]
    assert result["data"]["validation"] is None


def test_validation_error_is_the_mean_of_monthly_errors():
    checks = [
        {"reference": "modis", "month": 3, "n_ref": 100, "n_grp": 50},  # 50 * 2.0 = 100 -> 0 %
        {"reference": "modis", "month": 3, "n_ref": 100, "n_grp": 40},  # 40 * 2.0 = 80 -> 20 %
    ]

    assert validation_error(checks, {("modis", 3): 2.0}) == 10.0


@pytest.mark.parametrize("checks", [[], [{"reference": "modis", "month": 3, "n_ref": 0, "n_grp": 5}]])
def test_validation_error_without_usable_rows_is_none(checks):
    assert validation_error(checks, {("modis", 3): 2.0}) is None
