"""The manual and the help blocks against a real migrated PostgreSQL.

Covers what needs the database: the manual opens with an empty registry and with no
administrator, every other page links to it and carries a help block whose link resolves,
and every refusal the panel really shows is explained in the manual with the same words.
"""

from __future__ import annotations

import ast
import html
import inspect
import re
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import Engine, create_engine, text
from sqlalchemy.engine import URL
from sqlalchemy.orm import Session, sessionmaker

from osa.admin import services
from osa.core.settings import Settings
from osa.domain import owner as owner_rules
from osa.panel import app as panel
from osa.panel import manual

PORT = 8765
ORIGIN = f"http://127.0.0.1:{PORT}"
CNPJ_FICTICIO = "11222333000181"  # check digits valid, belongs to nobody


@dataclass
class Web:
    client: TestClient
    engine: Engine

    def get(self, path: str, status: int = 200) -> str:
        response = self.client.get(path)
        assert response.status_code == status, (path, response.status_code)
        return response.text

    def post(self, path: str, status: int | None = None, **data: str) -> tuple[int, str]:
        self.client.get("/panel.css")  # makes sure the CSRF cookie exists
        response = self.client.post(
            path,
            data={**data, "csrf_token": self.client.cookies.get("osa_csrf") or ""},
            headers={"Origin": ORIGIN},
            follow_redirects=False,
        )
        if status is not None:
            assert response.status_code == status, (path, response.status_code, response.text[:200])
        return response.status_code, html.unescape(response.text)

    def db(self) -> Session:
        return Session(self.engine)


def _build(url: URL, *, with_key: bool) -> Iterator[Web]:
    from pydantic import SecretStr

    engine = create_engine(url)
    settings = Settings(external_llm_api_key=SecretStr("fictional-key")) if with_key else Settings()
    application = panel.create_app(
        session_factory=sessionmaker(engine, expire_on_commit=False),
        settings=settings,
        port=PORT,
        is_mount_point=lambda _path: True,
    )
    client = TestClient(application, base_url=ORIGIN, client=("127.0.0.1", 50000))
    try:
        yield Web(client, engine)
    finally:
        client.close()
        engine.dispose()


@pytest.fixture
def web(migrated_database: URL) -> Iterator[Web]:
    yield from _build(migrated_database, with_key=True)


@pytest.fixture
def web_without_key(migrated_database: URL) -> Iterator[Web]:
    yield from _build(migrated_database, with_key=False)


def bootstrap(web: Web) -> None:
    with web.db() as session:
        services.bootstrap_admin(
            session, telegram_id=1001, nome="Admin Ficticio", actor="cli:bootstrap"
        )
        session.commit()


def manual_ids(web: Web) -> set[str]:
    return set(re.findall(r'\sid="([^"]+)"', web.get("/manual")))


# --- the manual is always reachable --------------------------------------------------


def test_the_manual_opens_with_an_empty_registry(web: Web) -> None:
    bootstrap(web)

    page = web.get("/manual")

    assert "<h1>Manual de uso</h1>" in page
    assert 'class="alert alert-blocked"' not in page


def test_the_manual_opens_when_there_is_no_administrator(web: Web) -> None:
    page = web.get("/manual")

    assert "<h1>Manual de uso</h1>" in page
    assert 'class="alert alert-blocked"' not in page  # the manual never needs the registry
    assert 'aria-current="page"' in re.search(r'<a href="/manual"[^>]*>', page).group(0)  # type: ignore[union-attr]


def test_the_manual_does_not_need_the_database_at_all() -> None:
    def broken() -> Session:
        raise RuntimeError("database is down")

    application = panel.create_app(session_factory=broken, settings=Settings(), port=PORT)
    client = TestClient(
        application,
        base_url=ORIGIN,
        client=("127.0.0.1", 50000),
        raise_server_exceptions=False,
    )

    assert client.get("/manual").status_code == 200
    assert client.get("/roots").status_code == 500  # the other pages do need it


# --- every page: nav link and help block ---------------------------------------------


def _all_paths(web: Web) -> list[str]:
    with web.db() as session:
        owner = services.create_owner(session, nome_fantasia="Alfa Ficticia", actor="test")
        session.commit()
        owner_id = owner.id
    return ["/", "/roots", "/departments", "/owners", f"/owners/{owner_id}", "/users", "/model"]


