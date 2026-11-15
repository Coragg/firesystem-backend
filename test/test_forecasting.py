import json

import numpy as np
import pandas as pd
import pytest
import tifffile
import xarray as xr
from shapely.geometry import box
from sklearn.linear_model import LogisticRegression

from src.utils import fire_datasets as fd
from src.utils import forecasting as fc

LAT = np.array([-35.0, -35.005, -35.01])
LON = np.array([-71.0, -70.995, -70.99])


def burned_area_ds(months: pd.DatetimeIndex, burns: dict[tuple[int, int, int], int]) -> xr.Dataset:
    """Burn_Date grid with day of year `burns[(t, i, j)]` at month t, pixel (i, j); everything else unburned."""
    doy = np.zeros((len(months), len(LAT), len(LON)), dtype="int16")
    for (t, i, j), day in burns.items():
        doy[t, i, j] = day
    doy[:, 2, 2] = -2  # water pixel
    return xr.Dataset(
        {"MCD64A1_061_Burn_Date": (("time", "lat", "lon"), doy)},
        coords={"time": months, "lat": LAT, "lon": LON},
    )


def history_for(months: pd.DatetimeIndex, burns: dict) -> xr.DataArray:
    return fc.monthly_burn_history(burned_area_ds(months, burns))


def test_load_training_data_reads_csv_and_json(tmp_path):
    df = pd.DataFrame({"year": [2020], "quarter": [1], "month": [2], "day": [3], "lat": [-35.0], "lon": [-71.0], "burned": [1]})
    df.attrs["description"] = {"title": "t"}
    csv_paths = fd.save_dataframe(df, tmp_path / "train", "csv")
    json_paths = fd.save_dataframe(df, tmp_path / "train_json", "json")

    for path in (csv_paths[0], json_paths[0]):
        loaded = fc.load_training_data(path)
        assert loaded["date"].iloc[0] == pd.Timestamp("2020-02-03")
        assert loaded.attrs["description"]["title"] == "t"


def test_month_number_is_continuous_across_years():
    assert fc.month_number(pd.to_datetime(["2019-12-15", "2020-01-01"])).tolist() == [2019 * 12 + 11, 2020 * 12]


def test_monthly_burn_history_fills_gaps_and_masks_water():
    months = pd.DatetimeIndex(["2020-01-01", "2020-03-01"])  # February missing
    history = history_for(months, {(1, 0, 0): 70})
    assert list(history.indexes["month"]) == list(pd.date_range("2020-01-01", "2020-03-01", freq="MS"))
    assert history.values[2, 0, 0] == 1 and history.values[0, 0, 0] == 0
    assert np.isnan(history.values[1]).all()       # missing month
    assert np.isnan(history.values[:, 2, 2]).all()  # water


def test_monthly_burn_history_applies_region():
    months = pd.DatetimeIndex(["2020-01-01"])
    region = box(-71.002, -35.002, -70.998, -34.99)  # only pixel (0, 0)
    history = fc.monthly_burn_history(burned_area_ds(months, {}), region)
    assert np.isfinite(history.values[0]).sum() == 1


def test_add_burn_lags_reads_the_pixel_history():
    months = pd.date_range("2018-01-01", periods=36, freq="MS")
    # pixel (0, 0) burned in Jan 2018 and Feb 2019; neighbour (0, 1) burned in Jan 2019
    history = history_for(months, {(0, 0, 0): 10, (13, 0, 0): 40, (12, 0, 1): 5})
    rows = pd.DataFrame({
        "date": pd.to_datetime(["2020-01-20", "2020-02-05", "2018-06-01"]),
        "lat": [-35.0001, -35.0, -35.0],
        "lon": [-71.0, -71.0001, -71.0],
    })
    lags = fc.add_burn_lags(rows, history, lags=[12, 24], window=range(12, 24))

    assert lags["burned_lag_12"].tolist()[:2] == [0, 1]         # Jan 2019 / Feb 2019 at pixel (0, 0)
    assert lags["burned_lag_24"].tolist()[:2] == [1, 0]         # Jan 2018 / Feb 2018
    # Window t-23..t-12: Feb 2018-Jan 2019 for the January row, Mar 2018-Feb 2019 for the February row
    assert lags["burned_months_12_23"].tolist()[:2] == [0, 1]
    # January row: neighbour (0, 1) in Jan 2019; February row: that plus its own pixel in Feb 2019
    assert lags["nbr_burned_12_23"].tolist()[:2] == [1, 2]
    assert lags.iloc[2].isna().all()                            # before the history starts


def test_add_burn_lags_outside_grid_is_nan():
    months = pd.date_range("2018-01-01", periods=30, freq="MS")
    rows = pd.DataFrame({"date": pd.to_datetime(["2020-03-01"]), "lat": [-30.0], "lon": [-71.0]})
    assert fc.add_burn_lags(rows, history_for(months, {})).isna().all(axis=None)


