"""Tests for S3 service.

Public methods are ``async`` (blocking boto3 calls are offloaded to a thread
pool), so these tests drive the service through ``anyio`` and assert on the
same mocked boto3 client.
"""

from datetime import UTC, datetime
from unittest.mock import MagicMock, patch

import pytest
from botocore.exceptions import ClientError

from app.services.s3_service import (
    S3InvalidCursorError,
    S3Service,
    sanitize_filename,
    sanitize_object_key,
)


@pytest.fixture
def s3_service() -> S3Service:
    return S3Service()


@pytest.mark.anyio
@patch("app.services.s3_service.boto3.client")
async def test_list_files(mock_client: MagicMock, s3_service: S3Service) -> None:
    mock_client.return_value.list_objects_v2.return_value = {
        "Contents": [
            {"Key": "file1.txt"},
            {"Key": "file2.txt"},
        ]
    }
    files = await s3_service.list_files()
    assert files == ["file1.txt", "file2.txt"]


@pytest.mark.anyio
@patch("app.services.s3_service.boto3.client")
async def test_list_files_empty(mock_client: MagicMock, s3_service: S3Service) -> None:
    mock_client.return_value.list_objects_v2.return_value = {}
    files = await s3_service.list_files()
    assert files == []


@pytest.mark.anyio
@patch("app.services.s3_service.boto3.client")
async def test_list_files_pagination(mock_client: MagicMock, s3_service: S3Service) -> None:
    """Buckets with more than 1000 objects must be listed across pages."""
    mock_client.return_value.list_objects_v2.side_effect = [
        {
            "Contents": [{"Key": "file1.txt"}, {"Key": "file2.txt"}],
            "IsTruncated": True,
            "NextContinuationToken": "token-1",
        },
        {
            "Contents": [{"Key": "file3.txt"}],
            "IsTruncated": False,
        },
    ]
    files = await s3_service.list_files()
    assert files == ["file1.txt", "file2.txt", "file3.txt"]
    # The second call must carry the continuation token
    second_call = mock_client.return_value.list_objects_v2.call_args_list[1]
    assert second_call.kwargs["ContinuationToken"] == "token-1"


@pytest.mark.anyio
@patch("app.services.s3_service.boto3.client")
async def test_get_bucket_stats_pagination(mock_client: MagicMock, s3_service: S3Service) -> None:
    """Bucket stats must aggregate across pagination pages."""
    mock_client.return_value.list_objects_v2.side_effect = [
        {
            "Contents": [{"Key": "a.txt", "Size": 256}],
            "IsTruncated": True,
            "NextContinuationToken": "token-1",
        },
        {
            "Contents": [{"Key": "b.txt", "Size": 512}],
            "IsTruncated": False,
        },
    ]
    stats = await s3_service.get_bucket_stats()
    assert stats["total_files"] == 2
    assert stats["total_size_bytes"] == 768
    largest = stats["largest_file"]
    assert largest is not None
    assert largest["filename"] == "b.txt"


@pytest.mark.anyio
@patch("app.services.s3_service.boto3.client")
async def test_upload_file(mock_client: MagicMock, s3_service: S3Service) -> None:
    result = await s3_service.upload_file("test.txt", b"hello")
    assert result == "test.txt"
    mock_client.return_value.put_object.assert_called_once_with(
        Bucket="tacitus-files",
        Key="test.txt",
        Body=b"hello",
    )


@pytest.mark.anyio
@patch("app.services.s3_service.boto3.client")
async def test_get_presigned_url(mock_client: MagicMock, s3_service: S3Service) -> None:
    mock_client.return_value.generate_presigned_url.return_value = "http://example.com/url"
    url = await s3_service.get_presigned_url("test.txt")
    assert url == "http://example.com/url"
    mock_client.return_value.head_object.assert_called_once_with(
        Bucket="tacitus-files",
        Key="test.txt",
    )
    mock_client.return_value.generate_presigned_url.assert_called_once_with(
        "get_object",
        Params={"Bucket": "tacitus-files", "Key": "test.txt"},
        ExpiresIn=300,
    )


@pytest.mark.anyio
@patch("app.services.s3_service.boto3.client")
async def test_get_presigned_url_custom_expiration(
    mock_client: MagicMock,
    s3_service: S3Service,
) -> None:
    mock_client.return_value.generate_presigned_url.return_value = "http://example.com/url"
    url = await s3_service.get_presigned_url("test.txt", expiration=600)
    assert url == "http://example.com/url"
    mock_client.return_value.generate_presigned_url.assert_called_once_with(
        "get_object",
        Params={"Bucket": "tacitus-files", "Key": "test.txt"},
        ExpiresIn=600,
    )


