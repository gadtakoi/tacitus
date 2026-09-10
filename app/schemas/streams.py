"""Schemas for MediaMTX stream monitoring and configuration."""

from datetime import UTC, datetime
from typing import Annotated

from pydantic import AfterValidator, BaseModel, ConfigDict, Field, model_validator


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


# Crop percentages are bounded twice over: once here, once by CHECK constraints
# on the ``streams`` table. Cropping replaces the recorded segment in place, so
# a setting that leaves nothing of the frame destroys footage that cannot be
# recovered.
#
# The rule that carries the weight is the combined one: whatever the split
# between the two edges, at least a tenth of the frame has to survive. The
# per-edge bound is the same number because removing everything but the bottom
# tenth is a legitimate setting for a camera aimed mostly at the sky.
MAX_CROP_PERCENT = 90
MAX_CROP_TOTAL_PERCENT = 90


def validate_crop_total(top: int, bottom: int) -> None:
    """Check that a crop leaves a usable part of the frame.

    Exposed rather than private because a partial update reaches this rule only
    after the incoming fields have been merged with the stored ones, which
    happens in the service layer.

    Args:
        top: Percentage removed from the top of the frame.
        bottom: Percentage removed from the bottom of the frame.

    Raises:
        ValueError: If the two together remove more than
            ``MAX_CROP_TOTAL_PERCENT`` of the frame height.
    """
    if top + bottom > MAX_CROP_TOTAL_PERCENT:
        raise ValueError(f"Crop percentages together must not exceed {MAX_CROP_TOTAL_PERCENT}")


CropPercent = Annotated[int, Field(ge=0, le=MAX_CROP_PERCENT)]


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
    crop_enabled: bool = False
    crop_top_percent: CropPercent = 0
    crop_bottom_percent: CropPercent = 0
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(UTC))

    @model_validator(mode="after")
    def _check_crop_total(self) -> StreamConfig:
        validate_crop_total(self.crop_top_percent, self.crop_bottom_percent)
        return self


class StreamCreateRequest(BaseModel):
    """Request schema for creating a new stream."""

    name: StreamName
    source: SourceSpec = "publisher"
    record_enabled: bool = True
    crop_enabled: bool = False
    crop_top_percent: CropPercent = 0
    crop_bottom_percent: CropPercent = 0

    @model_validator(mode="after")
    def _check_crop_total(self) -> StreamCreateRequest:
        validate_crop_total(self.crop_top_percent, self.crop_bottom_percent)
        return self


class StreamUpdateRequest(BaseModel):
    """Request schema for updating an existing stream.

    Every field is optional, so the combined crop total cannot be checked here:
    a request that only raises ``crop_top_percent`` is legal or not depending on
    the stored ``crop_bottom_percent``. That rule is applied in the service
    layer, once the update has been merged with the stored row.
    """

    source: OptionalSourceSpec = None
    record_enabled: bool | None = None
    crop_enabled: bool | None = None
    crop_top_percent: CropPercent | None = None
    crop_bottom_percent: CropPercent | None = None
