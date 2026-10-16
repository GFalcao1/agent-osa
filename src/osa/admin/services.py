"""Registry use cases shared by the panel and the CLI.

Every mutation is audited in the same transaction (actor required) and the caller
owns the commit. Messages raised here are constants: they never carry a secret, a
DSN or a raw input value, so boundaries can show them as they are.
"""

from __future__ import annotations

import os
import re
import unicodedata
import uuid
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Final

from sqlalchemy import exists, func, select
from sqlalchemy.orm import Session

from osa.core.settings import Settings
from osa.db.audit import RESULT_DENIED, record_event
from osa.db.models import (
    Department,
    InstallationSettings,
    Owner,
    OwnerAlias,
    Root,
    User,
    WatchedFolder,
)
from osa.domain.errors import AccessDenied, Conflict, NotFound, UnsafePath
from osa.domain.owner import normalize_alias, normalize_cnpj
from osa.domain.user import Role

# --- folder names -------------------------------------------------------------------

MAX_FOLDER_NAME = 200
_FORBIDDEN_CHARS = frozenset('<>:"|?*/\\')
_RESERVED_WINDOWS = frozenset(
    {"con", "prn", "aux", "nul"}
    | {f"com{n}" for n in range(1, 10)}
    | {f"lpt{n}" for n in range(1, 10)}
)
_WHITESPACE = re.compile(r"\s+")


def _nfc(value: str) -> str:
    return unicodedata.normalize("NFC", value)


def folder_key(name: str) -> str:
    """Collision key of a folder name: SMB and APFS ignore case and Unicode form."""
    return _nfc(_nfc(name).casefold())


def _is_forbidden(char: str) -> bool:
    return char in _FORBIDDEN_CHARS or unicodedata.category(char).startswith("C")


def validate_folder_name(name: str) -> str:
    """The NFC name if it is one safe path component; ValueError otherwise."""
    value = _nfc(name)
    if not value.strip():
        raise ValueError("nome de pasta não pode ser vazio")
    if len(value) > MAX_FOLDER_NAME:
        raise ValueError(f"nome de pasta excede {MAX_FOLDER_NAME} caracteres")
    if any(_is_forbidden(char) for char in value):
        raise ValueError("nome de pasta contém caractere proibido")
    if value != value.strip() or value.startswith(".") or value.endswith("."):
        raise ValueError("nome de pasta não pode começar ou terminar com ponto ou espaço")
    if value.split(".", 1)[0].strip().casefold() in _RESERVED_WINDOWS:
        raise ValueError("nome de pasta é reservado")
    return value


def derive_folder_name(nome: str) -> str:
    """Folder name from a display name: forbidden characters out, spaces collapsed."""
    cleaned = "".join(" " if _is_forbidden(char) else char for char in _nfc(nome))
    collapsed = _WHITESPACE.sub(" ", cleaned).strip(" .")
    return validate_folder_name(collapsed)


# --- roots --------------------------------------------------------------------------

MountPredicate = Callable[[Path], bool]
_PROBE_PREFIX = ".osa-probe-"


def default_is_mount_point(path: Path) -> bool:
    """True when the path or one of its ancestors, except ``/``, is a mount point."""
    for candidate in (path, *path.parents):
        if candidate == Path(candidate.anchor):
            continue
        if os.path.ismount(candidate):
            return True
    return False


