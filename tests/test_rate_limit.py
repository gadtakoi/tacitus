"""Tests for rate limiting.

Rate-limit counters use the ``memory://`` backend in tests, so each test
starts with a clean state thanks to ``reset_global_state``.
"""

import pytest
from httpx import AsyncClient

_STRONG_PASSWORD = "Str0ng!Pass"


@pytest.mark.anyio
async def test_rate_limit_register(client: AsyncClient) -> None:
    """Register endpoint allows 3 requests per hour then returns 429."""
    base = "rlreg"
    for i in range(3):
        response = await client.post(
            "/auth/register",
            json={"username": f"{base}{i}", "password": _STRONG_PASSWORD},
        )
        assert response.status_code == 201

    response = await client.post(
        "/auth/register",
        json={"username": f"{base}3", "password": _STRONG_PASSWORD},
    )
    assert response.status_code == 429
    assert response.json()["error"] == "Rate limit exceeded"


@pytest.mark.anyio
async def test_rate_limit_login(client: AsyncClient) -> None:
    """Login endpoint allows 5 requests per minute then returns 429."""
    username = "rllogin"
    await client.post("/auth/register", json={"username": username, "password": _STRONG_PASSWORD})
    for _ in range(5):
        response = await client.post(
            "/auth/login",
            json={"username": username, "password": "Wrong1!Pass"},
        )
        assert response.status_code == 401

    response = await client.post(
        "/auth/login",
        json={"username": username, "password": "Wrong1!Pass"},
    )
    assert response.status_code == 429


@pytest.mark.anyio
async def test_rate_limit_per_ip(client: AsyncClient) -> None:
    """Rate limits are applied per IP when no user is authenticated."""
    # Two different IPs should have independent counters on the health endpoint,
    # which is not rate-limited. Use register endpoint instead: unauthenticated
    # requests are keyed by IP.
    base = "rlip"
    for i in range(3):
        response = await client.post(
            "/auth/register",
            json={"username": f"{base}a{i}", "password": _STRONG_PASSWORD},
            headers={"X-Forwarded-For": "10.0.0.1"},
        )
        assert response.status_code == 201

    # Same IP is now rate limited.
    response = await client.post(
        "/auth/register",
        json={"username": f"{base}a3", "password": _STRONG_PASSWORD},
        headers={"X-Forwarded-For": "10.0.0.1"},
    )
    assert response.status_code == 429

    # Different IP is still allowed.
    response = await client.post(
        "/auth/register",
        json={"username": f"{base}b0", "password": _STRONG_PASSWORD},
        headers={"X-Forwarded-For": "10.0.0.2"},
    )
    assert response.status_code == 201
