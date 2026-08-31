"""File management router."""

import logging
import posixpath

from fastapi import APIRouter, Depends, HTTPException, Request, UploadFile

from app.api.dependencies import get_current_user
from app.core.ip import get_client_ip
from app.core.rate_limit import get_user_or_ip, limiter
from app.core.security_logging import log_security_event
from app.schemas.files import (
    FileDeleteResponse,
    FileListResponse,
    FileUploadResponse,
    FileUrlResponse,
)
from app.schemas.stats import FileStat, S3StatsResponse
from app.services.s3_service import (
    ALLOWED_TYPES,
    FORBIDDEN_EXTENSIONS,
    S3FileNotFoundError,
    s3_service,
    sanitize_filename,
)

router = APIRouter(prefix="/files", tags=["files"])
logger = logging.getLogger(__name__)

# Maximum upload size (10 MB). Checked while streaming the request body so a
# malicious client cannot exhaust server memory before the limit kicks in.
MAX_UPLOAD_SIZE = 10 * 1024 * 1024
# Size of chunks read from the upload stream.
_UPLOAD_CHUNK_SIZE = 1024 * 1024


async def _read_upload_stream(file: UploadFile, max_size: int) -> bytes:
    """Read an upload in bounded chunks, enforcing the size limit eagerly.

    Reading the whole body first (``await file.read()``) allows a client to
    push an arbitrarily large payload — potentially gigabytes via chunked
    transfer encoding — into memory before any check happens. Instead this
    helper consumes the stream in fixed chunks and aborts as soon as the
    cumulative size exceeds ``max_size``.

    Args:
        file: Uploaded file handle.
        max_size: Maximum allowed size in bytes.

    Returns:
        File content.

    Raises:
        HTTPException: 413 if the payload exceeds ``max_size``.
    """
    chunks: list[bytes] = []
    total = 0
    while True:
        chunk = await file.read(_UPLOAD_CHUNK_SIZE)
        if not chunk:
            break
        total += len(chunk)
        if total > max_size:
            raise HTTPException(status_code=413, detail="File too large (max 10 MB)")
        chunks.append(chunk)
    return b"".join(chunks)


def _validate_filename(filename: str) -> str:
    """Sanitize filename and return clean version.

    Args:
        filename: Raw filename.

    Returns:
        Sanitized filename.

    Raises:
        HTTPException: 400 if filename is invalid.
    """
    try:
        return sanitize_filename(filename)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("/", response_model=FileListResponse)
@limiter.limit("60/minute", key_func=get_user_or_ip)
async def list_files(
    request: Request,
    current_user: str = Depends(get_current_user),
) -> FileListResponse:
    """List all files in S3 bucket.

    Args:
        request: FastAPI request object.
        current_user: Authenticated username.

    Returns:
        FileListResponse with list of filenames.
    """
    request_id = getattr(request.state, "request_id", "unknown")
    logger.info("Listing files", extra={"request_id": request_id, "user": current_user})
    files = await s3_service.list_files()
    return FileListResponse(files=files)


@router.post("/upload", response_model=FileUploadResponse)
@limiter.limit("10/minute", key_func=get_user_or_ip)
async def upload_file(
    request: Request,
    file: UploadFile,
    current_user: str = Depends(get_current_user),
) -> FileUploadResponse:
    """Upload file to S3 bucket.

    Args:
        request: FastAPI request object.
        file: Uploaded file.
        current_user: Authenticated username.

    Returns:
        FileUploadResponse with uploaded filename.
    """
    request_id = getattr(request.state, "request_id", "unknown")
    content = await _read_upload_stream(file, MAX_UPLOAD_SIZE)
    if file.content_type not in ALLOWED_TYPES:
        raise HTTPException(
            status_code=415,
            detail=f"Unsupported media type: {file.content_type}",
        )

    filename = file.filename or "unnamed"
    sanitized = _validate_filename(filename)

    _, ext = posixpath.splitext(sanitized.lower())
    if ext in FORBIDDEN_EXTENSIONS:
        raise HTTPException(status_code=400, detail="Forbidden file extension")
    if ext not in ALLOWED_TYPES.get(file.content_type, set()):
        raise HTTPException(
            status_code=415,
            detail="File extension does not match content type",
        )

    log_security_event(
        "file.upload",
        ip=get_client_ip(request),
        user=current_user,
        extra={"request_id": request_id, "file_name": sanitized, "size": len(content)},
    )
    await s3_service.upload_file(sanitized, content)
    return FileUploadResponse(filename=sanitized)


