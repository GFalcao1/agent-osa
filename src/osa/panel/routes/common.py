"""Shared helpers of the panel routes: session, CSRF guard, rendering and mutations."""

import base64
import hashlib
import hmac
import re
import secrets
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Final
from urllib.parse import urlencode

from fastapi import HTTPException, Request
from fastapi.responses import RedirectResponse, Response
from fastapi.templating import Jinja2Templates
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from osa.admin import services
from osa.core.settings import Settings
from osa.db.models import Department, Owner, OwnerAlias, Root, User, WatchedFolder
from osa.domain.errors import AccessDenied, Conflict, DomainError, NotFound
from osa.panel.assets import font_path
from osa.panel.icons import icon
from osa.panel.styles import PRELOAD_FONTS

ACTOR: Final = "panel:local"
TEMPLATE_DIR: Final = Path(__file__).resolve().parent.parent / "templates"
templates = Jinja2Templates(directory=str(TEMPLATE_DIR))
templates.env.globals["icon"] = icon  # the only way templates emit trusted SVG markup
templates.env.globals["font_url"] = font_path  # fixed names only; the file is hashed
templates.env.globals["preload_fonts"] = PRELOAD_FONTS  # logical names from the stylesheet

# Fixed texts for redirect query codes: nothing user-supplied travels in a URL.
NOTICES: Final = {
    "pastas": "Pastas já existentes no servidor não foram renomeadas; ajuste-as à mão se precisar.",
}


@dataclass(frozen=True)
class Flash:
    """One success sentence: ``before`` + the item's name (from the database) + ``after``.

    The redirect after a save carries a code from ``FLASHES`` and, for items, the row id
    (``?ok=dono-cadastrado&id=7``). Nothing typed by the user travels in the URL: the code
    must be a key of this table, the id must be digits, and the name is read from the database
    by that id and rendered escaped. A made-up link can at most show one of these fixed
    sentences for an item that exists.
    """

    before: str
    after: str = ""
    model: type | None = None
    attr: str = ""


FLASHES: Final = {
    "raiz-cadastrada": Flash("Raiz ", " cadastrada.", Root, "caminho"),
    "raiz-ativada": Flash("Raiz ", " ativada.", Root, "caminho"),
    "raiz-desativada": Flash("Raiz ", " desativada.", Root, "caminho"),
    "pasta-adicionada": Flash("Pasta monitorada ", " adicionada.", WatchedFolder, "caminho"),
    "pasta-ativada": Flash("Pasta monitorada ", " ativada.", WatchedFolder, "caminho"),
    "pasta-desativada": Flash("Pasta monitorada ", " desativada.", WatchedFolder, "caminho"),
    "departamento-adicionado": Flash("Departamento ", " adicionado.", Department, "nome"),
    "departamento-salvo": Flash("Departamento ", " salvo.", Department, "nome"),
    "departamento-subiu": Flash("Departamento ", " subiu uma posição.", Department, "nome"),
    "departamento-desceu": Flash("Departamento ", " desceu uma posição.", Department, "nome"),
    "departamento-ativado": Flash("Departamento ", " ativado.", Department, "nome"),
    "departamento-desativado": Flash("Departamento ", " desativado.", Department, "nome"),
    "dono-cadastrado": Flash("Dono ", " cadastrado.", Owner, "nome_fantasia"),
    "dono-salvo": Flash("Dono ", " salvo.", Owner, "nome_fantasia"),
    "dono-ativado": Flash("Dono ", " ativado.", Owner, "nome_fantasia"),
    "dono-desativado": Flash("Dono ", " desativado.", Owner, "nome_fantasia"),
    "alias-adicionado": Flash("Alias ", " adicionado.", OwnerAlias, "alias"),
    "alias-removido": Flash("Alias removido do dono ", ".", Owner, "nome_fantasia"),
    "usuario-cadastrado": Flash("Usuário ", " cadastrado.", User, "nome"),
    "usuario-salvo": Flash("Usuário ", " salvo.", User, "nome"),
    "usuario-ativado": Flash("Usuário ", " ativado.", User, "nome"),
    "usuario-desativado": Flash("Usuário ", " desativado.", User, "nome"),
    "modelo-salvo": Flash("Modelo salvo."),
}
_FLASH_CODE: Final = re.compile(r"[a-z]{1,16}-[a-z]{1,16}")
_FLASH_ID: Final = re.compile(r"[0-9]{1,9}")

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
    confirm: dict[str, Any] | None = None,
) -> Response:
    base = {
        "csrf_token": request.state.csrf_token,
        "bootstrapped": services.has_admin(session),
        "error": error,
        "notice": NOTICES.get(request.query_params.get("aviso", "")),
        "success": None if error or confirm else _flash(request, session),
        "confirm": confirm,
    }
    return templates.TemplateResponse(request, template, {**base, **context}, status_code=status)


def _flash(request: Request, session: Session) -> dict[str, str] | None:
    """The success banner for ``?ok=<code>[&id=<n>]``, built only from server-side state."""
    code = request.query_params.get("ok", "")
    flash = FLASHES.get(code) if _FLASH_CODE.fullmatch(code) else None
    if flash is None:
        return None
    name = ""
    if flash.model is not None:
        ref = request.query_params.get("id", "")
        if not _FLASH_ID.fullmatch(ref):
            return None
        row = session.get(flash.model, int(ref))
        if row is None:
            return None
        name = str(getattr(row, flash.attr))
    return {"before": flash.before, "name": name, "after": flash.after}


def confirmation(
    *,
    before: str,
    name: str,
    after: str,
    detail: str,
    action: str,
    fields: dict[str, str],
    button: str,
    glyph: str,
    cancel: str,
) -> dict[str, Any]:
    """Context of the "are you sure?" banner. Everything is a constant or a database value."""
    return {
        "before": before, "name": name, "after": after, "detail": detail, "action": action,
        "fields": fields, "button": button, "icon": glyph, "cancel": cancel,
    }  # fmt: skip


def must_confirm(session: Session, confirmed: str, *, destructive: bool) -> bool:
    """True when a destructive action arrives without ``confirmar=1`` and could run at all.

    Without an administrator the mutation itself refuses (409), so no question is asked.
    """
    return destructive and confirmed != "1" and services.has_admin(session)


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
    saved: str,
    ref: int | None = None,
) -> Response:
    """Run one service call and commit; redirect on success, re-render on refusal.

    ``saved`` is the key of the success sentence in ``FLASHES``; its item is the entity the
    service returned, or ``ref`` when the service returns nothing. Messages come from the
    services and are constants: they never carry secrets.
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
    entity = result.entity if isinstance(result, services.Change) else result
    row_id = ref if ref is not None else getattr(entity, "id", None)
    query = {"ok": saved}
    if FLASHES[saved].model is not None and row_id is not None:
        query["id"] = str(row_id)
    if isinstance(result, services.Change) and result.warnings:
        query["aviso"] = "pastas"
    return RedirectResponse(f"{done}?{urlencode(query)}", status_code=303)


def parse_int(value: str, label: str) -> int:
    try:
        return int(value.strip())
    except ValueError:
        raise ValueError(f"{label} deve ser um número inteiro") from None


def parse_telegram_id(value: str) -> int:
    """The Telegram ID field: digits only, with a hint on where the number comes from."""
    try:
        return int(value.strip())
    except ValueError:
        raise ValueError(
            "Telegram ID deve ser só números. Não é o telefone nem o @nome: peça o número "
            "à própria pessoa."
        ) from None


def checked(value: str | None) -> bool:
    return value is not None
