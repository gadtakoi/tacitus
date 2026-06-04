"""Schemas for S3 bucket statistics."""

from pydantic import BaseModel


class FileStat(BaseModel):
    """Single file statistic."""

    filename: str
    size_bytes: int


class S3StatsResponse(BaseModel):
    """Response schema for S3 bucket statistics."""

    total_files: int
    total_size_bytes: int
    total_size_human: str
    average_size_bytes: float
    largest_file: FileStat | None
