"""Tests for health check endpoint."""

from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)


def test_health_check() -> None:
    """Test that health endpoint returns ok status."""
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}
