"""The built-in manual: one static page that needs neither the database nor an administrator."""

from fastapi import APIRouter, Request
from fastapi.responses import Response

from osa.panel import manual
from osa.panel.routes.common import templates

router = APIRouter()


@router.get("/manual")
def show_manual(request: Request) -> Response:
    # No session is opened on purpose: the manual must open with an empty registry, with no
    # administrator and even with the database down. ``bootstrapped`` is only the layout flag
    # that hides the "Cadastro bloqueado" box, which does not belong on this page.
    context = {
        "csrf_token": request.state.csrf_token,
        "bootstrapped": True,
        "error": None,
        "notice": None,
        "success": None,
        "confirm": None,
        "chapters": manual.CHAPTERS,
        "problems": manual.PROBLEMS,
    }
    return templates.TemplateResponse(request, "manual.html", context)
