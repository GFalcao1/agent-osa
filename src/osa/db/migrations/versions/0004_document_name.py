"""The original file name of an indexed document (TASK-108).

Revision ID: 0004
Revises: 0003

The quarantine stores an upload under an id, so the index must keep the name the file is
archived under; a review decided after a restart reads it from here.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0004"
down_revision: str | None = "0003"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("documents", sa.Column("nome", sa.Text()))


def downgrade() -> None:
    op.drop_column("documents", "nome")
