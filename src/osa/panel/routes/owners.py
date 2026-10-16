from collections.abc import Callable
from typing import Annotated

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import Response
from sqlalchemy import select
from sqlalchemy.orm import Session

from osa.admin import services
from osa.db.models import Owner, OwnerAlias
from osa.panel.routes.common import ACTOR, get_session, mutate, render

router = APIRouter()
Text = Annotated[str, Form()]


def _list_page(
    request: Request, session: Session, *, error: str | None = None, status: int = 200
) -> Response:
    owners = list(session.scalars(select(Owner).order_by(Owner.id)))
    return render(request, session, "owners.html", {"owners": owners}, error=error, status=status)


def _detail_page(
    request: Request,
    session: Session,
    owner_id: int,
    *,
    error: str | None = None,
    status: int = 200,
) -> Response:
    owner = session.get(Owner, owner_id)
    if owner is None:
        return render(
            request, session, "owners.html", {"owners": []}, error="dono não encontrado", status=404
        )
    aliases = list(
        session.scalars(
            select(OwnerAlias).where(OwnerAlias.owner_id == owner_id).order_by(OwnerAlias.alias)
        )
    )
    return render(
        request, session, "owner.html", {"owner": owner, "aliases": aliases},
        error=error, status=status,
    )  # fmt: skip


@router.get("/owners")
def list_owners(request: Request, session: Session = Depends(get_session)) -> Response:
    return _list_page(request, session)


@router.post("/owners")
def create_owner(
    request: Request,
    nome_fantasia: Text = "",
    razao_social: Text = "",
    cnpj: Text = "",
    pasta: Text = "",
    aliases: Text = "",
    session: Session = Depends(get_session),
) -> Response:
    names = [line for line in aliases.splitlines() if line.strip()]
    return mutate(
        session,
        lambda: services.create_owner(
            session,
            nome_fantasia=nome_fantasia,
            razao_social=razao_social or None,
            cnpj=cnpj or None,
            pasta=pasta or None,
            aliases=names,
            actor=ACTOR,
        ),
        done="/owners",
        fail=lambda message, status: _list_page(request, session, error=message, status=status),
    )


@router.get("/owners/{owner_id}")
def owner_detail(
    request: Request, owner_id: int, session: Session = Depends(get_session)
) -> Response:
    return _detail_page(request, session, owner_id)


def _detail_fail(
    request: Request, session: Session, owner_id: int
) -> Callable[[str, int], Response]:
    return lambda message, status: _detail_page(
        request, session, owner_id, error=message, status=status
    )


@router.post("/owners/{owner_id}")
def update_owner(
    request: Request,
    owner_id: int,
    nome_fantasia: Text = "",
    razao_social: Text = "",
    cnpj: Text = "",
    pasta: Text = "",
    session: Session = Depends(get_session),
) -> Response:
    return mutate(
        session,
        lambda: services.update_owner(
            session,
            owner_id,
            nome_fantasia=nome_fantasia,
            razao_social=razao_social or None,
            cnpj=cnpj or None,
            pasta=pasta,
            actor=ACTOR,
        ),
        done=f"/owners/{owner_id}",
        fail=_detail_fail(request, session, owner_id),
    )


@router.post("/owners/{owner_id}/aliases")
def add_alias(
    request: Request, owner_id: int, alias: Text = "", session: Session = Depends(get_session)
) -> Response:
    return mutate(
        session,
        lambda: services.add_alias(session, owner_id, alias, actor=ACTOR),
        done=f"/owners/{owner_id}",
        fail=_detail_fail(request, session, owner_id),
    )


@router.post("/owners/{owner_id}/aliases/{alias_id}/remover")
def remove_alias(
    request: Request, owner_id: int, alias_id: int, session: Session = Depends(get_session)
) -> Response:
    return mutate(
        session,
        lambda: services.remove_alias(session, owner_id, alias_id, actor=ACTOR),
        done=f"/owners/{owner_id}",
        fail=_detail_fail(request, session, owner_id),
    )


@router.post("/owners/{owner_id}/ativo")
def toggle_owner(
    request: Request, owner_id: int, ativo: Text = "0", session: Session = Depends(get_session)
) -> Response:
    return mutate(
        session,
        lambda: services.set_owner_active(session, owner_id, ativo == "1", actor=ACTOR),
        done=f"/owners/{owner_id}",
        fail=_detail_fail(request, session, owner_id),
    )
