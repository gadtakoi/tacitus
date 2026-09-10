"""Schemas for archived recordings.

A **recording** is an fmp4 segment written by MediaMTX; once it has been
uploaded to S3/MinIO and removed from local disk it becomes an **archive**
(CONTEXT.md). These schemas describe the archive as the API exposes it: the
object key is the identifier, because the archive manager derives it from the
stream name and the segment start time and nothing else identifies a segment.
"""

from datetime import datetime

from pydantic import BaseModel


class RecordingItem(BaseModel):
    """One archived segment in the object storage."""

    # Full S3 object key, archive prefix included: the value to pass back to
    # ``GET /recordings/{key}``.
    key: str
    # Stream the segment belongs to — the first key segment after the archive
    # prefix. Empty when the key has no stream component, which only happens
    # for objects written by something other than the archive manager.
    stream: str
    size_bytes: int
    last_modified: datetime


class RecordingListResponse(BaseModel):
    """One page of archived segments."""

    items: list[RecordingItem]
    # Opaque S3 continuation token. ``None`` means this was the last page;
    # anything else is passed back verbatim as ``?cursor=``.
    next_cursor: str | None = None


class RecordingUrlResponse(BaseModel):
    """Response schema for a recording download URL."""

    key: str
    url: str


class RecordingDeleteResponse(BaseModel):
    """Response schema for recording deletion."""

    key: str
    message: str = "Recording deleted successfully"
