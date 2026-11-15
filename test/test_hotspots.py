<<<<<<< HEAD
from datetime import date

import numpy as np
import pandas as pd
import pytest
import xarray as xr
from shapely.geometry import box

from src.utils import geotiff_layers as gl
from src.utils import hotspots as hs

LAT = np.array([-35.0, -35.01, -35.02])
LON = np.array([-71.0, -70.99, -70.98])
ARCHIVE_HEADER = "latitude,longitude,acq_date,confidence,frp,daynight,type\n"


class FakeResponse:
    def __init__(self, status_code=200, chunks=(b"",)):
        self.status_code, self._chunks = status_code, chunks

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(self.status_code)

    def iter_content(self, chunk_size):
        yield from self._chunks

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return None


def test_download_country_year_saves_skips_and_handles_missing(monkeypatch, tmp_path):
    calls = []

    def fake_get(url, stream=False, timeout=None):
        calls.append(url)
        return FakeResponse(404) if "2026" in url else FakeResponse(chunks=[b"a,b\n", b"1,2\n"])

    monkeypatch.setattr(hs.requests, "get", fake_get)
    path = hs.download_country_year("VIIRS", 2020, tmp_path)
    assert path.name == "viirs-snpp_2020_Chile.csv" and path.read_text() == "a,b\n1,2\n"
    assert calls[0] == "https://firms.modaps.eosdis.nasa.gov/data/country/viirs-snpp/2020/viirs-snpp_2020_Chile.csv"
    assert hs.download_country_year("VIIRS", 2020, tmp_path) == path and len(calls) == 1
    assert hs.download_country_year("MODIS", 2026, tmp_path) is None

    paths = hs.download_hotspots(tmp_path, 2011, 2012, sensors=("VIIRS",))  # VIIRS starts in 2012
    assert [p.name for p in paths] == ["viirs-snpp_2012_Chile.csv"]


def test_api_chunks_cover_range_in_five_day_requests():
    chunks = hs.api_chunks(date(2026, 1, 1), date(2026, 1, 12))
    assert chunks == [(date(2026, 1, 1), 5), (date(2026, 1, 6), 5), (date(2026, 1, 11), 2)]
    assert hs.api_chunks(date(2026, 1, 2), date(2026, 1, 1)) == []


def test_download_api_hotspots_uses_sp_then_nrt_per_sensor(monkeypatch, tmp_path):
    from src.utils import firms

    requests_made = []

    def fake_fetch(source, area, days, day):
        requests_made.append((source, day, days))
        return [{"latitude": -35.0, "longitude": -71.0, "acq_date": day, "confidence": "h"}]

    monkeypatch.setattr(firms, "fetch_hotspots", fake_fetch)
    availability = {
        "MODIS_SP": (date(2000, 1, 1), date(2026, 1, 3)), "MODIS_NRT": (date(2026, 1, 4), date(2026, 1, 20)),
        "VIIRS_SNPP_SP": (date(2012, 1, 1), date(2026, 1, 1)), "VIIRS_SNPP_NRT": (date(2026, 1, 2), date(2026, 1, 20)),
    }
    paths = hs.download_api_hotspots(tmp_path, (-72, -36, -70, -34), date(2026, 1, 1), date(2026, 1, 6), availability, pause_seconds=0)
    assert requests_made == [
        ("MODIS_SP", "2026-01-01", 3), ("MODIS_NRT", "2026-01-04", 3),
        ("VIIRS_SNPP_SP", "2026-01-01", 1), ("VIIRS_SNPP_NRT", "2026-01-02", 5),
    ]
    assert len(paths) == 4
    hs.download_api_hotspots(tmp_path, (-72, -36, -70, -34), date(2026, 1, 1), date(2026, 1, 6), availability, pause_seconds=0)
    assert len(requests_made) == 4  # cached chunks are not requested again


