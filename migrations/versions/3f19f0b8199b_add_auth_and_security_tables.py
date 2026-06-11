"""add auth and security tables

Revision ID: 3f19f0b8199b
Revises: 64327e575f70
Create Date: 2026-06-11 20:51:42.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "3f19f0b8199b"
down_revision: str | Sequence[str] | None = "64327e575f70"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Create authentication and security tables.

    New tables:
    - users: application accounts.
    - refresh_tokens: long-lived refresh tokens bound to users.
    - revoked_tokens: blacklist of revoked access token JTIs.
    - failed_login_attempts: brute-force protection audit records.
    - security_events: general security audit log.
    """
    # Users table: stores account credentials and status.
    op.create_table(
        "users",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("username", sa.String(length=64), nullable=False),
        sa.Column("password_hash", sa.String(length=255), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("username", name="uq_users_username"),
    )

    # Refresh tokens table: one-to-many with users.
    op.create_table(
        "refresh_tokens",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("token_hash", sa.String(length=128), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["users.id"],
            name="fk_refresh_tokens_user_id",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("token_hash", name="uq_refresh_tokens_token_hash"),
    )
    # Index for fast refresh/logout lookups by token hash.
    op.create_index(
        "ix_refresh_tokens_token_hash",
        "refresh_tokens",
        ["token_hash"],
        unique=False,
    )
    # Index for listing tokens by owner.
    op.create_index(
        "ix_refresh_tokens_user_id",
        "refresh_tokens",
        ["user_id"],
        unique=False,
    )

    # Revoked access tokens (JWT blacklist) table.
    op.create_table(
        "revoked_tokens",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("jti", sa.String(length=64), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "revoked_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("jti", name="uq_revoked_tokens_jti"),
    )
    # Index used by the cleanup routine to remove expired records.
    op.create_index(
        "ix_revoked_tokens_expires_at",
        "revoked_tokens",
        ["expires_at"],
        unique=False,
    )

    # Failed login attempts table: brute-force protection audit.
    op.create_table(
        "failed_login_attempts",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("username", sa.String(length=64), nullable=False),
        sa.Column("ip_address", sa.String(length=45), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    # Composite index: count failures for a user within a time window.
    op.create_index(
        "ix_failed_attempts_username_created",
        "failed_login_attempts",
        ["username", "created_at"],
        unique=False,
    )
    # Index for cleanup of old records.
    op.create_index(
        "ix_failed_attempts_created_at",
        "failed_login_attempts",
        ["created_at"],
        unique=False,
    )

    # Security events table: general audit log with JSONB details.
    op.create_table(
        "security_events",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("event_type", sa.String(length=64), nullable=False),
        sa.Column("ip_address", sa.String(length=45), nullable=True),
        sa.Column("username", sa.String(length=64), nullable=True),
        sa.Column("details", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    # Index for suspicious activity detection from a single IP.
    op.create_index(
        "ix_security_events_ip_created",
        "security_events",
        ["ip_address", "created_at"],
        unique=False,
    )
    # Index for event type queries and filtering.
    op.create_index(
        "ix_security_events_event_type",
        "security_events",
        ["event_type"],
        unique=False,
    )
    # Index for cleanup of old events.
    op.create_index(
        "ix_security_events_created_at",
        "security_events",
        ["created_at"],
        unique=False,
    )


def downgrade() -> None:
    """Drop authentication and security tables."""
    op.drop_index("ix_security_events_created_at", table_name="security_events")
    op.drop_index("ix_security_events_event_type", table_name="security_events")
    op.drop_index("ix_security_events_ip_created", table_name="security_events")
    op.drop_table("security_events")

    op.drop_index("ix_failed_attempts_created_at", table_name="failed_login_attempts")
    op.drop_index("ix_failed_attempts_username_created", table_name="failed_login_attempts")
    op.drop_table("failed_login_attempts")

    op.drop_index("ix_revoked_tokens_expires_at", table_name="revoked_tokens")
    op.drop_table("revoked_tokens")

    op.drop_index("ix_refresh_tokens_user_id", table_name="refresh_tokens")
    op.drop_index("ix_refresh_tokens_token_hash", table_name="refresh_tokens")
    op.drop_table("refresh_tokens")

    op.drop_table("users")
