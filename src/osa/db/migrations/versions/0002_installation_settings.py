"""Singleton table with the model choice and the external-provider toggle.

Revision ID: 0002
Revises: 0001

The external provider key is deliberately not a column: it stays in the environment.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0002"
down_revision: str | None = "0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "installation_settings",
        sa.Column("id", sa.Integer(), server_default=sa.text("1"), nullable=False),
        sa.Column("modelo_nome", sa.Text(), nullable=False),
        sa.Column("modelo_digest", sa.Text()),
        sa.Column(
            "provedor_externo_ativo", sa.Boolean(), server_default=sa.text("false"), nullable=False
        ),
        sa.Column(
            "atualizado_em",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_installation_settings")),
        sa.CheckConstraint("id = 1", name=op.f("ck_installation_settings_singleton")),
        sa.CheckConstraint(
            "length(btrim(modelo_nome)) > 0",
            name=op.f("ck_installation_settings_modelo_nome_not_blank"),
        ),
    )


def downgrade() -> None:
    op.drop_table("installation_settings")
