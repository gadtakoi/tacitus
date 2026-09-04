"""Tests for the MediaMTX proxy endpoints.

Every failure mode of the upstream call is mapped to a deliberate status code.
Before these tests the router had 36% coverage: only the happy path was
exercised, so a protocol error, a malformed body or a drifted MediaMTX
contract would have escaped as a generic 500 without anyone noticing.

Authentication of the outgoing request is covered separately in
``test_streams_auth.py``.
"""

from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest
from httpx import AsyncClient

_STRONG_PASSWORD = "Str0ng!Pass"

_PATHS_LIST_PAYLOAD = {
    "pageCount": 1,
    "itemCount": 1,
    "items": [
        {
            "name": "cam1",
            "confName": "cam1",
            "source": {"type": "rtspSession", "id": "abc"},
            "ready": True,
            "readyTime": "2026-09-05T10:00:00Z",
            "tracks": ["H264"],
            "bytesReceived": 1024,
            "bytesSent": 2048,
            "readers": [{"type": "rtspSession", "id": "def"}],
        },
    ],
}

_PATH_ITEM_PAYLOAD: dict[str, Any] = {
    "name": "cam1",
    "confName": "cam1",
    "source": None,
    "ready": False,
    "tracks": [],
    "readers": [],
}


async def _token(client: AsyncClient, username: str) -> str:
    """Register a user and return an access token.

    Args:
        client: Async HTTP client bound to the application.
        username: Username to create.

    Returns:
        Access token for the created user.
    """
    body = {"username": username, "password": _STRONG_PASSWORD}
    await client.post("/auth/register", json=body)
    login = await client.post("/auth/login", json=body)
    return login.json()["access_token"]


def _upstream(*, response: httpx.Response | None = None, error: Exception | None = None):
    """Stub ``httpx.AsyncClient`` returning a response or raising an error.

    Args:
        response: Response the stubbed ``get`` should return.
        error: Exception the stubbed ``get`` should raise instead.

    Returns:
        Mock replacement for ``httpx.AsyncClient``.
    """
    instance = MagicMock()
    instance.get = AsyncMock(return_value=response, side_effect=error)
    ctx = MagicMock()
    ctx.__aenter__ = AsyncMock(return_value=instance)
    ctx.__aexit__ = AsyncMock(return_value=False)
    return MagicMock(return_value=ctx)


async def _call(client: AsyncClient, stub, path: str = "/streams") -> httpx.Response:
    """Call a streams endpoint with the upstream client stubbed out.

    Args:
        client: Async HTTP client bound to the application.
        stub: Replacement for ``httpx.AsyncClient``.
        path: Endpoint path to request.

    Returns:
        The application's response.
    """
    with patch("app.api.routers.streams.httpx.AsyncClient", stub):
        token = await _token(client, "streamsuser")
        return await client.get(path, headers={"Authorization": f"Bearer {token}"})


@pytest.mark.anyio
async def test_list_streams_returns_the_documented_contract(client: AsyncClient) -> None:
    """MediaMTX's camelCase is parsed in and snake_case is served out.

    The schema's aliases exist to read MediaMTX's field names; they are not
    part of our contract. Both halves are asserted here: the upstream payload
    is camelCase, the response is snake_case like the rest of the API.
    """
    result = await _call(client, _upstream(response=httpx.Response(200, json=_PATHS_LIST_PAYLOAD)))

    assert result.status_code == 200
    body = result.json()
    assert body["page_count"] == 1
    assert body["item_count"] == 1

    item = body["items"][0]
    assert item["name"] == "cam1"
    assert item["conf_name"] == "cam1"
    assert item["ready"] is True
    assert item["ready_time"] == "2026-09-05T10:00:00Z"
    assert item["tracks"] == ["H264"]
    assert item["bytes_received"] == 1024
    assert item["bytes_sent"] == 2048
    assert item["source"] == {"type": "rtspSession", "id": "abc"}
    assert item["readers"] == [{"type": "rtspSession", "id": "def"}]


@pytest.mark.anyio
async def test_responses_carry_no_camelcase_aliases(client: AsyncClient) -> None:
    """No alias survives into the response body.

    ``response_model_by_alias`` defaults to True in FastAPI, so leaving it
    unset republishes MediaMTX's field names as our own. This guards the
    setting from being dropped in a later edit.
    """
    result = await _call(client, _upstream(response=httpx.Response(200, json=_PATHS_LIST_PAYLOAD)))

    raw = result.text
    for alias in ("pageCount", "itemCount", "confName", "readyTime", "bytesReceived", "bytesSent"):
        assert alias not in raw, f"response still exposes the MediaMTX alias {alias}"


