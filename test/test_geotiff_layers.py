from datetime import datetime

import numpy as np
import pytest
import tifffile

from src.utils import geotiff_layers as gl


def write_geotiff(path, values, west=-72.0, north=-35.0, step=0.5, nodata=None):
    extratags = [(33550, "d", 3, (step, step, 0.0)), (33922, "d", 6, (0.0, 0.0, 0.0, west, north, 0.0))]
    if nodata is not None:
        extratags.append((42113, "s", 0, str(nodata), True))
    tifffile.imwrite(path, values, extratags=extratags, compression="lzw")


def test_file_date_reads_appeears_doy():
    assert gl.file_date("MCD15A3H.061_Lai_500m_doy2012061000000_aid0001.tif") == datetime(2012, 3, 1)
    with pytest.raises(ValueError):
        gl.file_date("README.md")


def test_read_geotiff_returns_pixel_centres_and_nodata(tmp_path):
    path = tmp_path / "a_Lai_500m_doy2012061_aid0001.tif"
    write_geotiff(path, np.arange(6, dtype="uint8").reshape(2, 3), nodata=255)
    values, lat, lon, nodata = gl.read_geotiff(path)
    assert values.shape == (2, 3) and nodata == 255
    assert lat.tolist() == [-35.25, -35.75]
    assert lon.tolist() == [-71.75, -71.25, -70.75]


def test_layer_files_sorted_by_date(tmp_path):
    for doy in (65, 61, 69):
        write_geotiff(tmp_path / f"MCD15A3H.061_Lai_500m_doy2012{doy:03d}000000_aid0001.tif", np.zeros((1, 1), "uint8"))
    write_geotiff(tmp_path / "MCD15A3H.061_FparLai_QC_doy2012061000000_aid0001.tif", np.zeros((1, 1), "uint8"))
    assert [gl.file_date(p).day for p in gl.layer_files(tmp_path, "Lai_500m")] == [1, 5, 9]


def test_lai_quality_mask_keeps_main_algorithm():
    qc = np.array([0, 2, 32, 34, 64, 157, 255], dtype="uint8")  # SCF_QC = bits 5-7
    assert gl.lai_quality_mask(qc).tolist() == [True, True, True, True, False, False, False]


def test_build_and_open_lai_masks_fill_and_poor_quality(tmp_path):
    write_geotiff(tmp_path / "MCD15A3H.061_Lai_500m_doy2012061000000_aid0001.tif", np.array([[30, 250], [12, 40]], "uint8"), nodata=255)
    write_geotiff(tmp_path / "MCD15A3H.061_FparLai_QC_doy2012061000000_aid0001.tif", np.array([[0, 0], [64, 0]], "uint8"), nodata=255)
    write_geotiff(tmp_path / "MCD15A3H.061_Lai_500m_doy2012065000000_aid0001.tif", np.array([[10, 20], [30, 255]], "uint8"), nodata=255)

    lai = gl.open_lai(tmp_path)  # builds the NetCDF stack on first use
    assert (tmp_path / gl.LAI_NETCDF).exists()
    values = lai["lai"].values
    assert lai["time"].dt.day.values.tolist() == [1, 5]
    np.testing.assert_allclose(values[0], [[3.0, np.nan], [np.nan, 4.0]])  # 250 non-vegetated, QC 64 backup algorithm
    np.testing.assert_allclose(values[1], [[1.0, 2.0], [3.0, np.nan]])     # no QC file: valid range only


def test_build_lai_netcdf_rejects_mixed_grids(tmp_path):
    write_geotiff(tmp_path / "x_Lai_500m_doy2012061_aid0001.tif", np.zeros((2, 2), "uint8"))
    write_geotiff(tmp_path / "x_Lai_500m_doy2012065_aid0001.tif", np.zeros((2, 2), "uint8"), west=-60.0)
    with pytest.raises(ValueError, match="different grid"):
        gl.build_lai_netcdf(tmp_path)
    with pytest.raises(FileNotFoundError):
        gl.build_lai_netcdf(tmp_path / "missing")


def test_open_elevation_masks_voids_and_computes_slope(tmp_path):
    dem = np.array([[0, 0, 0], [100, 100, 100], [-32768, 200, 200]], dtype="int16")
    write_geotiff(tmp_path / "SRTMGL3_NC.003_SRTMGL3_DEM_doy2000042000000_aid0001.tif", dem, step=0.01, nodata=-32768)
    terrain = gl.open_elevation(tmp_path)
    assert np.isnan(terrain["elevation_m"].values[2, 0])
    assert terrain["elevation_m"].values[1, 1] == 100
    # 100 m rise over ~1.1 km north-south -> about 5 degrees
    assert terrain["slope_deg"].values[1, 1] == pytest.approx(np.degrees(np.arctan(100 / 1113.2)), rel=0.01)


def test_slope_degrees_flat_is_zero():
    assert np.all(gl.slope_degrees(np.full((3, 3), 50.0), np.array([-35.0, -35.01, -35.02]), np.array([-71.0, -70.99, -70.98])) == 0)
