"""Declared owner, the root that receives an upload, and the request for a new owner.

The operator names an owner in words; code resolves it against the registry (alias key or
valid CNPJ) and the model never takes part. A name that resolves to nothing becomes an
``OwnerRequest`` that only an active administrator can confirm; the confirmation creates
the owner and its template tree (SPEC §4).
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from osa.admin import services
from osa.courier.decision import TemplateDepartment
from osa.courier.ports import FsPort
from osa.db.audit import RESULT_DENIED, RESULT_OK, RESULT_SIMULATED, record_event
from osa.db.models import Department, Owner, OwnerAlias, Root, User
from osa.domain.errors import AccessDenied, Conflict, NotFound
from osa.domain.owner import is_valid_cnpj, normalize_alias, normalize_cnpj
from osa.domain.user import Role


def resolve_declared_owner(session: Session, text: str) -> Owner | None:
    """The active owner whose alias key or CNPJ equals ``text``; ``None`` when there is none.

    Exact match only: suggestions by similarity are the bot's job (TASK-108).
    """
    value = text.strip()
    if not value:
        return None
    if is_valid_cnpj(value):
        owner = session.scalar(select(Owner).where(Owner.cnpj == normalize_cnpj(value)))
        return owner if owner is not None and owner.ativo else None
    try:
        key = normalize_alias(value)
    except ValueError:
        return None
    owner_id = session.scalar(select(OwnerAlias.owner_id).where(OwnerAlias.alias == key))
    if owner_id is None:
        return None
    owner = session.get(Owner, owner_id)
    return owner if owner is not None and owner.ativo else None


def active_roots(session: Session) -> list[Root]:
    return list(session.scalars(select(Root).where(Root.ativo.is_(True)).order_by(Root.id)))


def upload_root(session: Session, owner: Owner, *, fs: FsPort) -> Root:
    """The root whose quarantine receives an upload for ``owner`` (SPEC §4).

    The first active root, in registration order, that already holds the owner's folder;
    when none does, the first active root. Deterministic, so the same owner always lands in
    the same tree; an owner spread over two roots keeps the older one.
    """
    roots = active_roots(session)
    if not roots:
        raise NotFound("nenhuma raiz ativa cadastrada")
    for root in roots:
        if fs.has_folder(Path(root.caminho), owner.pasta):
            return root
    return roots[0]


def active_template(session: Session) -> list[TemplateDepartment]:
    return [
        TemplateDepartment(d.id, d.nome, d.pasta, d.usa_ano)
        for d in session.scalars(
            select(Department)
            .where(Department.ativo.is_(True))
            .order_by(Department.ordem, Department.id)
        )
    ]


@dataclass(frozen=True, slots=True)
class OwnerRequest:
    """An owner the operator named and the registry does not know; pending until confirmed."""

    nome: str
    requested_by: int  # users.id of the operator


def _active_admin(session: Session, user_id: int) -> User:
    user = session.get(User, user_id)
    if user is None or not user.ativo or user.papel != Role.ADMIN.value:
        raise AccessDenied("só um administrador ativo confirma ou recusa um dono novo")
    return user


def confirm_owner_request(
    session: Session,
    request: OwnerRequest,
    *,
    admin_id: int,
    fs: FsPort,
    effects_enabled: bool,
    actor: str,
    aliases: Sequence[str] = (),
) -> Owner:
    """Create the owner, commit, then create its template tree in the upload root.

    Two commits, in this order, so a folder is never created for an owner the registry does
    not have (SPEC §10): the owner and the confirmation event first, the tree and its event
    after. Refused without an active administrator (``AccessDenied``) and for a name that
    already resolves to an owner (``Conflict``). With effects off the tree is only predicted.
    """
    admin = _active_admin(session, admin_id)
    if resolve_declared_owner(session, request.nome) is not None:
        raise Conflict("dono já cadastrado")
    owner = services.create_owner(session, nome_fantasia=request.nome, aliases=aliases, actor=actor)
    record_event(
        session,
        ator=actor,
        acao="owner.request.confirm",
        alvo_tipo="owner",
        alvo_id=owner.id,
        resultado=RESULT_OK,
        detalhes={"solicitante": request.requested_by, "administrador": admin.id},
    )
    session.commit()
    root = upload_root(session, owner, fs=fs)
    tree = fs.ensure_owner_tree(
        Path(root.caminho),
        owner.pasta,
        active_template(session),
        None,
        effects_enabled=effects_enabled,
    )
    record_event(
        session,
        ator=actor,
        acao="owner.tree",
        alvo_tipo="owner",
        alvo_id=owner.id,
        resultado=RESULT_SIMULATED if tree.simulated else RESULT_OK,
        detalhes={"root_id": root.id, "pastas_criadas": len(tree.created)},
    )
    session.commit()
    return owner


def refuse_owner_request(
    session: Session, request: OwnerRequest, *, admin_id: int, actor: str
) -> None:
    """Record the refusal; nothing is created. The caller commits."""
    admin = _active_admin(session, admin_id)
    record_event(
        session,
        ator=actor,
        acao="owner.request.refuse",
        alvo_tipo="owner",
        alvo_id=None,
        resultado=RESULT_DENIED,
        detalhes={"solicitante": request.requested_by, "administrador": admin.id},
    )
