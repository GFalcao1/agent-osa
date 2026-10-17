"""The panel end to end against a disposable migrated PostgreSQL (one database per test)."""

from __future__ import annotations

import logging
import re
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from pydantic import SecretStr
from sqlalchemy import Engine, create_engine, select
from sqlalchemy.engine import URL
from sqlalchemy.orm import Session, sessionmaker

from osa.admin import export, services
from osa.core.settings import Settings
from osa.db.models import (
    AuditEvent,
    Department,
    InstallationSettings,
    Owner,
    OwnerAlias,
    Root,
    User,
    WatchedFolder,
)
from osa.domain.errors import Conflict
from osa.panel import app as panel

PORT = 8765
ORIGIN = f"http://127.0.0.1:{PORT}"
SENTINEL = "fictional-key-not-real"


def make_cnpj(base12: str) -> str:
    def digit(digits: str, weights: list[int]) -> str:
        rest = sum(int(d) * w for d, w in zip(digits, weights, strict=True)) % 11
        return "0" if rest < 2 else str(11 - rest)

    first = digit(base12, [5, 4, 3, 2, 9, 8, 7, 6, 5, 4, 3, 2])
    second = digit(base12 + first, [6, 5, 4, 3, 2, 9, 8, 7, 6, 5, 4, 3, 2])
    return base12 + first + second


CNPJ_A = make_cnpj("112223330001")
CNPJ_B = make_cnpj("445556660001")


@dataclass
class Panel:
    client: TestClient
    engine: Engine
    pages: list[str]

    def get(self, path: str, status: int = 200) -> str:
        response = self.client.get(path)
        assert response.status_code == status, (path, response.status_code)
        self.pages.append(response.text)
        return response.text

    def post(self, path: str, status: int = 303, **data: str) -> str:
        self.client.get("/panel.css")  # makes sure the CSRF cookie exists
        token = self.client.cookies.get("osa_csrf")
        response = self.client.post(
            path,
            data={**data, "csrf_token": token or ""},
            headers={"Origin": ORIGIN},
            follow_redirects=False,
        )
        assert response.status_code == status, (path, response.status_code, response.text[:300])
        self.pages.append(response.text)
        return response.headers.get("location", response.text)

    def db(self) -> Session:
        return Session(self.engine)


def build_panel(url: URL, *, with_key: bool) -> Iterator[Panel]:
    engine = create_engine(url)
    settings = Settings(external_llm_api_key=SecretStr(SENTINEL)) if with_key else Settings()
    application = panel.create_app(
        session_factory=sessionmaker(engine, expire_on_commit=False),
        settings=settings,
        port=PORT,
        is_mount_point=lambda _path: True,
    )
    client = TestClient(application, base_url=ORIGIN, client=("127.0.0.1", 50000))
    try:
        yield Panel(client, engine, [])
    finally:
        client.close()
        engine.dispose()


@pytest.fixture
def web(migrated_database: URL) -> Iterator[Panel]:
    yield from build_panel(migrated_database, with_key=True)


@pytest.fixture
def web_without_key(migrated_database: URL) -> Iterator[Panel]:
    yield from build_panel(migrated_database, with_key=False)


def bootstrap(web: Panel, telegram_id: int = 1001) -> int:
    with web.db() as session:
        admin = services.bootstrap_admin(
            session, telegram_id=telegram_id, nome="Admin Ficticio", actor="cli:bootstrap"
        )
        session.commit()
        return admin.id


def test_panel_home_states_that_there_is_no_authentication(web: Panel) -> None:
    html = web.get("/")

    assert "não tem autenticação própria" in html
    assert "127.0.0.1" in html


def test_panel_mutates_nothing_until_an_admin_exists(web: Panel, tmp_path: Path) -> None:
    root = tmp_path / "raiz-ficticia"
    root.mkdir()

    html = web.get("/roots")
    assert "osa admin bootstrap" in html
    assert 'action="/roots"' not in html  # forms are not even offered

    body = web.post("/roots", status=409, caminho=str(root), descricao="")
    assert "osa admin bootstrap" in body
    web.post("/departments", status=409, nome="Fiscal")
    web.post("/owners", status=409, nome_fantasia="Alfa")
    web.post("/model", status=409, nome="modelo:1b")
    with web.db() as session:
        assert session.scalars(select(Root)).all() == []
        assert session.scalars(select(Department)).all() == []
        assert session.scalars(select(Owner)).all() == []
        assert session.scalars(select(InstallationSettings)).all() == []