def _help_block(page: str) -> str:
    match = re.search(r'<details class="help-block">.*?</details>', page, re.S)
    assert match, "page has no help block"
    return match.group(0)


@pytest.mark.parametrize("with_admin", [True, False], ids=["administrator", "no-administrator"])
def test_every_page_links_to_the_manual_and_has_a_help_block(web: Web, with_admin: bool) -> None:
    if with_admin:
        bootstrap(web)
    ids = manual_ids(web)

    for path in _all_paths(web):
        page = web.get(path)

        nav = re.search(r'<nav class="nav".*?</nav>', page, re.S)
        assert nav and re.search(r'<a href="/manual"[^>]*><svg', nav.group(0)), path

        block = _help_block(page)
        assert "Como usar esta página" in block, path
        links = re.findall(r'href="/manual#([^"]+)"', block)
        assert len(links) >= 1, path
        assert all(link in ids for link in links), (path, links)
        sentences = re.sub(r"<[^>]+>", " ", block.split('<div class="help-body">', 1)[1])
        first_paragraph = re.search(r"<p>(.*?)</p>", block, re.S)
        assert first_paragraph, path
        count = len(re.findall(r"[.!?](?:\s|$)", re.sub(r"<[^>]+>", "", first_paragraph.group(1))))
        assert 2 <= count <= 4, (path, count)
        assert sentences.strip(), path


def test_each_page_points_at_its_own_chapter(web: Web) -> None:
    bootstrap(web)
    expected = {
        "/": "comece",
        "/roots": "raizes",
        "/departments": "template",
        "/owners": "donos",
        "/users": "usuarios",
        "/model": "modelo",
    }
    for path, chapter in expected.items():
        assert f'href="/manual#{chapter}"' in _help_block(web.get(path)), path
    owner_path = next(p for p in _all_paths(web) if p.startswith("/owners/"))
    assert 'href="/manual#donos"' in _help_block(web.get(owner_path))


def test_the_blocked_state_adds_a_hint_to_every_help_block(web: Web) -> None:
    for path in _all_paths(web):
        block = _help_block(web.get(path))
        assert 'href="/manual#bloqueado"' in block, path


def test_the_home_page_has_a_manual_card(web: Web) -> None:
    bootstrap(web)

    page = web.get("/")

    assert re.search(r'<a class="card[^"]*" href="/manual"', page)
    assert "Manual de uso" in page


@pytest.mark.parametrize("with_admin", [True, False], ids=["administrator", "no-administrator"])
def test_every_internal_link_of_every_page_resolves(web: Web, with_admin: bool) -> None:
    if with_admin:
        bootstrap(web)
    ids = manual_ids(web)
    known = {"/", "/roots", "/departments", "/owners", "/users", "/model", "/manual"}

    for path in [*_all_paths(web), "/manual"]:
        page = web.get(path)
        own_ids = set(re.findall(r'\sid="([^"]+)"', page))
        for target in re.findall(r'<a [^>]*href="([^"#][^"]*|#[^"]*)"', page):
            if target.startswith("#"):
                assert target[1:] in own_ids, (path, target)
                continue
            page_path, _, fragment = target.partition("#")
            assert page_path in known or re.fullmatch(r"/owners/\d+", page_path), (path, target)
            if page_path == "/manual" and fragment:
                assert fragment in ids, (path, target)


def test_no_page_gets_an_inline_script_style_or_handler_from_the_help_blocks(web: Web) -> None:
    bootstrap(web)
    for path in [*_all_paths(web), "/manual"]:
        page = web.get(path)

        assert re.findall(r"<script\b[^>]*>", page) == ['<script src="/theme.js">'], path
        assert "<style" not in page and " style=" not in page, path
        assert not re.search(r"\son\w+\s*=", page), path


# --- the manual explains what the panel really answers ---------------------------------


def _quoted() -> set[str]:
    return {problem.message for problem in manual.all_problems()}


_STATUS_OF = {"Conflict": 409, "NotFound": 404, "AccessDenied": 403}  # routes.common._status


