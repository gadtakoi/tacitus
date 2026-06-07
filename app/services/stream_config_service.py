"""In-memory stream configuration storage and management."""

import logging
import threading
from datetime import UTC, datetime
from typing import Any

from app.schemas.streams import StreamConfig, StreamCreateRequest, StreamUpdateRequest

logger = logging.getLogger(__name__)

# In-memory storage for stream configurations (MVP)
_streams: dict[str, StreamConfig] = {}
_streams_lock = threading.Lock()


class StreamConfigError(Exception):
    """Error related to stream configuration."""

    def __init__(self, message: str) -> None:
        self.message = message
        super().__init__(message)


def create_stream(request: StreamCreateRequest) -> StreamConfig:
    """Create a new stream configuration.

    Args:
        request: Stream creation request.

    Returns:
        Created stream configuration.

    Raises:
        StreamConfigError: If stream with given name already exists.
    """
    with _streams_lock:
        if request.name in _streams:
            raise StreamConfigError(f"Stream '{request.name}' already exists")

        config = StreamConfig(
            name=request.name,
            source=request.source,
            record_enabled=request.record_enabled,
            credentials=request.credentials
            or StreamConfig.model_fields["credentials"].default_factory(),
        )
        _streams[request.name] = config
        logger.info("Stream created", extra={"stream": config.name})
        return config


def list_streams() -> list[StreamConfig]:
    """List all configured streams.

    Returns:
        List of stream configurations ordered by creation time.
    """
    with _streams_lock:
        return sorted(_streams.values(), key=lambda s: s.created_at)


def get_stream(name: str) -> StreamConfig | None:
    """Get stream configuration by name.

    Args:
        name: Stream name/path.

    Returns:
        Stream configuration or None if not found.
    """
    with _streams_lock:
        return _streams.get(name)


def update_stream(name: str, request: StreamUpdateRequest) -> StreamConfig:
    """Update an existing stream configuration.

    Args:
        name: Stream name to update.
        request: Update request with optional fields.

    Returns:
        Updated stream configuration.

    Raises:
        StreamConfigError: If stream does not exist.
    """
    with _streams_lock:
        existing = _streams.get(name)
        if not existing:
            raise StreamConfigError(f"Stream '{name}' not found")

        update_data: dict[str, Any] = {"updated_at": datetime.now(UTC)}
        if request.source is not None:
            update_data["source"] = request.source
        if request.record_enabled is not None:
            update_data["record_enabled"] = request.record_enabled
        if request.credentials is not None:
            update_data["credentials"] = request.credentials

        updated = existing.model_copy(update=update_data)
        _streams[name] = updated
        logger.info("Stream updated", extra={"stream": name})
        return updated


def delete_stream(name: str) -> None:
    """Delete a stream configuration.

    Args:
        name: Stream name to delete.

    Raises:
        StreamConfigError: If stream does not exist.
    """
    with _streams_lock:
        if name not in _streams:
            raise StreamConfigError(f"Stream '{name}' not found")
        del _streams[name]
        logger.info("Stream deleted", extra={"stream": name})


def stream_exists(name: str) -> bool:
    """Check if a stream configuration exists.

    Args:
        name: Stream name to check.

    Returns:
        True if stream exists.
    """
    with _streams_lock:
        return name in _streams


def clear_all_streams() -> None:
    """Clear all stream configurations. Intended for testing only."""
    with _streams_lock:
        _streams.clear()
