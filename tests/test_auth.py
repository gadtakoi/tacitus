"""Tests for authentication."""

from unittest.mock import MagicMock, patch

from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)

_STRONG_PASSWORD = "Str0ng!Pass"


def test_register_success() -> None:
    """Test successful user registration."""
    response = client.post(
        "/auth/register", json={"username": "testuser", "password": _STRONG_PASSWORD}
    )
    assert response.status_code == 201
    data = response.json()
    assert data["username"] == "testuser"


def test_register_duplicate() -> None:
    """Test registration with duplicate username."""
    client.post("/auth/register", json={"username": "dupuser", "password": _STRONG_PASSWORD})
    response = client.post(
        "/auth/register", json={"username": "dupuser", "password": _STRONG_PASSWORD}
    )
    assert response.status_code == 400


def test_login_success() -> None:
    """Test successful login returns JWT."""
    client.post("/auth/register", json={"username": "loginuser", "password": _STRONG_PASSWORD})
    response = client.post(
        "/auth/login", json={"username": "loginuser", "password": _STRONG_PASSWORD}
    )
    assert response.status_code == 200
    data = response.json()
    assert "access_token" in data
    assert "refresh_token" in data
    assert data["token_type"] == "bearer"


def test_login_wrong_password() -> None:
    """Test login with wrong password returns 401."""
    client.post("/auth/register", json={"username": "wrongpass", "password": _STRONG_PASSWORD})
    response = client.post("/auth/login", json={"username": "wrongpass", "password": "Bad1!wrong"})
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
    client.post("/auth/register", json={"username": "tokenuser", "password": _STRONG_PASSWORD})
    login = client.post("/auth/login", json={"username": "tokenuser", "password": _STRONG_PASSWORD})
    token = login.json()["access_token"]
    response = client.get("/files/", headers={"Authorization": f"Bearer {token}"})
    assert response.status_code == 200
    assert response.json() == {"files": []}


def test_auth_me() -> None:
    """Test fetching current authenticated user."""
    client.post("/auth/register", json={"username": "meuser", "password": _STRONG_PASSWORD})
    login = client.post("/auth/login", json={"username": "meuser", "password": _STRONG_PASSWORD})
    token = login.json()["access_token"]
    response = client.get("/auth/me", headers={"Authorization": f"Bearer {token}"})
    assert response.status_code == 200
    assert response.json()["username"] == "meuser"


def test_auth_me_no_token() -> None:
    """Test /auth/me without token returns 401."""
    response = client.get("/auth/me")
    assert response.status_code == 401


def test_password_policy_too_short() -> None:
    """Test registration rejects short password."""
    response = client.post("/auth/register", json={"username": "shortpass", "password": "Short1!"})
    assert response.status_code == 422


def test_password_policy_no_uppercase() -> None:
    """Test registration rejects password without uppercase."""
    response = client.post(
        "/auth/register", json={"username": "noupper", "password": "lower1!pass"}
    )
    assert response.status_code == 422


def test_password_policy_no_lowercase() -> None:
    """Test registration rejects password without lowercase."""
    response = client.post(
        "/auth/register", json={"username": "nolower", "password": "UPPER1!PASS"}
    )
    assert response.status_code == 422


def test_password_policy_no_digit() -> None:
    """Test registration rejects password without digit."""
    response = client.post(
        "/auth/register", json={"username": "nodigit", "password": "Strong!Pass"}
    )
    assert response.status_code == 422


def test_password_policy_no_special() -> None:
    """Test registration rejects password without special character."""
    response = client.post(
        "/auth/register", json={"username": "nospecial", "password": "Strong1Pass"}
    )
    assert response.status_code == 422


def test_token_blacklist() -> None:
    """Test revoked token returns 401."""
    client.post("/auth/register", json={"username": "revokeuser", "password": _STRONG_PASSWORD})
    login = client.post(
        "/auth/login", json={"username": "revokeuser", "password": _STRONG_PASSWORD}
    )
    token = login.json()["access_token"]
    # Access protected endpoint successfully before logout
    response = client.get("/auth/me", headers={"Authorization": f"Bearer {token}"})
    assert response.status_code == 200
    # Logout
    response = client.post("/auth/logout", headers={"Authorization": f"Bearer {token}"})
    assert response.status_code == 200
    # Token should now be invalid
    response = client.get("/auth/me", headers={"Authorization": f"Bearer {token}"})
    assert response.status_code == 401


def test_brute_force_lockout() -> None:
    """Test account locks after 5 failed attempts."""
    username = "bruteuser"
    client.post("/auth/register", json={"username": username, "password": _STRONG_PASSWORD})
    # 5 failed attempts from different IPs to avoid rate limiter
    for i in range(5):
        response = client.post(
            "/auth/login",
            json={"username": username, "password": "Wrong1!Pass"},
            headers={"X-Forwarded-For": f"10.0.0.{i}"},
        )
        assert response.status_code == 401
    # 6th attempt should be locked (different IP so rate limiter doesn't interfere)
    response = client.post(
        "/auth/login",
        json={"username": username, "password": "Wrong1!Pass"},
        headers={"X-Forwarded-For": "10.0.0.99"},
    )
    assert response.status_code == 429
    # Even correct password should be rejected while locked
    response = client.post(
        "/auth/login",
        json={"username": username, "password": _STRONG_PASSWORD},
        headers={"X-Forwarded-For": "10.0.0.100"},
    )
    assert response.status_code == 429


def test_rate_limit_register() -> None:
    """Test register rate limit returns 429 after threshold."""
    base = "rlreg"
    # 3 requests per hour allowed
    for i in range(3):
        response = client.post(
            "/auth/register",
            json={"username": f"{base}{i}", "password": _STRONG_PASSWORD},
        )
        assert response.status_code == 201
    # 4th request should be rate limited
    response = client.post(
        "/auth/register",
        json={"username": f"{base}3", "password": _STRONG_PASSWORD},
    )
    assert response.status_code == 429