def test_add_lagged_vegetation_uses_values_a_year_before():
    times = pd.to_datetime(["2019-01-01", "2019-01-17", "2020-01-01"])
    ndvi = np.stack([np.full((3, 3), v) for v in (0.2, 0.3, 0.9)])
    grid = xr.Dataset(
        {"ndvi": (("time", "lat", "lon"), ndvi), "evi": (("time", "lat", "lon"), ndvi / 2)},
        coords={"time": times, "lat": LAT, "lon": LON},
    )
    rows = pd.DataFrame({"date": pd.to_datetime(["2020-02-10"]), "lat": [-35.0], "lon": [-71.0]}, index=[7])
    description = fd.DATASETS["MODIS_vegetation"]["description"]
    veg = fc.add_lagged_vegetation(rows, grid, period_days=16, description=description, months=12)

    # 2019-02-10 minus 1 guard day -> last composite ending by 2019-02-09 is the one starting 2019-01-17
    assert veg.index.tolist() == [7]
    assert veg["ndvi_lag_12"].item() == pytest.approx(0.3)
    assert veg["evi_lag_12"].item() == pytest.approx(0.15)
    assert veg["veg_lag_12_age_days"].item() == 8


def test_add_calendar_features():
    rows = pd.DataFrame({"date": pd.to_datetime(["2020-01-01", "2020-07-01"]), "sensor": ["MODIS", "VIIRS"]})
    cal = fc.add_calendar_features(rows)
    assert cal["quarter"].tolist() == [1, 3]
    assert cal["sensor_code"].tolist() == [0, 1]
    assert cal["month_cos"].iloc[1] == pytest.approx(np.cos(2 * np.pi * 7 / 12))


def test_forecast_horizon_and_feature_columns():
    assert fc.forecast_horizon([12, 13], 12) == 12
    assert fc.forecast_horizon([6, 12], 12) == 6
    df = pd.DataFrame(columns=["date", "lat", "lon", "pixel_lat", "pixel_lon", "elevation_m", "month", "burned",
                               "burned_lag_12", "ndvi_lag_12", "lai_lag_24", "season"])
    assert fc.feature_columns(df) == ["pixel_lat", "pixel_lon", "elevation_m", "month", "burned_lag_12", "ndvi_lag_12", "lai_lag_24"]


def test_build_forecast_features_from_netcdf(tmp_path):
    months = pd.date_range("2018-01-01", periods=30, freq="MS")
    burned_area_ds(months, {(12, 0, 0): 5}).rename({"MCD64A1_061_Burn_Date": "Burn_Date"}).to_netcdf(
        tmp_path / "MCD64A1.061_500m_aid0001.nc", engine="h5netcdf")
    veg_times = pd.date_range("2018-01-01", periods=60, freq="16D")
    shape = (len(veg_times), len(LAT), len(LON))
    xr.Dataset({
        "_500m_16_days_NDVI": (("time", "lat", "lon"), np.full(shape, 0.5)),
        "_500m_16_days_EVI": (("time", "lat", "lon"), np.full(shape, 0.25)),
        "_500m_16_days_pixel_reliability": (("time", "lat", "lon"), np.zeros(shape, "int8")),
    }, coords={"time": veg_times, "lat": LAT, "lon": LON}).to_netcdf(tmp_path / "MOD13A1.061_500m_aid0001.nc", engine="h5netcdf")

    rows = pd.DataFrame({
        "sensor": "MODIS", "date": pd.to_datetime(["2020-01-10", "2020-03-10"]),
        "lat": [-35.0, -35.005], "lon": [-71.0, -70.995], "burned": [1, 0],
    }, index=[5, 3])
    histories = {}
    features = fc.build_forecast_features(rows, tmp_path, histories=histories)

    assert features.index.tolist() == [5, 3]
    assert features.loc[5, "burned_lag_12"] == 1 and features.loc[3, "burned_lag_12"] == 0
    assert features["ndvi_lag_12"].tolist() == [0.5, 0.5]
    assert "MODIS" in histories
    assert {"burned", "sensor_code", "month_sin"} <= set(features.columns)


def test_future_rows_cover_pixels_with_data_after_the_history():
    months = pd.date_range("2019-01-01", periods=24, freq="MS")
    future = fc.future_rows(history_for(months, {}), "MODIS", months_ahead=3)
    assert future["date"].drop_duplicates().tolist() == list(pd.to_datetime(["2021-01-15", "2021-02-15", "2021-03-15"]))
    assert len(future) == 3 * 8  # 9 pixels minus the water pixel
    assert len(fc.future_rows(history_for(months, {}), "MODIS", months_ahead=1, stride=2)) == 3


