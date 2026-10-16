from fastapi import APIRouter, Depends, Request
from fastapi.responses import Response
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from osa.admin import services
from osa.db.models import Department, Owner, Root, User
from osa.panel.routes.common import app_settings, get_session, render

router = APIRouter()


@router.get("/")
def index(request: Request, session: Session = Depends(get_session)) -> Response:
    counts = {
        "raízes": session.scalar(select(func.count()).select_from(Root)),
        "departamentos": session.scalar(select(func.count()).select_from(Department)),
        "donos": session.scalar(select(func.count()).select_from(Owner)),
        "usuários": session.scalar(select(func.count()).select_from(User)),
    }
    model = services.effective_model_config(session, app_settings(request))
    return render(request, session, "index.html", {"counts": counts, "model": model})
