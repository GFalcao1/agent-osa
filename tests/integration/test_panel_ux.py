"""Confirmation before destructive actions, named success banners, empty states, accessibility.

Against a real migrated PostgreSQL (one database per test), through the real panel app. All
data is fictitious. The rules under test: a destructive action is asked about on the same page
and only the confirmed POST mutates; the success sentence is built from a fixed code and a row
id and never reflects the query string; empty pages say what to do next; every field is named.
"""

from __future__ import annotations

import html as htmllib
import re
from collections.abc import Iterator
from dataclasses import dataclass
from html.parser import HTMLParser
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import Engine, create_engine, func, select
from sqlalchemy.engine import URL
from sqlalchemy.orm import Session, sessionmaker

from osa.admin import services
from osa.core.settings import Settings
from osa.db.models import (
    AuditEvent,
    Department,
    Owner,
    OwnerAlias,
    Root,
    User,
    WatchedFolder,
)
from osa.panel import app as panel
from osa.panel.routes.common import FLASHES

PORT = 8765
ORIGIN = f"http://127.0.0.1:{PORT}"
XSS = "<img src=x onerror=alert(1)>\"'"


@dataclass
class Panel:
    client: TestClient
    engine: Engine

    def get(self, path: str, status: int = 200) -> str:
        response = self.client.get(path)
        assert response.status_code == status, (path, response.status_code)
        return response.text

    def raw_post(self, path: str, token: bool = True, **data: str) -> tuple[int, str, str]:
        self.client.get("/panel.css")
        form = {**data}
        if token:
            form["csrf_token"] = self.client.cookies.get("osa_csrf") or ""
        response = self.client.post(
            path, data=form, headers={"Origin": ORIGIN}, follow_redirects=False
        )
        return response.status_code, response.text, response.headers.get("location", "")

    def post(self, path: str, status: int = 303, **data: str) -> tuple[str, str]:
        code, body, location = self.raw_post(path, **data)
        assert code == status, (path, code, body[:300])
        return body, location

    def db(self) -> Session:
        return Session(self.engine)

    def audits(self) -> int:
        with self.db() as session:
            return int(session.scalar(select(func.count()).select_from(AuditEvent)) or 0)


@pytest.fixture
def web(migrated_database: URL, tmp_path: Path) -> Iterator[Panel]:
    engine = create_engine(migrated_database)
    application = panel.create_app(
        session_factory=sessionmaker(engine, expire_on_commit=False),
        settings=Settings(),
        port=PORT,
        is_mount_point=lambda _path: True,
    )
    client = TestClient(application, base_url=ORIGIN, client=("127.0.0.1", 50000))
    try:
        yield Panel(client, engine)
    finally:
        client.close()
        engine.dispose()


def bootstrap(web: Panel) -> None:
    with web.db() as session:
        services.bootstrap_admin(
            session, telegram_id=1001, nome="Admin Ficticio", actor="cli:bootstrap"
        )
        session.commit()