def test_sensor_of_archive_and_api_names():
    assert hs.sensor_of("modis_2020_Chile.csv") == "MODIS"
    assert hs.sensor_of("viirs-snpp_2020_Chile.csv") == "VIIRS"
    assert hs.sensor_of("api_VIIRS_SNPP_NRT_20260101_5.csv") == "VIIRS"
    assert hs.sensor_of("api_MODIS_SP_20260101_5.csv") == "MODIS"
    with pytest.raises(ValueError):
        hs.sensor_of("other_2020.csv")


def test_load_hotspots_filters_type_confidence_and_region(tmp_path):
    (tmp_path / "modis_2020_Chile.csv").write_text(ARCHIVE_HEADER + "\n".join([
        "-35.0,-71.0,2020-01-05,80,10,D,0",    # kept
        "-35.0,-71.0,2020-01-06,10,10,D,0",    # low confidence
        "-35.0,-71.0,2020-01-07,90,10,D,2",    # static source
        "-30.0,-71.0,2020-01-08,90,10,D,0",    # outside region
    ]) + "\n")
    (tmp_path / "viirs-snpp_2020_Chile.csv").write_text(ARCHIVE_HEADER + "-35.01,-70.99,2020-02-01,n,3,N,0\n-35.01,-70.99,2020-02-02,l,3,N,0\n")
    (tmp_path / "api_VIIRS_SNPP_NRT_20260101_5.csv").write_text("latitude,longitude,acq_date,confidence,frp,daynight\n-35.02,-70.98,2026-01-02,h,5,D\n")
    (tmp_path / "api_MODIS_NRT_20260101_5.csv").write_text("")  # chunk without detections

    spots = hs.load_hotspots(sorted(tmp_path.glob("*.csv")), region=box(-72, -36, -70, -34))
    assert spots[["sensor", "api"]].values.tolist() == [["MODIS", False], ["VIIRS", False], ["VIIRS", True]]
    assert spots["date"].dt.day.tolist() == [5, 1, 2]
    assert hs.load_hotspots([]).empty


def test_static_source_mask_marks_non_vegetation_cells(tmp_path):
    (tmp_path / "modis_2020_Chile.csv").write_text(ARCHIVE_HEADER + "-35.0,-71.0,2020-01-01,90,1,D,2\n-35.02,-70.98,2020-01-01,90,1,D,0\n")
    (tmp_path / "api_MODIS_NRT_20260101_5.csv").write_text("latitude,longitude\n-35.02,-70.98\n")
    paths = sorted(tmp_path.glob("*.csv"))
    assert hs.static_source_mask(paths, LAT, LON, radius=0).sum() == 1
    grown = hs.static_source_mask(paths, LAT, LON, radius=1)
    assert grown[:2, :2].all() and not grown[2, 2] and not grown[0, 2]


def test_monthly_hotspot_grid_counts_and_drops_api_rows_in_static_cells():
    spots = pd.DataFrame({
        "date": pd.to_datetime(["2020-01-03", "2020-01-20", "2020-02-01", "2020-02-01", "2020-03-01"]),
        "lat": [-35.0, -35.0, -35.01, -35.0, -50.0], "lon": [-71.0, -71.0, -70.99, -71.0, -71.0],
        "api": [False, False, False, True, False],
    })
    exclude = np.zeros((3, 3), bool)
    exclude[0, 0] = True
    grid = hs.monthly_hotspot_grid(spots, LAT, LON, pd.date_range("2020-01-01", periods=3, freq="MS"), api_exclude=exclude)
    assert grid.values[0, 0, 0] == 2
    assert grid.values[1, 1, 1] == 1 and grid.values[1, 0, 0] == 0  # API row in an excluded cell dropped
    assert grid.values.sum() == 3                                   # the far point is off the grid


