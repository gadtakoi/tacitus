"""Stream repository for database operations."""

from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Stream
from app.schemas.streams import StreamCreateRequest, StreamUpdateRequest


class StreamRepositoryError(Exception):
    """Error in stream repository operations."""

    def __init__(self, message: str) -> None:
        self.message = message
        super().__init__(message)


class StreamNotFoundError(StreamRepositoryError):
    """The requested stream does not exist."""


class StreamAlreadyExistsError(StreamRepositoryError):
    """A stream with the requested name already exists."""


class StreamRepository:
    """Repository for Stream CRUD operations."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def create(self, request: StreamCreateRequest) -> Stream:
        """Create a new stream in the database.

        Args:
            request: Stream creation request.

        Returns:
            Created Stream model instance.

        Raises:
            StreamAlreadyExistsError: If stream name already exists.
        """
        stream = Stream(
            name=request.name,
            source=request.source,
            record_enabled=request.record_enabled,
            crop_enabled=request.crop_enabled,
            crop_top_percent=request.crop_top_percent,
            crop_bottom_percent=request.crop_bottom_percent,
        )
        self._session.add(stream)
        try:
            await self._session.flush()
        except IntegrityError as exc:
            raise StreamAlreadyExistsError(f"Stream '{request.name}' already exists") from exc
        return stream

    async def get_by_name(self, name: str) -> Stream | None:
        """Get stream by name.

        Args:
            name: Stream name.

        Returns:
            Stream or None if not found.
        """
        result = await self._session.execute(select(Stream).where(Stream.name == name))
        return result.scalar_one_or_none()

    async def list_all(self) -> list[Stream]:
        """List all streams ordered by creation time.

        Returns:
            List of Stream instances.
        """
        result = await self._session.execute(select(Stream).order_by(Stream.created_at))
        return list(result.scalars().all())

    async def update(self, name: str, request: StreamUpdateRequest) -> Stream:
        """Update an existing stream.

        Args:
            name: Stream name to update.
            request: Update request.

        Returns:
            Updated Stream instance.

        Raises:
            StreamNotFoundError: If stream not found.
        """
        stream = await self.get_by_name(name)
        if not stream:
            raise StreamNotFoundError(f"Stream '{name}' not found")

        if request.source is not None:
            stream.source = request.source
        if request.record_enabled is not None:
            stream.record_enabled = request.record_enabled
        if request.crop_enabled is not None:
            stream.crop_enabled = request.crop_enabled
        if request.crop_top_percent is not None:
            stream.crop_top_percent = request.crop_top_percent
        if request.crop_bottom_percent is not None:
            stream.crop_bottom_percent = request.crop_bottom_percent

        stream.updated_at = datetime.now(UTC)
        await self._session.flush()
        return stream

    async def delete(self, name: str) -> None:
        """Delete a stream.

        Args:
            name: Stream name to delete.

        Raises:
            StreamNotFoundError: If stream not found.
        """
        stream = await self.get_by_name(name)
        if not stream:
            raise StreamNotFoundError(f"Stream '{name}' not found")
        await self._session.delete(stream)
        await self._session.flush()
