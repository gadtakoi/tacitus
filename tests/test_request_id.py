"""Tests for correlation-id handling.

``X-Request-Id`` is accepted from the client so a request can be traced across
systems. The value then reaches every log line, the ``x-request-id`` response
header and error response bodies, so an unvalidated header let a client bloat
the logs and — under the plain-text log format — append forged lines of their
own.
"""

from unittest.mock import Mock

import pytest
from httpx import AsyncClient

from app.core.logging_config import _MAX_REQUEST_ID_LENGTH, get_request_id


def _request(header: str | None) -> Mock:
    """Build a request stub carrying the given ``X-Request-Id``.

    Args:
        header: Header value, or ``None`` when the header is absent.

    Returns:
        Mock request exposing ``headers``.
    """
    request = Mock()
    request.headers = {} if header is None else {"x-request-id": header}
    return request


@pytest.mark.parametrize(
    "value",
    [
        "0d4b9c2e-1f3a-4c5d-8e7f-1a2b3c4d5e6f",  # UUID
        "a1b2c3d4e5f6a7b8c9d0e1f2a3b4c5d6",  # nginx $request_id
        "00-4bf92f3577b34da6a3ce929d0e0e4736-00f067aa0ba902b7-01",  # traceparent
        "req_42",
        "A" * _MAX_REQUEST_ID_LENGTH,
    ],
)
def test_well_formed_ids_are_honoured(value: str) -> None:
    """Real-world correlation id formats pass through unchanged.

    Args:
        value: Header value that must be accepted verbatim.
    """
    assert get_request_id(_request(value)) == value


@pytest.mark.parametrize(
    ("value", "reason"),
    [
        ("valid-looking-id\n", "trailing newline forges a second log line"),
        ("first\nsecond", "embedded newline forges a log line"),
        ("id\rwith-cr", "carriage return forges a log line or response header"),
        ("A" * (_MAX_REQUEST_ID_LENGTH + 1), "over the length bound"),
        ("x" * 10_000, "log bloat"),
        ("id with spaces", "unexpected character"),
        ('{"injected": "json"}', "structure-breaking characters"),
        ("../../etc/passwd", "path-like value"),
        ("", "empty header"),
        ("\x00null", "null byte"),
    ],
)
def test_malformed_ids_are_replaced_with_a_generated_uuid(value: str, reason: str) -> None:
    """A rejected header never reaches the logs; a fresh UUID is used instead.

    The trailing-newline case is the one that matters most: a regex anchored
    with ``$`` instead of ``fullmatch`` would accept it, because ``$`` also
    matches just before a final newline.

    Args:
        value: Header value that must be rejected.
        reason: Why the value is unacceptable.
    """
    result = get_request_id(_request(value))

    assert result != value, f"accepted a value that should be rejected ({reason})"
    assert len(result) == 36, "expected a generated UUID4"


def test_absent_header_gets_a_generated_id() -> None:
    """Without the header a new UUID is generated."""
    first = get_request_id(_request(None))
    second = get_request_id(_request(None))

    assert len(first) == 36
    assert first != second


@pytest.mark.anyio
async def test_forged_id_does_not_reach_the_response_header(client: AsyncClient) -> None:
    """A rejected id is not echoed back in ``x-request-id``.

    The middleware reflects the correlation id into the response, so an
    unsanitised value would also be a header-injection vector.
    """
    response = await client.get("/health", headers={"x-request-id": "spoofed\nX-Evil: yes"})

    assert response.status_code == 200
    echoed = response.headers["x-request-id"]
    assert echoed != "spoofed\nX-Evil: yes"
    assert "\n" not in echoed
    assert "x-evil" not in {name.lower() for name in response.headers}


@pytest.mark.anyio
async def test_well_formed_id_is_echoed_back(client: AsyncClient) -> None:
    """A valid correlation id survives the round trip."""
    response = await client.get("/health", headers={"x-request-id": "trace-abc123"})

    assert response.status_code == 200
    assert response.headers["x-request-id"] == "trace-abc123"
