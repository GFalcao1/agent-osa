"""Panel exposure rules that need no database: bind, Host, client, Origin, CSRF, headers."""

from __future__ import annotations

import json
import re
import shutil
import subprocess
from collections.abc import Callable
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

import httpx
import pytest
from fastapi.testclient import TestClient
from pydantic import SecretStr
from sqlalchemy.orm import Session
from typer.testing import CliRunner

from osa.cli import app as cli_app
from osa.core.settings import Settings
from osa.db.session import get_engine, get_sessionmaker
from osa.panel import app as panel

PORT = 8765
SENTINEL = "fictional-key-not-real"
LOOPBACK = ("127.0.0.1", 50000)


class DatabaseTouched(AssertionError):
    pass


def _forbidden_session_factory() -> Session:
    raise DatabaseTouched("the panel opened a database session")


class _InertSession:
    """Stands in for a Session on routes that fail validation before using it."""

    def close(self) -> None:
        return None


def make_client(
    *,
    client: tuple[str, int] = LOOPBACK,
    factory: Callable[[], Any] = _forbidden_session_factory,
    settings: Settings | None = None,
) -> TestClient:
    application = panel.create_app(
        session_factory=factory, settings=settings or Settings(), port=PORT
    )
    return TestClient(
        application,
        base_url=f"http://127.0.0.1:{PORT}",
        client=client,
        raise_server_exceptions=False,
    )


def csrf_token(client: TestClient) -> str:
    assert client.get("/panel.css").status_code == 200
    token = client.cookies.get("osa_csrf")
    assert token
    return token


# --- bind ---------------------------------------------------------------------------


def test_bind_accepts_only_the_ipv4_loopback_literal() -> None:
    assert panel.validate_bind_host("127.0.0.1") == "127.0.0.1"


@pytest.mark.parametrize(
    "host",
    [
        "0.0.0.0",
        "::",
        "::1",
        "",
        " ",
        "localhost",
        "127.0.0.2",
        "127.0.0.1 ",
        " 127.0.0.1",
        "127.0.0.1\n",
        "192.0.2.10",
        "10.0.0.5",
        "example.invalid",
        "::ffff:127.0.0.1",
    ],
)
def test_bind_refuses_everything_else(host: str) -> None:
    with pytest.raises(ValueError, match="127.0.0.1"):
        panel.validate_bind_host(host)


@pytest.mark.parametrize("port", [0, 80, 1023, 65536, -1])
def test_port_must_be_unprivileged(port: int) -> None:
    with pytest.raises(ValueError, match="porta"):
        panel.validate_port(port)