@router.get("/stats", response_model=S3StatsResponse)
@limiter.limit("10/minute", key_func=get_user_or_ip)
async def get_stats(
    request: Request,
    current_user: str = Depends(get_current_user),
) -> S3StatsResponse:
    """Get S3 bucket statistics.

    Args:
        request: FastAPI request object.
        current_user: Authenticated username.

    Returns:
        S3StatsResponse with bucket statistics.
    """
    request_id = getattr(request.state, "request_id", "unknown")
    logger.info("Fetching bucket stats", extra={"request_id": request_id, "user": current_user})
    stats = await s3_service.get_bucket_stats()
    largest = stats.get("largest_file")
    return S3StatsResponse(
        total_files=stats["total_files"],
        total_size_bytes=stats["total_size_bytes"],
        total_size_human=stats["total_size_human"],
        average_size_bytes=stats["average_size_bytes"],
        largest_file=FileStat(**largest) if largest else None,
    )


@router.get("/{filename}", response_model=FileUrlResponse)
@limiter.limit("30/minute", key_func=get_user_or_ip)
async def get_file_url(
    request: Request,
    filename: str,
    current_user: str = Depends(get_current_user),
) -> FileUrlResponse:
    """Return a presigned URL for downloading a file from S3/MinIO.

    Authentication flow for S3 downloads:

    1. ``get_current_user`` validates the JWT Bearer token, including a database
       check against the revoked token blacklist.
    2. The filename is sanitized to prevent path traversal and hidden-file
       access.
    3. ``S3Service`` verifies that the object exists and generates a short-lived
       presigned URL signed with the application's S3 credentials.
    4. The client downloads the file directly from S3/MinIO using the returned
       URL; no additional client credentials are required.

    Args:
        request: FastAPI request object.
        filename: Name of the file.
        current_user: Authenticated username.

    Returns:
        FileUrlResponse with presigned URL.

    Raises:
        HTTPException: 400 if the filename is invalid; 404 if the file does not
            exist in S3; 429 if the rate limit is exceeded.
    """
    request_id = getattr(request.state, "request_id", "unknown")
    sanitized = _validate_filename(filename)

    log_security_event(
        "file.download",
        ip=get_client_ip(request),
        user=current_user,
        extra={"request_id": request_id, "file_name": sanitized},
    )

    try:
        url = await s3_service.get_presigned_url(sanitized)
    except S3FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail="File not found") from exc

    return FileUrlResponse(filename=sanitized, url=url)


@router.delete("/{filename}", response_model=FileDeleteResponse)
@limiter.limit("10/minute", key_func=get_user_or_ip)
async def delete_file(
    request: Request,
    filename: str,
    current_user: str = Depends(get_current_user),
) -> FileDeleteResponse:
    """Delete file from S3 bucket.

    Args:
        request: FastAPI request object.
        filename: Name of the file.
        current_user: Authenticated username.

    Returns:
        FileDeleteResponse with deleted filename.
    """
    request_id = getattr(request.state, "request_id", "unknown")
    sanitized = _validate_filename(filename)
    log_security_event(
        "file.delete",
        ip=get_client_ip(request),
        user=current_user,
        extra={"request_id": request_id, "file_name": sanitized},
    )
    await s3_service.delete_file(sanitized)
    return FileDeleteResponse(filename=sanitized)