def hotspot_grid(n_months: int = 6, seed: int = 0) -> xr.DataArray:
    counts = np.zeros((n_months, 3, 3), "int32")
    counts[2, 0, 0] = 3
    counts[4, 1, 1] = 1
    return xr.DataArray(counts, dims=("month", "lat", "lon"),
                        coords={"month": pd.date_range("2020-01-01", periods=n_months, freq="MS"), "lat": LAT, "lon": LON})


def test_sample_panel_keeps_positives_and_samples_negatives_evenly():
    grid = hotspot_grid()
    valid = np.ones((3, 3), bool)
    valid[2, 2] = False
    panel, rate = hs.sample_panel(grid, valid, pd.Timestamp("2020-02-01"), negatives_per_positive=2, seed=1)

    assert rate == pytest.approx(2 * 2 / (5 * 8 - 2))
    positives = panel[panel["hotspot"] == 1]
    assert positives[["month_idx", "lat_idx", "lon_idx", "hotspot_count"]].values.tolist() == [[2, 0, 0, 3], [4, 1, 1, 1]]
    assert panel["date"].min() == pd.Timestamp("2020-02-01")
    assert not ((panel["lat_idx"] == 2) & (panel["lon_idx"] == 2)).any()
    assert not panel.duplicated(["month_idx", "lat_idx", "lon_idx"]).any()
    assert panel["lat"].iloc[0] == LAT[panel["lat_idx"].iloc[0]]

    everything, rate_all = hs.sample_panel(grid, valid, pd.Timestamp("2020-01-01"), negatives_per_positive=1000)
    assert rate_all == 1 and len(everything) == 6 * 8


def test_corrected_probability_undoes_negative_sampling():
    # sampled odds 1:1 with 1 in 10 negatives kept -> real odds 1:10
    assert hs.corrected_probability(np.array([0.5]), 0.1)[0] == pytest.approx(1 / 11)
    assert hs.corrected_probability(np.array([0.5]), 1.0)[0] == pytest.approx(0.5)


def test_monthly_mean_averages_composites_per_month():
    times = pd.to_datetime(["2020-01-01", "2020-01-17", "2020-02-02", "2020-02-18"])
    values = np.stack([np.full((3, 3), v) for v in (1.0, 3.0, np.nan, 5.0)])
    ds = xr.Dataset({"lai": (("time", "lat", "lon"), values)}, coords={"time": times, "lat": LAT, "lon": LON})
    monthly = gl.monthly_mean(ds, "lai", pd.date_range("2020-01-01", periods=3, freq="MS"))
    assert monthly.values[:, 0, 0].tolist()[:2] == [2.0, 5.0]
    assert np.isnan(monthly.values[2]).all()


def test_lag_lookup_lai_and_hotspot_lag_features():
    grid = hotspot_grid()
    monthly_lai = xr.DataArray(np.arange(6 * 9, dtype=float).reshape(6, 3, 3), dims=("month", "lat", "lon"),
                               coords={"month": grid.indexes["month"], "lat": LAT, "lon": LON})
    panel = pd.DataFrame({"month_idx": [5, 1], "lat_idx": [0, 1], "lon_idx": [0, 1]})

    assert hs.lag_lookup(panel, monthly_lai, 3).tolist()[0] == 18.0       # month 2, pixel (0, 0)
    assert np.isnan(hs.lag_lookup(panel, monthly_lai, 3)[1])              # before the grid starts

    lai = hs.lai_features(panel, monthly_lai, lags=[2, 3])
    assert lai.loc[0, ["lai_lag_2", "lai_lag_3"]].tolist() == [27.0, 18.0]
    assert lai.loc[0, "lai_mean_2_3"] == 22.5 and lai.loc[0, "lai_valid_months_2_3"] == 2
    assert lai.loc[1, "lai_valid_months_2_3"] == 0 and np.isnan(lai.loc[1, "lai_mean_2_3"])

    lags = hs.hotspot_lag_features(panel, grid, [3, 1])
    assert lags.loc[0, "hotspot_lag_3"] == 1 and lags.loc[0, "hotspot_lag_1"] == 0
    assert np.isnan(lags.loc[1, "hotspot_lag_3"])