def test_serve_refuses_a_bad_host_before_touching_uvicorn(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def must_not_run(*args: object, **kwargs: object) -> None:
        raise AssertionError("uvicorn.run must not be called")

    monkeypatch.setattr("uvicorn.run", must_not_run)

    with pytest.raises(ValueError):
        panel.serve("0.0.0.0", PORT, settings=Settings())  # noqa: S104  # refused on purpose


def test_serve_binds_loopback_without_reload(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[tuple[object, dict[str, object]]] = []
    monkeypatch.setattr("uvicorn.run", lambda app, **kw: calls.append((app, kw)))

    panel.serve("127.0.0.1", PORT, settings=Settings(), session_factory=_forbidden_session_factory)

    [(_, kwargs)] = calls
    assert kwargs["host"] == "127.0.0.1" and kwargs["port"] == PORT
    assert kwargs.get("reload", False) is False
    assert kwargs.get("workers", 1) == 1
    # Only direct traffic: X-Forwarded-* must never rewrite the peer or the scheme.
    assert kwargs["proxy_headers"] is False
    assert kwargs["server_header"] is False


def test_cli_panel_refuses_non_loopback_host(monkeypatch: pytest.MonkeyPatch) -> None:
    def must_not_run(*args: object, **kwargs: object) -> None:
        raise AssertionError("uvicorn.run must not be called")

    monkeypatch.setattr("uvicorn.run", must_not_run)

    for host in ("0.0.0.0", "::", "localhost", "192.0.2.10"):  # noqa: S104
        result = CliRunner().invoke(cli_app, ["panel", "--host", host])
        assert result.exit_code != 0, host
        assert "127.0.0.1" in result.output


def test_cli_panel_starts_on_loopback_by_default(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OSA_DATABASE_URL", "postgresql://osa:fictional@db.example.invalid/osa_x")
    get_sessionmaker.cache_clear()
    get_engine.cache_clear()
    calls: list[dict[str, object]] = []
    monkeypatch.setattr("uvicorn.run", lambda app, **kw: calls.append(kw))
    try:
        result = CliRunner().invoke(cli_app, ["panel"])
    finally:
        get_sessionmaker.cache_clear()
        get_engine.cache_clear()

    assert result.exit_code == 0, result.output
    assert calls and calls[0]["host"] == "127.0.0.1" and calls[0]["port"] == PORT
    assert "sem autenticação" in result.output


def test_cli_panel_without_database_configuration_exits_one(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("uvicorn.run", lambda app, **kw: None)

    result = CliRunner().invoke(cli_app, ["panel"])

    assert result.exit_code == 1 and "OSA_DATABASE_URL" in result.output


# --- client and Host ----------------------------------------------------------------


@pytest.mark.parametrize("address", ["192.0.2.10", "10.1.2.3", "2001:db8::1", "testclient"])
def test_non_loopback_peers_are_refused(address: str) -> None:
    client = make_client(client=(address, 50000))

    assert client.get("/panel.css").status_code == 403
    assert client.post("/roots").status_code == 403


@pytest.mark.parametrize("host", ["127.0.0.1:8765", "localhost:8765", "LocalHost:8765"])
def test_allowed_host_headers(host: str) -> None:
    assert make_client().get("/panel.css", headers={"Host": host}).status_code == 200


@pytest.mark.parametrize(
    "host",
    [
        "evil.example",
        "evil.example:8765",
        "127.0.0.1",
        "localhost",
        "127.0.0.1:9999",
        "127.0.0.1:8765.evil.example",
        "127.0.0.1:8765@evil.example",
        "evil.example:8765, 127.0.0.1:8765",
        "0.0.0.0:8765",
        "[::1]:8765",
        "",
    ],
)
def test_forged_or_rebound_host_is_refused_even_for_reads(host: str) -> None:
    response = make_client().get("/panel.css", headers={"Host": host})

    assert response.status_code == 403
    assert "evil.example" not in response.text


# --- Origin / Referer ---------------------------------------------------------------


def _post(client: TestClient, headers: dict[str, str], token: str | None = None) -> httpx.Response:
    data = {"csrf_token": token} if token else {}
    return client.post("/roots", data=data, headers=headers)


@pytest.mark.parametrize(
    "headers",
    [
        {},
        {"Origin": "http://evil.example"},
        {"Origin": "null"},
        {"Origin": "http://127.0.0.1:9999"},
        {"Origin": "https://127.0.0.1:8765"},
        {"Origin": "http://127.0.0.1:8765.evil.example"},
        {"Origin": "http://evil.example", "Referer": "http://127.0.0.1:8765/"},
        {"Referer": "http://evil.example/roots"},
        {"Referer": "http://127.0.0.1:8765.evil.example/"},
        {"Referer": "http://127.0.0.1:87650/"},
        {"Referer": "http://127.0.0.1:8765@evil.example/"},
        {"Referer": "not a url"},
    ],
)
def test_mutations_with_missing_or_cross_origin_are_refused_before_the_body_or_db(
    headers: dict[str, str],
) -> None:
    client = make_client()
    token = csrf_token(client)

    response = _post(client, headers, token)

    assert response.status_code == 403  # DatabaseTouched would be a 500


@pytest.mark.parametrize(
    "headers",
    [
        {"Origin": "http://127.0.0.1:8765"},
        {"Origin": "http://localhost:8765"},
        {"Referer": "http://127.0.0.1:8765/roots"},
        {"Referer": "http://localhost:8765/"},
        {"Origin": "http://127.0.0.1:8765", "Referer": "http://evil.example/"},
    ],
)
def test_same_origin_mutations_with_csrf_reach_the_database_layer(
    headers: dict[str, str],
) -> None:
    touched: list[bool] = []

    def factory() -> Session:
        touched.append(True)
        raise DatabaseTouched("reached")

    client = make_client(factory=factory)
    token = csrf_token(client)

    response = _post(client, headers, token)

    assert response.status_code == 500 and touched  # past Host, Origin and CSRF


# --- what a real browser sends (Fetch: "append a request `Origin` header") ----------


def browser_form_post_origin(referrer_policy: str, document_url: str, target_url: str) -> str:
    """The ``Origin`` a browser puts on a ``<form method=post>`` (non-CORS) submission.

    Models the Fetch standard, step "if request's method is neither GET nor HEAD": the
    document's referrer policy can replace the serialized origin with ``null`` even for a
    same-origin request. ``TestClient`` sends whatever we pass, so without this model the
    suite would not notice a policy that breaks every real form.
    """
    source, target = urlsplit(document_url), urlsplit(target_url)
    origin = f"{source.scheme}://{source.netloc}"
    same_origin = (source.scheme, source.netloc) == (target.scheme, target.netloc)
    policy = referrer_policy.split(",")[-1].strip().lower()
    if policy == "no-referrer":
        return "null"
    if policy in {"no-referrer-when-downgrade", "strict-origin", "strict-origin-when-cross-origin"}:
        return "null" if source.scheme == "https" and target.scheme != "https" else origin
    if policy == "same-origin":
        return origin if same_origin else "null"
    return origin


def test_the_browser_model_reproduces_the_no_referrer_trap() -> None:
    page = f"http://127.0.0.1:{PORT}/roots"

    assert browser_form_post_origin("no-referrer", page, page) == "null"
    assert browser_form_post_origin("same-origin", page, page) == f"http://127.0.0.1:{PORT}"
    assert browser_form_post_origin("same-origin", "http://evil.example/", page) == "null"


def test_referrer_policy_is_same_origin_never_no_referrer() -> None:
    # no-referrer makes browsers send `Origin: null` on same-origin form POSTs, which the
    # Origin check refuses: every real form would get 403 (TASK-103 review, blocker 1).
    policy = make_client().get("/panel.css").headers["referrer-policy"]

    assert policy == "same-origin"


@pytest.mark.parametrize("host", ["127.0.0.1", "localhost"])
def test_a_same_origin_form_post_from_a_browser_passes_the_origin_check(host: str) -> None:
    touched: list[bool] = []

    def factory() -> Session:
        touched.append(True)
        raise DatabaseTouched("reached")

    client = make_client(factory=factory)
    page_url = f"http://{host}:{PORT}/roots"
    page = client.get("/panel.css", headers={"Host": f"{host}:{PORT}"})
    token = client.cookies.get("osa_csrf")
    assert token
    origin = browser_form_post_origin(page.headers["referrer-policy"], page_url, page_url)

    response = _post(client, {"Host": f"{host}:{PORT}", "Origin": origin}, token)

    assert origin != "null"
    assert response.status_code == 500 and touched  # past Host, Origin and CSRF


def test_a_form_post_from_another_site_is_refused_as_the_browser_sends_it() -> None:
    client = make_client()
    token = csrf_token(client)
    policy = client.get("/panel.css").headers["referrer-policy"]
    origin = browser_form_post_origin(policy, "http://evil.example/", f"http://127.0.0.1:{PORT}/")

    assert _post(client, {"Origin": origin}, token).status_code == 403


# --- CSRF ---------------------------------------------------------------------------

SAME_ORIGIN = {"Origin": f"http://127.0.0.1:{PORT}"}


def test_csrf_cookie_is_random_http_only_strict() -> None:
    first, second = make_client(), make_client()

    token_a, token_b = csrf_token(first), csrf_token(second)

    assert token_a != token_b and re.fullmatch(r"[A-Za-z0-9_-]{32,}\.[A-Za-z0-9_-]{43}", token_a)
    header = first.get("/panel.css").headers  # cookie already set: not sent again
    assert "set-cookie" not in header
    fresh = make_client().get("/panel.css").headers["set-cookie"].lower()
    assert "httponly" in fresh and "samesite=strict" in fresh and "path=/" in fresh


def test_post_without_token_or_with_wrong_token_is_refused() -> None:
    client = make_client()
    token = csrf_token(client)

    assert _post(client, SAME_ORIGIN, None).status_code == 403
    assert _post(client, SAME_ORIGIN, "x" * len(token)).status_code == 403
    assert _post(client, SAME_ORIGIN, token[:-1]).status_code == 403
    assert _post(client, SAME_ORIGIN, "çã").status_code == 403


def test_post_with_token_but_without_the_cookie_is_refused() -> None:
    client = make_client()
    token = csrf_token(client)
    client.cookies.clear()

    assert _post(client, SAME_ORIGIN, token).status_code == 403


def test_an_unsigned_cookie_planted_by_another_local_origin_is_refused() -> None:
    # Cookies ignore the port: http://127.0.0.1:<other> can set osa_csrf for this host.
    planted = "A" * 43
    client = make_client()
    client.cookies.set("osa_csrf", planted)

    response = _post(client, SAME_ORIGIN, planted)

    assert response.status_code == 403
    assert f"osa_csrf={planted}" not in response.headers.get("set-cookie", "")


def test_a_token_signed_by_another_panel_process_is_refused() -> None:
    other = make_client()
    foreign = csrf_token(other)
    client = make_client()
    client.cookies.set("osa_csrf", foreign)

    replaced = client.get("/panel.css").headers.get("set-cookie", "")
    client.cookies.set("osa_csrf", foreign)

    assert "osa_csrf=" in replaced and f"osa_csrf={foreign}" not in replaced
    assert _post(client, SAME_ORIGIN, foreign).status_code == 403


def test_a_signed_token_with_a_tampered_nonce_is_refused() -> None:
    client = make_client()
    token = csrf_token(client)
    nonce, signature = token.split(".")
    tampered = ("B" if nonce[0] != "B" else "C") + nonce[1:] + "." + signature
    client.cookies.set("osa_csrf", tampered)

    assert _post(client, SAME_ORIGIN, tampered).status_code == 403


def test_a_malformed_cookie_is_replaced_not_trusted() -> None:
    response = make_client().get("/panel.css", headers={"Cookie": "osa_csrf=short"})

    assert "osa_csrf=" in response.headers["set-cookie"]
    assert "osa_csrf=short" not in response.headers["set-cookie"]


# --- headers, docs, errors ----------------------------------------------------------


def test_security_headers_are_on_every_response_including_refusals() -> None:
    client = make_client()
    ok = client.get("/panel.css")
    refused = client.get("/panel.css", headers={"Host": "evil.example"})
    missing = client.get("/nao-existe")

    for response in (ok, refused, missing):
        headers = response.headers
        assert headers["content-security-policy"].startswith("default-src 'self'")
        assert "frame-ancestors 'none'" in headers["content-security-policy"]
        assert "form-action 'self'" in headers["content-security-policy"]
        assert headers["x-frame-options"] == "DENY"
        assert headers["referrer-policy"] == "same-origin"
        assert headers["x-content-type-options"] == "nosniff"
        assert headers["cache-control"] == "no-store"


def test_no_external_assets_are_referenced() -> None:
    css = make_client().get("/panel.css")

    assert css.headers["content-type"].startswith("text/css")
    assert "http://" not in css.text and "https://" not in css.text and "@import" not in css.text


EXPECTED_CSP = (
    "default-src 'self'; style-src 'self'; script-src 'self'; img-src 'self'; "
    "form-action 'self'; frame-ancestors 'none'; base-uri 'none'; object-src 'none'"
)


def test_theme_script_is_served_as_javascript_with_the_panel_headers() -> None:
    response = make_client().get("/theme.js")

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/javascript")
    assert response.headers["x-content-type-options"] == "nosniff"
    assert response.headers["cache-control"] == "no-store"
    assert "data-theme" in response.text


@pytest.mark.parametrize("host", ["evil.example", "evil.example:8765", "127.0.0.1:9999", ""])
def test_theme_script_is_subject_to_the_host_filter(host: str) -> None:
    response = make_client().get("/theme.js", headers={"Host": host})

    assert response.status_code == 403
    assert response.text == "forbidden"
    assert "data-theme" not in response.text


def test_theme_script_is_refused_to_a_non_loopback_peer() -> None:
    client = make_client(client=("192.0.2.10", 50000))

    assert client.get("/theme.js").status_code == 403


def test_csp_only_allows_the_panels_own_script_and_nothing_inline() -> None:
    # The sole change to the policy for the theme switch: script-src 'none' -> 'self'.
    # No 'unsafe-inline', no 'unsafe-eval', no scheme or host source anywhere.
    client = make_client()
    for path in ("/panel.css", "/theme.js", "/nao-existe"):
        policy = client.get(path).headers["content-security-policy"]
        assert policy == EXPECTED_CSP, path
        assert "unsafe" not in policy and "http" not in policy and "*" not in policy


def test_theme_script_has_no_network_eval_or_external_reference() -> None:
    script = make_client().get("/theme.js").text

    for forbidden in ("http://", "https://", "fetch(", "XMLHttpRequest", "eval(", "innerHTML"):
        assert forbidden not in script


# The theme script runs for real under Node with a fake DOM: storage that throws on access
# must not break start-up or the switch, and a system theme change must not undo a choice.
_THEME_HARNESS = r"""
const fs = require("fs");
const vm = require("vm");
const [script, mode] = [fs.readFileSync(process.argv[2], "utf8"), process.argv[3]];
const attrs = {};
const classes = new Set();
const pressed = [];
const listeners = {};
const mediaListeners = [];
let systemDark = false;
const button = {
  setAttribute: (name, value) => { if (name === "aria-pressed") pressed.push(value); },
  closest: (selector) => (selector === ".theme-switch" ? button : null),
};
const store = {};
const boom = () => { throw new Error("storage blocked"); };
let localStorage;
if (mode === "getter") localStorage = undefined;  // the getter itself throws (below)
else if (mode === "methods") localStorage = { getItem: boom, setItem: boom };
else localStorage = {
  getItem: (k) => (k in store ? store[k] : null),
  setItem: (k, v) => { store[k] = String(v); },
};
const window = {
  matchMedia: () => ({
    get matches() { return systemDark; },
    addEventListener: (type, fn) => mediaListeners.push(fn),
  }),
};
if (mode === "getter") Object.defineProperty(window, "localStorage", { get: boom });
else window.localStorage = localStorage;
const document = {
  documentElement: {
    classList: { add: (c) => classes.add(c) },
    getAttribute: (n) => (n in attrs ? attrs[n] : null),
    setAttribute: (n, v) => { attrs[n] = String(v); },
    removeAttribute: (n) => { delete attrs[n]; },
  },
  querySelectorAll: () => [button],
  addEventListener: (type, fn) => { (listeners[type] = listeners[type] || []).push(fn); },
};
const out = { errors: [] };
try {
  vm.runInNewContext(script, { window, document });
  (listeners.DOMContentLoaded || []).forEach((fn) => fn());
  out.start = attrs["data-theme"] || null;
  out.js = classes.has("js");
  (listeners.click || []).forEach((fn) => fn({ target: button }));
  out.afterClick = attrs["data-theme"] || null;
  systemDark = true;
  mediaListeners.forEach((fn) => fn());
  out.afterSystemChange = attrs["data-theme"] || null;
  out.pressed = pressed;
  out.stored = store["osa-theme"] || null;
} catch (error) {
  out.errors.push(String(error));
}
process.stdout.write(JSON.stringify(out));
"""


def _run_theme(tmp_path: Path, mode: str) -> dict[str, Any]:
    node = shutil.which("node")
    if node is None:
        pytest.skip("Node.js not installed: the theme script cannot be executed here")
    harness = tmp_path / "harness.js"
    harness.write_text(_THEME_HARNESS, encoding="utf-8")
    script = tmp_path / "theme.js"
    script.write_text(make_client().get("/theme.js").text, encoding="utf-8")
    result = subprocess.run(  # noqa: S603  # fixed argv, local files only
        [node, str(harness), str(script), mode],
        capture_output=True,
        text=True,
        timeout=30,
        check=True,
    )
    data: dict[str, Any] = json.loads(result.stdout)
    return data


@pytest.mark.parametrize("mode", ["getter", "methods"])
def test_theme_script_survives_blocked_storage_and_keeps_the_choice(
    tmp_path: Path, mode: str
) -> None:
    run = _run_theme(tmp_path, mode)

    assert run["errors"] == []
    assert run["js"] is True and run["start"] is None  # nothing saved: follows the system
    assert run["afterClick"] == "dark" and run["pressed"][-1] == "true"
    # The system then changes its theme: with no storage, the choice made on the page
    # (kept in memory) must still win instead of being wiped by the change event.
    assert run["afterSystemChange"] == "dark"


def test_theme_script_with_storage_saves_the_choice(tmp_path: Path) -> None:
    run = _run_theme(tmp_path, "ok")

    assert run["errors"] == [] and run["stored"] == "dark"
    assert run["afterSystemChange"] == "dark"


def test_both_themes_are_defined_and_the_dark_values_match_everywhere() -> None:
    css = make_client().get("/panel.css").text

    def block(opening: str) -> str:
        start = css.index(opening) + len(opening)
        return css[start : css.index("}", start)]

    pinned = block(':root[data-theme="dark"] {')
    by_media = block('@media (prefers-color-scheme: dark) {\n  :root:not([data-theme="light"]) {')
    assert "--paper:" in pinned and "color-scheme: dark" in pinned
    assert pinned.split() == by_media.split()  # same values before/without script
    assert "@media (prefers-reduced-motion: reduce)" in css
    assert "html.js .theme-switch" in css  # the switch only shows when the script runs


@pytest.mark.parametrize("path", ["/docs", "/redoc", "/openapi.json"])
def test_api_docs_are_not_served(path: str) -> None:
    assert make_client().get(path).status_code == 404


def test_validation_errors_do_not_echo_the_offending_value() -> None:
    client = make_client(factory=_InertSession)
    token = csrf_token(client)

    response = client.post(
        f"/roots/{SENTINEL}/ativo",
        data={"csrf_token": token, "ativo": SENTINEL},
        headers=SAME_ORIGIN,
    )

    assert response.status_code == 422
    assert SENTINEL not in response.text


def test_the_external_key_is_not_in_static_error_or_docs_responses() -> None:
    # Pages that render data need the database: covered in tests/integration/test_panel.py.
    client = make_client(settings=Settings(external_llm_api_key=SecretStr(SENTINEL)))

    for path in ("/panel.css", "/nao-existe", "/docs"):
        response = client.get(path)
        assert SENTINEL not in response.text
        assert SENTINEL not in str(response.headers)
