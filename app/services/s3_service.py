"""S3 storage service."""

import logging
import posixpath
import re
from typing import Any, TypedDict

import boto3
from botocore.client import Config
from botocore.exceptions import ClientError

from app.core.config import settings

logger = logging.getLogger(__name__)

ALLOWED_TYPES: dict[str, set[str]] = {
    "image/jpeg": {".jpg", ".jpeg"},
    "image/png": {".png"},
    "image/gif": {".gif"},
    "image/webp": {".webp"},
    "application/pdf": {".pdf"},
    "text/plain": {".txt"},
    "text/markdown": {".md"},
    "application/json": {".json"},
    "application/zip": {".zip"},
}

FORBIDDEN_EXTENSIONS: set[str] = {
    ".exe",
    ".bat",
    ".sh",
    ".py",
    ".js",
    ".php",
}


def sanitize_filename(filename: str) -> str:
    """Sanitize filename to prevent path traversal and unsafe names.

    Args:
        filename: Raw filename.

    Returns:
        Sanitized filename.

    Raises:
        ValueError: If filename is invalid or unsafe.
    """
    if "\x00" in filename:
        raise ValueError("Filename contains null bytes")
    if ".." in filename or filename.startswith("/") or filename.startswith("\\"):
        raise ValueError("Path traversal detected")
    # Normalize path separators and take basename
    filename = posixpath.basename(filename.replace("\\", "/"))
    if ".." in filename or filename.startswith("/"):
        raise ValueError("Path traversal detected")
    if len(filename) > 255:
        raise ValueError("Filename too long")
    if filename.startswith("."):
        raise ValueError("Hidden files are not allowed")
    if not re.match(r"^[a-zA-Z0-9._-]+$", filename):
        raise ValueError("Filename contains invalid characters")
    return filename


class _LargestFileStat(TypedDict):
    """Typed representation of the largest file returned by bucket stats."""

    filename: str
    size_bytes: int


class _BucketStats(TypedDict):
    """Typed representation of bucket statistics returned by S3Service."""

    total_files: int
    total_size_bytes: int
    total_size_human: str
    average_size_bytes: float
    largest_file: _LargestFileStat | None


class S3Service:
    """Service for S3 file operations."""

    def __init__(self) -> None:
        self.endpoint = settings.s3_endpoint
        self.access_key = settings.s3_access_key
        self.secret_key = settings.s3_secret_key
        self.bucket = settings.s3_bucket
        self._client: Any | None = None

    def _get_client(self) -> Any:
        if self._client is None:
            self._client = boto3.client(
                "s3",
                endpoint_url=self.endpoint,
                aws_access_key_id=self.access_key,
                aws_secret_access_key=self.secret_key,
                config=Config(
                    signature_version="s3v4",
                    connect_timeout=5,
                    read_timeout=10,
                ),
            )
        return self._client

    def list_files(self) -> list[str]:
        """Return list of file names in bucket.

        Returns:
            List of object keys.
        """
        try:
            response = self._get_client().list_objects_v2(Bucket=self.bucket)
            contents = response.get("Contents", [])
            return [obj["Key"] for obj in contents]
        except ClientError as e:
            logger.error("Failed to list files", extra={"error": str(e)})
            raise

    def upload_file(self, filename: str, content: bytes) -> str:
        """Upload file to S3 bucket.

        Args:
            filename: Name of the file.
            content: File bytes.

        Returns:
            Uploaded filename.
        """
        sanitized = sanitize_filename(filename)
        try:
            self._get_client().put_object(
                Bucket=self.bucket,
                Key=sanitized,
                Body=content,
            )
            logger.info(
                "File uploaded",
                extra={"file_name": filename, "bucket": self.bucket},
            )
            return filename
        except ClientError as e:
            logger.error("Failed to upload file", extra={"file_name": filename, "error": str(e)})
            raise

    def get_presigned_url(self, filename: str, expiration: int = 3600) -> str:
        """Generate presigned URL for file download.

        Args:
            filename: Name of the file.
            expiration: URL expiration time in seconds.

        Returns:
            Presigned URL string.
        """
        sanitized = sanitize_filename(filename)
        try:
            url = self._get_client().generate_presigned_url(
                "get_object",
                Params={"Bucket": self.bucket, "Key": sanitized},
                ExpiresIn=expiration,
            )
            return url
        except ClientError as e:
            logger.error(
                "Failed to generate presigned URL",
                extra={"file_name": filename, "error": str(e)},
            )
            raise

    def delete_file(self, filename: str) -> None:
        """Delete file from S3 bucket.

        Args:
            filename: Name of the file.
        """
        sanitized = sanitize_filename(filename)
        try:
            self._get_client().delete_object(Bucket=self.bucket, Key=sanitized)
            logger.info(
                "File deleted",
                extra={"file_name": filename, "bucket": self.bucket},
            )
        except ClientError as e:
            logger.error("Failed to delete file", extra={"file_name": filename, "error": str(e)})
            raise

    def get_bucket_stats(self) -> _BucketStats:
        """Return statistics for the S3 bucket.

        Returns:
            Dictionary with total_files, total_size_bytes, total_size_human,
            average_size_bytes, and largest_file.
        """
        try:
            response = self._get_client().list_objects_v2(Bucket=self.bucket)
            contents = response.get("Contents", [])
            if not contents:
                return {
                    "total_files": 0,
                    "total_size_bytes": 0,
                    "total_size_human": "0 B",
                    "average_size_bytes": 0.0,
                    "largest_file": None,
                }

            total_files = len(contents)
            total_size = sum(obj["Size"] for obj in contents)
            average_size = total_size / total_files
            largest = max(contents, key=lambda obj: obj["Size"])

            return {
                "total_files": total_files,
                "total_size_bytes": total_size,
                "total_size_human": _human_readable_size(total_size),
                "average_size_bytes": average_size,
                "largest_file": _LargestFileStat(
                    filename=largest["Key"], size_bytes=largest["Size"]
                ),
            }
        except ClientError as e:
            logger.error("Failed to get bucket stats", extra={"error": str(e)})
            raise


def _human_readable_size(size_bytes: int) -> str:
    """Convert bytes to human readable string.

    Args:
        size_bytes: Size in bytes.

    Returns:
        Human readable size string.
    """
    if size_bytes == 0:
        return "0 B"
    units = ["B", "KB", "MB", "GB", "TB"]
    idx = 0
    size = float(size_bytes)
    while size >= 1024 and idx < len(units) - 1:
        size /= 1024
        idx += 1
    return f"{size:.1f} {units[idx]}"


s3_service = S3Service()
