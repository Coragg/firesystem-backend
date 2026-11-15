from datetime import datetime
from pathlib import Path

import numpy as np
import pandas as pd
import pytest
import xarray as xr
from shapely.geometry import Polygon, box

from src.utils import fire_datasets as fd

BBOX = (-95.0, 20.0, -75.0, 35.0)
START, STOP = datetime(2005, 8, 1), datetime(2005, 9, 30)
LAT = np.linspace(35, 20, 6)    # 3° spacing, descending like AppEEARS output
LON = np.linspace(-95, -75, 5)  # 5° spacing


def write_product(directory: Path, product: str, times: list[str], variables: dict) -> None:
    """Write a synthetic AppEEARS-like NetCDF file for one product."""
    shape = (len(times), len(LAT), len(LON))
    ds = xr.Dataset(
        {name: (("time", "lat", "lon"), values(shape), attrs) for name, (values, attrs) in variables.items()},
        coords={"time": pd.to_datetime(times), "lat": LAT, "lon": LON},
    )
    ds.to_netcdf(directory / f"{product}_500m_aid0001.nc", engine="h5netcdf")


@pytest.fixture
def data_dir(tmp_path: Path) -> Path:
    rng = np.random.default_rng(0)

    def burn_dates(shape):
        values = np.zeros(shape, dtype="int16")
        values[0, 1, 1] = 225   # 2005-08-13
        values[0, 2, 3] = 213   # 2005-08-01
        values[1, 1, 1] = 250   # 2005-09-07 (same pixel, next month)
        values[0, 4, 0] = -2    # water
        values[1, 5, 4] = -1    # fill
        return values

    write_product(tmp_path, "MCD64A1.061", ["2005-08-01", "2005-09-01"], {
        "Burn_Date": (burn_dates, {}),
        "Burn_Date_Uncertainty": (lambda s: np.full(s, 2, dtype="uint8"), {}),
    })
    write_product(tmp_path, "MOD13A1.061", ["2005-07-28", "2005-08-13", "2005-08-29", "2005-09-14"], {
        "_500m_16_days_NDVI": (lambda s: rng.integers(0, 9000, s).astype("int16"),
                               {"scale_factor": 0.0001, "_FillValue": -3000}),
        "_500m_16_days_EVI": (lambda s: rng.integers(0, 9000, s).astype("int16"),
                              {"scale_factor": 0.0001, "_FillValue": -3000}),
        "_500m_16_days_pixel_reliability": (lambda s: np.where(rng.random(s) < 0.8, 0, 3).astype("int8"),
                                            {"_FillValue": -1}),
    })
    write_product(tmp_path, "MOD11A1.061", ["2005-08-01", "2005-08-13"], {
        "LST_Day_1km": (lambda s: np.full(s, 15000, dtype="uint16"), {"scale_factor": 0.02, "_FillValue": 0}),
        "LST_Night_1km": (lambda s: np.full(s, 14500, dtype="uint16"), {"scale_factor": 0.02, "_FillValue": 0}),
    })
    write_product(tmp_path, "MCD12Q1.061", ["2005-01-01"], {
        "LC_Type1": (lambda s: np.full(s, 10, dtype="uint8"), {"_FillValue": 255}),
    })
    return tmp_path


@pytest.fixture
def burns(data_dir: Path) -> pd.DataFrame:
    ds = fd.open_product("MCD64A1.061", data_dir)
    return fd.build_burned_area(ds, {**fd.DATASETS["MODIS_burned_area"]["description"]})


@pytest.fixture
def dfs(data_dir: Path) -> dict[str, pd.DataFrame]:
    return fd.build_dataframes(data_dir, BBOX, START, STOP)


# --- catalogue helpers -----------------------------------------------------
def test_active_datasets_excludes_viirs_before_2012():
    names = fd.active_datasets(datetime(2005, 9, 30))
    assert set(names) == {"MODIS_burned_area", "MODIS_vegetation", "MODIS_lst", "MODIS_land_cover"}
    assert "VIIRS_vegetation" in fd.active_datasets(datetime(2020, 1, 1))


def test_request_layers_lists_every_product_layer():
    layers = fd.request_layers({"MODIS_lst": fd.DATASETS["MODIS_lst"]})
    assert layers == [
        {"product": "MOD11A1.061", "layer": "LST_Day_1km"},
        {"product": "MOD11A1.061", "layer": "LST_Night_1km"},
    ]


def test_dataset_context_contains_bbox_and_time_range():
    ctx = fd.dataset_context(BBOX, START, STOP)
    assert ctx["bbox"] == {"west": -95.0, "south": 20.0, "east": -75.0, "north": 35.0}
    assert ctx["time_range"] == {"start": "2005-08-01", "stop": "2005-09-30"}