def test_the_form_cannot_create_an_admin(web: Panel) -> None:
    bootstrap(web)
    web.post("/departments", nome="Fiscal")
    with web.db() as session:
        department = session.scalars(select(Department)).one()

    body = web.post(
        "/users",
        status=403,
        telegram_id="2002",
        nome="Falso Admin",
        papel="admin",
        departamento_id=str(department.id),
    )

    assert "bootstrap" in body
    with web.db() as session:
        assert [u.papel for u in session.scalars(select(User))] == ["admin"]
    assert '<option value="admin"' not in web.get("/users")


def _user_form_department(html: str, user_id: int) -> tuple[str, str]:
    """(value, label) of the department option the browser would submit for this user."""
    # The row's controls sit in separate table cells and are tied to its (empty) form with the
    # form="usr-<id>" attribute; the form itself only carries the action and the token.
    assert re.search(
        rf'<form class="row-form" id="usr-{user_id}" method="post" action="/users/{user_id}"', html
    )
    select_html = re.search(
        rf'<select name="departamento_id" form="usr-{user_id}"[^>]*>(.*?)</select>', html, re.S
    )
    assert select_html, "edit select missing"
    options = re.findall(
        r'<option value="(\d+)"\s*(selected)?\s*>([^<]*)</option>', select_html.group(1)
    )
    chosen = [o for o in options if o[1]] or options[:1]  # browsers submit the first if none
    return chosen[0][0], chosen[0][2]


def test_editing_a_user_of_an_inactive_department_keeps_that_department(web: Panel) -> None:
    bootstrap(web)
    web.post("/departments", nome="Fiscal")
    web.post("/departments", nome="Pessoal")
    with web.db() as session:
        fiscal, pessoal = session.scalars(select(Department).order_by(Department.id)).all()
    web.post(
        "/users", telegram_id="3003", nome="Ana", papel="operator", departamento_id=str(fiscal.id)
    )
    web.post(f"/departments/{fiscal.id}/ativo", ativo="0", confirmar="1")
    with web.db() as session:
        user = session.scalars(select(User).where(User.telegram_id == 3003)).one()

    page = web.get("/users")
    value, label = _user_form_department(page, user.id)
    assert value == str(fiscal.id) and "inativo" in label
    new_user_form = page[page.index('action="/users"') :]
    assert f'<option value="{fiscal.id}"' not in new_user_form  # new users: active only

    web.post(f"/users/{user.id}", nome="Ana Maria", papel="operator", departamento_id=value)

    with web.db() as session:
        saved = session.get(User, user.id)
        assert saved is not None
        assert (saved.nome, saved.departamento_id) == ("Ana Maria", fiscal.id)
        assert pessoal.id != saved.departamento_id


def test_the_last_admin_cannot_be_deactivated_from_the_panel(web: Panel) -> None:
    admin_id = bootstrap(web)

    body = web.post(f"/users/{admin_id}/ativo", status=409, ativo="0", confirmar="1")

    assert "último administrador" in body
    with web.db() as session:
        assert session.get(User, admin_id).ativo is True  # type: ignore[union-attr]


