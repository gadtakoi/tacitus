"""Tests for the archived recordings router.

The archive shares a bucket with user files, so the tests here are mostly about
the boundary between the two: every key must stay inside
``settings.s3_archive_prefix``, and no amount of traversal spelling may push it
out. The remaining tests cover pagination and the download flow.
"""

from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from httpx import AsyncClient

from app.core.config import settings

_STRONG_PASSWORD = "Str0ng!Pass"
_PREFIX = "mediamtx-archives/"
_KEY = f"{_PREFIX}cam1/2026-09-10_12-00-00-000000.mp4"


async def _get_token(client: AsyncClient) -> str:
    """Register a test user and return an access token."""
    await client.post(
        "/auth/register",
        json={"username": "recuser", "password": _STRONG_PASSWORD},
    )
    login = await client.post(
        "/auth/login",
        json={"username": "recuser", "password": _STRONG_PASSWORD},
    )
    return login.json()["access_token"]


def _object(key: str, size: int = 1024) -> dict:
    """Build one object summary as ``S3Service.list_objects`` returns it."""
    return {
        "key": key,
        "size_bytes": size,
        "last_modified": datetime(2026, 9, 10, 12, 0, tzinfo=UTC),
    }


@pytest.mark.anyio
@patch("app.api.routers.recordings.s3_service")
async def test_list_recordings(mock_s3: MagicMock, client: AsyncClient) -> None:
    """The listing maps object keys to items and derives the stream name."""
    mock_s3.list_objects = AsyncMock(return_value=([_object(_KEY)], None))
    token = await _get_token(client)

    response = await client.get("/recordings", headers={"Authorization": f"Bearer {token}"})

    assert response.status_code == 200
    data = response.json()
    assert data["next_cursor"] is None
    assert data["items"][0]["key"] == _KEY
    assert data["items"][0]["stream"] == "cam1"
    assert data["items"][0]["size_bytes"] == 1024
    # Without a stream filter the whole archive prefix is listed.
    assert mock_s3.list_objects.await_args.args[0] == settings.s3_archive_prefix


@pytest.mark.anyio
@patch("app.api.routers.recordings.s3_service")
async def test_list_recordings_filtered_by_stream(
    mock_s3: MagicMock,
    client: AsyncClient,
) -> None:
    """A stream filter narrows the S3 prefix instead of filtering in Python."""
    mock_s3.list_objects = AsyncMock(return_value=([], None))
    token = await _get_token(client)

    response = await client.get(
        "/recordings?stream=cam1",
        headers={"Authorization": f"Bearer {token}"},
    )

    assert response.status_code == 200
    assert mock_s3.list_objects.await_args.args[0] == f"{settings.s3_archive_prefix}cam1/"


@pytest.mark.anyio
@patch("app.api.routers.recordings.s3_service")
async def test_list_recordings_rejects_invalid_stream(
    mock_s3: MagicMock,
    client: AsyncClient,
) -> None:
    """A stream name is validated before it reaches the S3 prefix."""
    mock_s3.list_objects = AsyncMock(return_value=([], None))
    token = await _get_token(client)

    response = await client.get(
        "/recordings?stream=../secret",
        headers={"Authorization": f"Bearer {token}"},
    )

    assert response.status_code == 422
    mock_s3.list_objects.assert_not_awaited()


