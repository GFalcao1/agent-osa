"""Department description and the identity of archive operations (TASK-107).

Revision ID: 0003
Revises: 0002

``departments.descricao``: the optional short phrase the model sees as installation data.
``archive_operations`` gains ``root_id`` and ``sha256`` (both required): an operation is
identified by its root, its root-relative paths and the content hash, never by the current
path of the document (SPEC §5-E). No installation has archive operations yet; a populated
table makes the ``NOT NULL`` additions fail loudly instead of guessing values.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0003"
down_revision: str | None = "0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SHA256 = "^[0-9a-f]{64}$"


def upgrade() -> None:
    op.add_column("departments", sa.Column("descricao", sa.Text()))
    op.add_column("archive_operations", sa.Column("root_id", sa.Integer(), nullable=False))
    op.add_column("archive_operations", sa.Column("sha256", sa.String(64), nullable=False))
    op.create_foreign_key(
        op.f("fk_archive_operations_root_id_roots"),
        "archive_operations",
        "roots",
        ["root_id"],
        ["id"],
    )
    op.create_check_constraint(
        op.f("ck_archive_operations_sha256_hex"), "archive_operations", f"sha256 ~ '{SHA256}'"
    )


def downgrade() -> None:
    op.drop_constraint(
        op.f("ck_archive_operations_sha256_hex"), "archive_operations", type_="check"
    )
    op.drop_constraint(
        op.f("fk_archive_operations_root_id_roots"), "archive_operations", type_="foreignkey"
    )
    op.drop_column("archive_operations", "sha256")
    op.drop_column("archive_operations", "root_id")
    op.drop_column("departments", "descricao")
