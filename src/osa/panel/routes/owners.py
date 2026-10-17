from collections.abc import Callable
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import Response
from sqlalchemy import select
from sqlalchemy.orm import Session

from osa.admin import services
from osa.db.models import Owner, OwnerAlias
from osa.panel.routes.common import (
    ACTOR,
    confirmation,
    get_session,
    must_confirm,
    mutate,
    render,
)

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
    confirm: dict[str, Any] | None = None,
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
        error=error, status=status, confirm=confirm,
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
        saved="dono-cadastrado",
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
        saved="dono-salvo",
        ref=owner_id,
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
        saved="alias-adicionado",
    )


@router.post("/owners/{owner_id}/aliases/{alias_id}/remover")
def remove_alias(
    request: Request,
    owner_id: int,
    alias_id: int,
    confirmar: Text = "",
    session: Session = Depends(get_session),
) -> Response:
    owner = session.get(Owner, owner_id)
    row = session.get(OwnerAlias, alias_id)
    if (
        owner is not None
        and row is not None
        and row.owner_id == owner_id
        and must_confirm(session, confirmar, destructive=True)
    ):
        return _detail_page(
            request,
            session,
            owner_id,
            confirm=confirmation(
                before="Remover o alias ",
                name=row.alias,
                after=f" do dono {owner.nome_fantasia}?",
                detail="O nome deixa de apontar para este dono, e não há desfazer. Para tê-lo "
                "de volta, adicione o alias de novo.",
                action=f"/owners/{owner_id}/aliases/{alias_id}/remover",
                fields={},
                button="Remover",
                glyph="trash",
                cancel=f"/owners/{owner_id}",
            ),
        )
    return mutate(
        session,
        lambda: services.remove_alias(session, owner_id, alias_id, actor=ACTOR),
        done=f"/owners/{owner_id}",
        fail=_detail_fail(request, session, owner_id),
        saved="alias-removido",
        ref=owner_id,
    )


@router.post("/owners/{owner_id}/ativo")
def toggle_owner(
    request: Request,
    owner_id: int,
    ativo: Text = "0",
    confirmar: Text = "",
    session: Session = Depends(get_session),
) -> Response:
    owner = session.get(Owner, owner_id)
    if owner is not None and must_confirm(session, confirmar, destructive=ativo != "1"):
        return _detail_page(
            request,
            session,
            owner_id,
            confirm=confirmation(
                before="Desativar o dono ",
                name=owner.nome_fantasia,
                after="?",
                detail="Ele fica inativo e não recebe documentos. O CNPJ, a pasta e os aliases "
                "continuam com ele. Nada é apagado, e você pode ativá-lo de novo.",
                action=f"/owners/{owner_id}/ativo",
                fields={"ativo": "0"},
                button="Desativar",
                glyph="power",
                cancel=f"/owners/{owner_id}",
            ),
        )
    return mutate(
        session,
        lambda: services.set_owner_active(session, owner_id, ativo == "1", actor=ACTOR),
        done=f"/owners/{owner_id}",
        fail=_detail_fail(request, session, owner_id),
        saved="dono-ativado" if ativo == "1" else "dono-desativado",
        ref=owner_id,
    )
