"""Tests for hello world endpoint."""

from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)


def test_hello_world_status_code() -> None:
    """Test that hello world endpoint returns 200 OK."""
    response = client.get("/")
    assert response.status_code == 200


def test_hello_world_response_body() -> None:
    """Test that hello world endpoint returns correct JSON."""
    response = client.get("/")
    data = response.json()
    assert data == {"message": "Hello World"}


def test_hello_world_response_model() -> None:
    """Test that hello world response conforms to contract."""
    response = client.get("/")
    data = response.json()
    assert "message" in data
    assert isinstance(data["message"], str)
    assert len(data["message"]) > 0


def test_hello_world_request_id_header() -> None:
    """Test that response includes x-request-id header."""
    response = client.get("/")
    assert "x-request-id" in response.headers
    assert len(response.headers["x-request-id"]) > 0


def test_hello_world_custom_request_id() -> None:
    """Test that custom x-request-id header is preserved."""
    custom_id = "test-request-123"
    response = client.get("/", headers={"x-request-id": custom_id})
    assert response.headers["x-request-id"] == custom_id
