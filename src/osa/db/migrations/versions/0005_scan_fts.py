"""Scan of the existing archive and full-text search in Portuguese (TASK-109).

Revision ID: 0005
Revises: 0004

``documents.texto_tsv`` is a stored generated column: name and document type (weight A),
summary (B) and the extracted text (C) through the ``portuguese`` configuration, indexed by
GIN. The database keeps it in step with the row, so no code path can forget it. Tags stay
out of it (``array_to_string`` is not immutable, which a generated column requires).

``documents.visto_em`` / ``ausente_em`` are what the scan knows about the file on disk;
``roots.scan_iniciado_em`` / ``scan_desde`` / ``scan_cursor`` / ``ultimo_scan`` make a scan
resumable per root (only by a run with the same ``--desde``).
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import TSVECTOR

revision: str = "0005"
down_revision: str | None = "0004"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

#: Frozen here on purpose: the model's ``TEXTO_TSV_EXPRESSION`` must equal it (unit test), and a
#: change to the expression is a new migration, never a rewrite of this one.
TEXTO_TSV_EXPRESSION = (
    "setweight(to_tsvector('portuguese', translate(coalesce(nome, ''), '._-', '   ')), 'A') || "
    "setweight(to_tsvector('portuguese', coalesce(tipo_documental, '')), 'A') || "
    "setweight(to_tsvector('portuguese', coalesce(resumo, '')), 'B') || "
    "setweight(to_tsvector('portuguese', coalesce(texto, '')), 'C')"
)


def upgrade() -> None:
    op.add_column(
        "documents",
        sa.Column("texto_tsv", TSVECTOR(), sa.Computed(TEXTO_TSV_EXPRESSION, persisted=True)),
    )
    op.add_column("documents", sa.Column("visto_em", sa.DateTime(timezone=True)))
    op.add_column("documents", sa.Column("ausente_em", sa.DateTime(timezone=True)))
    op.create_index(
        op.f("ix_documents_texto_tsv"), "documents", ["texto_tsv"], postgresql_using="gin"
    )
    op.create_index(op.f("ix_documents_ano"), "documents", ["ano"])
    op.add_column("roots", sa.Column("scan_iniciado_em", sa.DateTime(timezone=True)))
    op.add_column("roots", sa.Column("scan_desde", sa.DateTime(timezone=True)))
    op.add_column("roots", sa.Column("scan_cursor", sa.Text()))
    op.add_column("roots", sa.Column("ultimo_scan", sa.DateTime(timezone=True)))


def downgrade() -> None:
    op.drop_column("roots", "ultimo_scan")
    op.drop_column("roots", "scan_cursor")
    op.drop_column("roots", "scan_desde")
    op.drop_column("roots", "scan_iniciado_em")
    op.drop_index(op.f("ix_documents_ano"), table_name="documents")
    op.drop_index(op.f("ix_documents_texto_tsv"), table_name="documents")
    op.drop_column("documents", "ausente_em")
    op.drop_column("documents", "visto_em")
    op.drop_column("documents", "texto_tsv")
