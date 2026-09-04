"""Tests for logout authorization and rate limiting.

Logout revoked a refresh token by hash alone, without checking who it belonged
to: a session presenting somebody else's refresh token retired it, logging that
user out. The endpoint also carried no rate limit, unlike every other mutating
auth endpoint.
"""

import pytest
from httpx import AsyncClient

_PASSWORD = "Str0ng!Pass"


async def _session_for(client: AsyncClient, username: str) -> dict[str, str]:
    """Register a user and return their token pair.

    Args:
        client: Async HTTP client bound to the application.
        username: Username to create.

    Returns:
        Mapping with ``access_token`` and ``refresh_token``.
    """
    body = {"username": username, "password": _PASSWORD}
    await client.post("/auth/register", json=body)
    login = await client.post("/auth/login", json=body)
    return login.json()


async def _refresh_still_works(client: AsyncClient, refresh_token: str) -> bool:
    """Check whether a refresh token can still be exchanged.

    Args:
        client: Async HTTP client bound to the application.
        refresh_token: Token to try.

    Returns:
        True while the token is still usable.
    """
    response = await client.post("/auth/refresh", json={"refresh_token": refresh_token})
    return response.status_code == 200


@pytest.mark.anyio
async def test_logout_does_not_revoke_another_users_refresh_token(
    client: AsyncClient,
    reset_rate_limits,
) -> None:
    """A session cannot log another account out.

    Mallory holds a valid session of her own and presents Alice's refresh
    token. Alice's session must survive.
    """
    alice = await _session_for(client, "alice")
    reset_rate_limits()
    mallory = await _session_for(client, "mallory")
    reset_rate_limits()

    response = await client.post(
        "/auth/logout",
        headers={"Authorization": f"Bearer {mallory['access_token']}"},
        json={"refresh_token": alice["refresh_token"]},
    )
    reset_rate_limits()

    assert response.status_code == 200
    assert await _refresh_still_works(client, alice["refresh_token"]), (
        "logout revoked a refresh token belonging to a different account"
    )


@pytest.mark.anyio
async def test_logout_revokes_the_callers_own_refresh_token(
    client: AsyncClient,
    reset_rate_limits,
) -> None:
    """The ownership check does not get in the way of a normal logout."""
    session = await _session_for(client, "owner")
    reset_rate_limits()

    response = await client.post(
        "/auth/logout",
        headers={"Authorization": f"Bearer {session['access_token']}"},
        json={"refresh_token": session["refresh_token"]},
    )
    reset_rate_limits()

    assert response.status_code == 200
    assert not await _refresh_still_works(client, session["refresh_token"])


@pytest.mark.anyio
async def test_logout_still_works_when_the_access_token_cannot_be_decoded(
    client: AsyncClient,
    reset_rate_limits,
) -> None:
    """Possession of the refresh token is enough to retire it.

    Ownership cannot be established from an undecodable access token. Refusing
    here would strand a client whose access token has expired, so the token is
    revoked on proof of possession — the limitation is documented in
    ``_revoke_own_refresh_token``.
    """
    session = await _session_for(client, "expired")
    reset_rate_limits()

    response = await client.post(
        "/auth/logout",
        headers={"Authorization": "Bearer not-a-real-token"},
        json={"refresh_token": session["refresh_token"]},
    )
    reset_rate_limits()

    assert response.status_code == 200
    assert not await _refresh_still_works(client, session["refresh_token"])


@pytest.mark.anyio
async def test_logout_is_rate_limited(client: AsyncClient, reset_rate_limits) -> None:
    """Logout is bounded like the other mutating auth endpoints."""
    session = await _session_for(client, "flooder")
    reset_rate_limits()

    headers = {"Authorization": f"Bearer {session['access_token']}"}
    body = {"refresh_token": session["refresh_token"]}
    statuses = [
        (await client.post("/auth/logout", headers=headers, json=body)).status_code
        for _ in range(12)
    ]

    assert 429 in statuses, "POST /auth/logout accepted more than its per-minute limit"


@pytest.mark.anyio
async def test_auth_me_is_rate_limited(client: AsyncClient, reset_rate_limits) -> None:
    """``GET /auth/me`` is bounded too.

    It was the second auth endpoint without a limit; the audit note claimed
    logout was the only one.
    """
    session = await _session_for(client, "reader")
    reset_rate_limits()

    headers = {"Authorization": f"Bearer {session['access_token']}"}
    statuses = [(await client.get("/auth/me", headers=headers)).status_code for _ in range(65)]

    assert statuses[0] == 200
    assert 429 in statuses, "GET /auth/me accepted more than its per-minute limit"
