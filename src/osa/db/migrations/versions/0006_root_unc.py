"""The network path of a root, for the answers that point to the share (TASK-110).

Revision ID: 0006
Revises: 0005

``roots.unc`` is how the users' computers reach the root (``\\\\servidor\\acervo``), configured
in the panel; the bot builds a file's UNC path from it and the root-relative path when the
file cannot go through Telegram (above the send limit, or the sensitive class).
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0006"
down_revision: str | None = "0005"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("roots", sa.Column("unc", sa.Text()))


def downgrade() -> None:
    op.drop_column("roots", "unc")
