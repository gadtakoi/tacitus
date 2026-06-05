"""File management router."""

import logging

from fastapi import APIRouter, Depends, HTTPException, Request, UploadFile

from app.api.dependencies import get_current_user
from app.core.rate_limit import get_user_or_ip, limiter
from app.schemas.files import (
    FileDeleteResponse,
    FileListResponse,
    FileUploadResponse,
    FileUrlResponse,
)
from app.schemas.stats import FileStat, S3StatsResponse
from app.services.s3_service import s3_service

router = APIRouter(prefix="/files", tags=["files"])
logger = logging.getLogger(__name__)


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
    files = s3_service.list_files()
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
    content = await file.read()
    max_size = 10 * 1024 * 1024  # 10 MB
    if len(content) > max_size:
        raise HTTPException(status_code=413, detail="File too large (max 10 MB)")
    filename = file.filename or "unnamed"
    logger.info(
        "Uploading file",
        extra={
            "request_id": request_id,
            "file_name": filename,
            "size": len(content),
            "user": current_user,
        },
    )
    s3_service.upload_file(filename, content)
    return FileUploadResponse(filename=filename)


@router.get("/stats", response_model=S3StatsResponse)
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
    stats = s3_service.get_bucket_stats()
    largest = stats.get("largest_file")
    return S3StatsResponse(
        total_files=stats["total_files"],
        total_size_bytes=stats["total_size_bytes"],
        total_size_human=stats["total_size_human"],
        average_size_bytes=stats["average_size_bytes"],
        largest_file=FileStat(**largest) if largest else None,
    )


@router.get("/{filename}", response_model=FileUrlResponse)
async def get_file_url(
    request: Request,
    filename: str,
    current_user: str = Depends(get_current_user),
) -> FileUrlResponse:
    """Get presigned URL for file download.

    Args:
        request: FastAPI request object.
        filename: Name of the file.
        current_user: Authenticated username.

    Returns:
        FileUrlResponse with presigned URL.
    """
    request_id = getattr(request.state, "request_id", "unknown")
    logger.info(
        "Generating presigned URL",
        extra={"request_id": request_id, "file_name": filename, "user": current_user},
    )
    url = s3_service.get_presigned_url(filename)
    return FileUrlResponse(filename=filename, url=url)


@router.delete("/{filename}", response_model=FileDeleteResponse)
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
    logger.info(
        "Deleting file",
        extra={"request_id": request_id, "file_name": filename, "user": current_user},
    )
    s3_service.delete_file(filename)
    return FileDeleteResponse(filename=filename)
