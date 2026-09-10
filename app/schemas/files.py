"""Schemas for file operations."""

from pydantic import BaseModel


class FileUploadResponse(BaseModel):
    """Response schema for file upload."""

    filename: str
    message: str = "File uploaded successfully"


class FileListResponse(BaseModel):
    """Response schema for one page of the file list."""

    files: list[str]
    # Opaque S3 continuation token; ``None`` means this was the last page.
    # A page may hold fewer files than requested and still carry a cursor:
    # S3 counts the rolled-up archive prefix against the page size.
    next_cursor: str | None = None


class FileUrlResponse(BaseModel):
    """Response schema for file download URL."""

    filename: str
    url: str


class FileDeleteResponse(BaseModel):
    """Response schema for file deletion."""

    filename: str
    message: str = "File deleted successfully"
