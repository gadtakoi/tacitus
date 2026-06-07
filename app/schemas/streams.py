"""Schemas for MediaMTX stream monitoring."""

from pydantic import BaseModel, ConfigDict, Field


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
