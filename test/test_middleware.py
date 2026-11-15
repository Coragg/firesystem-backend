from fastapi.testclient import TestClient

from src.main import app

client = TestClient(app)


def test_openapi_schema_builds_for_every_route():
    response = client.get("/openapi.json")

    assert response.status_code == 200
    assert {"/api/v1/calendar", "/api/v1/hotspots", "/api/v1/hotspots/export", "/api/v1/sources", "/api/v1/health"} <= set(
        response.json()["paths"]
    )


def test_cors_allows_the_local_frontend():
    response = client.options(
        "/api/v1/health",
        headers={"Origin": "http://localhost:5173", "Access-Control-Request-Method": "GET"},
    )

    assert response.headers["access-control-allow-origin"] == "http://localhost:5173"


def test_cors_rejects_other_origins():
    response = client.options(
        "/api/v1/health",
        headers={"Origin": "http://evil.test", "Access-Control-Request-Method": "GET"},
    )

    assert "access-control-allow-origin" not in response.headers


def test_cors_exposes_request_id_to_the_browser():
    response = client.get("/", headers={"Origin": "http://localhost:5173"})

    assert response.headers["access-control-expose-headers"] == "X-Request-ID"
