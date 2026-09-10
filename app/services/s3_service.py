"""S3 storage service."""

import asyncio
import logging
import posixpath
import re
from datetime import datetime
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


class S3InvalidCursorError(Exception):
    """Raised when S3 rejects the pagination cursor supplied by the client.

    Continuation tokens travel through the client, so a malformed or expired
    one is a bad request, not a server fault. Without this distinction boto3's
    ``InvalidArgument`` would surface as a 500.
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


# Maximum key length accepted by S3, in bytes.
MAX_OBJECT_KEY_LENGTH = 1024

# Characters allowed in a single path segment of an object key. Deliberately the
# same alphabet ``sanitize_filename`` accepts: MediaMTX writes recordings as
# ``<stream>/<YYYY-MM-DD_HH-MM-SS-ffffff>.mp4`` and nothing outside it.
_KEY_SEGMENT_RE = re.compile(r"^[a-zA-Z0-9._-]+$")


def sanitize_object_key(key: str, allowed_prefix: str) -> str:
    """Validate a multi-segment object key and confine it to one prefix.

    Unlike :func:`sanitize_filename`, which flattens its input to a bare
    basename, this function keeps the directory structure: archived recordings
    live under keys such as ``mediamtx-archives/cam1/2026-09-10_12-00-00-0.mp4``
    and cannot be addressed without slashes. Keeping the slashes means the
    traversal checks have to be done per segment instead of being sidestepped by
    ``basename``, and it means the prefix check is the only thing standing
    between a caller and every other object in the shared bucket.

    Args:
        key: Raw object key received from a client. URL decoding has already
            happened by the time a path parameter reaches this function, so
            ``%2e%2e%2f`` arrives here as ``../``.
        allowed_prefix: Prefix the key must start with, trailing slash included
            (see ``Settings.s3_archive_prefix``, which guarantees that shape).

    Returns:
        The normalized key, safe to use as an S3 ``Key``.

    Raises:
        ValueError: If the key is empty, absolute, too long, contains traversal
            segments or hidden files, uses characters outside the allowed
            alphabet, or falls outside ``allowed_prefix``.
    """
    if "\x00" in key:
        raise ValueError("Object key contains null bytes")
    normalized = key.replace("\\", "/")
    if not normalized:
        raise ValueError("Object key is empty")
    if normalized.startswith("/"):
        raise ValueError("Path traversal detected")
    if len(normalized.encode("utf-8")) > MAX_OBJECT_KEY_LENGTH:
        raise ValueError("Object key too long")

    for segment in normalized.split("/"):
        if not segment:
            raise ValueError("Object key contains an empty path segment")
        if set(segment) == {"."}:
            # Catches ``.`` and ``..`` — both of which the alphabet below would
            # otherwise accept, since a dot is a legal filename character.
            raise ValueError("Path traversal detected")
        if segment.startswith("."):
            raise ValueError("Hidden files are not allowed")
        if not _KEY_SEGMENT_RE.match(segment):
            raise ValueError("Object key contains invalid characters")

    # Last, so that a traversal attempt is reported as traversal rather than as
    # a prefix mismatch.
    if not normalized.startswith(allowed_prefix):
        raise ValueError("Object key is outside the allowed prefix")
    return normalized


class ObjectSummary(TypedDict):
    """One object as returned by a paginated listing."""

    key: str
    size_bytes: int
    last_modified: datetime


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
    """Service for S3 file operations.

    boto3 is a synchronous SDK: every call performs blocking network I/O.
    Calling it directly from async endpoints would stall the event loop for
    the duration of each S3 request, freezing ALL concurrent requests of the
    worker. Public methods are therefore ``async`` and offload the blocking
    work to a thread pool via :func:`asyncio.to_thread`, while the actual
    logic lives in the ``_sync_*`` methods.
    """

    def __init__(self) -> None:
        self.endpoint = settings.s3_endpoint
        self.access_key = settings.s3_access_key
        self.secret_key = settings.s3_secret_key
        self.bucket = settings.s3_bucket
        self._client: Any | None = None

    def _get_client(self) -> Any:
        """Create the boto3 client lazily (not thread-safe on first call only)."""
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

    def _sync_list_all_objects(self) -> list[dict[str, Any]]:
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

    def _sync_list_objects_page(
        self,
        prefix: str,
        limit: int,
        cursor: str | None,
    ) -> tuple[list[ObjectSummary], str | None]:
        """Blocking part of the paginated listing: one ``list_objects_v2`` call."""
        kwargs: dict[str, Any] = {"Bucket": self.bucket, "MaxKeys": limit}
        if prefix:
            kwargs["Prefix"] = prefix
        if cursor:
            kwargs["ContinuationToken"] = cursor
        try:
            response = self._get_client().list_objects_v2(**kwargs)
        except ClientError as exc:
            if exc.response.get("Error", {}).get("Code") == "InvalidArgument":
                raise S3InvalidCursorError(cursor or "") from exc
            raise

        items: list[ObjectSummary] = [
            ObjectSummary(
                key=obj["Key"],
                size_bytes=obj["Size"],
                last_modified=obj["LastModified"],
            )
            for obj in response.get("Contents", [])
        ]
        # ``NextContinuationToken`` is only meaningful while the response is
        # truncated; returning it unconditionally would make the caller ask for
        # a page that does not exist.
        next_cursor = response.get("NextContinuationToken") if response.get("IsTruncated") else None
        return items, next_cursor

    async def list_objects(
        self,
        prefix: str = "",
        limit: int = 100,
        cursor: str | None = None,
    ) -> tuple[list[ObjectSummary], str | None]:
        """Return a single page of objects, newest pagination state included.

        Deliberately one page and not the whole bucket: the archive grows by a
        segment per hour per stream, and a listing that walks every key would
        make the response time a function of how long the service has been
        recording.

        Args:
            prefix: Key prefix to list; empty string lists the whole bucket.
            limit: Maximum number of keys in the page (S3 caps this at 1000).
            cursor: Continuation token returned by a previous call.

        Returns:
            Tuple of ``(items, next_cursor)``; ``next_cursor`` is ``None`` on the
            last page.

        Raises:
            S3InvalidCursorError: If S3 rejects ``cursor``.
            ClientError: If S3 returns any other error.
        """
        try:
            return await asyncio.to_thread(self._sync_list_objects_page, prefix, limit, cursor)
        except S3InvalidCursorError:
            logger.warning("S3 rejected the pagination cursor", extra={"prefix": prefix})
            raise
        except ClientError as e:
            logger.error("Failed to list objects", extra={"prefix": prefix, "error": str(e)})
            raise

    async def list_files(self) -> list[str]:
        """Return list of file names in bucket.

        Follows S3 pagination so buckets with more than 1000 objects are
        listed completely.

        Returns:
            List of object keys.
        """
        try:
            objects = await asyncio.to_thread(self._sync_list_all_objects)
            return [obj["Key"] for obj in objects]
        except ClientError as e:
            logger.error("Failed to list files", extra={"error": str(e)})
            raise

    def _sync_upload_file(self, sanitized: str, content: bytes) -> None:
        """Blocking part of the upload: single ``put_object`` call."""
        self._get_client().put_object(
            Bucket=self.bucket,
            Key=sanitized,
            Body=content,
        )

    async def upload_file(self, filename: str, content: bytes) -> str:
        """Upload file to S3 bucket.

        Args:
            filename: Name of the file.
            content: File bytes.

        Returns:
            Sanitized filename that was used as the S3 object key.
        """
        sanitized = sanitize_filename(filename)
        try:
            await asyncio.to_thread(self._sync_upload_file, sanitized, content)
            logger.info(
                "File uploaded",
                extra={"file_name": sanitized, "bucket": self.bucket},
            )
            return sanitized
        except ClientError as e:
            logger.error("Failed to upload file", extra={"file_name": sanitized, "error": str(e)})
            raise

    def _sync_get_presigned_url(self, sanitized: str, expiration: int) -> str:
        """Blocking part of URL generation: existence check + signing."""
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
            return client.generate_presigned_url(
                "get_object",
                Params={"Bucket": self.bucket, "Key": sanitized},
                ExpiresIn=expiration,
            )
        except ClientError as exc:
            logger.error(
                "Failed to generate presigned URL",
                extra={"file_name": sanitized, "error": str(exc)},
            )
            raise

    async def get_presigned_url(self, filename: str, expiration: int | None = None) -> str:
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
        return await self.get_presigned_url_for_key(sanitize_filename(filename), expiration)

    async def get_presigned_url_for_key(self, key: str, expiration: int | None = None) -> str:
        """Generate a presigned URL for an already-validated object key.

        Same authentication model as :meth:`get_presigned_url`, with one
        difference: this method performs **no** validation of its own. It exists
        for keys that legitimately contain slashes (archived recordings), which
        :func:`sanitize_filename` cannot express. The caller is responsible for
        having run :func:`sanitize_object_key` — passing raw client input here
        hands out signed URLs for arbitrary objects in the bucket.

        Args:
            key: Object key that has already passed :func:`sanitize_object_key`.
            expiration: URL expiration time in seconds; ``None`` means
                ``settings.presigned_url_expiration``.

        Returns:
            Presigned URL string.

        Raises:
            S3FileNotFoundError: If the object does not exist in the bucket.
            ClientError: If S3 returns an unexpected error.
        """
        if expiration is None:
            expiration = settings.presigned_url_expiration
        return await asyncio.to_thread(self._sync_get_presigned_url, key, expiration)

    def _sync_delete_file(self, sanitized: str) -> None:
        """Blocking part of the deletion: single ``delete_object`` call."""
        self._get_client().delete_object(Bucket=self.bucket, Key=sanitized)

    async def delete_file(self, filename: str) -> None:
        """Delete file from S3 bucket.

        Args:
            filename: Name of the file.
        """
        await self.delete_object(sanitize_filename(filename))

    async def delete_object(self, key: str) -> None:
        """Delete an object by an already-validated key.

        Performs no validation of its own, for the same reason as
        :meth:`get_presigned_url_for_key`: the caller must have run
        :func:`sanitize_object_key` first. Deletion is irreversible — an
        unvalidated key here removes arbitrary objects from the bucket.

        Args:
            key: Object key that has already passed validation.

        Raises:
            ClientError: If S3 returns an error.
        """
        try:
            await asyncio.to_thread(self._sync_delete_file, key)
            logger.info(
                "Object deleted",
                extra={"file_name": key, "bucket": self.bucket},
            )
        except ClientError as e:
            logger.error("Failed to delete object", extra={"file_name": key, "error": str(e)})
            raise

    def _sync_get_bucket_stats(self) -> _BucketStats:
        """Blocking part of stats computation over the full object listing."""
        contents = self._sync_list_all_objects()
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
            "largest_file": _LargestFileStat(filename=largest["Key"], size_bytes=largest["Size"]),
        }

    async def get_bucket_stats(self) -> _BucketStats:
        """Return statistics for the S3 bucket.

        Follows S3 pagination so statistics cover every object in the bucket,
        not just the first 1000 returned by a single ``list_objects_v2`` call.

        Returns:
            Dictionary with total_files, total_size_bytes, total_size_human,
            average_size_bytes, and largest_file.
        """
        try:
            return await asyncio.to_thread(self._sync_get_bucket_stats)
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