def test_month_offset_and_future_panel():
    grid = hotspot_grid()
    assert hs.month_offset(pd.date_range("2020-03-01", periods=2, freq="MS"), grid) == 2
    valid = np.ones((3, 3), bool)
    future = hs.future_panel(valid, grid.indexes["month"], LAT, LON, months_ahead=2, stride=2)
    assert future["date"].drop_duplicates().tolist() == list(pd.to_datetime(["2020-07-01", "2020-08-01"]))
    assert future["month_idx"].drop_duplicates().tolist() == [6, 7]
    assert len(future) == 2 * 4  # pixels (0,0), (0,2), (2,0), (2,2)


def test_land_cover_features_use_class_from_two_years_before():
    lc = xr.DataArray(
        np.stack([np.full((3, 3), 8), np.full((3, 3), 10), np.full((3, 3), 255)]).astype(float),
        dims=("time", "lat", "lon"),
        coords={"time": pd.to_datetime(["2018-01-01", "2019-01-01", "2020-01-01"]), "lat": LAT, "lon": LON},
    )
    lc[1, 0, 0] = 12
    panel = pd.DataFrame({
        "date": pd.to_datetime(["2021-03-01", "2021-03-01", "2020-01-01", "2023-01-01", "2019-06-01", "2021-03-01"]),
        "lat": [-35.0, -35.01, -35.0, -35.0, -35.0, -40.0],
        "lon": [-71.0, -70.99, -71.0, -71.0, -71.0, -71.0],
    })
    out = hs.land_cover_features(panel, lc, lag_years=2)

    assert out["land_cover"].dtype == "category" and list(out["land_cover"].cat.categories) == list(range(1, 18))
    assert out["land_cover"].tolist()[:3] == [12, 10, 8]          # 2019, 2019, 2018
    assert pd.isna(out["land_cover"].iloc[3])                     # 2021 -> latest is 2020, which is fill (255)
    assert pd.isna(out["land_cover"].iloc[4]) and np.isnan(out["land_cover_year"].iloc[4])  # no year <= 2017
    assert pd.isna(out["land_cover"].iloc[5])                     # off the grid
    assert out["land_cover_year"].tolist()[:4] == [2019, 2019, 2018, 2020]


def test_download_api_hotspots_refetches_chunks_near_the_end_of_availability(monkeypatch, tmp_path):
    from src.utils import firms

    calls = []
    monkeypatch.setattr(firms, "fetch_hotspots", lambda source, area, days, day: calls.append(day) or [])
    availability = {"VIIRS_SNPP_NRT": (date(2026, 1, 1), date(2026, 1, 10))}
    for _ in range(2):
        hs.download_api_hotspots(tmp_path, (-72, -36, -70, -34), date(2026, 1, 1), date(2026, 1, 10),
                                 availability, sensors=("VIIRS",), pause_seconds=0)
    # 01-01..01-05 is cached; 01-06..01-10 reaches the last 2 days, so it is downloaded on both runs
    assert calls == ["2026-01-01", "2026-01-06", "2026-01-06"]


def test_processing_and_coverage_from_file_names():
    assert hs.processing_of("viirs-snpp_2020_Chile.csv") == "SP"
    assert hs.processing_of("api_MODIS_SP_20260101_5.csv") == "SP"
    assert hs.processing_of("api_MODIS_NRT_20260901_5.csv") == "NRT"
    assert hs.covered_until("modis_2025_Chile.csv") == date(2025, 12, 31)
    assert hs.covered_until("api_VIIRS_SNPP_SP_20260730_2.csv") == date(2026, 7, 31)