def _expected_statuses() -> dict[str, int]:
    """Message -> the status the panel must answer, from the exception the code raises."""
    found: dict[str, int] = {}
    for module in (services, owner_rules):
        tree = ast.parse(Path(inspect.getfile(module)).read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Raise) and isinstance(node.exc, ast.Call) and node.exc.args:
                first, kind = node.exc.args[0], getattr(node.exc.func, "id", "")
                if isinstance(first, ast.Constant) and isinstance(first.value, str):
                    found[first.value] = _STATUS_OF.get(kind, 400)
    return found


EXPECTED_STATUS = _expected_statuses()


def refused(web: Web, path: str, message: str, status: int | None = None, **data: str) -> None:
    """``status`` only for templated texts (``_text``, ``_fetch``) that the AST map cannot see."""
    got, body = web.post(path, **data)
    expected = status if status is not None else EXPECTED_STATUS.get(message, 400)
    assert got == expected, (path, got, expected, message)
    assert message in body, (path, message)
    assert message in _quoted(), f"the manual does not explain: {message}"


def test_every_root_refusal_shown_by_the_panel_is_explained(web: Web, tmp_path: Path) -> None:
    bootstrap(web)
    root = tmp_path / "raiz"
    (root / "entrada").mkdir(parents=True)
    (root / "arquivo.txt").write_text("x")
    (tmp_path / "atalho").symlink_to(root)
    (root / "link").symlink_to(root / "entrada")

    refused(web, "/roots", "raiz deve ser um caminho absoluto", caminho="relativo/raiz")
    refused(web, "/roots", "raiz não existe", caminho=str(tmp_path / "nao-existe"))
    refused(web, "/roots", "raiz deve ser um diretório", caminho=str(root / "arquivo.txt"))
    refused(
        web,
        "/roots",
        "raiz não pode conter link simbólico ou '..'",
        caminho=str(tmp_path / "atalho"),
    )
    web.post("/roots", status=303, caminho=str(root), descricao="Raiz de teste")
    refused(web, "/roots", "raiz já cadastrada", caminho=str(root))
    refused(
        web, "/roots", "descrição inválido", status=400, caminho=str(tmp_path), descricao="a" * 501
    )
    refused(web, "/roots/99999/ativo", "raiz não encontrado", status=404, ativo="0")
    refused(web, "/roots", "raiz aninhada em outra raiz cadastrada", caminho=str(root / "entrada"))
    with web.db() as session:
        root_id = session.scalar(text("select id from roots"))
    folders = f"/roots/{root_id}/pastas"
    refused(web, folders, "pasta monitorada deve ser um caminho relativo simples", caminho="../x")
    refused(web, folders, "pasta monitorada não existe", caminho="nova")
    refused(web, folders, "pasta monitorada deve ser um diretório", caminho="arquivo.txt")
    refused(web, folders, "pasta monitorada não pode conter link simbólico", caminho="link")
    web.post(folders, status=303, caminho="entrada")
    refused(web, folders, "pasta monitorada já cadastrada nesta raiz", caminho="Entrada")


def test_every_department_refusal_and_notice_is_explained(web: Web) -> None:
    bootstrap(web)
    web.post("/departments", status=303, nome="Fiscal")

    refused(web, "/departments", "nome é obrigatório", nome=" ")
    refused(web, "/departments", "nome inválido", status=400, nome="a" * 201)
    refused(web, "/departments/99999/ativo", "departamento não encontrado", status=404, ativo="0")
    refused(web, "/departments", "nome de pasta não pode ser vazio", nome="///")
    refused(web, "/departments", "nome de pasta contém caractere proibido", nome="X", pasta="a:b")
    refused(web, "/departments", "nome de pasta é reservado", nome="X", pasta="Con")
    refused(
        web,
        "/departments",
        "nome de pasta não pode começar ou terminar com ponto ou espaço",
        nome="X",
        pasta=".oculta",
    )
    refused(
        web,
        "/departments",
        f"nome de pasta excede {services.MAX_FOLDER_NAME} caracteres",
        nome="X",
        pasta="a" * (services.MAX_FOLDER_NAME + 1),
    )
    refused(web, "/departments", "já existe departamento com este nome", nome="fiscal")
    refused(web, "/departments", "pasta já em uso por outro departamento", nome="Y", pasta="FISCAL")
    with web.db() as session:
        department_id = session.scalar(text("select id from departments"))
    web.post(f"/departments/{department_id}", status=303, nome="Fiscal", pasta="Fiscal Novo")
    notice = web.get("/departments?aviso=pastas")
    assert html.unescape(notice).count(manual.PROBLEMS["template"][-1].message) == 1
    assert manual.PROBLEMS["template"][-1].kind == "aviso"


