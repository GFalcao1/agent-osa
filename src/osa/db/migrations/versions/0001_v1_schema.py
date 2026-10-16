"""v1 schema: registry, index and append-only audit trail.

Revision ID: 0001
Revises:
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0001"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# Every constraint and index is named here, wrapped in op.f() so no naming
# convention (env.py's target_metadata or none) can rename it; tests compare these
# names with osa.db.models. Vocabularies and patterns are frozen as of this revision:
# a new value needs a new migration (tests/unit/test_db_session.py fails on drift).
STATES = (
    "DETECTADO",
    "ESTAVEL",
    "EXTRAIDO",
    "CLASSIFICADO",
    "ARQUIVADO",
    "EM_REVISAO",
    "NA_ORIGEM",
    "NAO_SUPORTADO",
    "FALHOU",
)
ORIGINS = ("telegram", "watched", "scan")
ROLES = ("admin", "operator", "reviewer")
MODES = ("auto", "review", "undo")
REVIEW_STATES = ("aberto", "decidido", "origem")
SHA256 = "^[0-9a-f]{64}$"
ALIAS_PATTERN = "^[0-9a-z]+( [0-9a-z]+)*$"
CNPJ_PATTERN = "^[0-9]{14}$"

AUDIT_FUNCTION = "audit_events_reject_mutation"


def _in(column: str, values: Sequence[str]) -> str:
    return f"{column} IN ({', '.join(repr(v) for v in values)})"


def _id(table: str) -> tuple[sa.Column[int], sa.PrimaryKeyConstraint]:
    column = sa.Column("id", sa.Integer(), sa.Identity(), nullable=False)
    return column, sa.PrimaryKeyConstraint("id", name=op.f(f"pk_{table}"))


def _uq(table: str, *columns: str) -> sa.UniqueConstraint:
    return sa.UniqueConstraint(*columns, name=op.f(f"uq_{table}_{'_'.join(columns)}"))


def _ck(table: str, name: str, sqltext: str) -> sa.CheckConstraint:
    return sa.CheckConstraint(sqltext, name=op.f(f"ck_{table}_{name}"))


def _fk(table: str, column: str, target: str, ondelete: str | None = None) -> sa.ForeignKey:
    return sa.ForeignKey(
        f"{target}.id", name=op.f(f"fk_{table}_{column}_{target}"), ondelete=ondelete
    )


def _index(table: str, column: str) -> None:
    op.create_index(op.f(f"ix_{table}_{column}"), table, [column])


def _flag(name: str) -> sa.Column[bool]:
    return sa.Column(name, sa.Boolean(), server_default=sa.text("true"), nullable=False)


def _now(name: str) -> sa.Column[datetime]:
    return sa.Column(name, sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False)


def upgrade() -> None:
    op.create_table(
        "departments",
        *_id("departments"),
        sa.Column("nome", sa.Text(), nullable=False),
        sa.Column("pasta", sa.Text(), nullable=False),
        sa.Column("ordem", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("usa_ano", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        _flag("ativo"),
        _uq("departments", "nome"),
        _uq("departments", "pasta"),
    )
    op.create_table(
        "users",
        *_id("users"),
        sa.Column("telegram_id", sa.BigInteger(), nullable=False),
        sa.Column("nome", sa.Text(), nullable=False),
        sa.Column("papel", sa.String(16), nullable=False),
        sa.Column("departamento_id", sa.Integer(), _fk("users", "departamento_id", "departments")),
        _flag("ativo"),
        _uq("users", "telegram_id"),
        _ck("users", "papel", _in("papel", ROLES)),
        # Every user has a department except an admin (bootstrap runs before the template).
        _ck(
            "users",
            "departamento_required",
            "papel = 'admin' OR departamento_id IS NOT NULL",
        ),
    )
    _index("users", "departamento_id")
    op.create_table(
        "owners",
        *_id("owners"),
        sa.Column("nome_fantasia", sa.Text(), nullable=False),
        sa.Column("razao_social", sa.Text()),
        sa.Column("cnpj", sa.String(14)),
        sa.Column("pasta", sa.Text(), nullable=False),
        _flag("ativo"),
        _uq("owners", "cnpj"),
        _uq("owners", "pasta"),
        _ck("owners", "cnpj_digits", f"cnpj IS NULL OR cnpj ~ '{CNPJ_PATTERN}'"),
    )
    op.create_table(
        "owner_aliases",
        *_id("owner_aliases"),
        sa.Column(
            "owner_id",
            sa.Integer(),
            _fk("owner_aliases", "owner_id", "owners", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("alias", sa.Text(), nullable=False),
        _uq("owner_aliases", "alias"),
        _ck("owner_aliases", "alias_normalized", f"alias ~ '{ALIAS_PATTERN}'"),
    )
    op.create_table(
        "roots",
        *_id("roots"),
        sa.Column("caminho", sa.Text(), nullable=False),
        sa.Column("descricao", sa.Text()),
        _flag("ativo"),
        _uq("roots", "caminho"),
    )
    op.create_table(
        "watched_folders",
        *_id("watched_folders"),
        sa.Column(
            "root_id", sa.Integer(), _fk("watched_folders", "root_id", "roots"), nullable=False
        ),
        sa.Column("caminho", sa.Text(), nullable=False),
        _flag("ativo"),
        _uq("watched_folders", "root_id", "caminho"),
    )
    op.create_table(
        "documents",
        *_id("documents"),
        sa.Column("sha256", sa.String(64), nullable=False),
        sa.Column("root_id", sa.Integer(), _fk("documents", "root_id", "roots")),
        sa.Column("caminho", sa.Text()),
        sa.Column("owner_id", sa.Integer(), _fk("documents", "owner_id", "owners")),
        sa.Column(
            "departamento_id", sa.Integer(), _fk("documents", "departamento_id", "departments")
        ),
        sa.Column("ano", sa.Integer()),
        sa.Column("tipo_documental", sa.Text()),
        sa.Column(
            "tags", sa.ARRAY(sa.Text()), server_default=sa.text("'{}'::text[]"), nullable=False
        ),
        sa.Column("resumo", sa.Text()),
        sa.Column(
            "datas", sa.ARRAY(sa.Date()), server_default=sa.text("'{}'::date[]"), nullable=False
        ),
        sa.Column("texto", sa.Text()),
        sa.Column("origem", sa.String(16), nullable=False),
        sa.Column("enviado_por", sa.Integer(), _fk("documents", "enviado_por", "users")),
        sa.Column("estado", sa.String(16), server_default="DETECTADO", nullable=False),
        sa.Column("motivo", sa.Text()),
        _now("criado_em"),
        _now("atualizado_em"),
        _uq("documents", "sha256"),
        _ck("documents", "estado", _in("estado", STATES)),
        _ck("documents", "origem", _in("origem", ORIGINS)),
        _ck("documents", "sha256_hex", f"sha256 ~ '{SHA256}'"),
    )
    _index("documents", "root_id")
    _index("documents", "owner_id")
    _index("documents", "departamento_id")
    _index("documents", "estado")
    op.create_table(
        "archive_operations",
        *_id("archive_operations"),
        sa.Column(
            "document_id",
            sa.Integer(),
            _fk("archive_operations", "document_id", "documents"),
            nullable=False,
        ),
        sa.Column("origem", sa.Text(), nullable=False),
        sa.Column("destino", sa.Text(), nullable=False),
        sa.Column("ator", sa.Text(), nullable=False),
        sa.Column("modo", sa.String(16), nullable=False),
        _now("timestamp"),
        _ck("archive_operations", "modo", _in("modo", MODES)),
    )
    _index("archive_operations", "document_id")
    op.create_table(
        "review_cases",
        *_id("review_cases"),
        sa.Column(
            "document_id",
            sa.Integer(),
            _fk("review_cases", "document_id", "documents"),
            nullable=False,
        ),
        sa.Column(
            "departamento_sugerido_id",
            sa.Integer(),
            _fk("review_cases", "departamento_sugerido_id", "departments"),
        ),
        sa.Column("motivo", sa.Text(), nullable=False),
        sa.Column("estado", sa.String(16), server_default="aberto", nullable=False),
        sa.Column("decidido_por", sa.Integer(), _fk("review_cases", "decidido_por", "users")),
        _now("criado_em"),
        _now("atualizado_em"),
        _ck("review_cases", "estado", _in("estado", REVIEW_STATES)),
    )
    _index("review_cases", "document_id")
    op.create_table(
        "audit_events",
        sa.Column("id", sa.BigInteger(), sa.Identity(), nullable=False),
        _now("timestamp"),
        sa.Column("ator", sa.Text(), nullable=False),
        sa.Column("acao", sa.Text(), nullable=False),
        sa.Column("alvo_tipo", sa.Text(), nullable=False),
        sa.Column("alvo_id", sa.Text()),
        sa.Column("sha256", sa.String(64)),
        sa.Column("resultado", sa.String(16), nullable=False),
        sa.Column(
            "detalhes",
            postgresql.JSONB(),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_audit_events")),
        _ck("audit_events", "sha256_hex", f"sha256 IS NULL OR sha256 ~ '{SHA256}'"),
    )
    op.execute(
        f"""
        CREATE FUNCTION {AUDIT_FUNCTION}() RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
            RAISE EXCEPTION 'audit_events is append-only: % is not allowed', TG_OP;
        END;
        $$
        """
    )
    # Statement-level, so an UPDATE or DELETE that matches no row is rejected too.
    op.execute(
        f"CREATE TRIGGER audit_events_no_update_delete BEFORE UPDATE OR DELETE "
        f"ON audit_events FOR EACH STATEMENT EXECUTE FUNCTION {AUDIT_FUNCTION}()"
    )
    op.execute(
        f"CREATE TRIGGER audit_events_no_truncate BEFORE TRUNCATE "
        f"ON audit_events FOR EACH STATEMENT EXECUTE FUNCTION {AUDIT_FUNCTION}()"
    )


def downgrade() -> None:
    # Triggers and function go before the table; the audit table is then plain again.
    op.execute("DROP TRIGGER audit_events_no_truncate ON audit_events")
    op.execute("DROP TRIGGER audit_events_no_update_delete ON audit_events")
    op.execute(f"DROP FUNCTION {AUDIT_FUNCTION}()")
    op.drop_table("audit_events")
    op.drop_table("review_cases")
    op.drop_table("archive_operations")
    op.drop_table("documents")
    op.drop_table("watched_folders")
    op.drop_table("roots")
    op.drop_table("owner_aliases")
    op.drop_table("owners")
    op.drop_table("users")
    op.drop_table("departments")
