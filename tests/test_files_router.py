"""Tests for files router."""

from unittest.mock import MagicMock, patch

from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)


@patch("app.api.routers.files.s3_service")
def test_list_files(mock_s3: MagicMock) -> None:
    mock_s3.list_files.return_value = ["file1.txt", "file2.txt"]
    response = client.get("/files/")
    assert response.status_code == 200
    assert response.json() == {"files": ["file1.txt", "file2.txt"]}


@patch("app.api.routers.files.s3_service")
def test_upload_file(mock_s3: MagicMock) -> None:
    mock_s3.upload_file.return_value = "test.txt"
    response = client.post(
        "/files/upload",
        files={"file": ("test.txt", b"content", "text/plain")},
    )
    assert response.status_code == 200
    data = response.json()
    assert data["filename"] == "test.txt"


@patch("app.api.routers.files.s3_service")
def test_get_file_url(mock_s3: MagicMock) -> None:
    mock_s3.get_presigned_url.return_value = "http://example.com/url"
    response = client.get("/files/test.txt")
    assert response.status_code == 200
    data = response.json()
    assert data["filename"] == "test.txt"
    assert data["url"] == "http://example.com/url"


@patch("app.api.routers.files.s3_service")
def test_delete_file(mock_s3: MagicMock) -> None:
    response = client.delete("/files/test.txt")
    assert response.status_code == 200
    data = response.json()
    assert data["filename"] == "test.txt"
    mock_s3.delete_file.assert_called_once_with("test.txt")
