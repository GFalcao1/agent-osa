"""What the worker observed in the watched folders (TASK-111).

Revision ID: 0007
Revises: 0006

A file in a watched folder may still be copying: it is never read before it is stable, so it
has no sha256 and cannot be an index row yet. ``watch_entries`` holds what each cycle saw
(size, mtime, how many cycles unchanged) and, once the courier processed the file, the
document it became, how many times the courier ran on it, and the notice still to be sent
(an outbox: a notice is sent until delivered). It is a work table: a row goes away when its
file leaves the folder. ``roots.scan_pulou`` says a scan in progress skipped a folder or a
file, so it never marks anything absent (kept on the root, across the parts of a scan).
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0007"
down_revision: str | None = "0006"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

#: Frozen here on purpose (``osa.domain.document.WatchState`` must equal it, unit test).
WATCH_STATES = ("DETECTADO", "ESTAVEL", "PROCESSADO")


def upgrade() -> None:
    listed = ", ".join(f"'{state}'" for state in WATCH_STATES)
    op.create_table(
        "watch_entries",
        sa.Column("id", sa.Integer(), sa.Identity(), nullable=False),
        sa.Column("watched_folder_id", sa.Integer(), nullable=False),
        sa.Column("caminho", sa.Text(), nullable=False),
        sa.Column("tamanho", sa.BigInteger(), nullable=False),
        sa.Column("mtime_ns", sa.BigInteger(), nullable=False),
        sa.Column("ciclos_estaveis", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("estado", sa.String(16), nullable=False),
        sa.Column("document_id", sa.Integer()),
        sa.Column("tentativas", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("aviso", sa.String(16)),
        sa.Column("aviso_motivo", sa.Text()),
        sa.Column("aviso_enviado_em", sa.DateTime(timezone=True)),
        sa.Column(
            "primeiro_visto", sa.DateTime(timezone=True), server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "ultimo_visto", sa.DateTime(timezone=True), server_default=sa.func.now(),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_watch_entries")),
        sa.ForeignKeyConstraint(
            ["watched_folder_id"], ["watched_folders.id"],
            name=op.f("fk_watch_entries_watched_folder_id_watched_folders"),
        ),
        sa.ForeignKeyConstraint(
            ["document_id"], ["documents.id"],
            name=op.f("fk_watch_entries_document_id_documents"),
        ),
        sa.UniqueConstraint(
            "watched_folder_id", "caminho", name=op.f("uq_watch_entries_watched_folder_id_caminho")
        ),
        sa.CheckConstraint(f"estado IN ({listed})", name=op.f("ck_watch_entries_estado")),
    )  # fmt: skip
    op.add_column(
        "roots",
        sa.Column("scan_pulou", sa.Boolean(), server_default=sa.text("false"), nullable=False),
    )


def downgrade() -> None:
    op.drop_column("roots", "scan_pulou")
    op.drop_table("watch_entries")
