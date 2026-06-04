"""Tests for authentication."""

from unittest.mock import MagicMock, patch

from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)


def test_register_success() -> None:
    """Test successful user registration."""
    response = client.post("/auth/register", params={"username": "testuser", "password": "secret"})
    assert response.status_code == 201
    data = response.json()
    assert data["username"] == "testuser"


def test_register_duplicate() -> None:
    """Test registration with duplicate username."""
    client.post("/auth/register", params={"username": "dupuser", "password": "secret"})
    response = client.post("/auth/register", params={"username": "dupuser", "password": "secret"})
    assert response.status_code == 400


def test_login_success() -> None:
    """Test successful login returns JWT."""
    client.post("/auth/register", params={"username": "loginuser", "password": "secret"})
    response = client.post("/auth/login", params={"username": "loginuser", "password": "secret"})
    assert response.status_code == 200
    data = response.json()
    assert "access_token" in data
    assert data["token_type"] == "bearer"


def test_login_wrong_password() -> None:
    """Test login with wrong password returns 401."""
    client.post("/auth/register", params={"username": "wrongpass", "password": "secret"})
    response = client.post("/auth/login", params={"username": "wrongpass", "password": "bad"})
    assert response.status_code == 401


def test_protected_endpoint_without_token() -> None:
    """Test accessing protected endpoint without token returns 401."""
    response = client.get("/files/")
    assert response.status_code == 401


def test_protected_endpoint_with_invalid_token() -> None:
    """Test accessing protected endpoint with invalid token returns 401."""
    response = client.get("/files/", headers={"Authorization": "Bearer invalidtoken"})
    assert response.status_code == 401


@patch("app.api.routers.files.s3_service")
def test_protected_endpoint_with_valid_token(mock_s3: MagicMock) -> None:
    """Test accessing protected endpoint with valid token."""
    mock_s3.list_files.return_value = []
    client.post("/auth/register", params={"username": "tokenuser", "password": "secret"})
    login = client.post("/auth/login", params={"username": "tokenuser", "password": "secret"})
    token = login.json()["access_token"]
    response = client.get("/files/", headers={"Authorization": f"Bearer {token}"})
    assert response.status_code == 200
    assert response.json() == {"files": []}


def test_auth_me() -> None:
    """Test fetching current authenticated user."""
    client.post("/auth/register", params={"username": "meuser", "password": "secret"})
    login = client.post("/auth/login", params={"username": "meuser", "password": "secret"})
    token = login.json()["access_token"]
    response = client.get("/auth/me", headers={"Authorization": f"Bearer {token}"})
    assert response.status_code == 200
    assert response.json()["username"] == "meuser"


def test_auth_me_no_token() -> None:
    """Test /auth/me without token returns 401."""
    response = client.get("/auth/me")
    assert response.status_code == 401