# --- NetCDF helpers --------------------------------------------------------
def test_get_layer_matches_suffix_and_rejects_ambiguity():
    ds = xr.Dataset({"_500m_16_days_EVI": ("x", [1]), "_500m_16_days_EVI2": ("x", [2])})
    assert int(fd.get_layer(ds, "_EVI").values[0]) == 1
    with pytest.raises(KeyError):
        fd.get_layer(ds, "_NDVI")


def test_open_product_raises_when_missing(tmp_path):
    with pytest.raises(FileNotFoundError):
        fd.open_product("MOD13A1.061", tmp_path)


def test_grid_spacing():
    assert fd.grid_spacing(LAT) == pytest.approx(3.0)
    assert fd.grid_spacing(np.array([1.0])) == 0.0


def test_to_dataframe_adds_pixel_bounds_and_drops_empty_rows():
    da = xr.DataArray(
        [[[1.0, np.nan], [np.nan, np.nan]]],
        dims=("time", "lat", "lon"),
        coords={"time": pd.to_datetime(["2005-08-01"]), "lat": [30.0, 29.0], "lon": [-90.0, -88.0]},
    )
    df = fd.to_dataframe({"v": da}, {"title": "t"})

    assert len(df) == 1
    row = df.iloc[0]
    assert (row.lat_min, row.lat_max, row.lon_min, row.lon_max) == (29.5, 30.5, -91.0, -89.0)
    assert df.attrs["description"]["grid_spacing_deg"] == {"lat": 1.0, "lon": 2.0}


# --- burned area -----------------------------------------------------------
def test_build_burned_area_uses_day_of_burn(burns):
    assert list(burns.columns) == [
        "year", "quarter", "month", "day", "lat", "lon", "burn_date_uncertainty",
        "lat_min", "lat_max", "lon_min", "lon_max",
    ]
    dates = set(zip(burns.month, burns.day))
    assert dates == {(8, 1), (8, 13), (9, 7)}
    assert (burns.quarter == 3).all() and (burns.year == 2005).all()
    assert "update_frequency" in burns.attrs["description"]


