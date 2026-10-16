from typing import Annotated

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import Response
from sqlalchemy import select
from sqlalchemy.orm import Session

from osa.admin import services
from osa.db.models import Root, WatchedFolder
from osa.panel.routes.common import ACTOR, get_session, mutate, render

router = APIRouter()
Text = Annotated[str, Form()]


def _page(
    request: Request, session: Session, *, error: str | None = None, status: int = 200
) -> Response:
    roots = list(session.scalars(select(Root).order_by(Root.id)))
    folders: dict[int, list[WatchedFolder]] = {}
    for folder in session.scalars(select(WatchedFolder).order_by(WatchedFolder.id)):
        folders.setdefault(folder.root_id, []).append(folder)
    return render(
        request, session, "roots.html", {"roots": roots, "folders": folders},
        error=error, status=status,
    )  # fmt: skip


@router.get("/roots")
def list_roots(request: Request, session: Session = Depends(get_session)) -> Response:
    return _page(request, session)


@router.post("/roots")
def create_root(
    request: Request,
    caminho: Text = "",
    descricao: Text = "",
    session: Session = Depends(get_session),
) -> Response:
    is_mount_point = request.app.state.is_mount_point
    return mutate(
        session,
        lambda: services.create_root(
            session,
            caminho=caminho,
            descricao=descricao,
            is_mount_point=is_mount_point,
            actor=ACTOR,
        ),
        done="/roots",
        fail=lambda message, status: _page(request, session, error=message, status=status),
    )


@router.post("/roots/{root_id}/ativo")
def toggle_root(
    request: Request, root_id: int, ativo: Text = "0", session: Session = Depends(get_session)
) -> Response:
    return mutate(
        session,
        lambda: services.set_root_active(session, root_id, ativo == "1", actor=ACTOR),
        done="/roots",
        fail=lambda message, status: _page(request, session, error=message, status=status),
    )


@router.post("/roots/{root_id}/pastas")
def create_watched_folder(
    request: Request, root_id: int, caminho: Text = "", session: Session = Depends(get_session)
) -> Response:
    return mutate(
        session,
        lambda: services.create_watched_folder(
            session, root_id=root_id, caminho=caminho, actor=ACTOR
        ),
        done="/roots",
        fail=lambda message, status: _page(request, session, error=message, status=status),
    )


@router.post("/pastas/{folder_id}/ativo")
def toggle_watched_folder(
    request: Request, folder_id: int, ativo: Text = "0", session: Session = Depends(get_session)
) -> Response:
    return mutate(
        session,
        lambda: services.set_watched_folder_active(session, folder_id, ativo == "1", actor=ACTOR),
        done="/roots",
        fail=lambda message, status: _page(request, session, error=message, status=status),
    )