def test_fire_season_starts_in_july():
    seasons = fc.fire_season(pd.Series(pd.to_datetime(["2019-06-30", "2019-07-01", "2020-01-15"])))
    assert seasons.tolist() == [2018, 2019, 2019]


def synthetic_rows(seasons: range, per_season: int = 40, seed: int = 0) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    dates = pd.to_datetime([f"{s}-07-01" for s in seasons for _ in range(per_season)]) + pd.to_timedelta(
        rng.integers(0, 365, len(seasons) * per_season), unit="D")
    x = rng.normal(size=len(dates))
    return pd.DataFrame({
        "date": dates, "x": x, "month": dates.month, "sensor_code": 0,
        "burned_lag_12": (x > 0).astype(float),
        "burned": (x + rng.normal(scale=0.5, size=len(x)) > 0).astype(int),
    })


def test_chronological_split_orders_seasons():
    df, seasons = fc.chronological_split(synthetic_rows(range(2014, 2020)), val_seasons=2, test_seasons=1)
    assert seasons == {"train": [2014, 2015, 2016], "val": [2017, 2018], "test": [2019]}
    assert df.groupby("split")["date"].max()["train"] < df.groupby("split")["date"].min()["val"]
    assert df.groupby("split")["date"].max()["val"] < df.groupby("split")["date"].min()["test"]
    with pytest.raises(ValueError):
        fc.chronological_split(synthetic_rows(range(2014, 2016)), val_seasons=1, test_seasons=1)


def test_classification_metrics_handles_single_class():
    good = fc.classification_metrics(np.array([0, 1, 0, 1]), np.array([0.1, 0.9, 0.2, 0.8]))
    assert good["roc_auc"] == 1 and good["average_precision"] == 1 and good["burn_share"] == 0.5
    single = fc.classification_metrics(np.array([0, 0]), np.array([0.1, 0.2]))
    assert np.isnan(single["roc_auc"]) and np.isfinite(single["log_loss"])


def test_walk_forward_trains_only_on_earlier_seasons():
    df, seasons = fc.chronological_split(synthetic_rows(range(2014, 2020)), 2, 1)
    seen = []

    class Recorder(LogisticRegression):
        def fit(self, X, y):
            seen.append(df.loc[X.index, "season"].max())
            return super().fit(X, y)

    predictions = fc.walk_forward(df, ["x"], "burned", Recorder, seasons["val"])
    assert seen == [2016, 2017]
    assert set(predictions["season"]) == {2017, 2018}
    assert predictions["p"].between(0, 1).all()
    scores = fc.score_predictions(predictions)
    assert list(scores.index) == [2017, 2018, "all"] and scores.loc["all", "roc_auc"] > 0.8


def test_tune_walk_forward_ranks_parameters():
    df, seasons = fc.chronological_split(synthetic_rows(range(2014, 2020)), 2, 1)
    results = fc.tune_walk_forward(
        df, ["x"], "burned", lambda params: LogisticRegression(**params), {"C": [1e-6, 1.0]}, seasons["val"], metric="log_loss",
    )
    assert results["C"].iloc[0] == 1.0
    assert results["log_loss"].is_monotonic_increasing


def test_baselines():
    df, seasons = fc.chronological_split(synthetic_rows(range(2014, 2020)), 2, 1)
    naive = fc.walk_forward(df, ["burned_lag_12"], "burned", fc.SeasonalNaive, seasons["val"])
    assert set(np.round(naive["p"], 2)) <= {0.01, 0.99}

    X = pd.DataFrame({"sensor_code": [0, 0, 1], "month": [1, 1, 2]})
    model = fc.Climatology().fit(X, pd.Series([1, 0, 1]))
    p = model.predict_proba(pd.DataFrame({"sensor_code": [0, 1, 0], "month": [1, 2, 5]}))[:, 1]
    assert p.tolist() == pytest.approx([0.5, 1.0, 2 / 3])


def test_xgboost_classifier_in_walk_forward():
    try:
        import xgboost
    except Exception as error:  # missing package or OpenMP runtime (macOS: brew install libomp)
        pytest.skip(f"xgboost unavailable: {error.__class__.__name__}")
    df, seasons = fc.chronological_split(synthetic_rows(range(2014, 2020)), 2, 1)
    predictions = fc.walk_forward(
        df, ["x", "month"], "burned", lambda: xgboost.XGBClassifier(n_estimators=20, max_depth=2), seasons["test"])
    assert fc.score_predictions(predictions).loc["all", "roc_auc"] > 0.8