def test_add_unburned_samples_fills_every_other_month(burns):
    out = fd.add_unburned_samples(burns, BBOX, seed=1)
    burned, generated = out[out.burned == 1], out[out.burned == 0]

    assert len(burned) == len(burns)
    # 2 burned pixels; pixel A burned in Aug and Sep, pixel B only in Aug
    assert len(generated) == 10 + 11
    assert generated.lat.between(generated.lat_min, generated.lat_max).all()
    assert generated.lon.between(generated.lon_min, generated.lon_max).all()
    days_in_month = pd.to_datetime(dict(year=generated.year, month=generated.month, day=1)).dt.days_in_month
    assert generated.day.between(1, days_in_month).all()
    assert (generated.quarter == (generated.month - 1) // 3 + 1).all()


def test_add_unburned_samples_is_reproducible_with_seed(burns):
    a = fd.add_unburned_samples(burns, BBOX, seed=7)
    b = fd.add_unburned_samples(burns, BBOX, seed=7)
    pd.testing.assert_frame_equal(a, b)


def test_add_unburned_samples_study_area_stays_in_bbox(burns):
    out = fd.add_unburned_samples(burns, BBOX, seed=1, extent="study_area")
    generated = out[out.burned == 0]
    assert generated.lat.between(20, 35).all() and generated.lon.between(-95, -75).all()


def test_add_unburned_samples_rejects_unknown_extent(burns):
    with pytest.raises(ValueError):
        fd.add_unburned_samples(burns, BBOX, extent="world")


# --- gridded features ------------------------------------------------------
def test_vegetation_grid_masks_unreliable_pixels(data_dir):
    ds = fd.open_product("MOD13A1.061", data_dir)
    grid = fd.vegetation_grid(ds, [0]).compute()
    bad = (ds["_500m_16_days_pixel_reliability"] == 3).values
    assert np.isnan(grid.ndvi.values[bad]).all()
    assert not np.isnan(grid.ndvi.values[~bad]).any()


def test_lst_grid_converts_to_celsius(data_dir):
    grid = fd.lst_grid(fd.open_product("MOD11A1.061", data_dir), {"lst_day_c": "LST_Day_1km"})
    assert float(grid.lst_day_c.max()) == pytest.approx(15000 * 0.02 - 273.15)


def test_land_cover_grid(data_dir):
    grid = fd.land_cover_grid(fd.open_product("MCD12Q1.061", data_dir))
    assert float(grid.land_cover_igbp.max()) == 10


def test_sample_at_burn_dates_picks_covering_composite_and_nearest_pixel(data_dir):
    grid = fd.vegetation_grid(fd.open_product("MOD13A1.061", data_dir), [0, 3]).compute()
    points = pd.DataFrame({
        "year": [2005, 2005, 2005], "quarter": [3, 3, 1], "month": [8, 8, 1], "day": [20, 13, 15],
        "lat": [LAT[1] + 1.0, LAT[2], LAT[2]], "lon": [LON[1] - 2.0, LON[3], LON[3]], "burned": [1, 0, 0],
        "lat_min": 0.0, "lat_max": 0.0, "lon_min": 0.0, "lon_max": 0.0,
    })
    out = fd.sample_at_burn_dates(grid, points, 16, fd.DATASETS["MODIS_vegetation"]["description"], timing="covering")

    expected = float(grid.ndvi.sel(time="2005-08-13", lat=LAT[1], lon=LON[1]))
    assert out.ndvi[0] == pytest.approx(expected)
    assert out.source_date[0] == pd.Timestamp("2005-08-13")
    assert out.source_date[1] == pd.Timestamp("2005-08-13")  # start day of a composite is inside it
    assert np.isnan(out.ndvi[2]) and pd.isna(out.source_date[2])  # January was not downloaded


def test_sample_at_burn_dates_returns_nan_outside_grid(data_dir):
    grid = fd.lst_grid(fd.open_product("MOD11A1.061", data_dir), {"lst_day_c": "LST_Day_1km"})
    points = pd.DataFrame({
        "year": [2005], "quarter": [3], "month": [8], "day": [1], "lat": [40.0], "lon": [-90.0], "burned": [0],
        "lat_min": 0.0, "lat_max": 0.0, "lon_min": 0.0, "lon_max": 0.0,
    })
    out = fd.sample_at_burn_dates(grid, points, 1, fd.DATASETS["MODIS_lst"]["description"])
    assert np.isnan(out.lst_day_c[0])


# --- pipeline --------------------------------------------------------------
def test_build_dataframes_creates_aligned_burn_date_samples(dfs):
    assert set(dfs) == {
        "MODIS_burned_area", "MODIS_burned_area_fire_summary",
        "MODIS_vegetation", "MODIS_vegetation_at_burn_dates",
        "MODIS_lst", "MODIS_lst_at_burn_dates",
        "MODIS_land_cover", "MODIS_land_cover_at_burn_dates",
    }
    burns = dfs["MODIS_burned_area"]
    for name in ("MODIS_vegetation_at_burn_dates", "MODIS_lst_at_burn_dates", "MODIS_land_cover_at_burn_dates"):
        sampled = dfs[name]
        assert len(sampled) == len(burns)
        np.testing.assert_allclose(sampled[["lat", "lon", "burned"]], burns[["lat", "lon", "burned"]])
        assert {"update_frequency", "spatial_granularity", "bbox"} <= set(sampled.attrs["description"])
    assert dfs["MODIS_land_cover_at_burn_dates"]["land_cover_igbp"].dtype == "Int64"


def test_build_training_dataset_joins_label_and_features(dfs):
    full = fd.build_training_dataset(dfs, drop_missing=False)
    burns, veg = dfs["MODIS_burned_area"], dfs["MODIS_vegetation_at_burn_dates"]

    assert list(full.columns) == [
        "sensor", "year", "quarter", "month", "day", "lat", "lon", "ndvi", "evi", "veg_age_days", "burned",
    ]
    assert "before the date" in full.attrs["description"]["time_matching"]
    assert (full.burned.to_numpy() == burns.burned.to_numpy()).all()
    np.testing.assert_array_equal(full.ndvi.to_numpy(), veg.ndvi.to_numpy())

    kept = fd.build_training_dataset(dfs)
    assert kept[["ndvi", "evi"]].notna().any(axis=1).all()
    desc = kept.attrs["description"]
    assert desc["rows"]["total"] == len(full) and desc["rows"]["kept"] == len(kept)
    assert sum(desc["class_balance"].values()) == len(kept)


def test_build_training_dataset_detects_misalignment(dfs):
    broken = dict(dfs)
    broken["MODIS_vegetation_at_burn_dates"] = dfs["MODIS_vegetation_at_burn_dates"].iloc[::-1]
    with pytest.raises(ValueError):
        fd.build_training_dataset(broken)


def test_build_training_dataset_requires_vegetation():
    with pytest.raises(ValueError):
        fd.build_training_dataset({})


# --- fire events and proportional sampling ------------------------------------
def make_burns(rows: list[tuple[int, int, int, float, float]]) -> pd.DataFrame:
    """Burn rows from (year, month, day, lat, lon) on a 1° grid."""
    df = pd.DataFrame(rows, columns=["year", "month", "day", "lat", "lon"])
    df["quarter"] = (df["month"] - 1) // 3 + 1
    df["burn_date_uncertainty"] = 1.0
    df["lat_min"], df["lat_max"] = df["lat"] - 0.5, df["lat"] + 0.5
    df["lon_min"], df["lon_max"] = df["lon"] - 0.5, df["lon"] + 0.5
    return df[["year", "quarter", "month", "day", "lat", "lon", "burn_date_uncertainty", *fd.PIXEL_BOUND_COLS]]


def test_label_fire_events_joins_neighbours_within_gap():
    burns = make_burns([
        (2017, 1, 30, -35.0, -72.0),  # fire A, spreading over neighbouring pixels...
        (2017, 2, 1, -35.0, -71.0),
        (2017, 2, 4, -34.0, -70.0),   # ...diagonal neighbour, 3 days later
        (2017, 3, 10, -35.0, -72.0),  # same pixel as A but 39 days later -> new fire
        (2017, 2, 1, -30.0, -72.0),   # far away -> new fire
    ])
    events = fd.label_fire_events(burns, max_gap_days=5)

    assert events[0] == events[1] == events[2]
    assert len({events[0], events[3], events[4]}) == 3
    assert fd.label_fire_events(burns.iloc[:0]).size == 0


def test_fire_summary_counts_events_in_start_month():
    burns = make_burns([
        (2017, 1, 30, -35.0, -72.0), (2017, 2, 1, -35.0, -71.0),  # one fire starting in January
        (2017, 2, 10, -30.0, -72.0),                               # one fire in February
        (2018, 2, 5, -30.0, -72.0), (2018, 2, 5, -20.0, -72.0),    # two fires in February 2018
    ])
    summary = fd.fire_summary(burns)

    assert summary[["year", "month", "fires", "burned_pixels"]].values.tolist() == [
        [2017, 1, 1, 1], [2017, 2, 1, 2], [2018, 2, 2, 2],
    ]
    assert summary["ratio"].sum() == pytest.approx(1.0)
    assert summary["ratio"].tolist() == pytest.approx([0.25, 0.25, 0.5])

    yearly = fd.fire_summary(burns, by=("year",))
    assert yearly[["year", "fires"]].values.tolist() == [[2017, 2], [2018, 2]]


def test_fire_summary_pixels_mode_and_validation():
    burns = make_burns([(2017, 1, 1, -35.0, -72.0), (2017, 1, 2, -35.0, -71.0)])
    assert fd.fire_summary(burns, fire_count="pixels")["fires"].tolist() == [2]
    with pytest.raises(ValueError):
        fd.fire_summary(burns, fire_count="fires")


def test_allocate_samples_is_proportional_and_redistributes_caps():
    ratios = pd.Series({"a": 0.5, "b": 0.3, "c": 0.2})

    assert fd.allocate_samples(ratios, pd.Series({"a": 100, "b": 100, "c": 100}), 10).to_dict() == {
        "a": 5, "b": 3, "c": 2,
    }
    # "a" can only give 2; its remaining share goes to "b" and "c" in proportion 3:2
    capped = fd.allocate_samples(ratios, pd.Series({"a": 2, "b": 100, "c": 100}), 20)
    assert capped["a"] == 2 and capped.sum() == 20 and capped["b"] > capped["c"]
    # fewer rows than the maximum -> take everything
    assert fd.allocate_samples(ratios, pd.Series({"a": 1, "b": 2, "c": 3}), 50).to_dict() == {"a": 1, "b": 2, "c": 3}
    assert fd.allocate_samples(ratios, pd.Series({"a": 5, "b": 5, "c": 5}), 0).sum() == 0
    # strict proportions: "a" stays capped at 2 and nothing is moved to "b" or "c"
    strict = fd.allocate_samples(ratios, pd.Series({"a": 2, "b": 100, "c": 100}), 20, redistribute=False)
    assert strict.to_dict() == {"a": 2, "b": 6, "c": 4}
    with pytest.raises(ValueError):
        fd.allocate_samples(ratios, pd.Series({"a": 1, "b": 1, "c": 1}), -1)


def test_sample_burns_by_period_follows_allocation():
    rows = [(2017, 1, d, -35.0, -72.0 + 0.0 * d) for d in range(1, 21)]        # 20 pixels-days, 1 fire
    rows += [(2017, 2, 1, -30.0 - 2 * i, -72.0) for i in range(30)]             # 30 separate fires
    burns = make_burns(rows)
    summary = fd.fire_summary(burns)
    sampled, out = fd.sample_burns_by_period(burns, summary, max_burn_samples=31, seed=3)

    assert len(sampled) == 31
    assert out.set_index("month")["burn_samples"].to_dict() == {1: 1, 2: 30}
    assert (sampled.groupby("month").size() == out.set_index("month")["burn_samples"]).all()

    again, _ = fd.sample_burns_by_period(burns, summary, max_burn_samples=31, seed=3)
    pd.testing.assert_frame_equal(sampled, again)


def test_add_unburned_samples_never_uses_unsampled_burn_months():
    burns = make_burns([(2017, 8, 10, -35.0, -72.0), (2017, 9, 12, -35.0, -72.0)])
    out = fd.add_unburned_samples(burns.iloc[[0]], BBOX_CHILE, seed=1, burned_reference=burns)
    generated = out[out.burned == 0]

    assert len(generated) == 10
    assert not generated.month.isin([8, 9]).any()


def test_build_dataframes_caps_burn_samples(data_dir):
    dfs = fd.build_dataframes(data_dir, BBOX, START, STOP, max_burn_samples=2)
    burns = dfs["MODIS_burned_area"]
    summary = dfs["MODIS_burned_area_fire_summary"]

    assert (burns.burned == 1).sum() == 2
    assert summary["burn_samples"].sum() == 2
    assert summary["ratio"].sum() == pytest.approx(1.0)
    assert summary["no_burn_samples"].sum() == (burns.burned == 0).sum()
    assert len(dfs["MODIS_vegetation_at_burn_dates"]) == len(burns)


BBOX_CHILE = (-80.0, -60.0, -60.0, -15.0)


def test_check_grid_in_bbox_rejects_files_for_another_bbox(data_dir):
    ds = fd.open_product("MCD64A1.061", data_dir)
    fd.check_grid_in_bbox(ds, BBOX, "MCD64A1.061")
    with pytest.raises(ValueError, match="outside bbox"):
        fd.check_grid_in_bbox(ds, (-72.79, -36.55, -70.31, -34.68), "MCD64A1.061")
    with pytest.raises(ValueError, match="outside bbox"):
        fd.build_dataframes(data_dir, (-72.79, -36.55, -70.31, -34.68), START, STOP)


# --- region clipping -----------------------------------------------------------
REGION = Polygon([(-95, 20), (-75, 20), (-75, 27), (-85, 35), (-95, 35)])  # cuts the north-east corner


def test_build_dataframes_clips_everything_to_region(data_dir):
    from src.utils.chile_regions import points_in_geometry

    dfs = fd.build_dataframes(data_dir, BBOX, START, STOP, region=REGION, region_name="Test")
    for name, df in dfs.items():
        if name.endswith("_fire_summary"):
            continue
        assert points_in_geometry(REGION, df["lat"].to_numpy(), df["lon"].to_numpy()).all(), name
        assert df.attrs["description"]["region"] == "Test"

    full = fd.build_dataframes(data_dir, BBOX, START, STOP)
    assert len(dfs["MODIS_vegetation"]) < len(full["MODIS_vegetation"])

    training = fd.build_training_dataset(dfs, drop_missing=False)
    assert list(training.columns[:2]) == ["region", "sensor"] and (training.region == "Test").all()
    assert training.attrs["description"]["region"] == "Test"


@pytest.mark.parametrize("extent", ["pixel", "study_area"])
def test_add_unburned_samples_keeps_points_in_region(extent):
    from src.utils.chile_regions import points_in_geometry

    burns = make_burns([(2017, 2, 1, -35.0, -72.0), (2017, 3, 5, -36.0, -71.0)])
    region = Polygon([(-72.5, -36.5), (-70.5, -36.5), (-72.5, -34.5)])  # triangle: half of each pixel is outside
    out = fd.add_unburned_samples(burns, (-73, -37, -70, -34), seed=1, extent=extent, region=region)
    generated = out[out.burned == 0]

    assert len(generated) > 0
    assert points_in_geometry(region, generated.lat.to_numpy(), generated.lon.to_numpy()).all()


# --- saving --------------------------------------------------------------------
def test_save_dataframe_csv_writes_rows_and_description(dfs, tmp_path):
    import json

    training = fd.build_training_dataset(dfs, drop_missing=False)
    paths = fd.save_dataframe(training, tmp_path / "out" / "training.csv")

    assert [p.name for p in paths] == ["training.csv", "training.description.json"]
    loaded = pd.read_csv(paths[0])
    assert list(loaded.columns) == list(training.columns) and len(loaded) == len(training)
    description = json.loads(paths[1].read_text(encoding="utf-8"))
    assert description["title"] == training.attrs["description"]["title"]
    assert description["rows"]["total"] == len(training)


def test_save_dataframe_json_embeds_description_and_nulls(dfs, tmp_path):
    import json

    veg = dfs["MODIS_vegetation_at_burn_dates"]
    (path,) = fd.save_dataframe(veg, tmp_path / "veg", output_format="json")
    content = json.loads(path.read_text(encoding="utf-8"))

    assert path.suffix == ".json"
    assert content["description"]["update_frequency"].startswith("Sampled on burn")
    assert len(content["data"]) == len(veg)
    assert set(content["data"][0]) == set(veg.columns)
    assert any(row["ndvi"] is None for row in content["data"])  # NaN -> null
    dated = [row["source_date"] for row in content["data"] if row["source_date"]]
    assert dated and dated[0].startswith("2005-")


def test_save_dataframe_rejects_unknown_format(tmp_path):
    with pytest.raises(ValueError):
        fd.save_dataframe(pd.DataFrame({"a": [1]}), tmp_path / "x.parquet")



# --- several regions ----------------------------------------------------------
def test_allocate_across_regions_is_proportional_to_fires():
    a = pd.DataFrame({"year": [2017, 2017], "month": [1, 2], "fires": [6, 2], "burned_pixels": [100, 100]})
    b = pd.DataFrame({"year": [2017], "month": [1], "fires": [2], "burned_pixels": [100]})
    allocations, per_region = fd.allocate_across_regions({"A": a, "B": b}, max_burn_samples=50)

    assert per_region.set_index("region")["burn_samples"].to_dict() == {"A": 40, "B": 10}  # 8:2 fires
    assert per_region.set_index("region")["ratio"].to_dict() == pytest.approx({"A": 0.8, "B": 0.2})
    assert allocations["A"]["burn_samples"].tolist() == [30, 10]  # 6:2 inside region A
    assert allocations["B"]["burn_samples"].tolist() == [10]


def test_allocate_across_regions_caps_and_redistributes():
    a = pd.DataFrame({"year": [2017], "month": [1], "fires": [5], "burned_pixels": [3]})
    b = pd.DataFrame({"year": [2017], "month": [1], "fires": [5], "burned_pixels": [100]})
    _, per_region = fd.allocate_across_regions({"A": a, "B": b}, max_burn_samples=20)
    assert per_region.set_index("region")["burn_samples"].to_dict() == {"A": 3, "B": 17}
    _, strict = fd.allocate_across_regions({"A": a, "B": b}, max_burn_samples=20, redistribute=False)
    assert strict.set_index("region")["burn_samples"].to_dict() == {"A": 3, "B": 10}


def test_sample_burns_with_allocation_draws_requested_counts():
    burns = make_burns([(2017, 1, d, -35.0, -72.0 + d) for d in range(1, 11)]
                       + [(2017, 2, d, -36.0, -72.0 + d) for d in range(1, 6)])
    summary = fd.fire_summary(burns)
    allocation = pd.DataFrame({"year": [2017, 2017], "month": [1, 2], "burn_samples": [4, 99]})
    sampled, out = fd.sample_burns_with_allocation(burns, summary, allocation, seed=0)

    assert out.set_index("month")["burn_samples"].to_dict() == {1: 4, 2: 5}  # month 2 capped at 5
    assert sampled.groupby("month").size().to_dict() == {1: 4, 2: 5}


def test_build_dataframes_without_full_grids(data_dir):
    dfs = fd.build_dataframes(data_dir, BBOX, START, STOP, full_grids=False)
    assert "MODIS_vegetation" not in dfs and "MODIS_lst" not in dfs
    assert "MODIS_vegetation_at_burn_dates" in dfs


@pytest.fixture
def two_regions(data_dir, tmp_path):
    """Region A = the synthetic data; region B = the same grid with every burn removed but one pixel."""
    b_dir = tmp_path / "region_b"
    b_dir.mkdir()
    for f in data_dir.glob("*.nc"):
        ds = xr.open_dataset(f, engine="h5netcdf").load()
        if f.name.startswith("MCD64A1"):
            values = ds["Burn_Date"].values.copy()
            values[values > 0] = 0
            values[0, 2, 3] = 213
            ds["Burn_Date"].values[:] = values
        ds.to_netcdf(b_dir / f.name, engine="h5netcdf")
    return {
        "A": {"directory": data_dir, "bbox": BBOX, "geometry": None},
        "B": {"directory": b_dir, "bbox": BBOX, "geometry": None},
    }


def test_build_multi_region_dataset_allocates_by_fires(two_regions):
    out = fd.build_multi_region_dataset(two_regions, START, STOP, max_burn_samples=4, drop_missing=False)
    summary = out["region_summary"].set_index("region")

    assert summary.loc["A", "fires"] == 3 and summary.loc["B", "fires"] == 1
    assert summary["ratio"].tolist() == pytest.approx([0.75, 0.25])
    assert summary["burn_samples"].to_dict() == {"A": 3, "B": 1}  # 4 samples split 3:1 like the fires

    small = fd.build_multi_region_dataset(two_regions, START, STOP, max_burn_samples=2, drop_missing=False)
    assert small["region_summary"]["burn_samples"].sum() == 2

    training = out["training"]
    assert set(training["region"]) == {"A", "B"}
    burn_rows = training[training.burned == 1].groupby("region").size()
    assert burn_rows.reindex(["A", "B"], fill_value=0).tolist() == summary["burn_samples"].tolist()
    assert summary["training_burn_rows"].tolist() == summary["burn_samples"].tolist()
    assert set(out["fire_summary"]["region"]) == {"A", "B"}
    assert out["dataframes"] == {}
    assert training.attrs["description"]["regions"]["A"]["rows"] == int((training.region == "A").sum())


def test_build_multi_region_dataset_keeps_all_burns_without_max(two_regions):
    out = fd.build_multi_region_dataset(two_regions, START, STOP, keep_dataframes=True, drop_missing=False)
    summary = out["region_summary"].set_index("region")
    assert summary["burn_samples"].tolist() == summary["burned_pixels"].tolist() == [3, 1]
    assert set(out["dataframes"]) == {"A", "B"} and "MODIS_vegetation" in out["dataframes"]["A"]


# --- size budget and regional monthly series -----------------------------------
def test_region_mask(data_dir):
    ds = fd.open_product("MOD13A1.061", data_dir)
    assert fd.region_mask(ds, None).all()
    mask = fd.region_mask(ds, REGION)
    assert mask.shape == (len(LAT), len(LON)) and 0 < mask.sum() < mask.size


def test_build_burned_area_respects_region(data_dir):
    ds = fd.open_product("MCD64A1.061", data_dir)
    everything = fd.build_burned_area(ds, {"title": "t"})
    clipped = fd.build_burned_area(ds, {"title": "t"}, region=box(-95, 20, -86, 35))
    assert len(clipped) < len(everything)
    assert (clipped.lon <= -86).all()


def test_estimate_download_bytes_scales_with_area_and_time():
    veg = {"MODIS_vegetation": fd.DATASETS["MODIS_vegetation"]}
    one_year = fd.estimate_download_bytes(1000, datetime(2017, 1, 1), datetime(2017, 12, 31), veg)
    assert one_year == pytest.approx(1000 / 0.2147 * 23 * 5)
    assert fd.estimate_download_bytes(2000, datetime(2017, 1, 1), datetime(2018, 12, 31), veg) == pytest.approx(
        4 * one_year, rel=0.05
    )


def test_max_burn_samples_for_bytes():
    assert fd.max_burn_samples_for_bytes(1_200_000) == 1000           # 1.2 MB / (100 B x 12 rows)
    assert fd.max_burn_samples_for_bytes(1_200_000, sensors=2) == 500
    assert fd.max_burn_samples_for_bytes(-5) == 0


def test_composite_vegetation_and_as_of(data_dir):
    grid = fd.vegetation_grid(fd.open_product("MOD13A1.061", data_dir), [0, 1, 2, 3])
    mask = np.ones((len(LAT), len(LON)), dtype=bool)
    composites = fd.composite_vegetation(grid, mask)

    assert composites["start"].dt.strftime("%m-%d").tolist() == ["07-28", "08-13", "08-29", "09-14"]
    assert (composites["end"] - composites["start"]).dt.days.eq(16).all()
    assert composites.loc[1, "ndvi_mean"] == pytest.approx(float(grid.ndvi.sel(time="2005-08-13").mean()))

    # As of 1 Sep: the 08-13 composite (ended 08-29); the 08-29 composite only ends 09-14
    as_of = fd.vegetation_as_of(composites, pd.Series(pd.to_datetime(["2005-09-01", "2005-08-01"])))
    assert as_of.loc[0, "ndvi_mean"] == pytest.approx(composites.loc[1, "ndvi_mean"])
    assert as_of.loc[0, "veg_age_days"] == 3
    assert np.isnan(as_of.loc[1, "ndvi_mean"])  # nothing had ended by 1 Aug


def manual_grid() -> xr.Dataset:
    """Four 16-day composites over a 2 x 2 grid; pixel (0, 0) is masked (NaN) in the 08-29 composite."""
    times = pd.to_datetime(["2005-07-28", "2005-08-13", "2005-08-29", "2005-09-14"])
    ndvi = np.array([[[0.1, 0.1], [0.1, 0.1]], [[0.2, 0.2], [0.2, 0.2]], [[np.nan, 0.3], [0.3, 0.3]], [[0.4, 0.4], [0.4, 0.4]]])
    return xr.Dataset(
        {"ndvi": (("time", "lat", "lon"), ndvi), "evi": (("time", "lat", "lon"), ndvi / 2)},
        coords={"time": times, "lat": [-35.0, -35.5], "lon": [-72.0, -71.5]},
    )


def points_at(dates: list[str], lat: float = -35.0, lon: float = -72.0, uncertainty=None) -> pd.DataFrame:
    when = pd.to_datetime(dates)
    return pd.DataFrame({
        "year": when.year, "quarter": when.quarter, "month": when.month, "day": when.day,
        "lat": lat, "lon": lon, "burned": 1,
        "burn_date_uncertainty": uncertainty if uncertainty is not None else np.nan,
        "lat_min": 0.0, "lat_max": 0.0, "lon_min": 0.0, "lon_max": 0.0,
    })


def test_sample_before_uses_last_observation_ending_before_the_date():
    desc = fd.DATASETS["MODIS_vegetation"]["description"]
    # 20 Aug, uncertainty 2 -> cutoff 17 Aug: only the 07-28 composite (ends 08-13) has ended
    out = fd.sample_at_burn_dates(manual_grid(), points_at(["2005-08-20"], uncertainty=2.0), 16, desc)
    assert out.ndvi[0] == pytest.approx(0.1)
    assert out.source_date[0] == pd.Timestamp("2005-07-28")
    assert out.source_age_days[0] == 7
    assert "no data from the day of burn or later" in out.attrs["description"]["time_matching"]

    # A composite ending exactly on the cutoff is allowed; one ending after it is not
    on_cutoff = fd.sample_at_burn_dates(manual_grid(), points_at(["2005-08-30"], lon=-71.5), 16, desc)
    assert on_cutoff.source_date[0] == pd.Timestamp("2005-08-13")  # ends 08-29 = 08-30 - 1 guard day


def test_sample_before_skips_masked_observations_and_respects_max_age():
    desc = fd.DATASETS["MODIS_vegetation"]["description"]
    # 20 Sep: last ended composite is 08-29 (ends 09-14), masked at pixel (0, 0) -> falls back to 08-13
    out = fd.sample_at_burn_dates(manual_grid(), points_at(["2005-09-20", "2005-09-20"]).assign(lon=[-72.0, -71.5]), 16, desc)
    assert out.ndvi.tolist() == pytest.approx([0.2, 0.3])
    assert out.source_date.dt.strftime("%m-%d").tolist() == ["08-13", "08-29"]

    old = fd.sample_at_burn_dates(manual_grid(), points_at(["2005-12-31"]), 16, desc, max_age_days=30)
    assert np.isnan(old.ndvi[0]) and pd.isna(old.source_date[0])
    with pytest.raises(ValueError):
        fd.sample_at_burn_dates(manual_grid(), points_at(["2005-09-20"]), 16, desc, timing="after")


def test_training_features_never_come_from_on_or_after_the_burn(dfs):
    veg = dfs["MODIS_vegetation_at_burn_dates"]
    burns = dfs["MODIS_burned_area"]
    dates = pd.to_datetime(dict(year=veg.year, month=veg.month, day=veg.day))
    period_end = veg.source_date + pd.Timedelta(days=16)
    margin = pd.to_timedelta(burns.burn_date_uncertainty.fillna(0) + 1, unit="D")
    matched = veg.source_date.notna()
    assert matched.any()
    assert (period_end[matched] <= (dates - margin)[matched]).all()


def test_build_regional_monthly_series_has_every_month(two_regions):
    datasets = {k: v for k, v in fd.active_datasets(STOP).items() if k in ("MODIS_burned_area", "MODIS_vegetation")}
    series = fd.build_regional_monthly_series(two_regions, datetime(2005, 7, 1), STOP, datasets=datasets)

    assert len(series) == 2 * 3                                   # 2 regions x Jul, Aug, Sep 2005
    assert series.groupby("region")["MODIS_fires"].sum().to_dict() == {"A": 3, "B": 1}
    assert series.loc[(series.region == "A") & (series.month == 7), "MODIS_fires"].item() == 0
    assert {"MODIS_burned_pixels", "MODIS_ndvi_mean", "MODIS_evi_mean", "MODIS_veg_age_days"} <= set(series.columns)
    # Vegetation as of the 1st of each month: nothing had ended by 1 Jul; by 1 Sep the 08-13 composite had
    assert series.loc[series.month == 7, "MODIS_ndvi_mean"].isna().all()
    assert (series.loc[series.month == 9, "MODIS_veg_age_days"] == 3).all()
    assert series["date"].dt.day.eq(1).all()
    assert series.attrs["description"]["update_frequency"].startswith("Monthly")


def test_open_product_decodes_appeears_julian_label_as_standard_dates(tmp_path):

    # AppEEARS writes: seconds since 2000-01-01, calendar "julian"; the values are standard dates
    seconds = (pd.to_datetime(["2012-03-01", "2012-04-01"]) - pd.Timestamp("2000-01-01")).total_seconds().astype("int32")
    ds = xr.Dataset({"Burn_Date": (("time", "lat", "lon"), np.zeros((2, 1, 1), "int16"))},
                    coords={"time": ("time", seconds, {"units": "seconds since 2000-01-01 00:00:00+00:00", "calendar": "julian"}),
                            "lat": [-35.0], "lon": [-71.0]})
    ds.to_netcdf(tmp_path / "MCD64A1.061_500m_aid0001.nc", engine="h5netcdf")
    opened = fd.open_product("MCD64A1.061", tmp_path)
    assert list(pd.DatetimeIndex(opened["time"].values)) == list(pd.to_datetime(["2012-03-01", "2012-04-01"]))
