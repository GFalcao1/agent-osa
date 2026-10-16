"""YAML export and import of the registry, users included.

Secrets never travel here: export copies an explicit whitelist of columns (never
``Settings``), and import refuses any key that looks like a secret, any anchor or alias,
duplicated keys and unknown fields, without echoing a value. Import is all-or-nothing in
the caller's transaction and only fills an empty registry (no merge).
"""

from __future__ import annotations

import os
import re
import stat
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Annotated, Any, Final, Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator
from sqlalchemy import exists, select
from sqlalchemy.orm import Session
from yaml.nodes import MappingNode, Node, ScalarNode

from osa.admin import services
from osa.core.settings import Settings
from osa.db.models import (
    Department,
    InstallationSettings,
    Owner,
    OwnerAlias,
    Root,
    User,
    WatchedFolder,
)
from osa.domain.errors import Conflict
from osa.domain.user import Role

VERSION: Final = 1
MAX_BYTES: Final = 1_048_576
# Same words the structured logger masks (osa.core.logging).
SENSITIVE_KEY: Final = re.compile(r"token|password|passwd|secret|api[_-]?key|authorization", re.I)
_MERGE_TAG: Final = "tag:yaml.org,2002:merge"


class _Entry(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class WatchedEntry(_Entry):
    caminho: str
    ativo: bool = True


class RootEntry(_Entry):
    caminho: str
    descricao: str | None = None
    ativo: bool = True
    pastas_monitoradas: list[WatchedEntry] = []


class DepartmentEntry(_Entry):
    nome: str
    pasta: str
    usa_ano: bool = False
    ativo: bool = True


class OwnerEntry(_Entry):
    nome_fantasia: str
    razao_social: str | None = None
    cnpj: str | None = None
    pasta: str
    ativo: bool = True
    aliases: list[str] = []


class UserEntry(_Entry):
    telegram_id: Annotated[int, Field(gt=0)]
    nome: str
    papel: Literal["admin", "operator", "reviewer"]
    departamento: str | None = None
    ativo: bool = True


class ModelEntry(_Entry):
    nome: str
    digest: str | None = None
    provedor_externo_ativo: bool = False


class Registry(_Entry):
    version: Literal[1]
    raizes: list[RootEntry] = []
    departamentos: list[DepartmentEntry] = []
    donos: list[OwnerEntry] = []
    usuarios: list[UserEntry] = []
    modelo: ModelEntry | None = None

    @model_validator(mode="after")
    def _consistent(self) -> Registry:
        _unique([services.folder_key(d.nome) for d in self.departamentos], "nomes de departamento")
        _unique(
            [services.folder_key(d.pasta) for d in self.departamentos], "pastas de departamento"
        )
        _unique([services.folder_key(r.caminho) for r in self.raizes], "caminhos de raiz")
        _unique([services.folder_key(o.pasta) for o in self.donos], "pastas de dono")
        _unique([u.telegram_id for u in self.usuarios], "telegram_id de usuário")
        names = {d.nome for d in self.departamentos}
        for user in self.usuarios:
            if user.papel == Role.ADMIN.value:
                continue
            if user.departamento is None or user.departamento not in names:
                raise ValueError("usuário sem departamento do template")
        if sum(u.papel == Role.ADMIN.value for u in self.usuarios) > 1:
            raise ValueError("mais de um administrador")
        return self


def _unique(values: list[Any], label: str) -> None:
    if len(set(values)) != len(values):
        raise ValueError(f"{label} repetidos")


# --- parsing ------------------------------------------------------------------------


class _Rejected(yaml.YAMLError):
    """Raised by the strict loader; the message is a constant."""


class _StrictLoader(yaml.SafeLoader):
    """SafeLoader without anchors, aliases, merge keys or duplicated keys."""

    def compose_node(self, parent: Node | None, index: Any) -> Node | None:
        if getattr(self.peek_event(), "anchor", None) is not None:  # type: ignore[no-untyped-call]
            raise _Rejected("YAML não pode usar âncoras ou aliases")
        return super().compose_node(parent, index)

    def construct_mapping(self, node: MappingNode, deep: bool = False) -> dict[Any, Any]:
        seen: set[str] = set()
        for key_node, _value in node.value:
            if key_node.tag == _MERGE_TAG:
                raise _Rejected("YAML não pode usar chave de mesclagem")
            if isinstance(key_node, ScalarNode):
                if key_node.value in seen:
                    raise _Rejected("YAML tem chave duplicada")
                seen.add(key_node.value)
        return super().construct_mapping(node, deep)


_UNKNOWN_KEY: Final = "<chave desconhecida>"


def _known_fields() -> frozenset[str]:
    models = (WatchedEntry, RootEntry, DepartmentEntry, OwnerEntry, UserEntry, ModelEntry, Registry)
    return frozenset(name for model in models for name in model.model_fields)


_KNOWN_FIELDS: Final = _known_fields()


def _safe_path(parts: Sequence[object]) -> str:
    """A location built only from schema field names and list positions.

    Any other key (unknown, or a credential pasted where a key goes) is never echoed.
    """
    shown = [
        str(part) if isinstance(part, int) or part in _KNOWN_FIELDS else _UNKNOWN_KEY
        for part in parts
    ]
    return ".".join(shown) or "documento"


def _find_sensitive(value: object, path: tuple[object, ...] = ()) -> str | None:
    if isinstance(value, dict):
        for key, item in value.items():
            where = (*path, key)
            if SENSITIVE_KEY.search(str(key)):
                return _safe_path(where)
            found = _find_sensitive(item, where)
            if found:
                return found
    elif isinstance(value, list):
        for index, item in enumerate(value):
            found = _find_sensitive(item, (*path, index))
            if found:
                return found
    return None


def _describe(error: ValidationError) -> str:
    parts = []
    for item in error.errors(include_input=False, include_url=False, include_context=False):
        if item["type"] == "extra_forbidden":
            message = "campo não permitido"
        else:
            message = item["msg"].removeprefix("Value error, ")
        parts.append(f"{_safe_path(item['loc'])}: {message}")
    return "; ".join(parts)


def parse_registry_yaml(text: str) -> Registry:
    """Parse and validate an import document; ValueError messages never echo input."""
    if len(text.encode("utf-8", errors="replace")) > MAX_BYTES:
        raise ValueError("YAML grande demais")
    try:
        data = yaml.load(text, Loader=_StrictLoader)  # strict SafeLoader subclass
    except _Rejected as error:
        raise ValueError(str(error)) from None
    except yaml.YAMLError as error:
        mark = getattr(error, "problem_mark", None)
        where = f" (linha {mark.line + 1})" if mark is not None else ""
        raise ValueError(f"YAML inválido{where}") from None
    except RecursionError:
        raise ValueError("YAML aninhado demais") from None
    if not isinstance(data, dict):
        raise ValueError("YAML deve ser um mapeamento no topo")
    sensitive = _find_sensitive(data)
    if sensitive:
        raise ValueError(f"YAML contém chave sensível ({sensitive}); segredos não entram no YAML")
    try:
        return Registry.model_validate(data)
    except ValidationError as error:
        raise ValueError(f"YAML inválido: {_describe(error)}") from None


def dump_registry_yaml(registry: Registry) -> str:
    return yaml.safe_dump(
        registry.model_dump(mode="python"),
        sort_keys=False,
        allow_unicode=True,
        default_flow_style=False,
    )


# --- files --------------------------------------------------------------------------


def write_export_file(path: Path, text: str) -> None:
    """Create ``path`` exclusively, without following links, mode 0600; never overwrite."""
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as handle:
        handle.write(text)


def read_registry_file(path: Path) -> str:
    """Contents of a regular, non-link, size-capped UTF-8 file."""
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    except FileNotFoundError:
        raise ValueError("arquivo não encontrado") from None
    except OSError:
        raise ValueError("arquivo inacessível ou é link simbólico") from None
    try:
        if not stat.S_ISREG(os.fstat(fd).st_mode):
            raise ValueError("caminho não é um arquivo comum")
    except ValueError:
        os.close(fd)
        raise
    with os.fdopen(fd, "rb") as handle:
        raw = handle.read(MAX_BYTES + 1)
    if len(raw) > MAX_BYTES:
        raise ValueError("arquivo grande demais")
    try:
        return raw.decode("utf-8")
    except UnicodeDecodeError:
        raise ValueError("arquivo não está em UTF-8") from None


# --- database <-> registry ----------------------------------------------------------


def registry_from_session(session: Session) -> Registry:
    """Build the export from an explicit whitelist of columns."""
    watched: dict[int, list[WatchedEntry]] = {}
    for folder in session.scalars(select(WatchedFolder).order_by(WatchedFolder.id)):
        watched.setdefault(folder.root_id, []).append(
            WatchedEntry(caminho=folder.caminho, ativo=folder.ativo)
        )
    departments = list(
        session.scalars(select(Department).order_by(Department.ordem, Department.id))
    )
    names = {d.id: d.nome for d in departments}
    aliases: dict[int, list[str]] = {}
    for owner_id, alias in session.execute(select(OwnerAlias.owner_id, OwnerAlias.alias)):
        aliases.setdefault(owner_id, []).append(alias)
    settings_row = session.get(InstallationSettings, 1)
    return Registry(
        version=VERSION,
        raizes=[
            RootEntry(
                caminho=r.caminho,
                descricao=r.descricao,
                ativo=r.ativo,
                pastas_monitoradas=watched.get(r.id, []),
            )
            for r in session.scalars(select(Root).order_by(Root.id))
        ],
        departamentos=[
            DepartmentEntry(nome=d.nome, pasta=d.pasta, usa_ano=d.usa_ano, ativo=d.ativo)
            for d in departments
        ],
        donos=[
            OwnerEntry(
                nome_fantasia=o.nome_fantasia,
                razao_social=o.razao_social,
                cnpj=o.cnpj,
                pasta=o.pasta,
                ativo=o.ativo,
                aliases=sorted(aliases.get(o.id, [])),
            )
            for o in session.scalars(select(Owner).order_by(Owner.id))
        ],
        usuarios=[
            UserEntry(
                telegram_id=u.telegram_id,
                nome=u.nome,
                papel=Role(u.papel).value,
                departamento=None if u.departamento_id is None else names.get(u.departamento_id),
                ativo=u.ativo,
            )
            for u in session.scalars(select(User).order_by(User.id))
        ],
        modelo=None
        if settings_row is None
        else ModelEntry(
            nome=settings_row.modelo_nome,
            digest=settings_row.modelo_digest,
            provedor_externo_ativo=settings_row.provedor_externo_ativo,
        ),
    )


def export_yaml(session: Session) -> str:
    return dump_registry_yaml(registry_from_session(session))


@dataclass(frozen=True)
class ImportSummary:
    raizes: int
    pastas_monitoradas: int
    departamentos: int
    donos: int
    usuarios: int
    modelo: bool


def _require_empty_registry(session: Session) -> None:
    """Import never merges: any registry row, or a stored model configuration, refuses it."""
    populated = (
        session.get(InstallationSettings, 1) is not None,
        session.scalar(select(exists().where(Root.id.is_not(None)))),
        session.scalar(select(exists().where(Department.id.is_not(None)))),
        session.scalar(select(exists().where(Owner.id.is_not(None)))),
        session.scalar(select(exists().where(User.papel != Role.ADMIN.value))),
    )
    if any(populated):
        raise Conflict("o cadastro não está vazio: import não faz merge")


def import_registry(
    session: Session,
    registry: Registry,
    *,
    settings: Settings,
    is_mount_point: services.MountPredicate = services.default_is_mount_point,
    actor: str,
) -> ImportSummary:
    """Fill an empty registry from ``registry`` inside the caller's transaction.

    Every row goes through the same services as the panel (so it is validated and audited).
    Any failure raises; the caller rolls back and nothing stays.
    """
    services.ensure_bootstrapped(session)
    services.lock_registry(session)
    _require_empty_registry(session)
    admin_ids = set(session.scalars(select(User.telegram_id).where(User.papel == Role.ADMIN.value)))
    for entry in registry.usuarios:
        if entry.papel == Role.ADMIN.value and entry.telegram_id not in admin_ids:
            raise Conflict("o YAML traz um administrador diferente do existente")

    department_ids: dict[str, int] = {}
    for position in registry.departamentos:
        created = services.create_department(
            session,
            nome=position.nome,
            pasta=position.pasta,
            usa_ano=position.usa_ano,
            actor=actor,
        )
        department_ids[position.nome] = created.id
    users = 0
    inactive_users: list[int] = []
    for entry in registry.usuarios:
        if entry.papel == Role.ADMIN.value:
            continue
        user = services.create_user(
            session,
            telegram_id=entry.telegram_id,
            nome=entry.nome,
            papel=entry.papel,
            departamento_id=department_ids[entry.departamento or ""],
            actor=actor,
        )
        users += 1
        if not entry.ativo:
            inactive_users.append(user.id)
    for user_id in inactive_users:
        services.set_user_active(session, user_id, False, actor=actor)
    for position in registry.departamentos:
        if not position.ativo:
            services.set_department_active(
                session, department_ids[position.nome], False, actor=actor
            )

    folders = 0
    for root_entry in registry.raizes:
        root = services.create_root(
            session,
            caminho=root_entry.caminho,
            descricao=root_entry.descricao,
            is_mount_point=is_mount_point,
            actor=actor,
        )
        for watched_entry in root_entry.pastas_monitoradas:
            folder = services.create_watched_folder(
                session, root_id=root.id, caminho=watched_entry.caminho, actor=actor
            )
            folders += 1
            if not watched_entry.ativo:
                services.set_watched_folder_active(session, folder.id, False, actor=actor)
        if not root_entry.ativo:
            services.set_root_active(session, root.id, False, actor=actor)

    for owner_entry in registry.donos:
        owner = services.create_owner(
            session,
            nome_fantasia=owner_entry.nome_fantasia,
            razao_social=owner_entry.razao_social,
            cnpj=owner_entry.cnpj,
            pasta=owner_entry.pasta,
            aliases=owner_entry.aliases,
            actor=actor,
        )
        if not owner_entry.ativo:
            services.set_owner_active(session, owner.id, False, actor=actor)

    if registry.modelo is not None:
        services.set_model_config(
            session,
            nome=registry.modelo.nome,
            digest=registry.modelo.digest,
            provedor_externo_ativo=registry.modelo.provedor_externo_ativo,
            settings=settings,
            actor=actor,
        )
    summary = ImportSummary(
        raizes=len(registry.raizes),
        pastas_monitoradas=folders,
        departamentos=len(registry.departamentos),
        donos=len(registry.donos),
        usuarios=users,
        modelo=registry.modelo is not None,
    )
    services.audit_import(session, actor, summary.__dict__)
    return summary


__all__ = [
    "MAX_BYTES",
    "SENSITIVE_KEY",
    "ImportSummary",
    "Registry",
    "dump_registry_yaml",
    "export_yaml",
    "import_registry",
    "parse_registry_yaml",
    "read_registry_file",
    "registry_from_session",
    "write_export_file",
]
