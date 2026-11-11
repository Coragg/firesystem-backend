from fastapi.testclient import TestClient

from src.main import app


client = TestClient(app)


def test_root_returns_welcome_message():
    response = client.get("/")

    assert response.status_code == 200
    assert response.json() == {
        "success": True,
        "data": {"message": "Welcome to NASA"},
    }
