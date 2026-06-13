"""Tests for authentication."""

from unittest.mock import MagicMock, patch

import pytest
from httpx import AsyncClient

_STRONG_PASSWORD = "Str0ng!Pass"


async def _get_token(client: AsyncClient) -> str:
    """Register a test user and return an access token."""
    password = _STRONG_PASSWORD
    await client.post("/auth/register", json={"username": "tokenuser", "password": password})
    login = await client.post("/auth/login", json={"username": "tokenuser", "password": password})
    return login.json()["access_token"]


@pytest.mark.anyio
async def test_register_success(client: AsyncClient) -> None:
    """Test successful user registration."""
    response = await client.post(
        "/auth/register", json={"username": "testuser", "password": _STRONG_PASSWORD}
    )
    assert response.status_code == 201
    data = response.json()
    assert data["username"] == "testuser"


@pytest.mark.anyio
async def test_register_duplicate(client: AsyncClient) -> None:
    """Test registration with duplicate username returns 400."""
    await client.post("/auth/register", json={"username": "dupuser", "password": _STRONG_PASSWORD})
    response = await client.post(
        "/auth/register", json={"username": "dupuser", "password": _STRONG_PASSWORD}
    )
    assert response.status_code == 400


@pytest.mark.anyio
async def test_login_success(client: AsyncClient) -> None:
    """Test successful login returns JWT tokens."""
    await client.post(
        "/auth/register", json={"username": "loginuser", "password": _STRONG_PASSWORD}
    )
    response = await client.post(
        "/auth/login", json={"username": "loginuser", "password": _STRONG_PASSWORD}
    )
    assert response.status_code == 200
    data = response.json()
    assert "access_token" in data
    assert "refresh_token" in data
    assert data["token_type"] == "bearer"


@pytest.mark.anyio
async def test_login_wrong_password(client: AsyncClient) -> None:
    """Test login with wrong password returns 401."""
    await client.post(
        "/auth/register", json={"username": "wrongpass", "password": _STRONG_PASSWORD}
    )
    response = await client.post(
        "/auth/login", json={"username": "wrongpass", "password": "Bad1!wrong"}
    )
    assert response.status_code == 401


@pytest.mark.anyio
async def test_protected_endpoint_without_token(client: AsyncClient) -> None:
    """Test accessing protected endpoint without token returns 401."""
    response = await client.get("/files/")
    assert response.status_code == 401


@pytest.mark.anyio
async def test_protected_endpoint_with_invalid_token(client: AsyncClient) -> None:
    """Test accessing protected endpoint with invalid token returns 401."""
    response = await client.get("/files/", headers={"Authorization": "Bearer invalidtoken"})
    assert response.status_code == 401


@pytest.mark.anyio
@patch("app.api.routers.files.s3_service")
async def test_protected_endpoint_with_valid_token(mock_s3: MagicMock, client: AsyncClient) -> None:
    """Test accessing protected endpoint with valid token."""
    mock_s3.list_files.return_value = []
    token = await _get_token(client)
    response = await client.get("/files/", headers={"Authorization": f"Bearer {token}"})
    assert response.status_code == 200
    assert response.json() == {"files": []}


@pytest.mark.anyio
async def test_auth_me(client: AsyncClient) -> None:
    """Test fetching current authenticated user."""
    await client.post("/auth/register", json={"username": "meuser", "password": _STRONG_PASSWORD})
    login = await client.post(
        "/auth/login", json={"username": "meuser", "password": _STRONG_PASSWORD}
    )
    token = login.json()["access_token"]
    response = await client.get("/auth/me", headers={"Authorization": f"Bearer {token}"})
    assert response.status_code == 200
    assert response.json()["username"] == "meuser"


@pytest.mark.anyio
async def test_auth_me_no_token(client: AsyncClient) -> None:
    """Test /auth/me without token returns 401."""
    response = await client.get("/auth/me")
    assert response.status_code == 401