def test_load_hotspots_drops_nrt_days_covered_by_sp_and_duplicates(tmp_path):
    header = "latitude,longitude,acq_date,acq_time,confidence,frp,daynight\n"
    # SP covers up to 2026-01-06; an overlapping SP chunk repeats the 01-05 detection
    (tmp_path / "api_VIIRS_SNPP_SP_20260101_5.csv").write_text(header + "-35.0,-71.0,2026-01-05,0530,n,1,N\n")
    (tmp_path / "api_VIIRS_SNPP_SP_20260105_2.csv").write_text(header + "-35.0,-71.0,2026-01-05,0530,n,1,N\n-35.0,-71.0,2026-01-06,0530,h,1,N\n")
    # NRT downloaded earlier for 01-05..01-08: 01-05 and 01-06 are now covered by SP
    (tmp_path / "api_VIIRS_SNPP_NRT_20260105_4.csv").write_text(header + "\n".join([
        "-35.0001,-71.0,2026-01-05,0531,n,1,N", "-35.0,-71.0,2026-01-06,0530,h,1,N", "-35.0,-71.0,2026-01-08,0530,h,1,N"]) + "\n")
    spots = hs.load_hotspots(sorted(tmp_path.glob("*.csv")))
    assert spots["date"].dt.day.tolist() == [5, 6, 8]
    assert spots["processing"].tolist() == ["SP", "SP", "NRT"]


def test_grid_lag_features_uses_prefix():
    grid = hotspot_grid()
    monthly = xr.DataArray(np.arange(6 * 9, dtype=float).reshape(6, 3, 3), dims=("month", "lat", "lon"),
                           coords={"month": grid.indexes["month"], "lat": LAT, "lon": LON})
    panel = pd.DataFrame({"month_idx": [5], "lat_idx": [0], "lon_idx": [0]})
    out = hs.grid_lag_features(panel, monthly, [2, 3], "ndvi")
    assert list(out.columns) == ["ndvi_lag_2", "ndvi_lag_3", "ndvi_mean_2_3", "ndvi_min_2_3", "ndvi_max_2_3",
                                 "ndvi_std_2_3", "ndvi_valid_months_2_3"]
    assert out.loc[0, "ndvi_max_2_3"] == 27.0
=======
from datetime import date, timedelta

import pytest
from fakes import FakeClient
from fastapi.testclient import TestClient

from src import main
from src.main import app

client = TestClient(app, raise_server_exceptions=False)

BIOBIO = [[-73.2, -37.9], [-71.5, -37.9], [-71.5, -36.5], [-73.2, -36.5]]
HEADER = "id;acq_time_utc;solar_date;lat;lon;satellite;instrument;confidence;frp;source;excluded"


def row(id: int, **changes) -> dict:
    return {
        "id": id, "acq_time": "2026-11-13T18:40:00+00:00", "solar_date": "2026-11-13", "lat": -37.41,
        "lon": -72.35, "satellite": "noaa20", "instrument": "VIIRS", "confidence_level": 1, "frp": 4.1,
        "source_kind": "NRT", "excluded": False, **changes,
    }


def body(start: str = "2026-11-01", end: str = "2026-11-14", **extra) -> dict:
    return {"aoi": {"type": "Polygon", "coordinates": [BIOBIO]}, "start_date": start, "end_date": end, **extra}


def test_hotspots_page_maps_rows_and_next_cursor(use_fake_db):
    use_fake_db(FakeClient(rpcs={"hotspots_page": [row(7), row(9, confidence_level=0, source_kind="SP", excluded=True)]}))

    data = client.post("/api/v1/hotspots", json=body(limit=2)).json()["data"]

    assert data["next_after_id"] == 9
    first, second = data["items"]
    assert (first["confidence"], first["provisional"], first["excluded"]) == ("nominal", True, False)
    assert (second["confidence"], second["provisional"], second["excluded"]) == ("low", False, True)
    assert first["satellite"] == "noaa20"


def test_hotspots_last_page_has_no_cursor(use_fake_db):
    use_fake_db(FakeClient(rpcs={"hotspots_page": [row(7)]}))

    assert client.post("/api/v1/hotspots", json=body(limit=5)).json()["data"]["next_after_id"] is None


