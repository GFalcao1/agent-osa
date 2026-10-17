from typing import Annotated

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import Response
from sqlalchemy.orm import Session

from osa.admin import services
from osa.panel.routes.common import ACTOR, app_settings, checked, get_session, mutate, render

router = APIRouter()
Text = Annotated[str, Form()]
Flag = Annotated[str | None, Form()]


def _page(
    request: Request, session: Session, *, error: str | None = None, status: int = 200
) -> Response:
    config = services.effective_model_config(session, app_settings(request))
    return render(request, session, "model.html", {"model": config}, error=error, status=status)


@router.get("/model")
def show_model(request: Request, session: Session = Depends(get_session)) -> Response:
    return _page(request, session)


@router.post("/model")
def save_model(
    request: Request,
    nome: Text = "",
    digest: Text = "",
    provedor_externo_ativo: Flag = None,
    session: Session = Depends(get_session),
) -> Response:
    return mutate(
        session,
        lambda: services.set_model_config(
            session,
            nome=nome,
            digest=digest or None,
            provedor_externo_ativo=checked(provedor_externo_ativo),
            settings=app_settings(request),
            actor=ACTOR,
        ),
        done="/model",
        fail=lambda message, status: _page(request, session, error=message, status=status),
        saved="modelo-salvo",
    )
