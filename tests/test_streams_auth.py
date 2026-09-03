"""Tests for authentication against the MediaMTX Control API.

MediaMTX grants the ``api`` action to loopback addresses only by default, so a
backend reaching it over the Docker network is answered with 401 unless it
presents credentials. Before those credentials were added, ``GET /streams``
failed against a real deployment while every unit test still passed — nothing
asserted that the outgoing request was authenticated.
"""

from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest
from httpx import AsyncClient

from app.core.config import settings

_STRONG_PASSWORD = "Str0ng!Pass"

_PATHS_LIST_PAYLOAD = {
    "pageCount": 1,
    "itemCount": 1,
    "items": [
        {
            "name": "cam1",
            "confName": "cam1",
            "source": None,
            "ready": False,
            "tracks": [],
            "readers": [],
        },
    ],
}


async def _get_token(client: AsyncClient) -> str:
    """Register a test user and return an access token.

    Args:
        client: Async HTTP client bound to the application.

    Returns:
        Access token for the created user.
    """
    password = _STRONG_PASSWORD
    await client.post("/auth/register", json={"username": "streamuser", "password": password})
    login = await client.post("/auth/login", json={"username": "streamuser", "password": password})
    return login.json()["access_token"]


def _mock_client(response: httpx.Response) -> MagicMock:
    """Build an ``httpx.AsyncClient`` stub usable as an async context manager.

    Args:
        response: Response the stubbed ``get`` call should return.

    Returns:
        Mock replacement for ``httpx.AsyncClient``.
    """
    instance = MagicMock()
    instance.get = AsyncMock(return_value=response)
    ctx = MagicMock()
    ctx.__aenter__ = AsyncMock(return_value=instance)
    ctx.__aexit__ = AsyncMock(return_value=False)
    return MagicMock(return_value=ctx)


@pytest.mark.anyio
async def test_control_api_request_carries_credentials(client: AsyncClient) -> None:
    """The outgoing MediaMTX request is authenticated with the api user."""
    response = httpx.Response(200, json=_PATHS_LIST_PAYLOAD)
    fake_client = _mock_client(response)

    with patch("app.api.routers.streams.httpx.AsyncClient", fake_client):
        token = await _get_token(client)
        result = await client.get("/streams", headers={"Authorization": f"Bearer {token}"})

    assert result.status_code == 200

    instance = fake_client.return_value.__aenter__.return_value
    auth = instance.get.await_args.kwargs["auth"]
    assert isinstance(auth, httpx.BasicAuth)

    # BasicAuth keeps no public attributes for the credentials, so the header
    # it produces is inspected instead.
    request = httpx.Request("GET", "http://mediamtx:9997/v3/paths/list")
    authenticated = next(auth.auth_flow(request))
    expected = httpx.BasicAuth(settings.mtx_api_user, settings.mtx_api_password)
    assert (
        authenticated.headers["authorization"]
        == next(
            expected.auth_flow(httpx.Request("GET", "http://mediamtx:9997/v3/paths/list")),
        ).headers["authorization"]
    )


@pytest.mark.anyio
@pytest.mark.parametrize("mtx_status", [401, 403])
async def test_rejected_credentials_surface_as_bad_gateway(
    client: AsyncClient,
    mtx_status: int,
) -> None:
    """A rejected backend credential is reported as a gateway failure.

    It must not be echoed back as a 401, which would tell the caller their own
    token is bad, and the detail must name authentication so the deployment
    problem is diagnosable from the response alone.

    Args:
        client: Async HTTP client bound to the application.
        mtx_status: Status code MediaMTX answers with.
    """
    fake_client = _mock_client(httpx.Response(mtx_status))

    with patch("app.api.routers.streams.httpx.AsyncClient", fake_client):
        token = await _get_token(client)
        result = await client.get("/streams", headers={"Authorization": f"Bearer {token}"})

    assert result.status_code == 502
    assert result.json()["detail"] == "MediaMTX API authentication failed"


@pytest.mark.anyio
async def test_streams_still_requires_a_tacitus_token(client: AsyncClient) -> None:
    """MediaMTX credentials do not replace the caller's own authentication."""
    fake_client = _mock_client(httpx.Response(200, json=_PATHS_LIST_PAYLOAD))

    with patch("app.api.routers.streams.httpx.AsyncClient", fake_client):
        result = await client.get("/streams")

    assert result.status_code == 401
    fake_client.assert_not_called()