def text_of(page: str) -> str:
    return htmllib.unescape(re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", page)))


class _Inspect(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.images: list[str] = []
        self.handlers: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag == "img":
            self.images.append(tag)
        self.handlers += [name for name, _ in attrs if name.startswith("on")]


def _tags_and_handlers(page: str) -> tuple[list[str], list[str]]:
    """(img elements, event-handler attributes) as a browser would parse the page."""
    parser = _Inspect()
    parser.feed(page)
    return parser.images, parser.handlers


def confirm_banner(page: str) -> str:
    match = re.search(r'<section class="confirm".*?</section>', page, re.S)
    assert match, "no confirmation banner on the page"
    return match.group(0)


# --- confirmation: alias removal ------------------------------------------------------------


def _owner_with_alias(
    web: Panel, name: str = "Alfa Comercio", alias: str = "Apelido Extra"
) -> tuple[int, int]:
    web.post("/owners", nome_fantasia=name, aliases=alias)
    key = alias.lower()
    with web.db() as session:
        owner = session.scalars(select(Owner).where(Owner.nome_fantasia == name)).one()
        row = session.scalars(
            select(OwnerAlias).where(OwnerAlias.owner_id == owner.id, OwnerAlias.alias == key)
        ).one()
        return owner.id, row.id


def test_removing_an_alias_first_asks_and_changes_nothing(web: Panel) -> None:
    bootstrap(web)
    owner_id, alias_id = _owner_with_alias(web)
    before = web.audits()

    code, page, _ = web.raw_post(f"/owners/{owner_id}/aliases/{alias_id}/remover")

    assert code == 200  # the same page, re-rendered; not a redirect
    banner = confirm_banner(page)
    assert "Remover o alias <strong>apelido extra</strong> do dono Alfa Comercio?" in banner
    assert "não há desfazer" in banner
    # the confirm button posts the same URL with the hidden flag, the token and no script
    assert f'action="/owners/{owner_id}/aliases/{alias_id}/remover"' in banner
    assert '<input type="hidden" name="confirmar" value="1">' in banner
    assert 'name="csrf_token"' in banner and "<script" not in banner
    assert not re.search(r"\son\w+\s*=", page)
    assert f'<a class="btn btn-quiet" href="/owners/{owner_id}" autofocus>Cancelar</a>' in banner
    assert 'role="alert"' in banner
    with web.db() as session:
        assert session.get(OwnerAlias, alias_id) is not None
    assert web.audits() == before  # asking is not a mutation: no audit event, no commit
    # and the page still shows everything else, so the person keeps their place
    assert "apelido extra" in page and "Dados" in page


def test_only_the_confirmed_post_removes_the_alias_and_audits_once(web: Panel) -> None:
    bootstrap(web)
    owner_id, alias_id = _owner_with_alias(web)
    before = web.audits()

    _, location = web.post(f"/owners/{owner_id}/aliases/{alias_id}/remover", confirmar="1")

    assert location == f"/owners/{owner_id}?ok=alias-removido&id={owner_id}"
    with web.db() as session:
        assert session.get(OwnerAlias, alias_id) is None
        events = list(session.scalars(select(AuditEvent).order_by(AuditEvent.id)))
    assert web.audits() == before + 1 and events[-1].acao == "owner_alias.remove"
    page = web.get(location)
    assert "Alias removido do dono <strong>Alfa Comercio</strong>." in page
    assert 'class="confirm"' not in page


def test_cancel_leaves_everything_as_it_was(web: Panel) -> None:
    bootstrap(web)
    owner_id, alias_id = _owner_with_alias(web)
    web.raw_post(f"/owners/{owner_id}/aliases/{alias_id}/remover")
    cancel = re.search(
        r'<a class="btn btn-quiet" href="([^"]+)"',
        confirm_banner(web.raw_post(f"/owners/{owner_id}/aliases/{alias_id}/remover")[1]),
    )
    assert cancel

    page = web.get(cancel.group(1))  # Cancelar is a plain link back to the page

    assert 'class="confirm"' not in page and "apelido extra" in page
    with web.db() as session:
        assert session.get(OwnerAlias, alias_id) is not None


def test_a_confirmed_post_still_needs_the_token_the_origin_and_the_host(web: Panel) -> None:
    bootstrap(web)
    owner_id, alias_id = _owner_with_alias(web)
    path = f"/owners/{owner_id}/aliases/{alias_id}/remover"
    web.client.get("/panel.css")
    token = web.client.cookies.get("osa_csrf") or ""

    no_token, _, _ = web.raw_post(path, token=False, confirmar="1")
    bad_token, _, _ = web.raw_post(path, token=False, confirmar="1", csrf_token="x" * 10)
    cross = web.client.post(
        path,
        data={"confirmar": "1", "csrf_token": token},
        headers={"Origin": "http://evil.example"},
    )
    no_origin = web.client.post(path, data={"confirmar": "1", "csrf_token": token})
    forged_host = web.client.post(
        path, data={"confirmar": "1", "csrf_token": token},
        headers={"Origin": ORIGIN, "Host": "evil.example"},
    )  # fmt: skip

    assert (no_token, bad_token) == (403, 403)
    assert cross.status_code == no_origin.status_code == forged_host.status_code == 403
    with web.db() as session:
        assert session.get(OwnerAlias, alias_id) is not None


def test_get_never_removes_or_deactivates_anything(web: Panel) -> None:
    bootstrap(web)
    owner_id, alias_id = _owner_with_alias(web)
    web.post("/departments", nome="Fiscal")
    with web.db() as session:
        department = session.scalars(select(Department)).one()
    before = web.audits()

    for path in (
        f"/owners/{owner_id}/aliases/{alias_id}/remover?confirmar=1",
        f"/owners/{owner_id}/ativo?ativo=0&confirmar=1",
        f"/departments/{department.id}/ativo?ativo=0&confirmar=1",
    ):
        assert web.client.get(path).status_code == 405, path
    with web.db() as session:
        assert session.get(OwnerAlias, alias_id) is not None
        assert session.get(Owner, owner_id).ativo is True  # type: ignore[union-attr]
        assert session.get(Department, department.id).ativo is True  # type: ignore[union-attr]
    assert web.audits() == before


def test_a_query_string_confirmar_does_not_confirm_a_post(web: Panel) -> None:
    bootstrap(web)
    owner_id, alias_id = _owner_with_alias(web)

    code, page, _ = web.raw_post(f"/owners/{owner_id}/aliases/{alias_id}/remover?confirmar=1")

    assert code == 200 and 'class="confirm"' in page  # only the form field counts
    with web.db() as session:
        assert session.get(OwnerAlias, alias_id) is not None


@pytest.mark.parametrize("value", ["0", "", "true", "yes", "2", " 1", "1 "])
def test_only_the_exact_value_1_confirms(web: Panel, value: str) -> None:
    bootstrap(web)
    owner_id, alias_id = _owner_with_alias(web)

    code, _, _ = web.raw_post(f"/owners/{owner_id}/aliases/{alias_id}/remover", confirmar=value)

    assert code == 200
    with web.db() as session:
        assert session.get(OwnerAlias, alias_id) is not None


def test_an_alias_of_another_owner_is_never_confirmed_or_removed(web: Panel) -> None:
    bootstrap(web)
    owner_a, _alias_a = _owner_with_alias(web, "Alfa Comercio")
    _owner_b, alias_b = _owner_with_alias(web, "Beta Servicos", "Outro Apelido")

    code, page, _ = web.raw_post(f"/owners/{owner_a}/aliases/{alias_b}/remover")

    assert code == 404 and "alias não encontrado" in page and 'class="confirm"' not in page
    with web.db() as session:
        assert session.get(OwnerAlias, alias_b) is not None


def test_removing_what_is_gone_is_the_old_refusal_not_a_question(web: Panel) -> None:
    bootstrap(web)
    owner_id, alias_id = _owner_with_alias(web)
    web.post(f"/owners/{owner_id}/aliases/{alias_id}/remover", confirmar="1")

    code, page, _ = web.raw_post(f"/owners/{owner_id}/aliases/{alias_id}/remover")

    assert code == 404 and "alias não encontrado" in page and "Nada foi salvo." in page


def test_without_an_administrator_nothing_is_asked_and_nothing_changes(web: Panel) -> None:
    with web.db() as session:  # rows can exist without an administrator (imports, services)
        owner = services.create_owner(session, nome_fantasia="Alfa Comercio", actor="test")
        alias = services.add_alias(session, owner.id, "Apelido Extra", actor="test")
        department = services.create_department(session, nome="Fiscal", actor="test")
        session.commit()
        owner_id, alias_id, department_id = owner.id, alias.id, department.id
    before = web.audits()

    code, page, _ = web.raw_post(f"/owners/{owner_id}/aliases/{alias_id}/remover")
    deactivate, other, _ = web.raw_post(f"/departments/{department_id}/ativo", ativo="0")

    assert code == 409 and "osa admin bootstrap" in page and 'class="confirm"' not in page
    assert deactivate == 409 and "osa admin bootstrap" in other and 'class="confirm"' not in other
    with web.db() as session:
        assert session.get(OwnerAlias, alias_id) is not None
        assert session.get(Department, department_id).ativo is True  # type: ignore[union-attr]
    assert web.audits() == before


def test_names_in_the_confirmation_are_escaped(web: Panel) -> None:
    bootstrap(web)
    web.post("/owners", nome_fantasia=XSS, aliases=XSS)
    with web.db() as session:
        owner = session.scalars(select(Owner)).one()
        alias = session.scalars(select(OwnerAlias).where(OwnerAlias.owner_id == owner.id)).first()
        assert alias is not None
    for path, data in (
        (f"/owners/{owner.id}/aliases/{alias.id}/remover", {}),
        (f"/owners/{owner.id}/ativo", {"ativo": "0"}),
    ):
        code, page, _ = web.raw_post(path, **data)

        assert code == 200
        banner = confirm_banner(page)
        assert "<img" not in banner and "&lt;img src=x onerror=alert(1)&gt;" in banner
        assert _tags_and_handlers(page) == ([], [])


# --- confirmation: every deactivation ---------------------------------------------------------


def _create_everything(web: Panel, tmp_path: Path) -> dict[str, int]:
    bootstrap(web)
    (tmp_path / "entrada").mkdir()
    web.post("/roots", caminho=str(tmp_path), descricao="Raiz de teste")
    with web.db() as session:
        root = session.scalars(select(Root)).one()
    web.post(f"/roots/{root.id}/pastas", caminho="entrada")
    web.post("/departments", nome="Fiscal")
    web.post("/owners", nome_fantasia="Alfa Comercio")
    with web.db() as session:
        department = session.scalars(select(Department)).one()
        folder = session.scalars(select(WatchedFolder)).one()
        owner = session.scalars(select(Owner)).one()
    web.post(
        "/users", telegram_id="2002", nome="Maria Ficticia", papel="operator",
        departamento_id=str(department.id),
    )  # fmt: skip
    with web.db() as session:
        user = session.scalars(select(User).where(User.papel == "operator")).one()
    return {
        "root": root.id, "folder": folder.id, "department": department.id,
        "owner": owner.id, "user": user.id,
    }  # fmt: skip


DEACTIVATE = (
    ("root", "/roots/{id}/ativo", "Desativar a raiz", "raiz-desativada", Root),
    ("folder", "/pastas/{id}/ativo", "Desativar a pasta monitorada", "pasta-desativada",
     WatchedFolder),
    ("department", "/departments/{id}/ativo", "Desativar o departamento",
     "departamento-desativado", Department),
    ("owner", "/owners/{id}/ativo", "Desativar o dono", "dono-desativado", Owner),
    ("user", "/users/{id}/ativo", "Desativar o usuário", "usuario-desativado", User),
)  # fmt: skip

# where each confirmed deactivation lands: the page of its own section
DONE = {
    "root": "/roots", "folder": "/roots", "department": "/departments",
    "owner": "/owners/{id}", "user": "/users",
}  # fmt: skip


@pytest.mark.parametrize(("key", "path", "question", "flash", "model"), DEACTIVATE)
def test_every_deactivation_asks_first_and_only_the_confirmation_deactivates(
    web: Panel, tmp_path: Path, key: str, path: str, question: str, flash: str, model: type
) -> None:
    ids = _create_everything(web, tmp_path)
    target = path.format(id=ids[key])
    before = web.audits()

    code, page, _ = web.raw_post(target, ativo="0")

    assert code == 200 and question in confirm_banner(page)
    banner = confirm_banner(page)
    assert '<input type="hidden" name="ativo" value="0">' in banner
    assert '<input type="hidden" name="confirmar" value="1">' in banner
    assert "Nada é apagado" in banner or "não é apagado" in banner
    with web.db() as session:
        assert session.get(model, ids[key]).ativo is True  # type: ignore[attr-defined]
    assert web.audits() == before

    _, location = web.post(target, ativo="0", confirmar="1")

    path_part, _, query = location.partition("?")
    assert path_part == DONE[key].format(id=ids[key])
    assert query == f"ok={flash}&id={ids[key]}"
    with web.db() as session:
        assert session.get(model, ids[key]).ativo is False  # type: ignore[attr-defined]
    assert web.audits() == before + 1
    assert "alert-success" in web.get(location)


@pytest.mark.parametrize(("key", "path", "question", "flash", "model"), DEACTIVATE)
def test_activating_needs_no_confirmation(
    web: Panel, tmp_path: Path, key: str, path: str, question: str, flash: str, model: type
) -> None:
    ids = _create_everything(web, tmp_path)
    target = path.format(id=ids[key])
    web.post(target, ativo="0", confirmar="1")

    _, location = web.post(target, ativo="1")  # straight to the redirect

    assert f"ok={flash.replace('desativ', 'ativ')}" in location
    with web.db() as session:
        assert session.get(model, ids[key]).ativo is True  # type: ignore[attr-defined]


def test_deactivating_the_last_administrator_is_refused_without_asking_first(
    web: Panel,
) -> None:
    bootstrap(web)
    with web.db() as session:
        admin = session.scalars(select(User)).one()
    before = web.audits()

    # no "are you sure?" for an action the service would refuse anyway: the domain error, now
    code, page, _ = web.raw_post(f"/users/{admin.id}/ativo", ativo="0")

    assert code == 409 and 'class="confirm"' not in page and "Desativar o usuário" not in page
    assert "não é possível desativar o último administrador ativo" in page
    assert "Nada foi salvo." in page
    # a forged confirmation gets the same refusal
    code, page, _ = web.raw_post(f"/users/{admin.id}/ativo", ativo="0", confirmar="1")
    assert code == 409 and 'class="confirm"' not in page and "último administrador" in page
    with web.db() as session:
        assert session.get(User, admin.id).ativo is True  # type: ignore[union-attr]
    assert web.audits() == before


def test_an_unknown_owner_is_not_found_without_claiming_there_are_no_owners(
    web: Panel,
) -> None:
    bootstrap(web)
    web.post("/owners", nome_fantasia="Alfa Comercio")

    page = web.get("/owners/99999", status=404)

    assert "dono não encontrado" in page and "Nada foi salvo." in page
    assert "Alfa Comercio" in page  # the real list, not an empty one
    assert "Nenhum dono cadastrado." not in page and "Cadastrar o primeiro dono" not in page
    code, page, _ = web.raw_post("/owners/99999/ativo", ativo="0")
    assert code == 404 and "dono não encontrado" in page and "Alfa Comercio" in page
    assert "Nenhum dono cadastrado." not in page


def test_deactivating_something_that_does_not_exist_is_the_old_not_found(web: Panel) -> None:
    bootstrap(web)

    for path in ("/departments/999/ativo", "/owners/999/ativo", "/users/999/ativo",
                 "/roots/999/ativo", "/pastas/999/ativo"):  # fmt: skip
        code, page, _ = web.raw_post(path, ativo="0")
        assert code == 404 and 'class="confirm"' not in page, path


def test_confirmation_banners_never_carry_script_style_or_remote_urls(
    web: Panel, tmp_path: Path
) -> None:
    ids = _create_everything(web, tmp_path)
    for _key, path, *_rest in DEACTIVATE:
        _, page, _ = web.raw_post(path.format(id=ids[_key]), ativo="0")
        assert len(re.findall(r"<script\b", page)) == 3  # the three panel scripts only
        assert " style=" not in page and "<style" not in page and "javascript:" not in page
        assert not re.search(r'(?:src|href|action)="https?:', page)


# --- the named success banner ----------------------------------------------------------------


def test_the_success_sentence_names_the_item_and_escapes_it(web: Panel) -> None:
    bootstrap(web)

    _, location = web.post("/departments", nome=XSS)
    page = web.get(location)

    assert "<strong>&lt;img src=x onerror=alert(1)&gt;" in page
    assert "<img" not in page and " adicionado." in page


def test_each_flash_code_resolves_to_its_fixed_sentence_for_an_existing_item(
    web: Panel, tmp_path: Path
) -> None:
    ids = _create_everything(web, tmp_path)
    with web.db() as session:
        alias = session.scalars(select(OwnerAlias)).first()
        assert alias is not None
    row_for = {
        "Root": ids["root"], "WatchedFolder": ids["folder"], "Department": ids["department"],
        "Owner": ids["owner"], "User": ids["user"], "OwnerAlias": alias.id,
    }  # fmt: skip
    pages = {
        "Root": "/roots", "WatchedFolder": "/roots", "Department": "/departments",
        "Owner": "/owners", "User": "/users", "OwnerAlias": "/owners",
    }  # fmt: skip

    for code, flash in FLASHES.items():
        if flash.model is None:
            page = web.get(f"/model?ok={code}")
            assert flash.before in page, code
            continue
        name = flash.model.__name__
        page = web.get(f"{pages[name]}?ok={code}&id={row_for[name]}")
        assert flash.before in page and flash.after.strip() in page, code
        assert '<div class="alert alert-success" role="status">' in page


@pytest.mark.parametrize(
    "query",
    [
        "?ok=dono-cadastrado",  # an item code without an id
        "?ok=dono-cadastrado&id=",
        "?ok=dono-cadastrado&id=abc",
        "?ok=dono-cadastrado&id=1.5",
        "?ok=dono-cadastrado&id=-1",
        "?ok=dono-cadastrado&id=99999999999999999999",
        "?ok=dono-cadastrado&id=999999",  # no such row
        "?ok=dono-cadastrado&id=1%27%20OR%201=1",
        "?ok=dono-cadastrado&id=%3Cscript%3Ealert(1)%3C/script%3E",
        "?ok=%3Cscript%3Ealert(1)%3C/script%3E",
        "?ok=nao-existe&id=1",
        "?ok=Dono-Cadastrado&id=1",
        "?ok=" + "a" * 5000,
        "?ok=1",
        "?ok=",
        "?id=1",
        "?ok[]=dono-cadastrado&id=1",
    ],
)
def test_a_made_up_query_never_produces_a_banner_or_reflects_itself(web: Panel, query: str) -> None:
    bootstrap(web)
    web.post("/owners", nome_fantasia="Alfa Comercio")

    page = web.get("/owners" + query)

    assert "alert-success" not in page
    assert "<script>alert" not in page and "OR 1=1" not in page and "aaaa" not in page


def test_a_valid_code_with_a_real_id_shows_only_the_server_side_name(web: Panel) -> None:
    bootstrap(web)
    web.post("/owners", nome_fantasia="Alfa Comercio")
    with web.db() as session:
        owner = session.scalars(select(Owner)).one()

    page = web.get(f"/owners?ok=dono-salvo&id={owner.id}&texto=<script>alert(1)</script>&name=Evil")

    assert "Dono <strong>Alfa Comercio</strong> salvo." in page
    assert "Evil" not in page and "alert(1)" not in page


def test_flash_ids_do_not_leak_other_tables_names(web: Panel) -> None:
    """The id is looked up in the table of its own code: a user id cannot name an owner."""
    bootstrap(web)
    web.post("/owners", nome_fantasia="Alfa Comercio")
    with web.db() as session:
        admin = session.scalars(select(User)).one()

    page = web.get(f"/owners?ok=dono-salvo&id={admin.id}")  # the admin's id, an owner code

    assert "Admin Ficticio" not in page


def test_an_error_page_shows_the_context_line_and_never_a_success_banner(
    web: Panel, tmp_path: Path
) -> None:
    bootstrap(web)
    web.post("/departments", nome="Fiscal")

    code, page, _ = web.raw_post("/departments", nome="Fiscal")

    assert code == 409
    assert re.search(r'<div class="alert alert-error" role="alert">.*?Nada foi salvo\.', page, re.S)
    assert "alert-success" not in page


# --- empty states and dependencies ------------------------------------------------------------


@pytest.mark.parametrize(
    ("path", "title", "target", "label"),
    [
        ("/roots", "Nenhuma raiz cadastrada.", "nova-raiz", "Cadastrar a primeira raiz"),
        ("/departments", "Nenhum departamento cadastrado.", "novo-departamento",
         "Cadastrar o primeiro departamento"),
        ("/owners", "Nenhum dono cadastrado.", "novo-dono", "Cadastrar o primeiro dono"),
    ],
)  # fmt: skip
def test_empty_pages_offer_the_next_step_as_a_link_to_their_own_form(
    web: Panel, path: str, title: str, target: str, label: str
) -> None:
    bootstrap(web)

    page = web.get(path)

    assert title in page
    assert re.search(rf'<a class="btn btn-primary" href="#{target}">.*?{label}</a>', page, re.S)
    assert f'id="{target}"' in page  # the anchor lands on the form
    assert 'data-lottie="float"' not in page and "empty-art" not in page  # no decoration


def test_empty_pages_offer_no_button_when_nothing_can_be_saved_yet(web: Panel) -> None:
    for path in ("/roots", "/departments", "/owners", "/users"):
        page = web.get(path)

        assert 'class="btn btn-primary" href="#' not in page, path
        assert "Cadastro bloqueado" in page


def test_users_without_an_active_department_get_the_dependency_notice_not_the_form(
    web: Panel,
) -> None:
    bootstrap(web)

    page = web.get("/users")

    assert "Antes, cadastre um departamento no Template." in page
    assert re.search(r'<a href="/departments">Ir para o Template</a>', page)
    assert 'action="/users"' not in page and "Cadastrar usuário" not in page.split("<main")[1]
    assert "Cadastrar o primeiro usuário" not in page  # nothing to point at yet
    web.post("/departments", nome="Fiscal")

    page = web.get("/users")

    assert "Antes, cadastre um departamento" not in page and 'action="/users"' in page
    assert 'id="novo-usuario"' in page  # the administrator row exists: no empty state to point
    assert "Cadastrar o primeiro usuário" not in page


def test_the_dependency_notice_also_covers_departments_that_are_all_inactive(web: Panel) -> None:
    bootstrap(web)
    web.post("/departments", nome="Fiscal")
    with web.db() as session:
        department = session.scalars(select(Department)).one()
    web.post(f"/departments/{department.id}/ativo", ativo="0", confirmar="1")

    assert "Antes, cadastre um departamento no Template." in web.get("/users")


# --- home hierarchy ------------------------------------------------------------------------


def test_home_shows_the_counts_and_the_manual_before_the_local_only_notice(web: Panel) -> None:
    bootstrap(web)
    page = web.get("/")
    main = page.split("<main", 1)[1]

    order = [
        main.index('class="grid stats"'),
        main.index('class="card manual-link" href="/manual"'),
        main.index("Acesso somente local"),
        main.index("<h2>Modelo efetivo</h2>"),
    ]
    assert order == sorted(order)
    assert main.count('<a class="card stat" href="') == 4  # four count cards, each a link
    notice = re.search(r'<div class="notice" role="note">.*?</div>\s*</div>', main, re.S)
    assert notice
    sentence = text_of(notice.group(0))
    assert (
        "Este painel não tem autenticação própria na V1: só é alcançável nesta máquina" in sentence
    )
    assert "127.0.0.1" in sentence and "Não exponha a porta na rede." in sentence
    assert 'class="alert alert-error"' not in main and "notice-red" not in page


def test_home_says_what_to_do_next_in_the_order_of_the_manual(web: Panel, tmp_path: Path) -> None:
    bootstrap(web)
    assert re.search(r'Próximo passo: <a href="/roots">cadastrar a primeira raiz</a>', web.get("/"))
    web.post("/roots", caminho=str(tmp_path), descricao="")
    assert 'href="/departments">montar o template de departamentos' in web.get("/")
    web.post("/departments", nome="Fiscal")
    assert 'href="/owners">cadastrar o primeiro dono' in web.get("/")
    web.post("/owners", nome_fantasia="Alfa Comercio")
    assert 'href="/users">cadastrar o primeiro usuário' in web.get("/")
    with web.db() as session:
        department = session.scalars(select(Department)).one()
    web.post(
        "/users", telegram_id="2002", nome="Maria Ficticia", papel="operator",
        departamento_id=str(department.id),
    )  # fmt: skip
    assert "Próximo passo" not in web.get("/")


def test_every_page_has_the_local_only_badge_in_the_sidebar_footer(web: Panel) -> None:
    bootstrap(web)
    for path in ("/", "/roots", "/departments", "/owners", "/users", "/model", "/manual"):
        page = web.get(path)
        side = re.search(r'<div class="side-foot">.*?</header>', page, re.S)

        assert side, path
        assert "Somente nesta máquina" in text_of(side.group(0)) and "127.0.0.1" in side.group(0)
        assert 'class="access-badge"' in side.group(0) and "<svg" in side.group(0)


# --- fields, labels, hints, a11y ---------------------------------------------------------------


def _inputs(page: str) -> list[str]:
    return re.findall(r"<(?:input|select|textarea)\b[^>]*>", page.split("<main", 1)[1])


def test_every_visible_control_has_a_label_or_an_aria_label(web: Panel, tmp_path: Path) -> None:
    ids = _create_everything(web, tmp_path)
    paths = ("/roots", "/departments", f"/owners/{ids['owner']}", "/owners", "/users", "/model")
    for path in paths:
        page = web.get(path)
        labelled = set(re.findall(r'<label[^>]*\bfor="([^"]+)"', page))
        for tag in _inputs(page):
            if 'type="hidden"' in tag:
                continue
            control_id = re.search(r'\bid="([^"]+)"', tag)
            assert (
                'aria-label="' in tag
                or (control_id and control_id.group(1) in labelled)
                # a checkbox wrapped by its own visible label
                or ('type="checkbox"' in tag and f'<label class="check">{tag}' in page)
            ), (path, tag)


def test_hints_and_help_lines_are_tied_to_their_fields_with_aria_describedby(
    web: Panel, tmp_path: Path
) -> None:
    ids = _create_everything(web, tmp_path)
    for path in (
        "/roots",
        "/departments",
        f"/owners/{ids['owner']}",
        "/owners",
        "/users",
        "/model",
    ):
        page = web.get(path)
        page_ids = set(re.findall(r'\sid="([^"]+)"', page))
        described = re.findall(r'aria-describedby="([^"]+)"', page)

        assert described, path
        for value in described:
            assert all(part in page_ids for part in value.split()), (path, value)
        for hint_id in re.findall(r'<span class="hint" id="([^"]+)"', page):
            assert any(hint_id in value.split() for value in described), (path, hint_id)
        for help_id in re.findall(r'<p class="help" id="([^"]+)"', page):
            assert any(help_id in value.split() for value in described), (path, help_id)


def test_cnpj_and_telegram_id_fields_say_what_they_take(web: Panel, tmp_path: Path) -> None:
    ids = _create_everything(web, tmp_path)
    owners = web.get("/owners")
    detail = web.get(f"/owners/{ids['owner']}")
    users = web.get("/users")

    for page in (owners, detail):
        cnpj = re.search(r'<input type="text" id="(?:novo-)?dono-cnpj"[^>]*>', page)
        assert cnpj, page[:0]
        tag = cnpj.group(0)
        assert 'inputmode="numeric"' in tag and 'autocomplete="off"' in tag
        assert re.search(r'pattern="\[0-9A-Za-z\.\\/\\- \]\*"', tag)
        assert "opcional, com ou sem máscara" in page
    telegram = re.search(r'<input type="text" id="novo-usr-telegram"[^>]*>', users)
    assert telegram
    tag = telegram.group(0)
    assert (
        'inputmode="numeric"' in tag and 'pattern="[0-9]+"' in tag and 'autocomplete="off"' in tag
    )
    assert "Não é o telefone nem o @nome" in users
    # the short label and the grey hint are separate elements
    assert re.search(r'<label for="novo-usr-telegram">Telegram ID</label><span class="hint"', users)


def test_the_telegram_refusal_tells_where_to_find_the_number(web: Panel, tmp_path: Path) -> None:
    ids = _create_everything(web, tmp_path)

    code, page, _ = web.raw_post(
        "/users", telegram_id="@maria", nome="Maria", papel="operator",
        departamento_id=str(ids["department"]),
    )  # fmt: skip

    assert code == 400
    assert "Telegram ID deve ser só números. Não é o telefone nem o @nome" in page
    assert "peça o número à própria pessoa" in page and "@maria" not in page  # nothing reflected


def test_tables_have_scoped_headers_and_inactive_badges_explain_themselves(
    web: Panel, tmp_path: Path
) -> None:
    ids = _create_everything(web, tmp_path)
    web.post(f"/departments/{ids['department']}/ativo", ativo="0", confirmar="1")
    web.post(f"/owners/{ids['owner']}/ativo", ativo="0", confirmar="1")
    web.post(f"/users/{ids['user']}/ativo", ativo="0", confirmar="1")
    web.post(f"/roots/{ids['root']}/ativo", ativo="0", confirmar="1")

    for path in ("/departments", "/owners", "/users"):
        page = web.get(path)
        heads = re.findall(r"<th\b[^>]*>", page)
        assert heads and all(h == '<th scope="col">' for h in heads), path
        assert '<span class="badge badge-off" title="' in page, path
    assert 'title="não recebe documentos"' in web.get("/departments")
    assert 'title="não fala com o agente"' in web.get("/users")
    assert 'title="o agente não usa esta raiz"' in web.get("/roots")


def test_the_success_banner_keeps_role_status_and_the_error_banner_role_alert(
    web: Panel,
) -> None:
    bootstrap(web)
    _, location = web.post("/departments", nome="Fiscal")

    assert '<div class="alert alert-success" role="status">' in web.get(location)
    code, page, _ = web.raw_post("/departments", nome="Fiscal")
    assert code == 409 and '<div class="alert alert-error" role="alert">' in page


# --- row forms: the Save highlight hooks --------------------------------------------------------


def test_department_and_user_rows_are_real_columns_tied_to_one_row_form(
    web: Panel, tmp_path: Path
) -> None:
    ids = _create_everything(web, tmp_path)
    departments = web.get("/departments")
    users = web.get("/users")

    dep = ids["department"]
    form = (
        f'<form class="row-form" id="dep-{dep}" method="post" action="/departments/{dep}" '
        'data-track="row">'
    )
    assert form in departments
    for name in ("nome", "pasta", "usa_ano"):
        assert re.search(
            rf'<input type="(?:text|checkbox)" name="{name}" form="dep-{ids["department"]}"',
            departments,
        )
    save = re.search(
        rf'<button type="submit" form="dep-{ids["department"]}" class="btn btn-sm btn-save">',
        departments,
    )
    assert save
    assert (
        '<th scope="col">Nome</th><th scope="col">Pasta</th><th scope="col">Ano</th>' in departments
    )
    assert (
        f'id="usr-{ids["user"]}"' in users
        and f'form="usr-{ids["user"]}" class="btn btn-sm btn-save"' in users
    )
    # the arrows are icon-only, named by aria-label, and the other actions are separate forms
    assert (
        'aria-label="Subir Fiscal"' in departments and 'aria-label="Descer Fiscal"' in departments
    )
    assert re.search(
        r'<button type="submit" class="btn btn-sm btn-icon" aria-label="Subir Fiscal" '
        r'title="Subir"><svg',
        departments,
    )
    assert departments.count("</form>") >= 4


def test_a_row_save_still_posts_the_same_fields_the_old_inline_form_did(
    web: Panel, tmp_path: Path
) -> None:
    ids = _create_everything(web, tmp_path)

    _, location = web.post(
        f"/departments/{ids['department']}", nome="Fiscal Novo", pasta="Fiscal", usa_ano="on"
    )

    assert location == f"/departments?ok=departamento-salvo&id={ids['department']}"
    page = web.get(location)
    assert "Departamento <strong>Fiscal Novo</strong> salvo." in page
    with web.db() as session:
        saved = session.get(Department, ids["department"])
        assert saved is not None and (saved.nome, saved.usa_ano) == ("Fiscal Novo", True)


def test_pages_preload_the_main_font_and_load_no_remote_resource(
    web: Panel, tmp_path: Path
) -> None:
    ids = _create_everything(web, tmp_path)
    for path in (
        "/",
        "/roots",
        "/departments",
        "/owners",
        f"/owners/{ids['owner']}",
        "/users",
        "/model",
    ):
        page = web.get(path)
        head = page.split("</head>")[0]

        assert len(re.findall(r'<link rel="preload"[^>]*as="font"[^>]*crossorigin>', head)) == 2
        for stem in ("ibm-plex-sans-400-latin1", "ibm-plex-serif-600-latin1"):
            assert re.search(rf'href="/fonts/{stem}\.[0-9a-f]{{10}}\.woff2"', head), stem
        assert not re.search(r'(?:src|href)="https?:', page), path