def test_every_owner_refusal_and_notice_is_explained(web: Web) -> None:
    bootstrap(web)
    web.post(
        "/owners", status=303, nome_fantasia="Alfa Comercio", cnpj=CNPJ_FICTICIO, aliases="Alpha"
    )
    web.post("/owners", status=303, nome_fantasia="Beta Servicos")

    refused(web, "/owners", "nome fantasia é obrigatório", nome_fantasia=" ")
    refused(web, "/owners", "CNPJ inválido", nome_fantasia="Gama", cnpj="123")
    refused(
        web,
        "/owners",
        "CNPJ alfanumérico não suportado nesta versão",
        nome_fantasia="Gama",
        cnpj="12.ABC.345/0001-00",
    )
    refused(
        web, "/owners", "CNPJ já cadastrado em outro dono", nome_fantasia="Gama", cnpj=CNPJ_FICTICIO
    )
    refused(
        web,
        "/owners",
        "pasta já em uso por outro dono",
        nome_fantasia="Gama",
        pasta="alfa comercio",
    )
    refused(web, "/owners", "alias já pertence a outro dono", nome_fantasia="Gama", aliases="ALPHA")
    refused(web, "/owners", "nome de pasta é reservado", nome_fantasia="Gama", pasta="nul")
    with web.db() as session:
        owner_id = session.scalar(text("select id from owners where pasta = 'Beta Servicos'"))
    refused(web, f"/owners/{owner_id}/aliases", "alias já cadastrado", alias="alpha")
    refused(web, f"/owners/{owner_id}/aliases", "alias deve conter letras ou números", alias="---")
    refused(web, "/owners", "nome fantasia inválido", status=400, nome_fantasia="a" * 201)
    refused(
        web, "/owners", "razão social inválido", status=400, nome_fantasia="Gama",
        razao_social="a" * 201,
    )  # fmt: skip
    web.post(f"/owners/{owner_id}/aliases", status=303, alias="Beta Extra")
    with web.db() as session:
        extra = session.scalar(text("select id from owner_aliases where alias = 'beta extra'"))
    web.post(f"/owners/{owner_id}/aliases/{extra}/remover", status=303)
    refused(web, f"/owners/{owner_id}/aliases/{extra}/remover", "alias não encontrado")  # 2nd click
    web.post(f"/owners/{owner_id}", status=303, nome_fantasia="Beta Servicos", pasta="Beta Novo")
    notice = html.unescape(web.get(f"/owners/{owner_id}?aviso=pastas"))
    assert manual.PROBLEMS["donos"][-1].message in notice
    assert "dono não encontrado" in web.get("/owners/99999", status=404)
    assert "dono não encontrado" in _quoted()


def test_every_user_refusal_is_explained(web: Web) -> None:
    bootstrap(web)
    web.post("/departments", status=303, nome="Fiscal")
    web.post("/departments", status=303, nome="Antigo")
    with web.db() as session:
        active = session.scalar(text("select id from departments where nome = 'Fiscal'"))
        old = session.scalar(text("select id from departments where nome = 'Antigo'"))
        admin = session.scalar(text("select id from users where papel = 'admin'"))
    web.post(f"/departments/{old}/ativo", status=303, ativo="0")
    web.post(
        "/users", status=303, telegram_id="2002", nome="Maria Ficticia", papel="operator",
        departamento_id=str(active),
    )  # fmt: skip

    def create(message: str, status: int | None = None, **changes: str) -> None:
        form = {
            "telegram_id": "3003",
            "nome": "Joao Ficticio",
            "papel": "reviewer",
            "departamento_id": str(active),
        }
        form.update(changes)
        refused(web, "/users", message, status, **form)

    create("telegram_id deve ser um número inteiro", telegram_id="abc")
    create("telegram_id deve ser um inteiro positivo", telegram_id="0")
    create("telegram_id já cadastrado", telegram_id="2002")
    create("nome é obrigatório", nome=" ")
    create("nome inválido", status=400, nome="a" * 201)
    create("departamento inativo", departamento_id=str(old))
    status, body = web.post("/users", telegram_id="3003", nome="Joao", papel="reviewer")
    assert status == 400 and "departamento deve ser um número inteiro" in body
    assert "departamento deve ser um número inteiro" in _quoted()
    refused(
        web, f"/users/{admin}/ativo", "não é possível desativar o último administrador ativo",
        ativo="0",
    )  # fmt: skip


