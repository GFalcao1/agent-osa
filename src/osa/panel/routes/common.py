"""Shared helpers of the panel routes: session, CSRF guard, rendering and mutations."""

import base64
import hashlib
import hmac
import secrets
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any, Final

from fastapi import HTTPException, Request
from fastapi.responses import RedirectResponse, Response
from fastapi.templating import Jinja2Templates
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from osa.admin import services
from osa.core.settings import Settings
from osa.domain.errors import AccessDenied, Conflict, DomainError, NotFound

ACTOR: Final = "panel:local"
TEMPLATE_DIR: Final = Path(__file__).resolve().parent.parent / "templates"
templates = Jinja2Templates(directory=str(TEMPLATE_DIR))

# Fixed texts for redirect query codes: nothing user-supplied travels in a URL.
NOTICES: Final = {
    "pastas": "Pastas já existentes no servidor não foram renomeadas; ajuste-as à mão se precisar.",
}
_SAFE_METHODS: Final = frozenset({"GET", "HEAD"})
CSRF_COOKIE: Final = "osa_csrf"
_NONCE_BYTES: Final = 32
_NONCE_CHARS: Final = 43  # token_urlsafe(32) without padding
_ALPHABET: Final = frozenset("ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789_-")


def _signature(secret: bytes, nonce: str) -> str:
    digest = hmac.new(secret, nonce.encode("ascii"), hashlib.sha256).digest()
    return base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")


def new_csrf_token(secret: bytes) -> str:
    """``nonce.signature``: only this panel process (``secret``) can mint a valid token."""
    nonce = secrets.token_urlsafe(_NONCE_BYTES)
    return f"{nonce}.{_signature(secret, nonce)}"


def csrf_token_is_valid(value: str | None, secret: bytes) -> bool:
    """A cookie planted by another local origin (cookies ignore the port) fails here."""
    if not value or value.count(".") != 1:
        return False
    nonce, signature = value.split(".")
    if len(nonce) != _NONCE_CHARS or not all(char in _ALPHABET for char in nonce):
        return False
    return hmac.compare_digest(
        signature.encode("ascii", "replace"), _signature(secret, nonce).encode()
    )


def get_session(request: Request) -> Iterator[Session]:
    """One Session per request, always closed (uncommitted work is rolled back)."""
    session: Session = request.app.state.session_factory()
    try:
        yield session
    finally:
        session.close()


def app_settings(request: Request) -> Settings:
    settings: Settings = request.app.state.settings
    return settings


async def csrf_guard(request: Request) -> None:
    """Signed double-submit check for every mutation; runs before any route code."""
    if request.method in _SAFE_METHODS:
        return
    cookie = request.cookies.get(CSRF_COOKIE, "")
    form = await request.form()
    supplied = form.get("csrf_token", "")
    if (
        not csrf_token_is_valid(cookie, request.app.state.csrf_secret)
        or not isinstance(supplied, str)
        or not secrets.compare_digest(cookie.encode(), supplied.encode())
    ):
        raise HTTPException(status_code=403, detail="forbidden")


def render(
    request: Request,
    session: Session,
    template: str,
    context: dict[str, Any],
    *,
    error: str | None = None,
    status: int = 200,
) -> Response:
    base = {
        "csrf_token": request.state.csrf_token,
        "bootstrapped": services.has_admin(session),
        "error": error,
        "notice": NOTICES.get(request.query_params.get("aviso", "")),
    }
    return templates.TemplateResponse(request, template, {**base, **context}, status_code=status)


def _status(error: Exception) -> int:
    if isinstance(error, Conflict):
        return 409
    if isinstance(error, NotFound):
        return 404
    if isinstance(error, AccessDenied):
        return 403
    return 400


def mutate(
    session: Session,
    action: Callable[[], object],
    *,
    done: str,
    fail: Callable[[str, int], Response],
) -> Response:
    """Run one service call and commit; redirect on success, re-render on refusal.

    Messages come from the services and are constants: they never carry secrets.
    """
    try:
        services.ensure_bootstrapped(session)
        result = action()
        session.commit()
    except (DomainError, ValueError) as error:
        session.rollback()
        return fail(str(error), _status(error))
    except IntegrityError:
        session.rollback()
        return fail("conflito com o cadastro existente", 409)
    warned = isinstance(result, services.Change) and bool(result.warnings)
    return RedirectResponse(f"{done}?aviso=pastas" if warned else done, status_code=303)


def parse_int(value: str, label: str) -> int:
    try:
        return int(value.strip())
    except ValueError:
        raise ValueError(f"{label} deve ser um número inteiro") from None


def checked(value: str | None) -> bool:
    return value is not None