@pytest.mark.anyio
@patch("app.services.s3_service.boto3.client")
async def test_get_presigned_url_file_not_found(
    mock_client: MagicMock,
    s3_service: S3Service,
) -> None:
    from botocore.exceptions import ClientError

    # Литерал передаётся прямо в вызов, а не через переменную: botocore-stubs
    # объявляют первый аргумент как TypedDict, а отдельная переменная выводится
    # как `dict[str, dict[str, str]]` и к нему уже не приводится. Инлайн
    # оставляет структуру ответа под проверкой mypy — приведение типом `Any`
    # молча пропустило бы и опечатку в имени ключа.
    mock_client.return_value.head_object.side_effect = ClientError(
        {"Error": {"Code": "404", "Message": "Not Found"}},
        "HeadObject",
    )

    from app.services.s3_service import S3FileNotFoundError

    with pytest.raises(S3FileNotFoundError):
        await s3_service.get_presigned_url("missing.txt")


@pytest.mark.anyio
@patch("app.services.s3_service.boto3.client")
async def test_delete_file(mock_client: MagicMock, s3_service: S3Service) -> None:
    await s3_service.delete_file("test.txt")
    mock_client.return_value.delete_object.assert_called_once_with(
        Bucket="tacitus-files",
        Key="test.txt",
    )


@pytest.mark.anyio
@patch("app.services.s3_service.boto3.client")
async def test_get_bucket_stats(mock_client: MagicMock, s3_service: S3Service) -> None:
    """Test fetching bucket statistics."""
    mock_client.return_value.list_objects_v2.return_value = {
        "Contents": [
            {"Key": "a.txt", "Size": 256},
            {"Key": "b.txt", "Size": 512},
            {"Key": "c.txt", "Size": 768},
        ]
    }
    stats = await s3_service.get_bucket_stats()
    assert stats["total_files"] == 3
    assert stats["total_size_bytes"] == 1536
    assert stats["total_size_human"] == "1.5 KB"
    assert stats["average_size_bytes"] == 512.0
    largest = stats["largest_file"]
    assert largest is not None
    assert largest["filename"] == "c.txt"
    assert largest["size_bytes"] == 768


@pytest.mark.anyio
@patch("app.services.s3_service.boto3.client")
async def test_get_bucket_stats_empty(mock_client: MagicMock, s3_service: S3Service) -> None:
    """Test fetching stats for empty bucket."""
    mock_client.return_value.list_objects_v2.return_value = {}
    stats = await s3_service.get_bucket_stats()
    assert stats["total_files"] == 0
    assert stats["total_size_bytes"] == 0
    assert stats["total_size_human"] == "0 B"
    assert stats["average_size_bytes"] == 0.0
    assert stats["largest_file"] is None


def test_sanitize_filename_valid() -> None:
    assert sanitize_filename("file.txt") == "file.txt"
    assert sanitize_filename("my-file_v2.jpg") == "my-file_v2.jpg"


def test_sanitize_filename_path_traversal() -> None:
    with pytest.raises(ValueError, match="Path traversal"):
        sanitize_filename("../../../etc/passwd")


def test_sanitize_filename_backslash_traversal() -> None:
    with pytest.raises(ValueError, match="Path traversal"):
        sanitize_filename("..\\windows\\system32\\config.sys")


def test_sanitize_filename_null_byte() -> None:
    with pytest.raises(ValueError, match="null bytes"):
        sanitize_filename("file\x00.txt")


def test_sanitize_filename_too_long() -> None:
    with pytest.raises(ValueError, match="too long"):
        sanitize_filename("a" * 256)


def test_sanitize_filename_hidden() -> None:
    with pytest.raises(ValueError, match="Hidden files"):
        sanitize_filename(".htaccess")


def test_sanitize_filename_invalid_chars() -> None:
    with pytest.raises(ValueError, match="invalid characters"):
        sanitize_filename("file name.txt")
    with pytest.raises(ValueError, match="invalid characters"):
        sanitize_filename("file:name.txt")


