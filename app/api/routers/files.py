"""File management router."""

import logging

from fastapi import APIRouter, Depends, Request, UploadFile

from app.api.dependencies import get_current_user
from app.schemas.files import (
    FileDeleteResponse,
    FileListResponse,
    FileUploadResponse,
    FileUrlResponse,
)
from app.services.s3_service import s3_service

router = APIRouter(prefix="/files", tags=["files"])
logger = logging.getLogger(__name__)


@router.get("/", response_model=FileListResponse)
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
