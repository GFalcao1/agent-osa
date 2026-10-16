"""Panel application factory, loopback-only guard, request filtering and ``serve``.

The panel has no authentication of its own (V1): it is reachable only from this machine.
Three layers keep it that way, because a browser tab on the same machine can still talk to
127.0.0.1: the bind is refused for any host but ``127.0.0.1``; every request must come from
a loopback peer with an exact ``Host``; and every mutation must carry a same-origin
``Origin``/``Referer`` plus a double-submit CSRF token signed with a per-process secret
(a cookie planted by another ``127.0.0.1:<port>`` origin does not verify). Secrets are
never rendered. ADR 0005 records why there is no login.
"""

from __future__ import annotations

import ipaddress
import secrets
from collections.abc import Callable
from typing import Any, Final

from fastapi import Depends, FastAPI, Request
from fastapi.exceptions import RequestValidationError
from starlette.datastructures import Headers, MutableHeaders
from starlette.requests import cookie_parser
from starlette.responses import PlainTextResponse, Response
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from osa.admin.services import MountPredicate, default_is_mount_point
from osa.core.settings import Settings
from osa.panel.routes import departments, home, manual, model, owners, roots, users
from osa.panel.routes.common import CSRF_COOKIE, csrf_guard, csrf_token_is_valid, new_csrf_token
from osa.panel.styles import CSS
from osa.panel.theme import JS

DEFAULT_HOST: Final = "127.0.0.1"
DEFAULT_PORT: Final = 8765
_SAFE_METHODS: Final = frozenset({"GET", "HEAD"})

SECURITY_HEADERS: Final = {
    "content-security-policy": (
        "default-src 'self'; style-src 'self'; script-src 'self'; img-src 'self'; "
        "form-action 'self'; frame-ancestors 'none'; base-uri 'none'; object-src 'none'"
    ),
    "x-frame-options": "DENY",
    # Not no-referrer: with it browsers send `Origin: null` on same-origin form POSTs,
    # which the Origin check refuses (Fetch, "append a request Origin header").
    "referrer-policy": "same-origin",
    "x-content-type-options": "nosniff",
    "cache-control": "no-store",
}


def validate_bind_host(host: str) -> str:
    """Only the IPv4 loopback literal is accepted (spec: the panel binds 127.0.0.1).

    An exact string comparison on purpose: no parsing, so no alias (``localhost``,
    ``127.1``, ``::ffff:127.0.0.1``, padding) can slip through.
    """
    if host != DEFAULT_HOST:
        raise ValueError(f"o painel só sobe em {DEFAULT_HOST}: bind em outro endereço é recusado")
    return host


def validate_port(port: int) -> int:
    if isinstance(port, bool) or not 1024 <= port <= 65535:
        raise ValueError("porta do painel deve estar entre 1024 e 65535")
    return port


def _is_loopback(address: str | None) -> bool:
    if not address:
        return False
    try:
        return ipaddress.ip_address(address).is_loopback
    except ValueError:
        return False


class LocalOnlyMiddleware:
    """Pure ASGI middleware: peer and Host filter, Origin check, CSRF cookie, headers."""

    def __init__(self, app: ASGIApp, *, port: int, secret: bytes) -> None:
        self.app = app
        self.secret = secret
        self.hosts = frozenset({f"127.0.0.1:{port}", f"localhost:{port}"})
        self.origins = tuple(f"http://{host}" for host in sorted(self.hosts))

    def _origin_ok(self, headers: Headers) -> bool:
        origins = headers.getlist("origin")
        if len(origins) > 1:
            return False
        if origins:
            return origins[0].lower() in self.origins
        referers = headers.getlist("referer")
        if len(referers) != 1:
            return False
        referer = referers[0].lower()
        return any(referer == origin or referer.startswith(origin + "/") for origin in self.origins)

    def _allowed(self, scope: Scope, headers: Headers) -> bool:
        client = scope.get("client")
        if client is None or not _is_loopback(client[0]):
            return False
        hosts = headers.getlist("host")
        if len(hosts) != 1 or hosts[0].lower() not in self.hosts:
            return False
        return scope["method"] in _SAFE_METHODS or self._origin_ok(headers)

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] == "lifespan":
            await self.app(scope, receive, send)
            return
        if scope["type"] != "http":
            await send({"type": "websocket.close", "code": 1008})
            return
        headers = Headers(scope=scope)
        cookie = cookie_parser(headers.get("cookie", "")).get(CSRF_COOKIE)
        fresh = not csrf_token_is_valid(cookie, self.secret)
        token = new_csrf_token(self.secret) if fresh else str(cookie)
        scope.setdefault("state", {})["csrf_token"] = token

        async def guarded_send(message: Message) -> None:
            if message["type"] == "http.response.start":
                out = MutableHeaders(scope=message)
                for name, value in SECURITY_HEADERS.items():
                    out[name] = value
                if fresh:
                    out.append(
                        "set-cookie", f"{CSRF_COOKIE}={token}; HttpOnly; SameSite=Strict; Path=/"
                    )
            await send(message)

        if not self._allowed(scope, headers):
            await PlainTextResponse("forbidden", status_code=403)(scope, receive, guarded_send)
            return
        await self.app(scope, receive, guarded_send)


def _secured(response: Response) -> Response:
    for name, value in SECURITY_HEADERS.items():
        response.headers[name] = value
    return response


async def _invalid_request(request: Request, error: Exception) -> Response:
    # The default handler echoes each failed input; here nothing the client sent comes back.
    del request, error
    return _secured(PlainTextResponse("requisição inválida", status_code=422))


async def _internal_error(request: Request, error: Exception) -> Response:
    del request, error
    return _secured(PlainTextResponse("erro interno", status_code=500))


def create_app(
    *,
    session_factory: Callable[[], Any],
    settings: Settings,
    port: int = DEFAULT_PORT,
    is_mount_point: MountPredicate = default_is_mount_point,
) -> FastAPI:
    """Build the panel. ``session_factory`` opens one Session per request."""
    application = FastAPI(
        title="Agent O.S.A. — painel local",
        debug=False,
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
        dependencies=[Depends(csrf_guard)],
    )
    secret = secrets.token_bytes(32)  # per process: tokens die with the panel
    application.state.csrf_secret = secret
    application.state.session_factory = session_factory
    application.state.settings = settings
    application.state.is_mount_point = is_mount_point
    application.add_exception_handler(RequestValidationError, _invalid_request)
    application.add_exception_handler(Exception, _internal_error)

    @application.get("/panel.css", include_in_schema=False)
    def stylesheet() -> Response:
        return Response(CSS, media_type="text/css")

    @application.get("/theme.js", include_in_schema=False)
    def theme_script() -> Response:
        return Response(JS, media_type="text/javascript")

    for module in (home, roots, departments, owners, users, model, manual):
        application.include_router(module.router)
    application.add_middleware(LocalOnlyMiddleware, port=port, secret=secret)
    return application


def serve(
    host: str,
    port: int,
    *,
    settings: Settings,
    session_factory: Callable[[], Any] | None = None,
) -> None:
    """Validate the bind, then run uvicorn on it (no reload, one worker, no proxy headers)."""
    import uvicorn

    host = validate_bind_host(host)
    port = validate_port(port)
    if session_factory is None:
        from osa.db.session import get_sessionmaker

        session_factory = get_sessionmaker()
    application = create_app(session_factory=session_factory, settings=settings, port=port)
    uvicorn.run(
        application,
        host=host,
        port=port,
        reload=False,
        workers=1,
        proxy_headers=False,
        server_header=False,
    )
