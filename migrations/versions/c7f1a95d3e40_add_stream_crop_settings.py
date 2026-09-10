"""add per-stream crop settings

Revision ID: c7f1a95d3e40
Revises: b2d8f6a3c714
Create Date: 2026-09-10 07:10:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "c7f1a95d3e40"
down_revision: str | Sequence[str] | None = "b2d8f6a3c714"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_CHECK_CONSTRAINTS = (
    ("ck_stream_crop_top_percent", "crop_top_percent >= 0 AND crop_top_percent <= 90"),
    ("ck_stream_crop_bottom_percent", "crop_bottom_percent >= 0 AND crop_bottom_percent <= 90"),
    ("ck_stream_crop_total_percent", "crop_top_percent + crop_bottom_percent <= 90"),
)


def upgrade() -> None:
    """Add the crop settings consumed by the video crop worker.

    Server-side defaults rather than application-side ones: existing rows are
    filled in by PostgreSQL itself, and the worker reads this table directly
    without going through the ORM defaults.

    ``crop_enabled`` defaults to ``false`` so an upgrade never starts rewriting
    recordings on its own — cropping replaces the recorded segment in place and
    the original frame cannot be restored afterwards.

    The CHECK constraints repeat the bounds enforced by the pydantic schemas.
    The HTTP boundary is not the only writer: the crop worker and any operator
    fixing a row by hand reach the table directly, and a crop leaving nothing of
    the frame destroys footage irreversibly.
    """
    op.add_column(
        "streams",
        sa.Column("crop_enabled", sa.Boolean(), nullable=False, server_default=sa.false()),
    )
    op.add_column(
        "streams",
        sa.Column("crop_top_percent", sa.Integer(), nullable=False, server_default="0"),
    )
    op.add_column(
        "streams",
        sa.Column("crop_bottom_percent", sa.Integer(), nullable=False, server_default="0"),
    )
    for name, condition in _CHECK_CONSTRAINTS:
        op.create_check_constraint(name, "streams", condition)


def downgrade() -> None:
    """Drop the crop settings again.

    Constraints go first: dropping a column that a CHECK constraint references
    fails on PostgreSQL unless the constraint is removed with it.
    """
    for name, _condition in _CHECK_CONSTRAINTS:
        op.drop_constraint(name, "streams", type_="check")
    op.drop_column("streams", "crop_bottom_percent")
    op.drop_column("streams", "crop_top_percent")
    op.drop_column("streams", "crop_enabled")
