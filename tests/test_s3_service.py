"""Tests for S3 service."""

from unittest.mock import MagicMock, patch

import pytest

from app.services.s3_service import S3Service


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


@patch("app.services.s3_service.boto3.client")
def test_delete_file(mock_client: MagicMock, s3_service: S3Service) -> None:
    s3_service.delete_file("test.txt")
    mock_client.return_value.delete_object.assert_called_once_with(
        Bucket="tacitus-files",
        Key="test.txt",
    )