@pytest.mark.anyio
async def test_password_policy_too_short(client: AsyncClient) -> None:
    """Test registration rejects short password."""
    response = await client.post(
        "/auth/register", json={"username": "shortpass", "password": "Short1!"}
    )
    assert response.status_code == 422


@pytest.mark.anyio
async def test_password_policy_no_uppercase(client: AsyncClient) -> None:
    """Test registration rejects password without uppercase."""
    response = await client.post(
        "/auth/register", json={"username": "noupper", "password": "lower1!pass"}
    )
    assert response.status_code == 422


@pytest.mark.anyio
async def test_password_policy_no_lowercase(client: AsyncClient) -> None:
    """Test registration rejects password without lowercase."""
    response = await client.post(
        "/auth/register", json={"username": "nolower", "password": "UPPER1!PASS"}
    )
    assert response.status_code == 422


@pytest.mark.anyio
async def test_password_policy_no_digit(client: AsyncClient) -> None:
    """Test registration rejects password without digit."""
    response = await client.post(
        "/auth/register", json={"username": "nodigit", "password": "Strong!Pass"}
    )
    assert response.status_code == 422


@pytest.mark.anyio
async def test_password_policy_no_special(client: AsyncClient) -> None:
    """Test registration rejects password without special character."""
    response = await client.post(
        "/auth/register", json={"username": "nospecial", "password": "Strong1Pass"}
    )
    assert response.status_code == 422


@pytest.mark.anyio
async def test_token_blacklist(client: AsyncClient) -> None:
    """Test revoked access token returns 401."""
    await client.post(
        "/auth/register", json={"username": "revokeuser", "password": _STRONG_PASSWORD}
    )
    login = await client.post(
        "/auth/login", json={"username": "revokeuser", "password": _STRONG_PASSWORD}
    )
    token = login.json()["access_token"]
    # Access protected endpoint successfully before logout
    response = await client.get("/auth/me", headers={"Authorization": f"Bearer {token}"})
    assert response.status_code == 200
    # Logout
    response = await client.post("/auth/logout", headers={"Authorization": f"Bearer {token}"})
    assert response.status_code == 200
    # Token should now be invalid
    response = await client.get("/auth/me", headers={"Authorization": f"Bearer {token}"})
    assert response.status_code == 401


@pytest.mark.anyio
async def test_brute_force_lockout(client: AsyncClient) -> None:
    """Test account locks after 5 failed attempts."""
    username = "bruteuser"
    await client.post("/auth/register", json={"username": username, "password": _STRONG_PASSWORD})
    # 5 failed attempts from different IPs to avoid rate limiter
    for i in range(5):
        response = await client.post(
            "/auth/login",
            json={"username": username, "password": "Wrong1!Pass"},
            headers={"X-Forwarded-For": f"10.0.0.{i}"},
        )
        assert response.status_code == 401
    # 6th attempt should be locked (different IP so rate limiter doesn't interfere)
    response = await client.post(
        "/auth/login",
        json={"username": username, "password": "Wrong1!Pass"},
        headers={"X-Forwarded-For": "10.0.0.99"},
    )
    assert response.status_code == 429
    # Even correct password should be rejected while locked
    response = await client.post(
        "/auth/login",
        json={"username": username, "password": _STRONG_PASSWORD},
        headers={"X-Forwarded-For": "10.0.0.100"},
    )
    assert response.status_code == 429


@pytest.mark.anyio
async def test_rate_limit_register(client: AsyncClient) -> None:
    """Test register rate limit returns 429 after threshold."""
    base = "rlreg"
    # 3 requests per hour allowed
    for i in range(3):
        response = await client.post(
            "/auth/register",
            json={"username": f"{base}{i}", "password": _STRONG_PASSWORD},
        )
        assert response.status_code == 201
    # 4th request should be rate limited
    response = await client.post(
        "/auth/register",
        json={"username": f"{base}3", "password": _STRONG_PASSWORD},
    )
    assert response.status_code == 429
