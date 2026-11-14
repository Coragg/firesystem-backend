from fastapi.testclient import TestClient
from pydantic import BaseModel

from src.main import app
from src.models.aoi import PolygonAoi
from src.utils.errors import ExportTooLarge


class AoiRequest(BaseModel):
    aoi: PolygonAoi


@app.get("/_test/export-too-large")
def _export_too_large() -> None:
    raise ExportTooLarge("too many rows")


@app.get("/_test/crash")
def _crash() -> None:
    raise RuntimeError("secret internals")


@app.post("/_test/aoi")
def _aoi(body: AoiRequest) -> dict:
    return {"ok": True}


@app.get("/_test/number")
def _number(n: int) -> dict:
    return {"n": n}


client = TestClient(app, raise_server_exceptions=False)


def test_app_error_uses_envelope():
    response = client.get("/_test/export-too-large")

    body = response.json()
    assert response.status_code == 413
    assert body["success"] is False
    assert body["error"]["code"] == "EXPORT_TOO_LARGE"
    assert body["error"]["request_id"] == response.headers["X-Request-ID"]


def test_unhandled_error_hides_details():
    response = client.get("/_test/crash")

    assert response.status_code == 500
    assert response.json()["error"]["code"] == "INTERNAL"
    assert "secret internals" not in response.text
    assert len(response.headers["X-Request-ID"]) == 12


def test_request_id_header_on_success():
    response = client.get("/")

    assert len(response.headers["X-Request-ID"]) == 12


def test_invalid_aoi_maps_to_aoi_invalid():
    swapped = [[37.7, -122.4], [37.8, -122.4], [37.8, -122.3], [37.7, -122.3]]

    response = client.post("/_test/aoi", json={"aoi": {"type": "Polygon", "coordinates": [swapped]}})

    error = response.json()["error"]
    assert response.status_code == 422
    assert error["code"] == "AOI_INVALID"
    assert "aoi" in error["details"][0]["field"]
    assert "GeoJSON uses [lon, lat]" in error["details"][0]["problem"]


def test_other_invalid_request_maps_to_invalid_request():
    response = client.get("/_test/number", params={"n": "abc"})

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "INVALID_REQUEST"