def probe_directory_writable(path: Path) -> None:
    """List the directory and create then remove one marked file, through its descriptor.

    Only exception to "no delete" (SPEC §10): the file is ours, created with O_EXCL under a
    fresh random name, removed in ``finally`` and never anything else. The directory is
    opened without following links and every step uses that descriptor, so a path swapped
    after validation cannot redirect the effect.
    """
    try:
        dir_fd = os.open(path, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    except OSError:
        raise UnsafePath("raiz inacessível ou é link simbólico") from None
    try:
        try:
            if Path(path).resolve(strict=True) != Path(path):
                raise UnsafePath("raiz mudou durante a validação (link simbólico)")
            held, current = os.fstat(dir_fd), os.stat(path, follow_symlinks=False)
            if (held.st_dev, held.st_ino) != (current.st_dev, current.st_ino):
                raise UnsafePath("raiz mudou durante a validação")
            os.listdir(dir_fd)
        except OSError:
            raise UnsafePath("aplicação não consegue listar a raiz") from None
        name = f"{_PROBE_PREFIX}{uuid.uuid4().hex}"
        created = False
        try:
            fd = os.open(
                name,
                os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                0o600,
                dir_fd=dir_fd,
            )
            created = True
            try:
                os.write(fd, b"osa write probe\n")
            finally:
                os.close(fd)
        except OSError:
            raise UnsafePath("aplicação não tem permissão de escrita na raiz") from None
        finally:
            if created:
                try:
                    os.unlink(name, dir_fd=dir_fd)
                except FileNotFoundError:
                    pass
    finally:
        os.close(dir_fd)


def validate_root_path(raw: str, *, is_mount_point: MountPredicate) -> Path:
    """The root as an absolute, link-free, existing directory in a mount point, writable.

    The write probe always runs, even with file effects disabled: it is the only way to
    know the service account can write there before an owner tree depends on it.
    """
    resolved = _checked_root_path(raw, is_mount_point=is_mount_point)
    probe_directory_writable(resolved)
    return resolved


def _checked_root_path(raw: str, *, is_mount_point: MountPredicate) -> Path:
    """Every root check that touches nothing: absolute, link-free, directory, mounted."""
    text = raw.strip()
    if not text or "\x00" in text or not text.startswith("/"):
        raise UnsafePath("raiz deve ser um caminho absoluto")
    candidate = Path(text)
    try:
        resolved = candidate.resolve(strict=True)
    except OSError:
        raise UnsafePath("raiz não existe") from None
    if resolved != candidate:
        raise UnsafePath("raiz não pode conter link simbólico ou '..'")
    if not resolved.is_dir():
        raise UnsafePath("raiz deve ser um diretório")
    if not is_mount_point(resolved):
        raise UnsafePath("raiz deve estar dentro de um ponto de montagem")
    return resolved


# --- shared plumbing ----------------------------------------------------------------

FOLDER_WARNING: Final = "pastas existentes não foram renomeadas"
# "OSA": every physical-name write, the model configuration and the import take it first.
_FOLDER_LOCK: Final = 0x4F5341_0001
_BOOTSTRAP_LOCK: Final = 0x4F5341_0002
_MAX_TEXT = 200
_MAX_DESCRIPTION = 500


@dataclass(frozen=True)
class Change[T]:
    """A mutated entity plus the warnings the caller should show."""

    entity: T
    warnings: tuple[str, ...] = ()


@dataclass(frozen=True)
class EffectiveModelConfig:
    """What consumers must use: database row over environment, provider off by default."""

    nome: str
    digest: str | None
    provedor_externo_ativo: bool
    chave_presente: bool


def _lock(session: Session, key: int) -> None:
    session.execute(select(func.pg_advisory_xact_lock(key)))


def lock_registry(session: Session) -> None:
    """Serialize registry-wide writes (import) with the physical-name writers."""
    _lock(session, _FOLDER_LOCK)


def audit_import(session: Session, actor: str, counts: dict[str, object]) -> None:
    _audit(session, actor, "registry.import", "registry", None, **counts)


def _audit(
    session: Session,
    actor: str,
    acao: str,
    alvo_tipo: str,
    alvo_id: int | None,
    **detalhes: object,
) -> None:
    record_event(
        session,
        ator=actor,
        acao=acao,
        alvo_tipo=alvo_tipo,
        alvo_id=alvo_id,
        resultado="ok",
        detalhes=dict(detalhes),
    )


def _audit_on_its_own(
    session: Session, actor: str, acao: str, alvo_tipo: str, resultado: str, **detalhes: object
) -> None:
    """Record an event on its own transaction, so the caller's rollback keeps it."""
    with Session(bind=session.get_bind()) as aux:
        record_event(
            aux,
            ator=actor,
            acao=acao,
            alvo_tipo=alvo_tipo,
            resultado=resultado,
            detalhes=dict(detalhes),
        )
        aux.commit()


def _audit_refusal(session: Session, actor: str, acao: str, alvo_tipo: str, motivo: str) -> None:
    _audit_on_its_own(session, actor, acao, alvo_tipo, RESULT_DENIED, motivo=motivo)


def _text(value: str | None, label: str, *, required: bool = True, limit: int = _MAX_TEXT) -> str:
    text = (value or "").strip()
    if required and not text:
        raise ValueError(f"{label} é obrigatório")
    if len(text) > limit or "\x00" in text:
        raise ValueError(f"{label} inválido")
    return text


def _telegram_id(value: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise ValueError("telegram_id deve ser um inteiro positivo")
    return value


def _active_verb(ativo: bool) -> str:
    return "activate" if ativo else "deactivate"


def _fetch[T](session: Session, model: type[T], entity_id: int, label: str) -> T:
    found = session.get(model, entity_id)
    if found is None:
        raise NotFound(f"{label} não encontrado")
    return found


def _set_active[T: (Root, WatchedFolder, Department, Owner)](
    session: Session,
    model: type[T],
    entity_id: int,
    ativo: bool,
    *,
    prefix: str,
    label: str,
    actor: str,
) -> T:
    entity = _fetch(session, model, entity_id, label)
    entity.ativo = ativo
    session.flush()
    _audit(session, actor, f"{prefix}.{_active_verb(ativo)}", prefix, entity_id)
    return entity


# --- bootstrap and users ------------------------------------------------------------


def has_admin(session: Session) -> bool:
    return bool(session.scalar(select(exists().where(User.papel == Role.ADMIN.value))))


def ensure_bootstrapped(session: Session) -> None:
    """The panel mutates nothing until the first administrator exists."""
    if not has_admin(session):
        raise Conflict("nenhum administrador cadastrado: rode `osa admin bootstrap`")


def bootstrap_admin(session: Session, *, telegram_id: int, nome: str, actor: str) -> User:
    """Create the first and only administrator; any existing admin, active or not, refuses."""
    telegram_id = _telegram_id(telegram_id)
    nome = _text(nome, "nome")
    _lock(session, _BOOTSTRAP_LOCK)
    if has_admin(session):
        _audit_refusal(session, actor, "admin.bootstrap", "user", "administrador já existe")
        raise Conflict("já existe um administrador: o bootstrap só roda uma vez")
    if session.scalar(select(exists().where(User.telegram_id == telegram_id))):
        raise Conflict("telegram_id já cadastrado")
    admin = User(telegram_id=telegram_id, nome=nome, papel=Role.ADMIN.value, departamento_id=None)
    session.add(admin)
    session.flush()
    _audit(session, actor, "admin.bootstrap", "user", admin.id)
    return admin


def _regular_role(papel: str) -> str:
    if papel == Role.ADMIN.value:
        raise AccessDenied("administrador só é criado por `osa admin bootstrap`")
    if papel not in {Role.OPERATOR.value, Role.REVIEWER.value}:
        raise ValueError("papel inválido")
    return papel


def _active_department(session: Session, department_id: int | None) -> int:
    if department_id is None:
        raise ValueError("departamento é obrigatório para este papel")
    department = _fetch(session, Department, department_id, "departamento")
    if not department.ativo:
        raise Conflict("departamento inativo")
    return department.id


def create_user(
    session: Session,
    *,
    telegram_id: int,
    nome: str,
    papel: str,
    departamento_id: int | None,
    actor: str,
) -> User:
    telegram_id = _telegram_id(telegram_id)
    nome = _text(nome, "nome")
    papel = _regular_role(papel)
    department_id = _active_department(session, departamento_id)
    if session.scalar(select(exists().where(User.telegram_id == telegram_id))):
        raise Conflict("telegram_id já cadastrado")
    user = User(telegram_id=telegram_id, nome=nome, papel=papel, departamento_id=department_id)
    session.add(user)
    session.flush()
    _audit(
        session, actor, "user.create", "user", user.id, papel=papel, departamento_id=department_id
    )
    return user


def update_user(
    session: Session,
    user_id: int,
    *,
    nome: str,
    papel: str,
    departamento_id: int | None,
    actor: str,
) -> User:
    user = _fetch(session, User, user_id, "usuário")
    nome = _text(nome, "nome")
    changed: list[str] = []
    if user.papel == Role.ADMIN.value:
        if papel != Role.ADMIN.value or departamento_id is not None:
            raise AccessDenied("administrador só pode ter o nome alterado")
    else:
        papel = _regular_role(papel)
        if departamento_id is not None and departamento_id == user.departamento_id:
            department_id = departamento_id  # kept as is, even if since deactivated
        else:
            department_id = _active_department(session, departamento_id)
        if papel != user.papel:
            user.papel = papel
            changed.append("papel")
        if department_id != user.departamento_id:
            user.departamento_id = department_id
            changed.append("departamento_id")
    if nome != user.nome:
        user.nome = nome
        changed.insert(0, "nome")
    session.flush()
    _audit(session, actor, "user.update", "user", user.id, campos=changed)
    return user


def set_user_active(session: Session, user_id: int, ativo: bool, *, actor: str) -> User:
    user = _fetch(session, User, user_id, "usuário")
    if not ativo and user.ativo and user.papel == Role.ADMIN.value:
        others = session.scalar(
            select(func.count())
            .select_from(User)
            .where(User.papel == Role.ADMIN.value, User.ativo.is_(True), User.id != user.id)
        )
        if not others:
            raise Conflict("não é possível desativar o último administrador ativo")
    user.ativo = ativo
    session.flush()
    _audit(session, actor, f"user.{_active_verb(ativo)}", "user", user.id)
    return user


# --- roots and watched folders ------------------------------------------------------


def _description(value: str | None) -> str | None:
    return _text(value, "descrição", required=False, limit=_MAX_DESCRIPTION) or None


def create_root(
    session: Session,
    *,
    caminho: str,
    descricao: str | None = None,
    is_mount_point: MountPredicate = default_is_mount_point,
    actor: str,
) -> Root:
    """Register a root: checks, then conflicts, then the write probe, then the row.

    Duplicates and nested roots are refused before the probe, so a refused root never gets
    a file created in it. The probe is the only filesystem effect of the registry (SPEC §10):
    its ``root.probe`` event goes on its own transaction and survives any later rollback
    (for instance of a failed import).
    """
    description = _description(descricao)
    try:
        path = _checked_root_path(caminho, is_mount_point=is_mount_point)
    except UnsafePath as error:
        _audit_refusal(session, actor, "root.create", "root", str(error))
        raise
    _lock(session, _FOLDER_LOCK)
    key = folder_key(str(path))
    for other in session.scalars(select(Root.caminho)):
        other_key = folder_key(other)
        if other_key == key:
            raise Conflict("raiz já cadastrada")
        if key.startswith(other_key + "/") or other_key.startswith(key + "/"):
            raise Conflict("raiz aninhada em outra raiz cadastrada")
    try:
        probe_directory_writable(path)
    except UnsafePath as error:
        _audit_refusal(session, actor, "root.create", "root", str(error))
        raise
    _audit_on_its_own(
        session, actor, "root.probe", "root", "ok", probe="ok", efeito="arquivo criado e removido"
    )
    root = Root(caminho=str(path), descricao=description)
    session.add(root)
    session.flush()
    _audit(session, actor, "root.create", "root", root.id, probe="ok")
    return root


def set_root_active(session: Session, root_id: int, ativo: bool, *, actor: str) -> Root:
    return _set_active(session, Root, root_id, ativo, prefix="root", label="raiz", actor=actor)


def _relative_parts(raw: str) -> list[str]:
    text = raw.strip()
    parts = text.split("/")
    if (
        not text
        or text.startswith("/")
        or "\x00" in text
        or "\\" in text
        or any(part in {"", ".", ".."} for part in parts)
    ):
        raise UnsafePath("pasta monitorada deve ser um caminho relativo simples")
    return parts


def create_watched_folder(
    session: Session, *, root_id: int, caminho: str, actor: str
) -> WatchedFolder:
    root = _fetch(session, Root, root_id, "raiz")
    relative = "/".join(_relative_parts(caminho))
    key = folder_key(relative)
    _lock(session, _FOLDER_LOCK)
    for other in session.scalars(
        select(WatchedFolder.caminho).where(WatchedFolder.root_id == root.id)
    ):
        if folder_key(other) == key:
            raise Conflict("pasta monitorada já cadastrada nesta raiz")
    full = Path(root.caminho) / relative
    try:
        resolved = full.resolve(strict=True)
    except OSError:
        raise UnsafePath("pasta monitorada não existe") from None
    if resolved != full:
        raise UnsafePath("pasta monitorada não pode conter link simbólico")
    if not resolved.is_dir():
        raise UnsafePath("pasta monitorada deve ser um diretório")
    folder = WatchedFolder(root_id=root.id, caminho=relative)
    session.add(folder)
    session.flush()
    _audit(session, actor, "watched_folder.create", "watched_folder", folder.id, root_id=root.id)
    return folder


def set_watched_folder_active(
    session: Session, folder_id: int, ativo: bool, *, actor: str
) -> WatchedFolder:
    return _set_active(
        session,
        WatchedFolder,
        folder_id,
        ativo,
        prefix="watched_folder",
        label="pasta monitorada",
        actor=actor,
    )


# --- departments (template) ---------------------------------------------------------


def _department_collision(
    session: Session, nome: str, pasta: str, *, exclude_id: int | None = None
) -> None:
    name_key, folder = folder_key(nome), folder_key(pasta)
    for other in session.scalars(select(Department)):
        if other.id == exclude_id:
            continue
        if folder_key(other.nome) == name_key:
            raise Conflict("já existe departamento com este nome")
        if folder_key(other.pasta) == folder:
            raise Conflict("pasta já em uso por outro departamento")


def create_department(
    session: Session,
    *,
    nome: str,
    pasta: str | None = None,
    usa_ano: bool = False,
    actor: str,
) -> Department:
    nome = _text(nome, "nome")
    folder = validate_folder_name(pasta) if pasta and pasta.strip() else derive_folder_name(nome)
    _lock(session, _FOLDER_LOCK)
    _department_collision(session, nome, folder)
    position = session.scalar(select(func.coalesce(func.max(Department.ordem), 0))) or 0
    department = Department(nome=nome, pasta=folder, ordem=position + 1, usa_ano=usa_ano)
    session.add(department)
    session.flush()
    _audit(session, actor, "department.create", "department", department.id, usa_ano=usa_ano)
    return department


def update_department(
    session: Session,
    department_id: int,
    *,
    nome: str,
    pasta: str,
    usa_ano: bool,
    actor: str,
) -> Change[Department]:
    department = _fetch(session, Department, department_id, "departamento")
    nome = _text(nome, "nome")
    folder = validate_folder_name(pasta)
    _lock(session, _FOLDER_LOCK)
    _department_collision(session, nome, folder, exclude_id=department.id)
    changed: list[str] = []
    warnings: list[str] = []
    if nome != department.nome:
        department.nome = nome
        changed.append("nome")
    if folder != department.pasta:
        department.pasta = folder
        changed.append("pasta")
        warnings.append(FOLDER_WARNING)
    if usa_ano != department.usa_ano:
        department.usa_ano = usa_ano
        changed.append("usa_ano")
    session.flush()
    details: dict[str, object] = {"campos": changed}
    if warnings:
        details["avisos"] = warnings
    _audit(session, actor, "department.update", "department", department.id, **details)
    return Change(department, tuple(warnings))


def move_department(session: Session, department_id: int, *, up: bool, actor: str) -> None:
    _fetch(session, Department, department_id, "departamento")
    ordered = list(session.scalars(select(Department).order_by(Department.ordem, Department.id)))
    index = next(i for i, d in enumerate(ordered) if d.id == department_id)
    target = index - 1 if up else index + 1
    moved = 0 <= target < len(ordered)
    if moved:
        ordered[index], ordered[target] = ordered[target], ordered[index]
    for position, department in enumerate(ordered, start=1):
        department.ordem = position
    session.flush()
    _audit(
        session, actor, "department.move", "department", department_id,
        direcao="up" if up else "down", movido=moved,
    )  # fmt: skip


def set_department_active(
    session: Session, department_id: int, ativo: bool, *, actor: str
) -> Department:
    return _set_active(
        session,
        Department,
        department_id,
        ativo,
        prefix="department",
        label="departamento",
        actor=actor,
    )


# --- owners and aliases -------------------------------------------------------------


def _alias_key(value: str) -> str:
    try:
        return normalize_alias(value)
    except ValueError:
        raise ValueError("alias deve conter letras ou números") from None


def _alias_keys(names: Iterable[str | None]) -> list[str]:
    keys: list[str] = []
    for name in names:
        if name and name.strip():
            key = _alias_key(name)
            if key not in keys:
                keys.append(key)
    return keys


def _owner_cnpj(value: str | None) -> str | None:
    if value is None or not value.strip():
        return None
    return normalize_cnpj(value.strip())


def _owner_folder(nome_fantasia: str, pasta: str | None) -> str:
    if pasta is not None and pasta.strip():
        return validate_folder_name(pasta)
    return derive_folder_name(nome_fantasia)


def _owner_conflicts(
    session: Session, *, cnpj: str | None, pasta: str, keys: Sequence[str], owner_id: int | None
) -> list[str]:
    """Raise on CNPJ, folder or alias clashes with other owners; return the new alias keys."""
    if cnpj is not None:
        taken = session.scalar(select(Owner.id).where(Owner.cnpj == cnpj))
        if taken is not None and taken != owner_id:
            raise Conflict("CNPJ já cadastrado em outro dono")
    folder = folder_key(pasta)
    for other_id, other_pasta in session.execute(select(Owner.id, Owner.pasta)):
        if other_id != owner_id and folder_key(other_pasta) == folder:
            raise Conflict("pasta já em uso por outro dono")
    own: set[str] = set()
    for alias_owner, alias in session.execute(select(OwnerAlias.owner_id, OwnerAlias.alias)):
        if alias in keys and alias_owner != owner_id:
            raise Conflict("alias já pertence a outro dono")
        if alias_owner == owner_id:
            own.add(alias)
    return [key for key in keys if key not in own]


def create_owner(
    session: Session,
    *,
    nome_fantasia: str,
    razao_social: str | None = None,
    cnpj: str | None = None,
    pasta: str | None = None,
    aliases: Sequence[str] = (),
    actor: str,
) -> Owner:
    nome_fantasia = _text(nome_fantasia, "nome fantasia")
    razao = _text(razao_social, "razão social", required=False) or None
    digits = _owner_cnpj(cnpj)
    folder = _owner_folder(nome_fantasia, pasta)
    keys = _alias_keys([nome_fantasia, razao, *aliases])
    _lock(session, _FOLDER_LOCK)
    new_keys = _owner_conflicts(session, cnpj=digits, pasta=folder, keys=keys, owner_id=None)
    owner = Owner(nome_fantasia=nome_fantasia, razao_social=razao, cnpj=digits, pasta=folder)
    session.add(owner)
    session.flush()
    session.add_all(OwnerAlias(owner_id=owner.id, alias=key) for key in new_keys)
    session.flush()
    _audit(session, actor, "owner.create", "owner", owner.id, aliases=len(new_keys))
    return owner


def update_owner(
    session: Session,
    owner_id: int,
    *,
    nome_fantasia: str,
    razao_social: str | None,
    cnpj: str | None,
    pasta: str,
    actor: str,
) -> Change[Owner]:
    owner = _fetch(session, Owner, owner_id, "dono")
    nome_fantasia = _text(nome_fantasia, "nome fantasia")
    razao = _text(razao_social, "razão social", required=False) or None
    digits = _owner_cnpj(cnpj)
    folder = validate_folder_name(pasta)
    keys = _alias_keys([nome_fantasia, razao])
    _lock(session, _FOLDER_LOCK)
    new_keys = _owner_conflicts(session, cnpj=digits, pasta=folder, keys=keys, owner_id=owner.id)
    changed: list[str] = []
    warnings: list[str] = []
    for field, value in (
        ("nome_fantasia", nome_fantasia),
        ("razao_social", razao),
        ("cnpj", digits),
        ("pasta", folder),
    ):
        if getattr(owner, field) != value:
            setattr(owner, field, value)
            changed.append(field)
    if "pasta" in changed:
        warnings.append(FOLDER_WARNING)
    session.flush()
    session.add_all(OwnerAlias(owner_id=owner.id, alias=key) for key in new_keys)
    session.flush()
    details: dict[str, object] = {"campos": changed, "aliases_novos": len(new_keys)}
    if warnings:
        details["avisos"] = warnings
    _audit(session, actor, "owner.update", "owner", owner.id, **details)
    return Change(owner, tuple(warnings))


def add_alias(session: Session, owner_id: int, alias: str, *, actor: str) -> OwnerAlias:
    owner = _fetch(session, Owner, owner_id, "dono")
    key = _alias_key(alias)
    holder = session.scalar(select(OwnerAlias.owner_id).where(OwnerAlias.alias == key))
    if holder is not None:
        raise Conflict("alias já cadastrado")
    row = OwnerAlias(owner_id=owner.id, alias=key)
    session.add(row)
    session.flush()
    _audit(session, actor, "owner_alias.add", "owner_alias", row.id, owner_id=owner.id)
    return row


def remove_alias(session: Session, owner_id: int, alias_id: int, *, actor: str) -> None:
    row = session.scalar(
        select(OwnerAlias).where(OwnerAlias.id == alias_id, OwnerAlias.owner_id == owner_id)
    )
    if row is None:
        raise NotFound("alias não encontrado")
    session.delete(row)
    session.flush()
    _audit(session, actor, "owner_alias.remove", "owner_alias", alias_id, owner_id=owner_id)


def set_owner_active(session: Session, owner_id: int, ativo: bool, *, actor: str) -> Owner:
    return _set_active(session, Owner, owner_id, ativo, prefix="owner", label="dono", actor=actor)


# --- model and external provider ----------------------------------------------------

_MODEL_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9:._/-]{0,127}$")
_MODEL_DIGEST = re.compile(r"^[A-Za-z0-9:._-]{1,128}$")


def _key_present(settings: Settings) -> bool:
    key = settings.external_llm_api_key
    return key is not None and bool(key.get_secret_value().strip())


def effective_model_config(session: Session, settings: Settings) -> EffectiveModelConfig:
    """Single resolver for panel and future consumers: the stored row wins over the env.

    The external provider counts as on only if its key is present in the environment.
    """
    present = _key_present(settings)
    row = session.get(InstallationSettings, 1)
    if row is None:
        return EffectiveModelConfig(
            settings.ollama_model,
            settings.ollama_model_digest,
            settings.external_llm_enabled and present,
            present,
        )
    return EffectiveModelConfig(
        row.modelo_nome, row.modelo_digest, row.provedor_externo_ativo and present, present
    )


def set_model_config(
    session: Session,
    *,
    nome: str,
    digest: str | None,
    provedor_externo_ativo: bool,
    settings: Settings,
    actor: str,
) -> InstallationSettings:
    """Store model name, digest and provider toggle; the key itself is never stored."""
    nome = _text(nome, "nome do modelo")
    if not _MODEL_NAME.fullmatch(nome):
        raise ValueError("nome de modelo inválido")
    digest_text = _text(digest, "digest do modelo", required=False) or None
    if digest_text is not None and not _MODEL_DIGEST.fullmatch(digest_text):
        raise ValueError("digest de modelo inválido")
    present = _key_present(settings)
    if provedor_externo_ativo and not present:
        raise Conflict("provedor externo exige OSA_EXTERNAL_LLM_API_KEY configurada no ambiente")
    _lock(session, _FOLDER_LOCK)  # an import checks "registry empty" under this same lock
    row = session.get(InstallationSettings, 1)
    if row is None:
        row = InstallationSettings(id=1, modelo_nome=nome)
        session.add(row)
        changed = ["modelo_nome", "modelo_digest", "provedor_externo_ativo"]
        row.modelo_digest = digest_text
        row.provedor_externo_ativo = provedor_externo_ativo
    else:
        changed = []
        for field, value in (
            ("modelo_nome", nome),
            ("modelo_digest", digest_text),
            ("provedor_externo_ativo", provedor_externo_ativo),
        ):
            if getattr(row, field) != value:
                setattr(row, field, value)
                changed.append(field)
    session.flush()
    _audit(
        session, actor, "model.update", "installation_settings", 1,
        campos=changed, provedor_externo_ativo=provedor_externo_ativo, chave_presente=present,
    )  # fmt: skip
    return row
