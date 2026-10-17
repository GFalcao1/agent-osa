from collections.abc import Callable
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import Response
from sqlalchemy import select
from sqlalchemy.orm import Session

from osa.admin import services
from osa.db.models import Department
from osa.panel.routes.common import (
    ACTOR,
    checked,
    confirmation,
    get_session,
    must_confirm,
    mutate,
    render,
)

router = APIRouter()
Text = Annotated[str, Form()]
Flag = Annotated[str | None, Form()]


def _page(
    request: Request,
    session: Session,
    *,
    error: str | None = None,
    status: int = 200,
    confirm: dict[str, Any] | None = None,
) -> Response:
    departments = list(
        session.scalars(select(Department).order_by(Department.ordem, Department.id))
    )
    return render(
        request, session, "departments.html", {"departments": departments},
        error=error, status=status, confirm=confirm,
    )  # fmt: skip


def _fail(request: Request, session: Session) -> Callable[[str, int], Response]:
    return lambda message, status: _page(request, session, error=message, status=status)


@router.get("/departments")
def list_departments(request: Request, session: Session = Depends(get_session)) -> Response:
    return _page(request, session)


@router.post("/departments")
def create_department(
    request: Request,
    nome: Text = "",
    pasta: Text = "",
    usa_ano: Flag = None,
    session: Session = Depends(get_session),
) -> Response:
    return mutate(
        session,
        lambda: services.create_department(
            session, nome=nome, pasta=pasta or None, usa_ano=checked(usa_ano), actor=ACTOR
        ),
        done="/departments",
        fail=_fail(request, session),
        saved="departamento-adicionado",
    )


@router.post("/departments/{department_id}")
def update_department(
    request: Request,
    department_id: int,
    nome: Text = "",
    pasta: Text = "",
    usa_ano: Flag = None,
    session: Session = Depends(get_session),
) -> Response:
    return mutate(
        session,
        lambda: services.update_department(
            session, department_id, nome=nome, pasta=pasta, usa_ano=checked(usa_ano), actor=ACTOR
        ),
        done="/departments",
        fail=_fail(request, session),
        saved="departamento-salvo",
        ref=department_id,
    )


@router.post("/departments/{department_id}/mover")
def move_department(
    request: Request,
    department_id: int,
    direcao: Text = "",
    session: Session = Depends(get_session),
) -> Response:
    return mutate(
        session,
        lambda: services.move_department(session, department_id, up=direcao == "up", actor=ACTOR),
        done="/departments",
        fail=_fail(request, session),
        saved="departamento-subiu" if direcao == "up" else "departamento-desceu",
        ref=department_id,
    )


@router.post("/departments/{department_id}/ativo")
def toggle_department(
    request: Request,
    department_id: int,
    ativo: Text = "0",
    confirmar: Text = "",
    session: Session = Depends(get_session),
) -> Response:
    department = session.get(Department, department_id)
    if department is not None and must_confirm(session, confirmar, destructive=ativo != "1"):
        return _page(
            request,
            session,
            confirm=confirmation(
                before="Desativar o departamento ",
                name=department.nome,
                after="?",
                detail="Ele fica inativo: não recebe documentos e não pode ser escolhido para uma "
                "pessoa nova. Nada é apagado, e você pode ativá-lo de novo.",
                action=f"/departments/{department_id}/ativo",
                fields={"ativo": "0"},
                button="Desativar",
                glyph="power",
                cancel="/departments",
            ),
        )
    return mutate(
        session,
        lambda: services.set_department_active(session, department_id, ativo == "1", actor=ACTOR),
        done="/departments",
        fail=_fail(request, session),
        saved="departamento-ativado" if ativo == "1" else "departamento-desativado",
        ref=department_id,
    )
