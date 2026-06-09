"""Stream configuration service backed by PostgreSQL."""

import logging

from sqlalchemy.ext.asyncio import AsyncSession

from app.db.repositories.streams import StreamRepository, StreamRepositoryError
from app.schemas.streams import StreamConfig, StreamCreateRequest, StreamUpdateRequest

logger = logging.getLogger(__name__)


class StreamConfigError(Exception):
    """Error related to stream configuration."""

    def __init__(self, message: str) -> None:
        self.message = message
        super().__init__(message)


async def create_stream(db: AsyncSession, request: StreamCreateRequest) -> StreamConfig:
    """Create a new stream configuration.

    Args:
        db: Database session.
        request: Stream creation request.

    Returns:
        Created stream configuration.

    Raises:
        StreamConfigError: If stream with given name already exists.
    """
    repo = StreamRepository(db)
    try:
        model = await repo.create(request)
    except StreamRepositoryError as exc:
        raise StreamConfigError(exc.message) from exc
    logger.info("Stream created", extra={"stream": model.name})
    return _to_schema(model)


async def list_streams(db: AsyncSession) -> list[StreamConfig]:
    """List all configured streams.

    Args:
        db: Database session.

    Returns:
        List of stream configurations ordered by creation time.
    """
    repo = StreamRepository(db)
    models = await repo.list_all()
    return [_to_schema(m) for m in models]


async def get_stream(db: AsyncSession, name: str) -> StreamConfig | None:
    """Get stream configuration by name.

    Args:
        db: Database session.
        name: Stream name/path.

    Returns:
        Stream configuration or None if not found.
    """
    repo = StreamRepository(db)
    model = await repo.get_by_name(name)
    return _to_schema(model) if model else None


async def update_stream(db: AsyncSession, name: str, request: StreamUpdateRequest) -> StreamConfig:
    """Update an existing stream configuration.

    Args:
        db: Database session.
        name: Stream name to update.
        request: Update request with optional fields.

    Returns:
        Updated stream configuration.

    Raises:
        StreamConfigError: If stream does not exist.
    """
    repo = StreamRepository(db)
    try:
        model = await repo.update(name, request)
    except StreamRepositoryError as exc:
        raise StreamConfigError(exc.message) from exc
    logger.info("Stream updated", extra={"stream": name})
    return _to_schema(model)


async def delete_stream(db: AsyncSession, name: str) -> None:
    """Delete a stream configuration.

    Args:
        db: Database session.
        name: Stream name to delete.

    Raises:
        StreamConfigError: If stream does not exist.
    """
    repo = StreamRepository(db)
    try:
        await repo.delete(name)
    except StreamRepositoryError as exc:
        raise StreamConfigError(exc.message) from exc
    logger.info("Stream deleted", extra={"stream": name})


async def stream_exists(db: AsyncSession, name: str) -> bool:
    """Check if a stream configuration exists.

    Args:
        db: Database session.
        name: Stream name to check.

    Returns:
        True if stream exists.
    """
    repo = StreamRepository(db)
    model = await repo.get_by_name(name)
    return model is not None


def _to_schema(model) -> StreamConfig:
    """Convert SQLAlchemy Stream model to Pydantic StreamConfig schema."""
    from app.schemas.streams import StreamCredentials

    return StreamConfig(
        name=model.name,
        source=model.source,
        record_enabled=model.record_enabled,
        credentials=StreamCredentials(
            publish_user=model.publish_user,
            publish_pass=model.publish_pass,
            read_user=model.read_user,
            read_pass=model.read_pass,
        ),
        created_at=model.created_at,
        updated_at=model.updated_at,
    )
