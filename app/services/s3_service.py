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


class S3FileNotFoundError(Exception):
    """Raised when the requested object does not exist in the S3 bucket.

    This exception is converted to an HTTP 404 by the API layer so the service
    stays transport-agnostic.
    """


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

    def _list_all_objects(self) -> list[dict[str, Any]]:
        """Fetch every object in the bucket, following S3 pagination.

        ``list_objects_v2`` returns at most 1000 keys per call. Buckets with
        more objects require following ``NextContinuationToken`` until the
        response is flagged as truncated. Without this loop, listings and
        statistics are silently capped at the first 1000 objects.

        Returns:
            List of raw object descriptors (``Key``, ``Size``, ...).

        Raises:
            ClientError: If S3 returns an error.
        """
        objects: list[dict[str, Any]] = []
        continuation_token: str | None = None
        client = self._get_client()
        while True:
            kwargs: dict[str, Any] = {"Bucket": self.bucket}
            if continuation_token is not None:
                kwargs["ContinuationToken"] = continuation_token
            response = client.list_objects_v2(**kwargs)
            objects.extend(response.get("Contents", []))
            if not response.get("IsTruncated"):
                break
            continuation_token = response.get("NextContinuationToken")
            if not continuation_token:
                # Truncated response without a token: bail out instead of
                # looping forever over the same page.
                logger.error("S3 returned truncated listing without continuation token")
                break
        return objects

    def list_files(self) -> list[str]:
        """Return list of file names in bucket.

        Follows S3 pagination so buckets with more than 1000 objects are
        listed completely.

        Returns:
            List of object keys.
        """
        try:
            objects = self._list_all_objects()
            return [obj["Key"] for obj in objects]
        except ClientError as e:
            logger.error("Failed to list files", extra={"error": str(e)})
            raise

    def upload_file(self, filename: str, content: bytes) -> str:
        """Upload file to S3 bucket.

        Args:
            filename: Name of the file.
            content: File bytes.

        Returns:
            Sanitized filename that was used as the S3 object key.
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
                extra={"file_name": sanitized, "bucket": self.bucket},
            )
            return sanitized
        except ClientError as e:
            logger.error("Failed to upload file", extra={"file_name": sanitized, "error": str(e)})
            raise

    def get_presigned_url(self, filename: str, expiration: int | None = None) -> str:
        """Generate a time-limited presigned URL for downloading an object.

        Authentication model for S3 downloads:

        1. The caller (``GET /files/{filename}``) MUST authenticate the user
           via JWT before invoking this method.
        2. The presigned URL itself is signed with the application's S3
           credentials and is valid only for a short, configurable period
           (default: ``settings.presigned_url_expiration``). It does not require
           the client to present separate S3 credentials.
        3. Because the URL is unguessable and time-bound, sharing it outside the
           authenticated session exposes the file only until the URL expires.

        Args:
            filename: Name of the file.
            expiration: URL expiration time in seconds. When ``None`` (default),
                ``settings.presigned_url_expiration`` is used.

        Returns:
            Presigned URL string.

        Raises:
            S3FileNotFoundError: If the object does not exist in the bucket.
            ClientError: If S3 returns an unexpected error.
        """
        sanitized = sanitize_filename(filename)
        if expiration is None:
            expiration = settings.presigned_url_expiration

        client = self._get_client()
        try:
            # Verify the object exists before handing out a presigned URL.
            # This avoids leaking signed URLs for non-existent files and lets
            # the API return a clean 404.
            client.head_object(Bucket=self.bucket, Key=sanitized)
        except ClientError as exc:
            error_code = exc.response.get("Error", {}).get("Code")
            if error_code in ("404", "NoSuchKey"):
                logger.warning(
                    "Requested file not found in S3",
                    extra={"file_name": sanitized, "bucket": self.bucket},
                )
                raise S3FileNotFoundError(sanitized) from exc
            logger.error(
                "Failed to check file existence",
                extra={"file_name": sanitized, "error": str(exc)},
            )
            raise

        try:
            url = client.generate_presigned_url(
                "get_object",
                Params={"Bucket": self.bucket, "Key": sanitized},
                ExpiresIn=expiration,
            )
            return url
        except ClientError as exc:
            logger.error(
                "Failed to generate presigned URL",
                extra={"file_name": sanitized, "error": str(exc)},
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

        Follows S3 pagination so statistics cover every object in the bucket,
        not just the first 1000 returned by a single ``list_objects_v2`` call.

        Returns:
            Dictionary with total_files, total_size_bytes, total_size_human,
            average_size_bytes, and largest_file.
        """
        try:
            contents = self._list_all_objects()
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