def test_hotspots_passes_cursor_and_limit_to_rpc(use_fake_db):
    fake = FakeClient()
    use_fake_db(fake)

    client.post("/api/v1/hotspots", json=body(after_id=41, limit=10))

    assert fake.calls[0][1]["after_id"] == 41
    assert fake.calls[0][1]["page_size"] == 10


def test_hotspots_366_days_is_allowed(use_fake_db):
    use_fake_db(FakeClient())
    start = date(2025, 1, 1)

    response = client.post("/api/v1/hotspots", json=body(str(start), str(start + timedelta(days=365))))

    assert response.status_code == 200


def test_hotspots_367_days_is_range_too_long(use_fake_db):
    use_fake_db(FakeClient())
    start = date(2025, 1, 1)

    response = client.post("/api/v1/hotspots", json=body(str(start), str(start + timedelta(days=366))))

    assert response.status_code == 422
    assert "366 days" in response.json()["detail"]


@pytest.mark.parametrize("extra", [{"limit": 1001}, {"limit": 0}, {"after_id": -1}])
def test_hotspots_rejects_bad_paging(use_fake_db, extra):
    use_fake_db(FakeClient())

    response = client.post("/api/v1/hotspots", json=body(**extra))

    assert response.status_code == 422
    assert response.json()["detail"][0]["loc"][-1] in {"limit", "after_id"}


def test_hotspots_end_before_start_is_invalid(use_fake_db):
    use_fake_db(FakeClient())

    response = client.post("/api/v1/hotspots", json=body("2026-11-14", "2026-11-01"))

    assert response.status_code == 422
    assert "start_date must not be after end_date" in str(response.json()["detail"])


def test_export_semicolon_with_bom_and_download_name(use_fake_db):
    use_fake_db(FakeClient(rpcs={"hotspots_count": 2, "hotspots_page": [row(1), row(2)]}))

    response = client.post("/api/v1/hotspots/export", json=body(separator=";"))

    lines = response.text.splitlines()
    assert response.status_code == 200
    assert lines[0] == "﻿" + HEADER
    assert lines[1] == "1;2026-11-13T18:40:00+00:00;2026-11-13;-37.41;-72.35;noaa20;VIIRS;nominal;4.1;NRT;false"
    assert 'filename="hotspots.csv"' in response.headers["content-disposition"]
    assert response.headers["content-type"].startswith("text/csv")


def test_export_over_one_million_rows_is_413(use_fake_db):
    use_fake_db(FakeClient(rpcs={"hotspots_count": 1_000_001}))

    response = client.post("/api/v1/hotspots/export", json=body())

    assert response.status_code == 413
    assert "1000000 limit" in response.json()["detail"]


def test_export_has_no_date_limit(use_fake_db):
    use_fake_db(FakeClient(rpcs={"hotspots_count": 0}))

    response = client.post("/api/v1/hotspots/export", json=body("2001-01-01", "2026-11-14"))

    assert response.status_code == 200


def test_export_marks_incomplete_file_when_database_fails_midway(use_fake_db, monkeypatch):
    monkeypatch.setattr(main, "EXPORT_PAGE_SIZE", 2)
    pages = iter([[row(1), row(2)]])

    def page(_params):
        try:
            return next(pages)
        except StopIteration:
            raise RuntimeError("database went away")

    use_fake_db(FakeClient(rpcs={"hotspots_count": 4, "hotspots_page": page}))

    response = client.post("/api/v1/hotspots/export", json=body(separator=";"))

    lines = response.text.splitlines()
    assert response.status_code == 200
    assert len(lines) == 4  # header, two rows, marker
    assert lines[-1] == f"# EXPORT_INCOMPLETE request_id={response.headers['X-Request-ID']}"
>>>>>>> feature/dev-1