@pytest.mark.anyio
async def test_get_stream_encodes_the_path_it_forwards(client: AsyncClient) -> None:
    """The stream name is percent-encoded before it reaches MediaMTX.

    A name carrying ``/`` or ``?`` would otherwise change which upstream
    endpoint is addressed.
    """
    stub = _upstream(response=httpx.Response(200, json=_PATH_ITEM_PAYLOAD))
    result = await _call(client, stub, "/streams/cam%201")

    assert result.status_code == 200
    requested_url = stub.return_value.__aenter__.return_value.get.await_args.args[0]
    assert requested_url.endswith("/paths/get/cam%201")


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("error", "expected_status"),
    [
        (httpx.ConnectTimeout("timed out"), 504),
        (httpx.ReadTimeout("timed out"), 504),
        (httpx.ConnectError("refused"), 503),
        (httpx.ReadError("reset"), 503),
        (httpx.RemoteProtocolError("bad chunk"), 502),
        (httpx.UnsupportedProtocol("no scheme"), 502),
        (httpx.TooManyRedirects("loop"), 502),
        (httpx.InvalidURL("not a url"), 502),
    ],
)
async def test_transport_failures_map_to_gateway_statuses(
    client: AsyncClient,
    error: Exception,
    expected_status: int,
) -> None:
    """No transport failure escapes as a 500.

    ``InvalidURL`` is included deliberately: httpx does not derive it from
    ``HTTPError``, so a catch-all on ``HTTPError`` alone would miss a
    misconfigured ``TACITUS_MTX_API_URL``.

    Args:
        client: Async HTTP client bound to the application.
        error: Exception raised by the upstream call.
        expected_status: Status the endpoint must answer with.
    """
    result = await _call(client, _upstream(error=error))
    assert result.status_code == expected_status


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("upstream_status", "expected_status"),
    [(404, 404), (400, 502), (500, 502), (503, 502)],
)
async def test_upstream_status_codes_are_translated(
    client: AsyncClient,
    upstream_status: int,
    expected_status: int,
) -> None:
    """Upstream error codes are not echoed back verbatim.

    Only 404 is meaningful to the caller — it means the stream does not exist.
    Everything else is an upstream failure and becomes a 502.

    Args:
        client: Async HTTP client bound to the application.
        upstream_status: Status MediaMTX answers with.
        expected_status: Status the endpoint must answer with.
    """
    result = await _call(client, _upstream(response=httpx.Response(upstream_status)))
    assert result.status_code == expected_status


@pytest.mark.anyio
async def test_non_json_body_is_reported_as_bad_gateway(client: AsyncClient) -> None:
    """A 200 carrying something other than JSON does not crash the endpoint."""
    stub = _upstream(response=httpx.Response(200, text="<html>not mediamtx</html>"))
    result = await _call(client, stub)

    assert result.status_code == 502
    assert result.json()["detail"] == "MediaMTX API returned a malformed response"


@pytest.mark.anyio
async def test_unexpected_schema_is_reported_as_bad_gateway(client: AsyncClient) -> None:
    """Valid JSON in the wrong shape is a contract break, not a server error.

    This is what a MediaMTX upgrade with a changed API would look like: the
    response parses, but the fields we depend on are gone.
    """
    stub = _upstream(response=httpx.Response(200, json={"unexpected": "shape"}))
    result = await _call(client, stub)

    assert result.status_code == 502
    assert result.json()["detail"] == "MediaMTX API returned an unexpected response"


@pytest.mark.anyio
async def test_streams_endpoints_are_rate_limited(client: AsyncClient) -> None:
    """The proxy endpoints are bounded like the rest of the API.

    Without a limit a single authenticated caller could relay an unbounded
    request rate onto MediaMTX through us.
    """
    stub = _upstream(response=httpx.Response(200, json=_PATHS_LIST_PAYLOAD))

    with patch("app.api.routers.streams.httpx.AsyncClient", stub):
        token = await _token(client, "limituser")
        headers = {"Authorization": f"Bearer {token}"}
        statuses = [(await client.get("/streams", headers=headers)).status_code for _ in range(65)]

    assert 429 in statuses, "GET /streams accepted more than its per-minute limit"
    assert statuses[0] == 200
