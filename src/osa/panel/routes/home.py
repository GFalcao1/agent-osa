from fastapi import APIRouter, Depends, Request
from fastapi.responses import Response
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from osa.admin import services
from osa.db.models import Department, Owner, Root, User
from osa.panel.routes.common import app_settings, get_session, render

router = APIRouter()

# The order of the manual: the first section still empty is the next thing to fill in.
_STEPS = (
    ("raízes", "/roots", "cadastrar a primeira raiz"),
    ("departamentos", "/departments", "montar o template de departamentos"),
    ("donos", "/owners", "cadastrar o primeiro dono"),
    ("usuários", "/users", "cadastrar o primeiro usuário"),
)


def _next_step(counts: dict[str, int | None], people: int | None) -> dict[str, str] | None:
    filled = {**counts, "usuários": people}
    for key, href, label in _STEPS:
        if not filled.get(key):
            return {"href": href, "label": label}
    return None


@router.get("/")
def index(request: Request, session: Session = Depends(get_session)) -> Response:
    counts = {
        "raízes": session.scalar(select(func.count()).select_from(Root)),
        "departamentos": session.scalar(select(func.count()).select_from(Department)),
        "donos": session.scalar(select(func.count()).select_from(Owner)),
        "usuários": session.scalar(select(func.count()).select_from(User)),
    }
    # The administrator always exists, so "no users yet" means no operator or reviewer.
    people = session.scalar(select(func.count()).select_from(User).where(User.papel != "admin"))
    model = services.effective_model_config(session, app_settings(request))
    return render(
        request,
        session,
        "index.html",
        {"counts": counts, "model": model, "next_step": _next_step(counts, people)},
    )