def test_complete_fictitious_registration_through_the_panel(
    web: Panel, tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """The gate demonstration, minus the browser: root, template, owners, users, model."""
    caplog.set_level(logging.INFO, logger="sqlalchemy.engine")  # SQL and its parameters
    bootstrap(web)
    root_dir = tmp_path / "raiz-ficticia"
    (root_dir / "entrada").mkdir(parents=True)

    web.post("/roots", caminho=str(root_dir), descricao="Raiz de teste")
    with web.db() as session:
        root = session.scalars(select(Root)).one()
    web.post(f"/roots/{root.id}/pastas", caminho="entrada")
    for nome, ano in (("Fiscal", "on"), ("Pessoal", None), ("Contábil", None), ("Jurídico", None)):
        web.post("/departments", nome=nome, **({"usa_ano": ano} if ano else {}))
    web.post(
        "/owners",
        nome_fantasia="Alfa Comércio",
        razao_social="Alfa Comércio de Alimentos Ltda",
        cnpj=CNPJ_A,
        aliases="Alfa\nAlpha Alimentos",
    )
    web.post("/owners", nome_fantasia="Beta Serviços", cnpj=CNPJ_B, aliases="Beta")
    with web.db() as session:
        fiscal, pessoal = (
            session.scalars(select(Department).where(Department.nome == n)).one()
            for n in ("Fiscal", "Pessoal")
        )
    web.post(
        "/users", telegram_id="2002", nome="Maria Ficticia", papel="operator",
        departamento_id=str(fiscal.id),
    )  # fmt: skip
    web.post(
        "/users", telegram_id="3003", nome="João Ficticio", papel="reviewer",
        departamento_id=str(pessoal.id),
    )  # fmt: skip
    web.post("/model", nome="modelo-ficticio:1b", digest="sha256:abc", provedor_externo_ativo="on")

    with web.db() as session:
        assert [d.nome for d in session.scalars(select(Department).order_by(Department.ordem))] == [
            "Fiscal", "Pessoal", "Contábil", "Jurídico",
        ]  # fmt: skip
        assert session.scalars(select(WatchedFolder)).one().caminho == "entrada"
        owners = session.scalars(select(Owner).order_by(Owner.id)).all()
        assert [(o.nome_fantasia, o.cnpj) for o in owners] == [
            ("Alfa Comércio", CNPJ_A),
            ("Beta Serviços", CNPJ_B),
        ]
        assert {a.alias for a in session.scalars(select(OwnerAlias))} >= {"alfa", "beta"}
        assert sorted(u.papel for u in session.scalars(select(User))) == [
            "admin", "operator", "reviewer",
        ]  # fmt: skip
        model = session.get(InstallationSettings, 1)
        assert model is not None and model.provedor_externo_ativo is True
        mutations = list(
            session.scalars(select(AuditEvent).where(AuditEvent.ator == "panel:local"))
        )
        probes = [e for e in mutations if e.acao == "root.probe"]
        assert len(probes) == 1  # the write probe, on its own transaction (SPEC §10)
        assert len(mutations) - len(probes) == 1 + 1 + 4 + 2 + 2 + 1
        assert {e.resultado for e in mutations} == {"ok"}
        yaml_text = export.export_yaml(session)
        details = [e.detalhes for e in session.scalars(select(AuditEvent))]

    for path in (
        "/",
        "/roots",
        "/departments",
        "/owners",
        f"/owners/{owners[0].id}",
        "/users",
        "/model",
    ):
        web.get(path)
    assert any("Alfa Comércio" in page for page in web.pages)
    assert str(root_dir) in web.get("/roots")
    assert SENTINEL not in yaml_text
    assert SENTINEL not in str(details)
    assert caplog.records, "SQL logging must be captured, or the next line proves nothing"
    assert SENTINEL not in caplog.text
    assert all(SENTINEL not in page for page in web.pages)
    assert "sim" in web.get("/model")  # key present, shown only as yes/no


def test_panel_shows_refusals_with_messages_and_audits_the_root_denial(
    web: Panel, tmp_path: Path
) -> None:
    bootstrap(web)

    body = web.post("/roots", status=400, caminho=str(tmp_path / "nao-existe"), descricao="")

    assert "raiz não existe" in body
    body = web.post("/roots", status=400, caminho="relativo/raiz", descricao="")
    assert "caminho absoluto" in body
    with web.db() as session:  # the refusal outlived the rolled-back request
        denied = list(
            session.scalars(
                select(AuditEvent).where(
                    AuditEvent.acao == "root.create", AuditEvent.resultado == "denied"
                )
            )
        )
        assert len(denied) == 2 and {e.ator for e in denied} == {"panel:local"}
        assert session.scalars(select(Root)).all() == []


def test_duplicate_alias_between_owners_is_refused_in_the_panel(web: Panel) -> None:
    bootstrap(web)
    web.post("/owners", nome_fantasia="Alfa", aliases="Alpha")

    body = web.post("/owners", status=409, nome_fantasia="Beta", aliases="ALPHA")

    assert "alias já pertence a outro dono" in body
    with web.db() as session:
        assert [o.nome_fantasia for o in session.scalars(select(Owner))] == ["Alfa"]


def test_owner_page_edits_aliases(web: Panel) -> None:
    bootstrap(web)
    web.post("/owners", nome_fantasia="Alfa")
    with web.db() as session:
        owner = session.scalars(select(Owner)).one()

    web.post(f"/owners/{owner.id}/aliases", alias="Alpha Ltda")
    html = web.get(f"/owners/{owner.id}")
    assert "alpha ltda" in html
    with web.db() as session:
        alias = session.scalars(select(OwnerAlias).where(OwnerAlias.alias == "alpha ltda")).one()
    web.post(f"/owners/{owner.id}/aliases/{alias.id}/remover", confirmar="1")
    assert "alpha ltda" not in web.get(f"/owners/{owner.id}")
    web.get("/owners/999999", status=404)


def test_changing_a_department_folder_shows_the_not_renamed_notice(web: Panel) -> None:
    bootstrap(web)
    web.post("/departments", nome="Fiscal")
    with web.db() as session:
        department = session.scalars(select(Department)).one()

    location = web.post(
        f"/departments/{department.id}", nome="Fiscal", pasta="Tributos", usa_ano="on"
    )

    assert location == f"/departments?ok=departamento-salvo&id={department.id}&aviso=pastas"
    page = web.get(location)
    assert "não foram renomeadas" in page and "Departamento <strong>Fiscal</strong> salvo." in page
    web.post(f"/departments/{department.id}/mover", direcao="up")
    web.post(f"/departments/{department.id}/ativo", ativo="0", confirmar="1")
    with web.db() as session:
        assert session.scalars(select(Department)).one().ativo is False


def test_model_toggle_without_key_is_refused_and_page_says_so(web_without_key: Panel) -> None:
    bootstrap(web_without_key)

    body = web_without_key.post("/model", status=409, nome="modelo:1b", provedor_externo_ativo="on")

    assert "OSA_EXTERNAL_LLM_API_KEY" in body
    assert "Chave presente no ambiente" in web_without_key.get("/model")
    web_without_key.post("/model", nome="modelo:1b", digest="")
    with web_without_key.db() as session:
        row = session.get(InstallationSettings, 1)
        assert row is not None and row.provedor_externo_ativo is False


def test_model_page_shows_only_key_presence_never_the_key(web: Panel) -> None:
    bootstrap(web)

    html = web.get("/model")

    assert "Chave presente no ambiente" in html and "<strong>sim</strong>" in html
    assert SENTINEL not in html and 'type="password"' not in html
    assert 'name="api_key"' not in html and 'name="external' not in html


def test_user_form_never_offers_admin_and_validates_numbers(web: Panel) -> None:
    bootstrap(web)
    web.post("/departments", nome="Fiscal")
    with web.db() as session:
        department = session.scalars(select(Department)).one()

    body = web.post(
        "/users", status=400, telegram_id="não-é-número", nome="X", papel="operator",
        departamento_id=str(department.id),
    )  # fmt: skip

    assert "Telegram ID deve ser só números" in body and "peça o número à própria pessoa" in body
    assert "não-é-número" not in body


def test_real_engine_bootstrap_twice_keeps_the_denial_after_rollback(
    migrated_database: URL,
) -> None:
    engine = create_engine(migrated_database)
    try:
        with Session(engine) as session:
            services.bootstrap_admin(session, telegram_id=1, nome="Admin", actor="cli:bootstrap")
            session.commit()
        with Session(engine) as session:
            with pytest.raises(Conflict):
                services.bootstrap_admin(
                    session, telegram_id=2, nome="Outro", actor="cli:bootstrap"
                )
            session.rollback()
        with Session(engine) as session:
            outcomes = [
                (e.acao, e.resultado)
                for e in session.scalars(select(AuditEvent).order_by(AuditEvent.id))
            ]
            assert outcomes == [("admin.bootstrap", "ok"), ("admin.bootstrap", "denied")]
            assert session.scalars(select(User)).all()[0].telegram_id == 1
    finally:
        engine.dispose()


NAV_SECTIONS = ("/", "/roots", "/departments", "/owners", "/users", "/model", "/manual")


def test_every_page_loads_the_theme_script_and_has_the_switch(web: Panel) -> None:
    bootstrap(web)
    with web.db() as session:
        owner = services.create_owner(session, nome_fantasia="Alfa Ficticia", actor="panel")
        session.commit()
        owner_id = owner.id
    for path in (*NAV_SECTIONS, f"/owners/{owner_id}"):
        html = web.get(path)

        # Three external scripts, all from the panel itself, all in <head>: theme.js blocks
        # (the saved theme applies before the first paint); the Lottie library and motion.js
        # are deferred and run in that order. No inline script anywhere.
        scripts = (
            '<script src="/theme.js"></script>',
            '<script src="/assets/lottie.min.js" defer></script>',
            '<script src="/motion.js" defer></script>',
        )
        assert re.findall(r"<script\b[^>]*>", html) == [tag[: -len("</script>")] for tag in scripts]
        head = html.split("</head>")[0]
        assert all(tag in head for tag in scripts)
        assert head.index("/panel.css") < head.index("/theme.js")
        assert [head.index(tag) for tag in scripts] == sorted(head.index(tag) for tag in scripts)
        assert '<link rel="icon" type="image/svg+xml" href="/favicon.svg">' in head
        remainder = html
        for tag in scripts:
            remainder = remainder.replace(tag, "")
        assert "</script>" not in remainder
        assert " style=" not in html and "<style" not in html
        assert not re.search(r"\son\w+\s*=", html)  # no inline event handler
        # The switch: a real button, labelled, with both icons, on every page.
        button = re.search(
            r'<button type="button" class="theme-switch"[^>]*>.*?</button>', html, re.S
        )
        assert button, path
        tag = button.group(0)
        assert 'aria-pressed="false"' in tag
        assert 'aria-label="Alternar tema claro/escuro"' in tag
        assert tag.count("<svg") == 2 and 'class="icon sun"' in tag and 'class="icon moon"' in tag
        # The sidebar nav lists every section once, with an icon.
        nav = re.search(r'<nav class="nav".*?</nav>', html, re.S)
        assert nav
        links = re.findall(r'<a href="([^"]+)"[^>]*><span class="nav-ico"><svg', nav.group(0))
        assert links == list(NAV_SECTIONS)


def test_the_home_notice_and_blocked_state_survive_the_sidebar_layout(web: Panel) -> None:
    home = web.get("/")
    assert "não tem autenticação própria" in home and "só é alcançável nesta" in home
    assert "osa admin bootstrap" in home
    assert 'class="alert alert-blocked"' in home


def test_a_saved_change_redirects_with_a_code_and_shows_the_success_flash(web: Panel) -> None:
    bootstrap(web)

    location = web.post("/departments", nome="Fiscal Ficticio")

    with web.db() as session:
        department = session.scalars(select(Department)).one()
    assert location == f"/departments?ok=departamento-adicionado&id={department.id}"
    page = web.get(location)
    assert '<div class="alert alert-success" role="status">' in page
    assert '<span class="alert-art" data-lottie="check" data-lottie-mode="once">' in page
    assert "Departamento <strong>Fiscal Ficticio</strong> adicionado." in page
    assert "Alterações salvas" not in page
    # The same page without the code, or with any other value, has no flash.
    assert "alert-success" not in web.get("/departments")
    assert "alert-success" not in web.get("/departments?ok=1")
    assert "alert-success" not in web.get("/departments?ok=%3Cscript%3E")


def test_the_pastas_warning_comes_with_the_success_flash_in_one_redirect(web: Panel) -> None:
    bootstrap(web)
    web.post("/departments", nome="Fiscal Ficticio")
    with web.db() as session:
        department = session.scalars(select(Department)).one()

    location = web.post(
        f"/departments/{department.id}", nome="Fiscal Ficticio", pasta="outra-pasta"
    )

    assert location.endswith("&aviso=pastas")
    page = web.get(location)
    assert "alert-info" in page and "Departamento <strong>Fiscal Ficticio</strong> salvo." in page


def test_a_refusal_never_shows_the_success_flash(web: Panel, tmp_path: Path) -> None:
    bootstrap(web)
    web.post("/departments", nome="Fiscal Ficticio")

    body = web.post(
        "/roots?ok=departamento-salvo&id=1", status=400,
        caminho=str(tmp_path / "nao-existe"), descricao="",
    )  # fmt: skip

    assert "alert alert-error" in body and "alert-success" not in body
    assert "Nada foi salvo." in body


def test_every_page_marks_the_hooks_of_the_micro_interactions(web: Panel) -> None:
    bootstrap(web)
    for path in NAV_SECTIONS:
        html = web.get(path)

        assert html.count('class="knob-art" data-lottie="theme" data-lottie-mode="theme"') == 1
        assert len(re.findall(r'<span class="nav-ico">', html)) == len(NAV_SECTIONS)
        # The brand mark is the static hand-drawn shield: no animation hook, on any page.
        assert "breathe" not in html and "brand-mark" not in html
        assert '<a class="brand" href="/">\n    <svg viewBox="0 0 32 32"' in html
        # Every submit button carries a decorative icon the spinner can take the place of.
        for button in re.findall(r'<button type="submit"[^>]*>.*?</button>', html, re.S):
            assert button.split(">", 1)[1].startswith('<svg class="icon"'), button
        # No other Lottie hook than the declared ones.
        names = set(re.findall(r'data-lottie="([a-z]+)"', html))
        assert names <= {"check", "theme"}, names
