"""Base repository layer.

Provides a shared exception and a minimal base class used by all
repositories. This keeps repository construction consistent across the
application and simplifies unit testing with in-memory fakes.
"""

from sqlalchemy.ext.asyncio import AsyncSession


class RepositoryError(Exception):
    """Base exception for repository-level errors."""

    def __init__(self, message: str) -> None:
        self.message = message
        super().__init__(message)


class BaseRepository:
    """Minimal base class for repositories working with an async SQLAlchemy session."""

    def __init__(self, session: AsyncSession) -> None:
        """Initialize repository with a database session.

        Args:
            session: Async SQLAlchemy session used for queries and transactions.
        """
        self._session = session
