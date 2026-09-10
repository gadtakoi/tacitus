"""Tests for authentication."""

from unittest.mock import AsyncMock, MagicMock, patch

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
    mock_s3.list_files = AsyncMock(return_value=([], None))
    token = await _get_token(client)
    response = await client.get("/files/", headers={"Authorization": f"Bearer {token}"})
    assert response.status_code == 200
    assert response.json() == {"files": [], "next_cursor": None}


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
    refresh_token = login.json()["refresh_token"]
    # Access protected endpoint successfully before logout
    response = await client.get("/auth/me", headers={"Authorization": f"Bearer {token}"})
    assert response.status_code == 200
    # Logout
    response = await client.post(
        "/auth/logout",
        json={"refresh_token": refresh_token},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert response.status_code == 200
    # Token should now be invalid
    response = await client.get("/auth/me", headers={"Authorization": f"Bearer {token}"})
    assert response.status_code == 401


@pytest.mark.anyio
async def test_logout_revokes_refresh_token(client: AsyncClient) -> None:
    """Logout must revoke the refresh token so it can no longer be exchanged."""
    await client.post(
        "/auth/register", json={"username": "revokeusr2", "password": _STRONG_PASSWORD}
    )
    login = await client.post(
        "/auth/login", json={"username": "revokeusr2", "password": _STRONG_PASSWORD}
    )
    token = login.json()["access_token"]
    refresh_token = login.json()["refresh_token"]

    # Refresh works before logout
    response = await client.post("/auth/refresh", json={"refresh_token": refresh_token})
    assert response.status_code == 200

    # Logout
    response = await client.post(
        "/auth/logout",
        json={"refresh_token": refresh_token},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert response.status_code == 200

    # Refresh token must be rejected after logout
    response = await client.post("/auth/refresh", json={"refresh_token": refresh_token})
    assert response.status_code == 401


@pytest.mark.anyio
async def test_refresh_rotates_token(client: AsyncClient) -> None:
    """Refresh must issue a NEW refresh token instead of returning the old one."""
    await client.post(
        "/auth/register", json={"username": "rotateuser", "password": _STRONG_PASSWORD}
    )
    login = await client.post(
        "/auth/login", json={"username": "rotateuser", "password": _STRONG_PASSWORD}
    )
    old_refresh = login.json()["refresh_token"]

    response = await client.post("/auth/refresh", json={"refresh_token": old_refresh})
    assert response.status_code == 200
    new_refresh = response.json()["refresh_token"]
    assert new_refresh != old_refresh

    # The new refresh token must be usable for the next rotation
    response = await client.post("/auth/refresh", json={"refresh_token": new_refresh})
    assert response.status_code == 200


@pytest.mark.anyio
async def test_refresh_reuse_detected_revokes_family(client: AsyncClient) -> None:
    """Presenting an already rotated token must revoke the whole token family."""
    await client.post(
        "/auth/register", json={"username": "reuseuser", "password": _STRONG_PASSWORD}
    )
    login = await client.post(
        "/auth/login", json={"username": "reuseuser", "password": _STRONG_PASSWORD}
    )
    old_refresh = login.json()["refresh_token"]

    # First use rotates the token
    response = await client.post("/auth/refresh", json={"refresh_token": old_refresh})
    assert response.status_code == 200
    new_refresh = response.json()["refresh_token"]

    # Replaying the OLD (already rotated) token must fail...
    response = await client.post("/auth/refresh", json={"refresh_token": old_refresh})
    assert response.status_code == 401

    # ...and the reuse must have killed the NEW token as well
    response = await client.post("/auth/refresh", json={"refresh_token": new_refresh})
    assert response.status_code == 401


async def _fail_login(client: AsyncClient, username: str, ip: str) -> int:
    """Attempt a login with a wrong password from the given IP.

    Args:
        client: HTTP client bound to the app.
        username: Account to target.
        ip: Value sent as ``X-Forwarded-For``.

    Returns:
        HTTP status code of the response.
    """
    response = await client.post(
        "/auth/login",
        json={"username": username, "password": "Wrong1!Pass"},
        headers={"X-Forwarded-For": ip},
    )
    return response.status_code


@pytest.mark.anyio
async def test_brute_force_locks_the_offending_ip(client: AsyncClient, reset_rate_limits) -> None:
    """5 failures from one IP lock that (username, IP) pair."""
    username = "brute1"
    await client.post("/auth/register", json={"username": username, "password": _STRONG_PASSWORD})

    for _ in range(5):
        assert await _fail_login(client, username, "10.0.0.1") == 401

    # The per-IP rate limit (5/min) is also exhausted now and answers 429 too;
    # clear it so the assertion below can only be the brute-force lock.
    reset_rate_limits()

    response = await client.post(
        "/auth/login",
        json={"username": username, "password": _STRONG_PASSWORD},
        headers={"X-Forwarded-For": "10.0.0.1"},
    )
    assert response.status_code == 429
    assert "locked" in response.json()["detail"].lower()


@pytest.mark.anyio
async def test_brute_force_does_not_lock_out_other_ips(
    client: AsyncClient,
    reset_rate_limits,
) -> None:
    """The per-IP threshold must not let an attacker lock the real user out.

    This is why the counter is keyed on ``(username, ip)`` rather than on
    ``username`` alone: failures sprayed from one address must leave the
    legitimate user's own address able to log in.
    """
    username = "brute2"
    await client.post("/auth/register", json={"username": username, "password": _STRONG_PASSWORD})

    for _ in range(5):
        assert await _fail_login(client, username, "10.0.0.1") == 401

    reset_rate_limits()

    response = await client.post(
        "/auth/login",
        json={"username": username, "password": _STRONG_PASSWORD},
        headers={"X-Forwarded-For": "10.0.0.2"},
    )
    assert response.status_code == 200


@pytest.mark.anyio
async def test_brute_force_global_threshold_locks_the_account(
    client: AsyncClient,
    reset_rate_limits,
) -> None:
    """20 failures spread across many IPs lock the account entirely.

    Backstop against credential stuffing: no single IP reaches the per-IP
    threshold, so only the global counter can trigger here.
    """
    username = "brute3"
    await client.post("/auth/register", json={"username": username, "password": _STRONG_PASSWORD})

    for i in range(20):
        assert await _fail_login(client, username, f"10.1.0.{i}") == 401

    reset_rate_limits()

    response = await client.post(
        "/auth/login",
        json={"username": username, "password": _STRONG_PASSWORD},
        headers={"X-Forwarded-For": "10.1.9.9"},
    )
    assert response.status_code == 429
    assert "locked" in response.json()["detail"].lower()


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
