"""Tests for CORS configuration."""

from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)


def test_cors_preflight_allowed() -> None:
    """Test preflight request from allowed origin succeeds."""
    response = client.options(
        "/health",
        headers={
            "Origin": "http://localhost:3000",
            "Access-Control-Request-Method": "GET",
        },
    )
    assert response.status_code == 200
    assert response.headers.get("access-control-allow-origin") == "http://localhost:3000"


def test_cors_preflight_rejected() -> None:
    """Test preflight request from unknown origin is rejected."""
    response = client.options(
        "/health",
        headers={
            "Origin": "http://evil.com",
            "Access-Control-Request-Method": "GET",
        },
    )
    assert response.status_code == 400
    assert "access-control-allow-origin" not in response.headers
