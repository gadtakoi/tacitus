"""Archived recordings router.

Archived segments live in the same bucket as user files but are addressed
differently: their keys carry a stream and a timestamp
(``mediamtx-archives/cam1/2026-09-10_12-00-00-000000.mp4``), so they cannot be
served by ``/files``, whose contract is a single flat, sanitized filename.

This router is therefore a separate resource with its own rules:

* every key is confined to ``settings.s3_archive_prefix`` by
  :func:`sanitize_object_key` — the prefix is the only boundary between the
  archive and the user files sharing the bucket;
* listings are paginated by the S3 continuation token, because the archive
  grows by one segment per hour per stream and a full listing would take
  longer the longer the service has been recording;
* downloads and deletions are recorded as security events, like their
  ``/files`` counterparts.
"""

import logging

from fastapi import APIRouter, Depends, HTTPException, Query, Request

from app.api.dependencies import get_current_user
from app.core.config import settings
from app.core.ip import get_client_ip
from app.core.rate_limit import get_user_or_ip, limiter
from app.core.security_logging import log_security_event
from app.schemas.recordings import (
    RecordingDeleteResponse,
    RecordingItem,
    RecordingListResponse,
    RecordingUrlResponse,
)
from app.schemas.streams import StreamName
from app.services.s3_service import (
    DEFAULT_PAGE_SIZE,
    MAX_PAGE_SIZE,
    S3FileNotFoundError,
    S3InvalidCursorError,
    s3_service,
    sanitize_object_key,
)

router = APIRouter(prefix="/recordings", tags=["recordings"])
logger = logging.getLogger(__name__)


def _validate_key(key: str) -> str:
    """Confine a client-supplied object key to the archive prefix.

    Args:
        key: Raw key from the request path.

    Returns:
        Validated key.

    Raises:
        HTTPException: 400 if the key is malformed or points outside the
            archive prefix.
    """
    try:
        return sanitize_object_key(key, settings.s3_archive_prefix)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


def _stream_of(key: str) -> str:
    """Extract the stream name from an archive key.

    The archive manager mirrors the recording directory into the bucket, so the
    first key segment after the prefix is the MediaMTX path the segment was
    recorded from.

    Args:
        key: Object key starting with the archive prefix.

    Returns:
        Stream name, or an empty string if the key has no stream component.
    """
    relative = key[len(settings.s3_archive_prefix) :]
    head, separator, _ = relative.partition("/")
    return head if separator else ""


@router.get("", response_model=RecordingListResponse)
@limiter.limit("60/minute", key_func=get_user_or_ip)
async def list_recordings(
    request: Request,
    stream: StreamName | None = None,
    limit: int = Query(DEFAULT_PAGE_SIZE, ge=1, le=MAX_PAGE_SIZE),
    cursor: str | None = None,
    current_user: str = Depends(get_current_user),
) -> RecordingListResponse:
    """List one page of archived recordings.

    Args:
        request: FastAPI request object.
        stream: Optional stream name; restricts the listing to that stream.
            Validated against the same alphabet as everywhere else in the API
            before it is substituted into an S3 prefix.
        limit: Page size.
        cursor: Continuation token from a previous response.
        current_user: Authenticated username.

    Returns:
        RecordingListResponse with the page and the cursor for the next one.

    Raises:
        HTTPException: 400 if the cursor is rejected by S3; 429 if the rate
            limit is exceeded.
    """
    request_id = getattr(request.state, "request_id", "unknown")
    prefix = settings.s3_archive_prefix
    if stream:
        prefix = f"{prefix}{stream}/"
    logger.info(
        "Listing recordings",
        extra={"request_id": request_id, "user": current_user, "stream": stream or "*"},
    )

    try:
        objects, next_cursor = await s3_service.list_objects(prefix, limit, cursor)
    except S3InvalidCursorError as exc:
        raise HTTPException(status_code=400, detail="Invalid pagination cursor") from exc

    items = [
        RecordingItem(
            key=obj["key"],
            stream=_stream_of(obj["key"]),
            size_bytes=obj["size_bytes"],
            last_modified=obj["last_modified"],
        )
        for obj in objects
    ]
    return RecordingListResponse(items=items, next_cursor=next_cursor)


@router.get("/{key:path}", response_model=RecordingUrlResponse)
@limiter.limit("30/minute", key_func=get_user_or_ip)
async def get_recording_url(
    request: Request,
    key: str,
    current_user: str = Depends(get_current_user),
) -> RecordingUrlResponse:
    """Return a presigned URL for downloading an archived recording.

    The authentication model is the one documented for ``GET /files/{filename}``
    (see ``docs/security.md``): the JWT protects this endpoint, and the
    presigned URL itself is a short-lived credential signed with the
    application's S3 keys. The difference is the validation step — the key may
    contain slashes and is checked against the archive prefix instead of being
    reduced to a basename.

    Args:
        request: FastAPI request object.
        key: Object key of the recording, archive prefix included.
        current_user: Authenticated username.

    Returns:
        RecordingUrlResponse with the presigned URL.

    Raises:
        HTTPException: 400 if the key is invalid or outside the archive prefix;
            404 if the recording does not exist; 429 if the rate limit is
            exceeded.
    """
    request_id = getattr(request.state, "request_id", "unknown")
    validated = _validate_key(key)

    log_security_event(
        "recording.download",
        ip=get_client_ip(request),
        user=current_user,
        extra={"request_id": request_id, "object_key": validated},
    )

    try:
        url = await s3_service.get_presigned_url_for_key(validated)
    except S3FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail="Recording not found") from exc

    return RecordingUrlResponse(key=validated, url=url)


@router.delete("/{key:path}", response_model=RecordingDeleteResponse)
@limiter.limit("10/minute", key_func=get_user_or_ip)
async def delete_recording(
    request: Request,
    key: str,
    current_user: str = Depends(get_current_user),
) -> RecordingDeleteResponse:
    """Delete an archived recording.

    The deletion is irreversible: the transition «recording → archive» already
    removed the local copy, so the object in S3/MinIO is the only one left.

    Args:
        request: FastAPI request object.
        key: Object key of the recording, archive prefix included.
        current_user: Authenticated username.

    Returns:
        RecordingDeleteResponse with the deleted key.

    Raises:
        HTTPException: 400 if the key is invalid or outside the archive prefix;
            429 if the rate limit is exceeded.
    """
    request_id = getattr(request.state, "request_id", "unknown")
    validated = _validate_key(key)

    log_security_event(
        "recording.delete",
        ip=get_client_ip(request),
        user=current_user,
        extra={"request_id": request_id, "object_key": validated},
    )

    await s3_service.delete_object(validated)
    return RecordingDeleteResponse(key=validated)
