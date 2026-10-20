from collections.abc import Callable
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import Response
from sqlalchemy import select
from sqlalchemy.orm import Session

from osa.admin import services
from osa.db.models import Department, User
from osa.domain.errors import Conflict
from osa.panel.routes.common import (
    ACTOR,
    confirmation,
    get_session,
    must_confirm,
    mutate,
    parse_int,
    parse_telegram_id,
    render,
)

router = APIRouter()
Text = Annotated[str, Form()]
ROLES = ("operator", "reviewer")  # the form never offers admin: only the bootstrap creates one


def _page(
    request: Request,
    session: Session,
    *,
    error: str | None = None,
    status: int = 200,
    confirm: dict[str, Any] | None = None,
) -> Response:
    users = list(session.scalars(select(User).order_by(User.id)))
    departments = list(
        session.scalars(select(Department).order_by(Department.ordem, Department.id))
    )
    names = {d.id: d.nome for d in departments}
    return render(
        request, session, "users.html",
        {
            "users": users,
            "departments": [d for d in departments if d.ativo],
            # The edit form keeps each user's current department even when it is inactive,
            # so saving only the name never moves the user to another department.
            "all_departments": departments,
            "department_names": names,
            "roles": ROLES,
        },
        error=error, status=status, confirm=confirm,
    )  # fmt: skip


def _fail(request: Request, session: Session) -> Callable[[str, int], Response]:
    return lambda message, status: _page(request, session, error=message, status=status)


@router.get("/users")
def list_users(request: Request, session: Session = Depends(get_session)) -> Response:
    return _page(request, session)


@router.post("/users")
def create_user(
    request: Request,
    telegram_id: Text = "",
    nome: Text = "",
    papel: Text = "",
    departamento_id: Text = "",
    session: Session = Depends(get_session),
) -> Response:
    return mutate(
        session,
        lambda: services.create_user(
            session,
            telegram_id=parse_telegram_id(telegram_id),
            nome=nome,
            papel=papel,
            departamento_id=parse_int(departamento_id, "departamento"),
            actor=ACTOR,
        ),
        done="/users",
        fail=_fail(request, session),
        saved="usuario-cadastrado",
    )


@router.post("/users/{user_id}")
def update_user(
    request: Request,
    user_id: int,
    nome: Text = "",
    papel: Text = "",
    departamento_id: Text = "",
    session: Session = Depends(get_session),
) -> Response:
    return mutate(
        session,
        lambda: services.update_user(
            session,
            user_id,
            nome=nome,
            papel=papel,
            departamento_id=parse_int(departamento_id, "departamento") if departamento_id else None,
            actor=ACTOR,
        ),
        done="/users",
        fail=_fail(request, session),
        saved="usuario-salvo",
        ref=user_id,
    )


@router.post("/users/{user_id}/ativo")
def toggle_user(
    request: Request,
    user_id: int,
    ativo: Text = "0",
    confirmar: Text = "",
    session: Session = Depends(get_session),
) -> Response:
    user = session.get(User, user_id)
    if user is not None and ativo != "1":
        # Refused anyway (last active administrator): say so now, without asking first.
        try:
            services.ensure_can_deactivate_user(session, user)
        except Conflict as error:
            return _page(request, session, error=str(error), status=409)
    if user is not None and must_confirm(session, confirmar, destructive=ativo != "1"):
        return _page(
            request,
            session,
            confirm=confirmation(
                before="Desativar o usuário ",
                name=user.nome,
                after="?",
                detail="Essa pessoa deixa de falar com o agente pelo Telegram. O cadastro não é "
                "apagado, e você pode ativá-la de novo.",
                action=f"/users/{user_id}/ativo",
                fields={"ativo": "0"},
                button="Desativar",
                glyph="power",
                cancel="/users",
            ),
        )
    return mutate(
        session,
        lambda: services.set_user_active(session, user_id, ativo == "1", actor=ACTOR),
        done="/users",
        fail=_fail(request, session),
        saved="usuario-ativado" if ativo == "1" else "usuario-desativado",
        ref=user_id,
    )
