from fastapi.testclient import TestClient

from src.main import app


client = TestClient(app)


def test_root_returns_welcome_message():
    response = client.get("/")

    assert response.status_code == 200
    body = response.json()
    assert body["success"] is True
    assert body["data"]["message"] == "Welcome to firesystem-backend"
