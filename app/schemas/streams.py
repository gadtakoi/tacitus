"""Schemas for MediaMTX stream monitoring and configuration."""

from datetime import UTC, datetime
from typing import Annotated

from pydantic import AfterValidator, BaseModel, ConfigDict, Field


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


# Stream name and source are validated identically wherever they appear, so the
# rules live in one place. They used to be copied verbatim into StreamConfig
# and StreamCreateRequest, which meant a rule could be tightened in one schema
# and silently left lax in the other.
_NAME_ALPHABET = frozenset("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_-")
_SOURCE_SCHEMES = ("rtsp://", "rtsps://", "rtmp://")


def _validate_stream_name(value: str) -> str:
    """Check a stream path name.

    Rules:
        - ASCII letters, digits, hyphen, underscore only
        - Must not start or end with a hyphen
        - Length 1-64 characters (enforced by the field constraints)

    Args:
        value: Candidate stream name.

    Returns:
        The name unchanged.

    Raises:
        ValueError: If the name is empty, hyphen-bounded or has stray characters.
    """
    if not value:
        raise ValueError("Stream name cannot be empty")
    if value.startswith("-") or value.endswith("-"):
        raise ValueError("Stream name must not start or end with a hyphen")
    if not set(value) <= _NAME_ALPHABET:
        raise ValueError(
            "Stream name may only contain ASCII letters, digits, underscore and hyphen"
        )
    return value


def _validate_source(value: str) -> str:
    """Check a stream source.

    Args:
        value: ``publisher`` or an RTSP/RTSPS/RTMP URL.

    Returns:
        The source unchanged.

    Raises:
        ValueError: If the source is empty or is not a supported URL.
    """
    if not value:
        raise ValueError("Source cannot be empty")
    if value != "publisher" and not value.startswith(_SOURCE_SCHEMES):
        raise ValueError("Source must be 'publisher' or a valid RTSP/RTSPS/RTMP URL")
    return value


def _validate_optional_source(value: str | None) -> str | None:
    """Check a source that may be omitted in a partial update.

    Args:
        value: Source to check, or ``None`` when the field is not being updated.

    Returns:
        The source unchanged, including ``None``.
    """
    return value if value is None else _validate_source(value)


StreamName = Annotated[
    str,
    Field(min_length=1, max_length=64),
    AfterValidator(_validate_stream_name),
]
SourceSpec = Annotated[str, Field(max_length=512), AfterValidator(_validate_source)]
OptionalSourceSpec = Annotated[
    str | None,
    Field(max_length=512),
    AfterValidator(_validate_optional_source),
]


class StreamConfig(BaseModel):
    """Full stream configuration stored in Tacitus.

    Publish/read credentials are deliberately absent. MediaMTX 1.15.1 has no
    per-path ``publishUser``/``publishPass``/``readUser``/``readPass``
    parameters — access is granted through the global ``authInternalUsers``
    list, where each entry carries ``permissions[].path``. Storing credentials
    here would mean keeping plaintext passwords that nothing can consume.
    See ADR-012 for the authentication model.
    """

    model_config = ConfigDict(populate_by_name=True)

    name: StreamName
    source: SourceSpec = "publisher"
    record_enabled: bool = True
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


class StreamCreateRequest(BaseModel):
    """Request schema for creating a new stream."""

    name: StreamName
    source: SourceSpec = "publisher"
    record_enabled: bool = True


class StreamUpdateRequest(BaseModel):
    """Request schema for updating an existing stream."""

    source: OptionalSourceSpec = None
    record_enabled: bool | None = None
