"""File management router."""

import logging

from fastapi import APIRouter, Request, UploadFile

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
async def list_files(request: Request) -> FileListResponse:
    """List all files in S3 bucket.

    Args:
        request: FastAPI request object.

    Returns:
        FileListResponse with list of filenames.
    """
    request_id = getattr(request.state, "request_id", "unknown")
    logger.info("Listing files", extra={"request_id": request_id})
    files = s3_service.list_files()
    return FileListResponse(files=files)


@router.post("/upload", response_model=FileUploadResponse)
async def upload_file(
    request: Request,
    file: UploadFile,
) -> FileUploadResponse:
    """Upload file to S3 bucket.

    Args:
        request: FastAPI request object.
        file: Uploaded file.

    Returns:
        FileUploadResponse with uploaded filename.
    """
    request_id = getattr(request.state, "request_id", "unknown")
    content = await file.read()
    filename = file.filename or "unnamed"
    logger.info(
        "Uploading file",
        extra={"request_id": request_id, "file_name": filename, "size": len(content)},
    )
    s3_service.upload_file(filename, content)
    return FileUploadResponse(filename=filename)


@router.get("/{filename}", response_model=FileUrlResponse)
async def get_file_url(request: Request, filename: str) -> FileUrlResponse:
    """Get presigned URL for file download.

    Args:
        request: FastAPI request object.
        filename: Name of the file.

    Returns:
        FileUrlResponse with presigned URL.
    """
    request_id = getattr(request.state, "request_id", "unknown")
    logger.info(
        "Generating presigned URL",
        extra={"request_id": request_id, "file_name": filename},
    )
    url = s3_service.get_presigned_url(filename)
    return FileUrlResponse(filename=filename, url=url)


@router.delete("/{filename}", response_model=FileDeleteResponse)
async def delete_file(request: Request, filename: str) -> FileDeleteResponse:
    """Delete file from S3 bucket.

    Args:
        request: FastAPI request object.
        filename: Name of the file.

    Returns:
        FileDeleteResponse with deleted filename.
    """
    request_id = getattr(request.state, "request_id", "unknown")
    logger.info(
        "Deleting file",
        extra={"request_id": request_id, "file_name": filename},
    )
    s3_service.delete_file(filename)
    return FileDeleteResponse(filename=filename)
