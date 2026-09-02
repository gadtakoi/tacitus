"""drop redundant refresh token hash index

Revision ID: a1c7e4d2b905
Revises: 3f19f0b8199b
Create Date: 2026-09-03 01:10:00.000000

"""

from collections.abc import Sequence

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "a1c7e4d2b905"
down_revision: str | Sequence[str] | None = "3f19f0b8199b"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Drop the duplicate index on ``refresh_tokens.token_hash``.

    The column already carries a unique constraint, and PostgreSQL implements
    that with a unique btree index. ``ix_refresh_tokens_token_hash`` indexed
    the very same column a second time, so every insert maintained two
    identical structures while lookups could only ever use one of them.

    ``if_exists`` keeps the migration safe on databases created from
    ``Base.metadata.create_all`` (test fixtures), where the redundant index
    was never present.
    """
    op.drop_index(
        "ix_refresh_tokens_token_hash",
        table_name="refresh_tokens",
        if_exists=True,
    )


def downgrade() -> None:
    """Recreate the redundant index."""
    op.create_index(
        "ix_refresh_tokens_token_hash",
        "refresh_tokens",
        ["token_hash"],
        unique=False,
    )
