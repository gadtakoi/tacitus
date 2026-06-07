"""SQLAlchemy models."""

from datetime import UTC, datetime

from sqlalchemy import String, UniqueConstraint
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    """Base class for all models."""


class Stream(Base):
    """Stream configuration stored in PostgreSQL."""

    __tablename__ = "streams"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(64), nullable=False)
    source: Mapped[str] = mapped_column(String(512), nullable=False, default="publisher")
    record_enabled: Mapped[bool] = mapped_column(default=True, nullable=False)
    publish_user: Mapped[str | None] = mapped_column(String(128), nullable=True)
    publish_pass: Mapped[str | None] = mapped_column(String(128), nullable=True)
    read_user: Mapped[str | None] = mapped_column(String(128), nullable=True)
    read_pass: Mapped[str | None] = mapped_column(String(128), nullable=True)
    created_at: Mapped[datetime] = mapped_column(default=lambda: datetime.now(UTC), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        default=lambda: datetime.now(UTC),
        onupdate=lambda: datetime.now(UTC),
        nullable=False,
    )

    __table_args__ = (UniqueConstraint("name", name="uq_stream_name"),)