def test_every_model_refusal_is_explained(web: Web) -> None:
    bootstrap(web)

    refused(web, "/model", "nome do modelo é obrigatório", nome=" ")
    refused(web, "/model", "nome de modelo inválido", nome="nome com espaço")
    refused(web, "/model", "digest de modelo inválido", nome="modelo-exemplo:8b", digest="a b")
    refused(web, "/model", "nome do modelo inválido", status=400, nome="a" * 201)
    refused(
        web, "/model", "digest do modelo inválido", status=400, nome="modelo-exemplo:8b",
        digest="a" * 201,
    )  # fmt: skip
    web.post("/model", status=303, nome="modelo-exemplo:8b", provedor_externo_ativo="on")


def test_turning_the_external_provider_on_without_a_key_is_explained(
    web_without_key: Web,
) -> None:
    bootstrap(web_without_key)

    refused(
        web_without_key,
        "/model",
        "provedor externo exige OSA_EXTERNAL_LLM_API_KEY configurada no ambiente",
        nome="modelo-exemplo:8b",
        provedor_externo_ativo="on",
    )
    assert "provedor externo" in web_without_key.get("/model").lower()


def test_the_blocked_banner_still_says_what_the_manual_quotes(web: Web) -> None:
    page = web.get("/")

    assert manual.BLOCKED_TITLE in page and manual.BLOCKED_TEXT in page
    assert "osa admin bootstrap" in page
    assert 'href="/manual#bloqueado"' in page


def test_the_stale_page_case_is_a_403_that_nothing_saves(web: Web) -> None:
    bootstrap(web)

    web.client.get("/panel.css")
    response = web.client.post(
        "/departments",
        data={"nome": "Fiscal", "csrf_token": "token-velho"},
        headers={"Origin": ORIGIN},
    )

    assert response.status_code == 403 and response.text == manual.STALE_PAGE_TEXT
    assert manual.STALE_PAGE_TEXT in _quoted()
    with web.db() as session:
        assert session.scalar(text("select count(*) from departments")) == 0


# --- the labels the manual quotes are on the rendered forms of their own pages --------

_CONTROL = re.compile(r"<(button|label|legend)\b[^>]*>(.*?)</\1>", re.S)


def _controls(page: str) -> str:
    """Texts of buttons, labels and legends in <main>, without the help block."""
    main = page.split("<main", 1)[1]
    main = re.sub(r'<details class="help-block">.*?</details>', " ", main, flags=re.S)
    texts = (re.sub(r"<[^>]+>|\s+", " ", body) for _, body in _CONTROL.findall(main))
    return html.unescape(" | ".join(texts))


def test_labels_quoted_by_the_manual_are_rendered_on_their_pages(web: Web, tmp_path: Path) -> None:
    bootstrap(web)
    (tmp_path / "raiz" / "entrada").mkdir(parents=True)
    web.post("/roots", status=303, caminho=str(tmp_path / "raiz"), descricao="")
    web.post("/departments", status=303, nome="Fiscal")
    web.post("/departments", status=303, nome="Pessoal")
    with web.db() as session:
        pessoal = session.scalar(text("select id from departments where nome = 'Pessoal'"))
    web.post(f"/departments/{pessoal}/ativo", status=303, ativo="0")  # shows "Ativar"
    web.post("/owners", status=303, nome_fantasia="Alfa Ficticia", aliases="Alfa Extra")
    with web.db() as session:
        owner = session.scalar(text("select id from owners where nome_fantasia = 'Alfa Ficticia'"))

    pages: dict[str, str] = {}
    for label, path in manual.SCREEN_LABELS.items():
        url = path.format(owner=owner)
        controls = pages.setdefault(url, _controls(web.get(url)))
        assert re.search(rf"(?<!\w){re.escape(label)}(?!\w)", controls), (label, url)