class TestSanitizeObjectKey:
    """Unit tests for the archive key boundary.

    ``sanitize_object_key`` is the only thing separating the archive from the
    user files sharing the bucket, so its negative cases are tested here as
    well as through the router.
    """

    PREFIX = "mediamtx-archives/"

    def test_valid_archive_key(self) -> None:
        key = f"{self.PREFIX}cam1/2026-09-10_12-00-00-000000.mp4"
        assert sanitize_object_key(key, self.PREFIX) == key

    def test_backslashes_are_normalized(self) -> None:
        key = rf"{self.PREFIX}cam1\segment.mp4"
        assert sanitize_object_key(key, self.PREFIX) == f"{self.PREFIX}cam1/segment.mp4"

    def test_parent_segment_is_traversal(self) -> None:
        with pytest.raises(ValueError, match="Path traversal"):
            sanitize_object_key(f"{self.PREFIX}../secret.png", self.PREFIX)

    def test_current_segment_is_traversal(self) -> None:
        with pytest.raises(ValueError, match="Path traversal"):
            sanitize_object_key(f"{self.PREFIX}./secret.png", self.PREFIX)

    def test_absolute_key_is_traversal(self) -> None:
        with pytest.raises(ValueError, match="Path traversal"):
            sanitize_object_key("/etc/passwd", self.PREFIX)

    def test_sibling_prefix_is_rejected(self) -> None:
        # The trailing slash in the prefix is what makes this fail; without it
        # the check would be a plain string prefix match and would pass.
        with pytest.raises(ValueError, match="outside the allowed prefix"):
            sanitize_object_key("mediamtx-archives-old/secret.png", self.PREFIX)

    def test_key_without_prefix_is_rejected(self) -> None:
        with pytest.raises(ValueError, match="outside the allowed prefix"):
            sanitize_object_key("uploaded-by-a-user.png", self.PREFIX)

    def test_empty_segment_is_rejected(self) -> None:
        with pytest.raises(ValueError, match="empty path segment"):
            sanitize_object_key(f"{self.PREFIX}cam1//segment.mp4", self.PREFIX)

    def test_hidden_segment_is_rejected(self) -> None:
        with pytest.raises(ValueError, match="Hidden files"):
            sanitize_object_key(f"{self.PREFIX}.hidden/segment.mp4", self.PREFIX)

    def test_null_byte_is_rejected(self) -> None:
        with pytest.raises(ValueError, match="null bytes"):
            sanitize_object_key(f"{self.PREFIX}cam1/seg\x00.mp4", self.PREFIX)

    def test_invalid_characters_are_rejected(self) -> None:
        with pytest.raises(ValueError, match="invalid characters"):
            sanitize_object_key(f"{self.PREFIX}cam1/seg ment.mp4", self.PREFIX)

    def test_too_long_key_is_rejected(self) -> None:
        with pytest.raises(ValueError, match="too long"):
            sanitize_object_key(self.PREFIX + "a" * 1024, self.PREFIX)


@pytest.mark.anyio
@patch("app.services.s3_service.boto3.client")
async def test_list_objects_single_page(mock_client: MagicMock, s3_service: S3Service) -> None:
    """A non-truncated response ends the pagination."""
    modified = datetime(2026, 9, 10, 12, 0, tzinfo=UTC)
    mock_client.return_value.list_objects_v2.return_value = {
        "Contents": [{"Key": "mediamtx-archives/cam1/a.mp4", "Size": 10, "LastModified": modified}],
        "IsTruncated": False,
        "NextContinuationToken": "ignored-when-not-truncated",
    }

    items, cursor = await s3_service.list_objects("mediamtx-archives/", limit=10)

    assert cursor is None
    assert items == [
        {
            "key": "mediamtx-archives/cam1/a.mp4",
            "size_bytes": 10,
            "last_modified": modified,
        }
    ]
    kwargs = mock_client.return_value.list_objects_v2.call_args.kwargs
    assert kwargs["Prefix"] == "mediamtx-archives/"
    assert kwargs["MaxKeys"] == 10
    assert "ContinuationToken" not in kwargs


@pytest.mark.anyio
@patch("app.services.s3_service.boto3.client")
async def test_list_objects_truncated(mock_client: MagicMock, s3_service: S3Service) -> None:
    """A truncated response hands the continuation token back to the caller."""
    mock_client.return_value.list_objects_v2.return_value = {
        "Contents": [],
        "IsTruncated": True,
        "NextContinuationToken": "page-2",
    }

    _, cursor = await s3_service.list_objects("mediamtx-archives/", limit=1, cursor="page-1")

    assert cursor == "page-2"
    assert (
        mock_client.return_value.list_objects_v2.call_args.kwargs["ContinuationToken"] == "page-1"
    )


@pytest.mark.anyio
@patch("app.services.s3_service.boto3.client")
async def test_list_objects_invalid_cursor(mock_client: MagicMock, s3_service: S3Service) -> None:
    """A cursor S3 refuses becomes a dedicated error, not a generic failure."""
    mock_client.return_value.list_objects_v2.side_effect = ClientError(
        {"Error": {"Code": "InvalidArgument", "Message": "invalid token"}},
        "ListObjectsV2",
    )

    with pytest.raises(S3InvalidCursorError):
        await s3_service.list_objects("mediamtx-archives/", cursor="broken")
