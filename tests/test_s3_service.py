"""Tests for S3 service."""

from unittest.mock import MagicMock, patch

import pytest

from app.services.s3_service import S3Service, sanitize_filename


@pytest.fixture
def s3_service() -> S3Service:
    return S3Service()


@patch("app.services.s3_service.boto3.client")
def test_list_files(mock_client: MagicMock, s3_service: S3Service) -> None:
    mock_client.return_value.list_objects_v2.return_value = {
        "Contents": [
            {"Key": "file1.txt"},
            {"Key": "file2.txt"},
        ]
    }
    files = s3_service.list_files()
    assert files == ["file1.txt", "file2.txt"]


@patch("app.services.s3_service.boto3.client")
def test_list_files_empty(mock_client: MagicMock, s3_service: S3Service) -> None:
    mock_client.return_value.list_objects_v2.return_value = {}
    files = s3_service.list_files()
    assert files == []


@patch("app.services.s3_service.boto3.client")
def test_list_files_pagination(mock_client: MagicMock, s3_service: S3Service) -> None:
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
    files = s3_service.list_files()
    assert files == ["file1.txt", "file2.txt", "file3.txt"]
    # The second call must carry the continuation token
    second_call = mock_client.return_value.list_objects_v2.call_args_list[1]
    assert second_call.kwargs["ContinuationToken"] == "token-1"


@patch("app.services.s3_service.boto3.client")
def test_get_bucket_stats_pagination(mock_client: MagicMock, s3_service: S3Service) -> None:
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
    stats = s3_service.get_bucket_stats()
    assert stats["total_files"] == 2
    assert stats["total_size_bytes"] == 768
    largest = stats["largest_file"]
    assert largest is not None
    assert largest["filename"] == "b.txt"


@patch("app.services.s3_service.boto3.client")
def test_upload_file(mock_client: MagicMock, s3_service: S3Service) -> None:
    result = s3_service.upload_file("test.txt", b"hello")
    assert result == "test.txt"
    mock_client.return_value.put_object.assert_called_once_with(
        Bucket="tacitus-files",
        Key="test.txt",
        Body=b"hello",
    )


@patch("app.services.s3_service.boto3.client")
def test_get_presigned_url(mock_client: MagicMock, s3_service: S3Service) -> None:
    mock_client.return_value.generate_presigned_url.return_value = "http://example.com/url"
    url = s3_service.get_presigned_url("test.txt")
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


@patch("app.services.s3_service.boto3.client")
def test_get_presigned_url_custom_expiration(mock_client: MagicMock, s3_service: S3Service) -> None:
    mock_client.return_value.generate_presigned_url.return_value = "http://example.com/url"
    url = s3_service.get_presigned_url("test.txt", expiration=600)
    assert url == "http://example.com/url"
    mock_client.return_value.generate_presigned_url.assert_called_once_with(
        "get_object",
        Params={"Bucket": "tacitus-files", "Key": "test.txt"},
        ExpiresIn=600,
    )


@patch("app.services.s3_service.boto3.client")
def test_get_presigned_url_file_not_found(mock_client: MagicMock, s3_service: S3Service) -> None:
    from botocore.exceptions import ClientError

    error_response = {"Error": {"Code": "404", "Message": "Not Found"}}
    mock_client.return_value.head_object.side_effect = ClientError(error_response, "HeadObject")

    from app.services.s3_service import S3FileNotFoundError

    with pytest.raises(S3FileNotFoundError):
        s3_service.get_presigned_url("missing.txt")


@patch("app.services.s3_service.boto3.client")
def test_delete_file(mock_client: MagicMock, s3_service: S3Service) -> None:
    s3_service.delete_file("test.txt")
    mock_client.return_value.delete_object.assert_called_once_with(
        Bucket="tacitus-files",
        Key="test.txt",
    )


@patch("app.services.s3_service.boto3.client")
def test_get_bucket_stats(mock_client: MagicMock, s3_service: S3Service) -> None:
    """Test fetching bucket statistics."""
    mock_client.return_value.list_objects_v2.return_value = {
        "Contents": [
            {"Key": "a.txt", "Size": 256},
            {"Key": "b.txt", "Size": 512},
            {"Key": "c.txt", "Size": 768},
        ]
    }
    stats = s3_service.get_bucket_stats()
    assert stats["total_files"] == 3
    assert stats["total_size_bytes"] == 1536
    assert stats["total_size_human"] == "1.5 KB"
    assert stats["average_size_bytes"] == 512.0
    largest = stats["largest_file"]
    assert largest is not None
    assert largest["filename"] == "c.txt"
    assert largest["size_bytes"] == 768


@patch("app.services.s3_service.boto3.client")
def test_get_bucket_stats_empty(mock_client: MagicMock, s3_service: S3Service) -> None:
    """Test fetching stats for empty bucket."""
    mock_client.return_value.list_objects_v2.return_value = {}
    stats = s3_service.get_bucket_stats()
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
