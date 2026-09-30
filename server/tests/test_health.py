from fastapi.testclient import TestClient

from livesubs.app import create_app
from livesubs.config import Settings


def test_health_returns_ok() -> None:
    client = TestClient(create_app(Settings(asr_backend="none")))
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json()["status"] == "ok"