def write_geotiff(path, values, west, north, step, nodata=None):
    extratags = [(33550, "d", 3, (step, step, 0.0)), (33922, "d", 6, (0.0, 0.0, 0.0, west, north, 0.0))]
    if nodata is not None:
        extratags.append((42113, "s", 0, str(nodata), True))
    tifffile.imwrite(path, values, extratags=extratags)


def test_pixel_centres_snap_rows_to_the_grid():
    history = history_for(pd.date_range("2018-01-01", periods=2, freq="MS"), {})
    rows = pd.DataFrame({"lat": [-35.0019, -40.0], "lon": [-70.9951, -71.0]})
    centres = fc.pixel_centres(rows, history)
    assert centres.iloc[0].tolist() == [-35.0, -70.995]
    assert centres.iloc[1].isna().all()


def test_add_lagged_lai_uses_values_two_years_before():
    times = pd.to_datetime(["2018-01-01", "2018-01-05", "2018-01-09", "2020-01-05"])
    lai = np.stack([np.full((3, 3), v) for v in (1.0, np.nan, 2.0, 9.0)])
    grid = xr.Dataset({"lai": (("time", "lat", "lon"), lai)}, coords={"time": times, "lat": LAT, "lon": LON})
    rows = pd.DataFrame({"date": pd.to_datetime(["2020-01-11", "2020-01-20"]), "lat": [-35.0, -35.0], "lon": [-71.0, -71.0]})
    out = fc.add_lagged_lai(rows, grid, months=24, max_age_days=32)
    # 2018-01-11 minus 1 guard day: last composite ending by 2018-01-10 is 2018-01-05 (NaN) -> falls back to 2018-01-01
    assert out["lai_lag_24"].tolist() == [1.0, 2.0]
    assert out["lai_lag_24_age_days"].tolist() == [6.0, 7.0]


def test_add_terrain_samples_nearest_cell():
    terrain = xr.Dataset({
        "elevation_m": (("lat", "lon"), np.arange(9.0).reshape(3, 3)),
        "slope_deg": (("lat", "lon"), np.full((3, 3), 5.0)),
    }, coords={"lat": LAT, "lon": LON})
    rows = pd.DataFrame(index=[4, 9])
    out = fc.add_terrain(rows, terrain, [-35.005, -50.0], [-70.99, -71.0])
    assert out.index.tolist() == [4, 9]
    assert out.loc[4].tolist() == [5.0, 5.0] and out.loc[9].isna().all()


def test_build_forecast_features_adds_lai_and_terrain(tmp_path):
    months = pd.date_range("2017-01-01", periods=42, freq="MS")
    burned_area_ds(months, {}).rename({"MCD64A1_061_Burn_Date": "Burn_Date"}).to_netcdf(
        tmp_path / "MCD64A1.061_500m_aid0001.nc", engine="h5netcdf")
    veg_times = pd.date_range("2017-01-01", periods=90, freq="16D")
    shape = (len(veg_times), len(LAT), len(LON))
    xr.Dataset({
        "_500m_16_days_NDVI": (("time", "lat", "lon"), np.full(shape, 0.5)),
        "_500m_16_days_EVI": (("time", "lat", "lon"), np.full(shape, 0.25)),
        "_500m_16_days_pixel_reliability": (("time", "lat", "lon"), np.zeros(shape, "int8")),
    }, coords={"time": veg_times, "lat": LAT, "lon": LON}).to_netcdf(tmp_path / "MOD13A1.061_500m_aid0001.nc", engine="h5netcdf")

    extra = tmp_path / "extra"
    extra.mkdir()
    west, north, step = LON[0] - 0.0025, LAT[0] + 0.0025, 0.005
    for doy in (1, 5, 9):
        write_geotiff(extra / f"MCD15A3H.061_Lai_500m_doy2018{doy:03d}000000_aid0001.tif", np.full((3, 3), 30, "uint8"), west, north, step, 255)
        write_geotiff(extra / f"MCD15A3H.061_FparLai_QC_doy2018{doy:03d}000000_aid0001.tif", np.zeros((3, 3), "uint8"), west, north, step, 255)
    write_geotiff(extra / "SRTMGL3_NC.003_SRTMGL3_DEM_doy2000042000000_aid0001.tif", np.full((3, 3), 400, "int16"), west, north, step, -32768)

    rows = pd.DataFrame({"sensor": "MODIS", "date": pd.to_datetime(["2020-01-12"]), "lat": [-35.001], "lon": [-71.0]})
    features = fc.build_forecast_features(rows, tmp_path, extra_dir=extra)
    assert features["lai_lag_24"].item() == pytest.approx(3.0)
    assert features["elevation_m"].item() == 400 and features["slope_deg"].item() == 0
    assert features["pixel_lat"].item() == -35.0
    assert {"lai_lag_24", "elevation_m", "slope_deg", "pixel_lat"} <= set(fc.feature_columns(features))
