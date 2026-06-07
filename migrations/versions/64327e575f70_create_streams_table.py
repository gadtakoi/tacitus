"""create streams table

Revision ID: 64327e575f70
Revises:
Create Date: 2026-06-07 20:38:24.297517

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "64327e575f70"
down_revision: str | Sequence[str] | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        "streams",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("name", sa.String(length=64), nullable=False),
        sa.Column("source", sa.String(length=512), nullable=False),
        sa.Column("record_enabled", sa.Boolean(), nullable=False),
        sa.Column("publish_user", sa.String(length=128), nullable=True),
        sa.Column("publish_pass", sa.String(length=128), nullable=True),
        sa.Column("read_user", sa.String(length=128), nullable=True),
        sa.Column("read_pass", sa.String(length=128), nullable=True),
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
        sa.UniqueConstraint("name", name="uq_stream_name"),
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_table("streams")
