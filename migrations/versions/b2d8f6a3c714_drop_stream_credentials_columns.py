"""drop stream credential columns

Revision ID: b2d8f6a3c714
Revises: a1c7e4d2b905
Create Date: 2026-09-03 06:20:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "b2d8f6a3c714"
down_revision: str | Sequence[str] | None = "a1c7e4d2b905"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_CREDENTIAL_COLUMNS = ("publish_user", "publish_pass", "read_user", "read_pass")


def upgrade() -> None:
    """Drop the per-path credential columns from ``streams``.

    The columns were modelled on MediaMTX path parameters ``publishUser`` /
    ``publishPass`` / ``readUser`` / ``readPass``, which no longer exist in the
    pinned version (1.15.1): access is granted through the global
    ``authInternalUsers`` list, where each entry carries ``permissions[].path``
    (ADR-012).

    Nothing ever read these columns — no router is wired to the stream
    configuration layer — so they held plaintext passwords that no component
    could consume. Dropping them removes that exposure.

    ``if_exists`` keeps the migration safe on databases created from
    ``Base.metadata.create_all`` (test fixtures) after the model was updated.
    """
    for column in _CREDENTIAL_COLUMNS:
        op.drop_column("streams", column, if_exists=True)


def downgrade() -> None:
    """Recreate the credential columns.

    Values are not restored: they are dropped with the columns. The columns are
    nullable, so existing rows come back with NULL credentials.
    """
    for column in _CREDENTIAL_COLUMNS:
        op.add_column("streams", sa.Column(column, sa.String(length=128), nullable=True))
