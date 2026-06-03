"""Schemas for file operations."""

from pydantic import BaseModel


class FileUploadResponse(BaseModel):
    """Response schema for file upload."""

    filename: str
    message: str = "File uploaded successfully"


class FileListResponse(BaseModel):
    """Response schema for file list."""

    files: list[str]


class FileUrlResponse(BaseModel):
    """Response schema for file download URL."""

    filename: str
    url: str


class FileDeleteResponse(BaseModel):
    """Response schema for file deletion."""

    filename: str
    message: str = "File deleted successfully"
