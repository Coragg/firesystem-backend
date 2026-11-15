from fastapi import HTTPException
from fastapi.testclient import TestClient
from pydantic import BaseModel

from src.main import app
from src.models.aoi import PolygonAoi


class AoiRequest(BaseModel):
    aoi: PolygonAoi


@app.get("/_test/too-large")
def _too_large() -> None:
    raise HTTPException(status_code=413, detail="too many rows")


@app.get("/_test/crash")
def _crash() -> None:
    raise RuntimeError("secret internals")


@app.post("/_test/aoi")
def _aoi(body: AoiRequest) -> dict:
    return {"ok": True}


client = TestClient(app, raise_server_exceptions=False)


def test_http_exception_uses_detail_format_with_request_id():
    response = client.get("/_test/too-large")

    assert response.status_code == 413
    assert response.json() == {"detail": "too many rows"}
    assert len(response.headers["X-Request-ID"]) == 12


def test_unhandled_error_hides_details_and_uses_detail_format():
    response = client.get("/_test/crash")

    assert response.status_code == 500
    assert response.json() == {"detail": "Internal server error"}
    assert "secret internals" not in response.text
    assert len(response.headers["X-Request-ID"]) == 12


def test_request_id_header_on_success():
    response = client.get("/")

    assert len(response.headers["X-Request-ID"]) == 12


def test_invalid_aoi_is_a_standard_validation_error_with_the_hint():
    swapped = [[37.7, -122.4], [37.8, -122.4], [37.8, -122.3], [37.7, -122.3]]

    response = client.post("/_test/aoi", json={"aoi": {"type": "Polygon", "coordinates": [swapped]}})

    problem = response.json()["detail"][0]
    assert response.status_code == 422
    assert "aoi" in problem["loc"]
    assert "GeoJSON uses [lon, lat]" in problem["msg"]