@pytest.mark.anyio
@patch("app.api.routers.recordings.s3_service")
async def test_list_recordings_pagination(mock_s3: MagicMock, client: AsyncClient) -> None:
    """The cursor is returned to the client and passed back to S3 verbatim."""
    mock_s3.list_objects = AsyncMock(return_value=([_object(_KEY)], "token-2"))
    token = await _get_token(client)

    first = await client.get(
        "/recordings?limit=1",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert first.status_code == 200
    cursor = first.json()["next_cursor"]
    assert cursor == "token-2"

    mock_s3.list_objects = AsyncMock(return_value=([], None))
    second = await client.get(
        f"/recordings?limit=1&cursor={cursor}",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert second.status_code == 200
    assert second.json()["next_cursor"] is None
    assert mock_s3.list_objects.await_args.args[1:] == (1, "token-2")


@pytest.mark.anyio
@patch("app.api.routers.recordings.s3_service")
async def test_list_recordings_invalid_cursor(mock_s3: MagicMock, client: AsyncClient) -> None:
    """A cursor S3 refuses is the client's fault: 400, not 500."""
    from app.services.s3_service import S3InvalidCursorError

    mock_s3.list_objects = AsyncMock(side_effect=S3InvalidCursorError("bad"))
    token = await _get_token(client)

    response = await client.get(
        "/recordings?cursor=bad",
        headers={"Authorization": f"Bearer {token}"},
    )

    assert response.status_code == 400
    assert "cursor" in response.json()["detail"].lower()


@pytest.mark.anyio
async def test_list_recordings_unauthorized(client: AsyncClient) -> None:
    """Missing JWT must be rejected with 401."""
    response = await client.get("/recordings")
    assert response.status_code == 401


@pytest.mark.anyio
@patch("app.api.routers.recordings.s3_service")
async def test_get_recording_url(mock_s3: MagicMock, client: AsyncClient) -> None:
    """A valid archive key yields a presigned URL for that exact key."""
    mock_s3.get_presigned_url_for_key = AsyncMock(return_value="http://example.com/url")
    token = await _get_token(client)

    response = await client.get(
        f"/recordings/{_KEY}",
        headers={"Authorization": f"Bearer {token}"},
    )

    assert response.status_code == 200
    data = response.json()
    assert data["key"] == _KEY
    assert data["url"] == "http://example.com/url"
    mock_s3.get_presigned_url_for_key.assert_awaited_once_with(_KEY)


@pytest.mark.anyio
async def test_get_recording_url_unauthorized(client: AsyncClient) -> None:
    """Missing JWT must be rejected with 401."""
    response = await client.get(f"/recordings/{_KEY}")
    assert response.status_code == 401


@pytest.mark.anyio
@patch("app.api.routers.recordings.s3_service")
async def test_get_recording_url_revoked_token(mock_s3: MagicMock, client: AsyncClient) -> None:
    """A token revoked by logout no longer opens the archive."""
    mock_s3.get_presigned_url_for_key = AsyncMock(return_value="http://example.com/url")
    password = _STRONG_PASSWORD
    await client.post("/auth/register", json={"username": "revoked", "password": password})
    login = await client.post("/auth/login", json={"username": "revoked", "password": password})
    tokens = login.json()
    headers = {"Authorization": f"Bearer {tokens['access_token']}"}
    await client.post("/auth/logout", headers=headers, json={"refresh_token": "unused"})

    response = await client.get(f"/recordings/{_KEY}", headers=headers)

    assert response.status_code == 401
    mock_s3.get_presigned_url_for_key.assert_not_awaited()


@pytest.mark.anyio
@patch("app.api.routers.recordings.s3_service")
async def test_get_recording_url_not_found(mock_s3: MagicMock, client: AsyncClient) -> None:
    """A key that does not exist in the bucket returns 404."""
    from app.services.s3_service import S3FileNotFoundError

    mock_s3.get_presigned_url_for_key = AsyncMock(side_effect=S3FileNotFoundError(_KEY))
    token = await _get_token(client)

    response = await client.get(
        f"/recordings/{_KEY}",
        headers={"Authorization": f"Bearer {token}"},
    )

    assert response.status_code == 404
    assert "not found" in response.json()["detail"].lower()


@pytest.mark.anyio
@patch("app.api.routers.recordings.s3_service")
async def test_get_recording_url_uses_configured_ttl(
    mock_s3: MagicMock,
    client: AsyncClient,
) -> None:
    """The endpoint does not override the configured presigned URL lifetime."""
    mock_s3.get_presigned_url_for_key = AsyncMock(return_value="http://example.com/url")
    token = await _get_token(client)

    await client.get(f"/recordings/{_KEY}", headers={"Authorization": f"Bearer {token}"})

    # No explicit expiration argument: S3Service falls back to
    # settings.presigned_url_expiration, which is what the operator configures.
    assert mock_s3.get_presigned_url_for_key.await_args.kwargs == {}
    assert mock_s3.get_presigned_url_for_key.await_args.args == (_KEY,)


@pytest.mark.anyio
@patch("app.api.routers.recordings.s3_service")
async def test_download_is_logged_as_security_event(
    mock_s3: MagicMock,
    client: AsyncClient,
) -> None:
    """Handing out a recording URL is auditable, like handing out a file URL."""
    mock_s3.get_presigned_url_for_key = AsyncMock(return_value="http://example.com/url")
    token = await _get_token(client)

    with patch("app.api.routers.recordings.log_security_event") as mock_log:
        response = await client.get(
            f"/recordings/{_KEY}",
            headers={"Authorization": f"Bearer {token}"},
        )

    assert response.status_code == 200
    mock_log.assert_called_once()
    assert mock_log.call_args.args[0] == "recording.download"
    assert mock_log.call_args.kwargs["extra"]["object_key"] == _KEY


@pytest.mark.anyio
@patch("app.api.routers.recordings.s3_service")
async def test_delete_recording(mock_s3: MagicMock, client: AsyncClient) -> None:
    """Deletion removes exactly the validated key and is audited."""
    mock_s3.delete_object = AsyncMock(return_value=None)
    token = await _get_token(client)

    with patch("app.api.routers.recordings.log_security_event") as mock_log:
        response = await client.delete(
            f"/recordings/{_KEY}",
            headers={"Authorization": f"Bearer {token}"},
        )

    assert response.status_code == 200
    assert response.json()["key"] == _KEY
    mock_s3.delete_object.assert_awaited_once_with(_KEY)
    assert mock_log.call_args.args[0] == "recording.delete"


@pytest.mark.anyio
@pytest.mark.parametrize(
    "key",
    [
        # Dot segments are percent-encoded on purpose. Spelled literally
        # (``../secret.png``) they never reach the application: httpx — like
        # browsers and most proxies — collapses them while building the
        # request, so the test would assert on a route that was never called.
        # Encoded, they survive the client and arrive here decoded, which is
        # exactly the shape an attacker would use.
        "%2e%2e/secret.png",
        "mediamtx-archives/%2e%2e/secret.png",
        "mediamtx-archives/cam1/%2e%2e/%2e%2e/secret.png",
        "mediamtx-archives%2f%2e%2e%2fsecret.png",
        "secret.png",
        "mediamtx-archives-old/secret.png",
        "mediamtx-archives/.hidden/segment.mp4",
        "mediamtx-archives/cam1//segment.mp4",
        "mediamtx-archives/cam1/seg ment.mp4",
    ],
)
@patch("app.api.routers.recordings.s3_service")
async def test_get_recording_url_rejects_keys_outside_archive(
    mock_s3: MagicMock,
    client: AsyncClient,
    key: str,
) -> None:
    """No spelling of traversal or prefix escape reaches S3.

    The check must fail with 400 — a 500 would mean the key got far enough to
    break something, and a 200 would mean the archive prefix is not a boundary.
    """
    mock_s3.get_presigned_url_for_key = AsyncMock(return_value="http://example.com/url")
    token = await _get_token(client)

    response = await client.get(
        f"/recordings/{key}",
        headers={"Authorization": f"Bearer {token}"},
    )

    assert response.status_code == 400, response.text
    mock_s3.get_presigned_url_for_key.assert_not_awaited()


@pytest.mark.anyio
@patch("app.api.routers.recordings.s3_service")
async def test_delete_recording_rejects_key_outside_archive(
    mock_s3: MagicMock,
    client: AsyncClient,
) -> None:
    """Deletion is irreversible, so the same boundary applies to it."""
    mock_s3.delete_object = AsyncMock(return_value=None)
    token = await _get_token(client)

    response = await client.delete(
        "/recordings/uploaded-by-a-user.png",
        headers={"Authorization": f"Bearer {token}"},
    )

    assert response.status_code == 400
    mock_s3.delete_object.assert_not_awaited()


@pytest.mark.anyio
@patch("app.api.routers.recordings.s3_service")
async def test_get_recording_url_rate_limit(mock_s3: MagicMock, client: AsyncClient) -> None:
    """More than 30 download requests per minute per user returns 429."""
    mock_s3.get_presigned_url_for_key = AsyncMock(return_value="http://example.com/url")
    token = await _get_token(client)
    headers = {"Authorization": f"Bearer {token}"}

    for _ in range(30):
        response = await client.get(f"/recordings/{_KEY}", headers=headers)
        assert response.status_code == 200

    response = await client.get(f"/recordings/{_KEY}", headers=headers)
    assert response.status_code == 429
    assert response.json()["error"] == "Rate limit exceeded"
