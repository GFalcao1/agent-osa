"""Schema of the index, the registry and the audit trail. Declarative only: no flow."""

from __future__ import annotations

import enum
from datetime import date, datetime
from typing import Any

from sqlalchemy import (
    ARRAY,
    BigInteger,
    Boolean,
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    Identity,
    Integer,
    MetaData,
    String,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, validates

from osa.domain.document import ArchiveMode, DocumentState, Origin, ReviewState
from osa.domain.owner import ALIAS_PATTERN, CNPJ_PATTERN, normalize_alias, normalize_cnpj
from osa.domain.user import Role

NAMING_CONVENTION = {
    "ix": "ix_%(table_name)s_%(column_0_N_name)s",
    "uq": "uq_%(table_name)s_%(column_0_N_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_N_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}

SHA256_PATTERN = "^[0-9a-f]{64}$"


class Base(DeclarativeBase):
    metadata = MetaData(naming_convention=NAMING_CONVENTION)


def values_check(column: str, values: type[enum.StrEnum], name: str) -> CheckConstraint:
    """CHECK built from the domain enum, so the vocabulary has a single source."""
    listed = ", ".join(f"'{member.value}'" for member in values)
    return CheckConstraint(f"{column} IN ({listed})", name=name)


def _now() -> Any:
    return mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)


class Department(Base):
    """One entry of the installation template, applied equally to every owner."""

    __tablename__ = "departments"

    id: Mapped[int] = mapped_column(Integer, Identity(), primary_key=True)
    nome: Mapped[str] = mapped_column(Text, unique=True)
    pasta: Mapped[str] = mapped_column(Text, unique=True)
    ordem: Mapped[int] = mapped_column(Integer, default=0, server_default=text("0"))
    usa_ano: Mapped[bool] = mapped_column(Boolean, default=False, server_default=text("false"))
    ativo: Mapped[bool] = mapped_column(Boolean, default=True, server_default=text("true"))


class User(Base):
    __tablename__ = "users"
    __table_args__ = (
        values_check("papel", Role, "papel"),
        # Every user has a department except an admin (bootstrap runs before the template).
        CheckConstraint(
            f"papel = '{Role.ADMIN.value}' OR departamento_id IS NOT NULL",
            name="departamento_required",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, Identity(), primary_key=True)
    telegram_id: Mapped[int] = mapped_column(BigInteger, unique=True)
    nome: Mapped[str] = mapped_column(Text)
    papel: Mapped[str] = mapped_column(String(16))
    departamento_id: Mapped[int | None] = mapped_column(ForeignKey("departments.id"), index=True)
    ativo: Mapped[bool] = mapped_column(Boolean, default=True, server_default=text("true"))


class Owner(Base):
    """CNPJ and alias are normalized by the ORM and their shape is enforced by CHECKs."""

    __tablename__ = "owners"
    __table_args__ = (
        CheckConstraint(f"cnpj IS NULL OR cnpj ~ '{CNPJ_PATTERN}'", name="cnpj_digits"),
    )

    id: Mapped[int] = mapped_column(Integer, Identity(), primary_key=True)
    nome_fantasia: Mapped[str] = mapped_column(Text)
    razao_social: Mapped[str | None] = mapped_column(Text)
    cnpj: Mapped[str | None] = mapped_column(String(14), unique=True)
    pasta: Mapped[str] = mapped_column(Text, unique=True)
    ativo: Mapped[bool] = mapped_column(Boolean, default=True, server_default=text("true"))

    @validates("cnpj")
    def _normalize_cnpj(self, _key: str, value: str | None) -> str | None:
        return None if value is None else normalize_cnpj(value)


class OwnerAlias(Base):
    __tablename__ = "owner_aliases"
    __table_args__ = (CheckConstraint(f"alias ~ '{ALIAS_PATTERN}'", name="alias_normalized"),)

    id: Mapped[int] = mapped_column(Integer, Identity(), primary_key=True)
    owner_id: Mapped[int] = mapped_column(ForeignKey("owners.id", ondelete="CASCADE"))
    alias: Mapped[str] = mapped_column(Text, unique=True)

    @validates("alias")
    def _normalize_alias(self, _key: str, value: str) -> str:
        return normalize_alias(value)


class Root(Base):
    __tablename__ = "roots"

    id: Mapped[int] = mapped_column(Integer, Identity(), primary_key=True)
    caminho: Mapped[str] = mapped_column(Text, unique=True)
    descricao: Mapped[str | None] = mapped_column(Text)
    ativo: Mapped[bool] = mapped_column(Boolean, default=True, server_default=text("true"))


class WatchedFolder(Base):
    __tablename__ = "watched_folders"
    __table_args__ = (UniqueConstraint("root_id", "caminho"),)

    id: Mapped[int] = mapped_column(Integer, Identity(), primary_key=True)
    root_id: Mapped[int] = mapped_column(ForeignKey("roots.id"))
    caminho: Mapped[str] = mapped_column(Text)
    ativo: Mapped[bool] = mapped_column(Boolean, default=True, server_default=text("true"))


class Document(Base):
    """Index entry. Owner, department, year and path stay null until archiving."""

    __tablename__ = "documents"
    __table_args__ = (
        values_check("estado", DocumentState, "estado"),
        values_check("origem", Origin, "origem"),
        CheckConstraint(f"sha256 ~ '{SHA256_PATTERN}'", name="sha256_hex"),
    )

    id: Mapped[int] = mapped_column(Integer, Identity(), primary_key=True)
    sha256: Mapped[str] = mapped_column(String(64), unique=True)
    root_id: Mapped[int | None] = mapped_column(ForeignKey("roots.id"), index=True)
    caminho: Mapped[str | None] = mapped_column(Text)
    owner_id: Mapped[int | None] = mapped_column(ForeignKey("owners.id"), index=True)
    departamento_id: Mapped[int | None] = mapped_column(ForeignKey("departments.id"), index=True)
    ano: Mapped[int | None] = mapped_column(Integer)
    tipo_documental: Mapped[str | None] = mapped_column(Text)
    tags: Mapped[list[str]] = mapped_column(
        ARRAY(Text), default=list, server_default=text("'{}'::text[]")
    )
    resumo: Mapped[str | None] = mapped_column(Text)
    datas: Mapped[list[date]] = mapped_column(
        ARRAY(Date), default=list, server_default=text("'{}'::date[]")
    )
    texto: Mapped[str | None] = mapped_column(Text)
    origem: Mapped[str] = mapped_column(String(16))
    enviado_por: Mapped[int | None] = mapped_column(ForeignKey("users.id"))
    estado: Mapped[str] = mapped_column(
        String(16),
        default=DocumentState.DETECTADO.value,
        server_default=DocumentState.DETECTADO.value,
        index=True,
    )
    motivo: Mapped[str | None] = mapped_column(Text)
    criado_em: Mapped[datetime] = _now()
    atualizado_em: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )


class ArchiveOperation(Base):
    __tablename__ = "archive_operations"
    __table_args__ = (values_check("modo", ArchiveMode, "modo"),)

    id: Mapped[int] = mapped_column(Integer, Identity(), primary_key=True)
    document_id: Mapped[int] = mapped_column(ForeignKey("documents.id"), index=True)
    origem: Mapped[str] = mapped_column(Text)
    destino: Mapped[str] = mapped_column(Text)
    ator: Mapped[str] = mapped_column(Text)
    modo: Mapped[str] = mapped_column(String(16))
    timestamp: Mapped[datetime] = _now()


class ReviewCase(Base):
    __tablename__ = "review_cases"
    __table_args__ = (values_check("estado", ReviewState, "estado"),)

    id: Mapped[int] = mapped_column(Integer, Identity(), primary_key=True)
    document_id: Mapped[int] = mapped_column(ForeignKey("documents.id"), index=True)
    departamento_sugerido_id: Mapped[int | None] = mapped_column(ForeignKey("departments.id"))
    motivo: Mapped[str] = mapped_column(Text)
    estado: Mapped[str] = mapped_column(
        String(16),
        default=ReviewState.ABERTO.value,
        server_default=ReviewState.ABERTO.value,
    )
    decidido_por: Mapped[int | None] = mapped_column(ForeignKey("users.id"))
    criado_em: Mapped[datetime] = _now()
    atualizado_em: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )


class AuditEvent(Base):
    """Append-only: a trigger rejects UPDATE, DELETE and TRUNCATE (see migration 0001)."""

    __tablename__ = "audit_events"
    __table_args__ = (
        CheckConstraint(f"sha256 IS NULL OR sha256 ~ '{SHA256_PATTERN}'", name="sha256_hex"),
    )

    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    timestamp: Mapped[datetime] = _now()
    ator: Mapped[str] = mapped_column(Text)
    acao: Mapped[str] = mapped_column(Text)
    alvo_tipo: Mapped[str] = mapped_column(Text)
    alvo_id: Mapped[str | None] = mapped_column(Text)
    sha256: Mapped[str | None] = mapped_column(String(64))
    resultado: Mapped[str] = mapped_column(String(16))
    detalhes: Mapped[dict[str, Any]] = mapped_column(
        JSONB, default=dict, server_default=text("'{}'::jsonb")
    )
