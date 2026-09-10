"""Schemas for S3 bucket statistics."""

from pydantic import BaseModel


class FileStat(BaseModel):
    """Single file statistic."""

    filename: str
    size_bytes: int


class S3StatsResponse(BaseModel):
    """Response schema for S3 bucket statistics.

    The ``total_*`` fields describe user files — the objects ``GET /files/``
    lists. Archived recordings live in the same bucket under the archive prefix
    and are counted separately, so the summary shown next to a list of files
    describes that list and not the whole bucket.
    """

    total_files: int
    total_size_bytes: int
    total_size_human: str
    average_size_bytes: float
    largest_file: FileStat | None
    archive_files: int
    archive_size_bytes: int
    archive_size_human: str
