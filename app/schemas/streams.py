"""Schemas for MediaMTX stream monitoring and configuration."""

from datetime import UTC, datetime
from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field, field_validator


class StreamSource(BaseModel):
    """Source of a stream (publisher)."""

    type: str
    id: str


class StreamReader(BaseModel):
    """Reader/client of a stream."""

    type: str
    id: str


class StreamItem(BaseModel):
    """Single active stream/path from MediaMTX."""

    model_config = ConfigDict(populate_by_name=True)

    name: str
    conf_name: str = Field(alias="confName")
    source: StreamSource | None = None
    ready: bool
    ready_time: str | None = Field(alias="readyTime", default=None)
    tracks: list[str] | None = None
    bytes_received: int | None = Field(alias="bytesReceived", default=None)
    bytes_sent: int | None = Field(alias="bytesSent", default=None)
    readers: list[StreamReader] | None = None


class StreamsListResponse(BaseModel):
    """Response schema for MediaMTX /v3/paths/list."""

    model_config = ConfigDict(populate_by_name=True)

    page_count: int = Field(alias="pageCount")
    item_count: int = Field(alias="itemCount")
    items: list[StreamItem]


class StreamCredentials(BaseModel):
    """Publish/read credentials for a stream path."""

    publish_user: str | None = Field(default=None, min_length=1, max_length=128)
    publish_pass: str | None = Field(default=None, min_length=1, max_length=128)
    read_user: str | None = Field(default=None, min_length=1, max_length=128)
    read_pass: str | None = Field(default=None, min_length=1, max_length=128)


class StreamConfig(BaseModel):
    """Full stream configuration stored in Tacitus."""

    model_config = ConfigDict(populate_by_name=True)

    name: Annotated[str, Field(min_length=1, max_length=64)]
    source: str = Field(default="publisher", max_length=512)
    record_enabled: bool = True
    credentials: StreamCredentials = Field(default_factory=StreamCredentials)
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(UTC))

    @field_validator("name")
    @classmethod
    def validate_name(cls, v: str) -> str:
        """Validate stream path name.

        Rules:
            - ASCII letters, digits, hyphen, underscore only
            - Must not start or end with hyphen
            - Length 1-64 characters
        """
        if not v:
            raise ValueError("Stream name cannot be empty")
        if v.startswith("-") or v.endswith("-"):
            raise ValueError("Stream name must not start or end with a hyphen")
        allowed = set("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_-")
        if not all(c in allowed for c in v):
            raise ValueError(
                "Stream name may only contain ASCII letters, digits, underscore and hyphen"
            )
        return v

    @field_validator("source")
    @classmethod
    def validate_source(cls, v: str) -> str:
        """Validate source string."""
        if not v:
            raise ValueError("Source cannot be empty")
        if v != "publisher" and not v.startswith(("rtsp://", "rtsps://", "rtmp://")):
            raise ValueError("Source must be 'publisher' or a valid RTSP/RTSPS/RTMP URL")
        return v


class StreamCreateRequest(BaseModel):
    """Request schema for creating a new stream."""

    name: Annotated[str, Field(min_length=1, max_length=64)]
    source: str = Field(default="publisher", max_length=512)
    record_enabled: bool = True
    credentials: StreamCredentials | None = None

    @field_validator("name")
    @classmethod
    def validate_name(cls, v: str) -> str:
        """Validate stream path name."""
        if not v:
            raise ValueError("Stream name cannot be empty")
        if v.startswith("-") or v.endswith("-"):
            raise ValueError("Stream name must not start or end with a hyphen")
        allowed = set("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_-")
        if not all(c in allowed for c in v):
            raise ValueError(
                "Stream name may only contain ASCII letters, digits, underscore and hyphen"
            )
        return v

    @field_validator("source")
    @classmethod
    def validate_source(cls, v: str) -> str:
        """Validate source string."""
        if not v:
            raise ValueError("Source cannot be empty")
        if v != "publisher" and not v.startswith(("rtsp://", "rtsps://", "rtmp://")):
            raise ValueError("Source must be 'publisher' or a valid RTSP/RTSPS/RTMP URL")
        return v


class StreamUpdateRequest(BaseModel):
    """Request schema for updating an existing stream."""

    source: str | None = Field(default=None, max_length=512)
    record_enabled: bool | None = None
    credentials: StreamCredentials | None = None

    @field_validator("source")
    @classmethod
    def validate_source(cls, v: str | None) -> str | None:
        """Validate source string."""
        if v is None:
            return v
        if v != "publisher" and not v.startswith(("rtsp://", "rtsps://", "rtmp://")):
            raise ValueError("Source must be 'publisher' or a valid RTSP/RTSPS/RTMP URL")
        return v
