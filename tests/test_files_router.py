"""Tests for files router."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from httpx import AsyncClient

_STRONG_PASSWORD = "Str0ng!Pass"


async def _get_token(client: AsyncClient) -> str:
    """Register a test user and return an access token."""
    password = _STRONG_PASSWORD
    await client.post("/auth/register", json={"username": "fileuser", "password": password})
    login = await client.post("/auth/login", json={"username": "fileuser", "password": password})
    return login.json()["access_token"]


@pytest.mark.anyio
@patch("app.api.routers.files.s3_service")
async def test_list_files(mock_s3: MagicMock, client: AsyncClient) -> None:
    mock_s3.list_files = AsyncMock(return_value=["file1.txt", "file2.txt"])
    token = await _get_token(client)
    response = await client.get("/files/", headers={"Authorization": f"Bearer {token}"})
    assert response.status_code == 200
    assert response.json() == {"files": ["file1.txt", "file2.txt"]}


@pytest.mark.anyio
@patch("app.api.routers.files.s3_service")
async def test_upload_file(mock_s3: MagicMock, client: AsyncClient) -> None:
    mock_s3.upload_file = AsyncMock(return_value="test.txt")
    token = await _get_token(client)
    response = await client.post(
        "/files/upload",
        headers={"Authorization": f"Bearer {token}"},
        files={"file": ("test.txt", b"content", "text/plain")},
    )
    assert response.status_code == 200
    data = response.json()
    assert data["filename"] == "test.txt"


@pytest.mark.anyio
@patch("app.api.routers.files.s3_service")
async def test_get_file_url(mock_s3: MagicMock, client: AsyncClient) -> None:
    mock_s3.get_presigned_url = AsyncMock(return_value="http://example.com/url")
    token = await _get_token(client)
    response = await client.get("/files/test.txt", headers={"Authorization": f"Bearer {token}"})
    assert response.status_code == 200
    data = response.json()
    assert data["filename"] == "test.txt"
    assert data["url"] == "http://example.com/url"


@pytest.mark.anyio
async def test_get_file_url_unauthorized(client: AsyncClient) -> None:
    """Missing JWT must be rejected with 401."""
    response = await client.get("/files/test.txt")
    assert response.status_code == 401


@pytest.mark.anyio
@patch("app.api.routers.files.s3_service")
async def test_get_file_url_not_found(mock_s3: MagicMock, client: AsyncClient) -> None:
    """Non-existent file must return 404."""
    from app.services.s3_service import S3FileNotFoundError

    mock_s3.get_presigned_url = AsyncMock(side_effect=S3FileNotFoundError("missing.txt"))
    token = await _get_token(client)
    response = await client.get("/files/missing.txt", headers={"Authorization": f"Bearer {token}"})
    assert response.status_code == 404
    assert "not found" in response.json()["detail"].lower()


@pytest.mark.anyio
@patch("app.api.routers.files.s3_service")
async def test_get_file_url_rate_limit(mock_s3: MagicMock, client: AsyncClient) -> None:
    """More than 30 download requests per minute per user returns 429."""
    mock_s3.get_presigned_url = AsyncMock(return_value="http://example.com/url")
    token = await _get_token(client)
    for _ in range(30):
        response = await client.get(
            "/files/test.txt",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert response.status_code == 200

    response = await client.get(
        "/files/test.txt",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert response.status_code == 429
    assert response.json()["error"] == "Rate limit exceeded"


@pytest.mark.anyio
@patch("app.api.routers.files.s3_service")
async def test_delete_file(mock_s3: MagicMock, client: AsyncClient) -> None:
    mock_s3.delete_file = AsyncMock(return_value=None)
    token = await _get_token(client)
    response = await client.delete("/files/test.txt", headers={"Authorization": f"Bearer {token}"})
    assert response.status_code == 200
    data = response.json()
    assert data["filename"] == "test.txt"
    mock_s3.delete_file.assert_called_once_with("test.txt")


@pytest.mark.anyio
@patch("app.api.routers.files.s3_service")
async def test_get_stats(mock_s3: MagicMock, client: AsyncClient) -> None:
    """Test fetching bucket statistics."""
    mock_s3.get_bucket_stats = AsyncMock(
        return_value={
            "total_files": 3,
            "total_size_bytes": 1536,
            "total_size_human": "1.5 KB",
            "average_size_bytes": 512.0,
            "largest_file": {"filename": "big.txt", "size_bytes": 1024},
        }
    )
    token = await _get_token(client)
    response = await client.get("/files/stats", headers={"Authorization": f"Bearer {token}"})
    assert response.status_code == 200
    data = response.json()
    assert data["total_files"] == 3
    assert data["total_size_human"] == "1.5 KB"
    assert data["largest_file"]["filename"] == "big.txt"


@pytest.mark.anyio
@patch("app.api.routers.files.s3_service")
async def test_get_stats_empty(mock_s3: MagicMock, client: AsyncClient) -> None:
    """Test fetching stats for empty bucket."""
    mock_s3.get_bucket_stats = AsyncMock(
        return_value={
            "total_files": 0,
            "total_size_bytes": 0,
            "total_size_human": "0 B",
            "average_size_bytes": 0.0,
            "largest_file": None,
        }
    )
    token = await _get_token(client)
    response = await client.get("/files/stats", headers={"Authorization": f"Bearer {token}"})
    assert response.status_code == 200
    data = response.json()
    assert data["total_files"] == 0
    assert data["largest_file"] is None


@pytest.mark.anyio
async def test_upload_file_too_large(client: AsyncClient) -> None:
    """Test uploading a file larger than 10 MB returns 413."""
    token = await _get_token(client)
    large_content = b"x" * (10 * 1024 * 1024 + 1)
    response = await client.post(
        "/files/upload",
        headers={"Authorization": f"Bearer {token}"},
        files={"file": ("large.txt", large_content, "text/plain")},
    )
    assert response.status_code == 413
    assert "too large" in response.json()["detail"].lower()


@pytest.mark.anyio
async def test_upload_forbidden_extension(client: AsyncClient) -> None:
    """Test uploading .exe file returns 400."""
    token = await _get_token(client)
    response = await client.post(
        "/files/upload",
        headers={"Authorization": f"Bearer {token}"},
        files={"file": ("malicious.exe", b"content", "application/octet-stream")},
    )
    assert response.status_code == 415


@pytest.mark.anyio
async def test_upload_unsupported_media_type(client: AsyncClient) -> None:
    """Test uploading unsupported MIME type returns 415."""
    token = await _get_token(client)
    response = await client.post(
        "/files/upload",
        headers={"Authorization": f"Bearer {token}"},
        files={"file": ("file.bin", b"content", "application/octet-stream")},
    )
    assert response.status_code == 415


@pytest.mark.anyio
async def test_upload_mime_extension_mismatch(client: AsyncClient) -> None:
    """Test uploading file with mismatched extension and MIME returns 415."""
    token = await _get_token(client)
    response = await client.post(
        "/files/upload",
        headers={"Authorization": f"Bearer {token}"},
        files={"file": ("file.jpg", b"content", "text/plain")},
    )
    assert response.status_code == 415


@pytest.mark.anyio
async def test_get_file_url_invalid_chars(client: AsyncClient) -> None:
    """Test invalid characters in filename returns 400."""
    token = await _get_token(client)
    response = await client.get(
        "/files/file%20name.txt",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert response.status_code == 400


@pytest.mark.anyio
async def test_delete_file_hidden(client: AsyncClient) -> None:
    """Test hidden file delete returns 400."""
    token = await _get_token(client)
    response = await client.delete(
        "/files/.htaccess",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert response.status_code == 400
