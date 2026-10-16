from __future__ import annotations

from collections.abc import Callable, Iterator
from contextlib import contextmanager
from datetime import date

import pytest
from alembic import command
from alembic.autogenerate import compare_metadata
from alembic.config import Config
from alembic.migration import MigrationContext
from alembic.script import ScriptDirectory
from sqlalchemy import Engine, create_engine, insert, inspect, select, text
from sqlalchemy.engine import URL
from sqlalchemy.exc import DBAPIError, IntegrityError
from sqlalchemy.orm import Session

from osa.db.audit import record_event
from osa.db.models import (
    ArchiveOperation,
    AuditEvent,
    Base,
    Department,
    Document,
    Owner,
    OwnerAlias,
    ReviewCase,
    Root,
    User,
    WatchedFolder,
)
from osa.domain.document import DocumentState

EXPECTED_TABLES = {
    "users",
    "owners",
    "owner_aliases",
    "departments",
    "roots",
    "watched_folders",
    "documents",
    "archive_operations",
    "review_cases",
    "audit_events",
}
SHA_A = "a" * 64
SHA_B = "b" * 64


def make_cnpj(base12: str) -> str:
    def digit(digits: str, weights: list[int]) -> str:
        rest = sum(int(d) * w for d, w in zip(digits, weights, strict=True)) % 11
        return "0" if rest < 2 else str(11 - rest)

    first = digit(base12, [5, 4, 3, 2, 9, 8, 7, 6, 5, 4, 3, 2])
    second = digit(base12 + first, [6, 5, 4, 3, 2, 9, 8, 7, 6, 5, 4, 3, 2])
    return base12 + first + second


CNPJ = make_cnpj("445556660001")


def masked(c: str) -> str:
    return f"{c[:2]}.{c[2:5]}.{c[5:8]}/{c[8:12]}-{c[12:]}"


@contextmanager
def rejected(
    session: Session, error: type[Exception] = IntegrityError, match: str | None = None
) -> Iterator[None]:
    """Expect a DB error; the failing statement is confined to a savepoint."""
    with pytest.raises(error, match=match):
        with session.begin_nested():
            yield


def owner(session: Session, name: str = "Alfa", pasta: str = "ALFA", **kw: object) -> Owner:
    obj = Owner(nome_fantasia=name, pasta=pasta, **kw)
    session.add(obj)
    session.flush()
    return obj


def document(session: Session, sha: str = SHA_A, **kw: object) -> Document:
    values: dict[str, object] = {"sha256": sha, "origem": "telegram"}
    values.update(kw)
    obj = Document(**values)
    session.add(obj)
    session.flush()
    return obj


# --- test-database guard (offline: builds connect args, never connects) -------------

GuardFn = Callable[[str], URL]
_FAKE = "postgresql://osa:fictional@127.0.0.1:55432"


@pytest.mark.parametrize(
    "suffix",
    [
        "/osa_test?dbname=production",
        "/osa_test?host=prod.example.invalid",
        "/osa_test?hostaddr=10.0.0.1",
        "/osa_test?port=5433",
        "/osa_test?service=production",
        "/osa_test?user=postgres",
        "/osa_test?options=-csearch_path%3Dprod",
        "/osa_test?sslmode=disable",
        "/production",
        "/osa_testing",
        "/latest_test",
        "",
    ],
)
def test_guard_rejects_unsafe_test_urls(test_url_guard: GuardFn, suffix: str) -> None:
    with pytest.raises(ValueError, match="refusing"):
        test_url_guard(_FAKE + suffix)


def test_guard_rejects_url_without_host(test_url_guard: GuardFn) -> None:
    with pytest.raises(ValueError, match="refusing"):
        test_url_guard("postgresql://osa:fictional@/osa_test")


@pytest.mark.parametrize("name", ["osa_test", "osa_test_mig_0123456789ab"])
def test_guard_accepts_plain_test_url(test_url_guard: GuardFn, name: str) -> None:
    url = test_url_guard(f"{_FAKE}/{name}")
    assert url.database == name
    assert url.drivername == "postgresql+psycopg"


def test_guard_error_never_echoes_the_password(test_url_guard: GuardFn) -> None:
    with pytest.raises(ValueError) as excinfo:
        test_url_guard(_FAKE + "/osa_test?dbname=production")
    assert "fictional" not in str(excinfo.value)


def test_all_tables_exist(engine: Engine) -> None:
    assert EXPECTED_TABLES <= set(inspect(engine).get_table_names())


def test_models_match_migrated_schema(engine: Engine) -> None:
    with engine.connect() as conn:
        diff = compare_metadata(MigrationContext.configure(conn), Base.metadata)
    assert diff == []


def test_database_constraint_names_match_models(engine: Engine) -> None:
    """Every PK, UQ, FK and CHECK in the database carries the name the models declare."""
    expected = {
        str(c.name)
        for table in Base.metadata.tables.values()
        for c in table.constraints
        if c.name is not None
    }
    with engine.connect() as conn:
        actual = set(
            conn.exec_driver_sql(
                "SELECT conname FROM pg_constraint c JOIN pg_namespace n "
                "ON n.oid = c.connamespace WHERE n.nspname = 'public' "
                "AND c.conrelid <> 'alembic_version'::regclass"
            ).scalars()
        )
    assert actual == expected


def test_foreign_key_columns_used_in_filters_are_indexed(engine: Engine) -> None:
    indexes = {
        (table, tuple(ix["column_names"]))
        for table in ("documents", "users")
        for ix in inspect(engine).get_indexes(table)
    }
    assert ("documents", ("departamento_id",)) in indexes
    assert ("documents", ("owner_id",)) in indexes
    assert ("users", ("departamento_id",)) in indexes


def test_single_alembic_head(test_url: URL, alembic_config: Callable[[URL], Config]) -> None:
    assert len(ScriptDirectory.from_config(alembic_config(test_url)).get_heads()) == 1


# --- uniqueness ---------------------------------------------------------------------


def test_sha256_is_unique(session: Session) -> None:
    document(session)
    with rejected(session):
        document(session)


def test_telegram_id_is_unique(session: Session) -> None:
    session.add(User(telegram_id=9_000_000_001, nome="Ana", papel="admin"))
    session.flush()
    with rejected(session):
        session.add(User(telegram_id=9_000_000_001, nome="Bia", papel="admin"))
        session.flush()


def test_telegram_id_holds_values_above_int32(session: Session) -> None:
    user = User(telegram_id=7_000_000_000_000, nome="Ana", papel="admin")
    session.add(user)
    session.flush()
    assert session.get(User, user.id).telegram_id == 7_000_000_000_000  # type: ignore[union-attr]


def test_cnpj_with_and_without_mask_collide(session: Session) -> None:
    owner(session, pasta="A", cnpj=masked(CNPJ))
    with rejected(session):
        owner(session, name="Beta", pasta="B", cnpj=CNPJ)


def test_cnpj_is_stored_normalized(session: Session) -> None:
    obj = owner(session, cnpj=masked(CNPJ))
    assert obj.cnpj == CNPJ


def test_invalid_cnpj_is_refused_before_insert(session: Session) -> None:
    wrong = CNPJ[:-1] + str((int(CNPJ[-1]) + 1) % 10)
    with pytest.raises(ValueError, match="CNPJ"):
        Owner(nome_fantasia="Alfa", pasta="ALFA", cnpj=wrong)


def test_many_owners_without_cnpj_are_allowed(session: Session) -> None:
    owner(session, name="Alfa", pasta="A")
    owner(session, name="Beta", pasta="B")


def test_alias_variants_collide(session: Session) -> None:
    dono = owner(session)
    session.add(OwnerAlias(owner_id=dono.id, alias="Alfa Ltda."))
    session.flush()
    for variant in ("ALFA LTDA", "alfa ltda"):
        with rejected(session):
            session.add(OwnerAlias(owner_id=dono.id, alias=variant))
            session.flush()


def test_alias_is_stored_normalized_and_unique_across_owners(session: Session) -> None:
    first = owner(session, name="Alfa", pasta="A")
    second = owner(session, name="Beta", pasta="B")
    alias = OwnerAlias(owner_id=first.id, alias="  Açaí & Cia. ")
    session.add(alias)
    session.flush()
    assert alias.alias == "acai cia"
    with rejected(session):
        session.add(OwnerAlias(owner_id=second.id, alias="ACAI CIA"))
        session.flush()


# Raw Core/SQL bypasses the ORM validators; the CHECKs keep the keys canonical anyway.


@pytest.mark.parametrize(
    "alias", ["Alfa Ltda.", "ALFA LTDA", "alfa  ltda", " alfa", "alfa\n", "açaí"]
)
def test_raw_insert_of_non_normalized_alias_is_rejected(session: Session, alias: str) -> None:
    dono = owner(session)
    with rejected(session, match="ck_owner_aliases_alias_normalized"):
        session.execute(insert(OwnerAlias.__table__).values(owner_id=dono.id, alias=alias))


def test_raw_insert_of_normalized_alias_is_accepted(session: Session) -> None:
    dono = owner(session)
    session.execute(insert(OwnerAlias.__table__).values(owner_id=dono.id, alias="alfa ltda"))


def _raw_owner(session: Session, cnpj: str) -> None:
    session.execute(insert(Owner.__table__).values(nome_fantasia="Alfa", pasta="ALFA", cnpj=cnpj))


@pytest.mark.parametrize(
    "cnpj", [f"{CNPJ[:8]}/{CNPJ[8:13]}", CNPJ[:13], CNPJ[:13] + "A", f" {CNPJ[:13]}"]
)
def test_raw_insert_of_non_normalized_cnpj_is_rejected(session: Session, cnpj: str) -> None:
    assert len(cnpj) <= 14  # fits VARCHAR(14): only the CHECK can stop it
    with rejected(session, match="ck_owners_cnpj_digits"):
        _raw_owner(session, cnpj)


def test_raw_insert_of_fully_masked_cnpj_is_rejected(session: Session) -> None:
    with rejected(session, DBAPIError):  # 18 chars: VARCHAR(14) rejects it first
        _raw_owner(session, masked(CNPJ))


def test_owner_pasta_is_globally_unique(session: Session) -> None:
    owner(session, name="Alfa", pasta="SAME")
    with rejected(session):
        owner(session, name="Beta", pasta="SAME")


def test_department_name_and_folder_are_unique(session: Session) -> None:
    session.add(Department(nome="Fiscal", pasta="01 Fiscal"))
    session.flush()
    with rejected(session):
        session.add(Department(nome="Outro", pasta="01 Fiscal"))
        session.flush()
    with rejected(session):
        session.add(Department(nome="Fiscal", pasta="02 Outro"))
        session.flush()


def test_root_path_and_watched_folder_are_unique(session: Session) -> None:
    root = Root(caminho="/srv/acervo-ficticio")
    session.add(root)
    session.flush()
    with rejected(session):
        session.add(Root(caminho="/srv/acervo-ficticio"))
        session.flush()
    session.add(WatchedFolder(root_id=root.id, caminho="entrada"))
    session.flush()
    with rejected(session):
        session.add(WatchedFolder(root_id=root.id, caminho="entrada"))
        session.flush()


# --- vocabulary ---------------------------------------------------------------------


@pytest.mark.parametrize("state", list(DocumentState))
def test_every_spec_state_is_accepted(session: Session, state: DocumentState) -> None:
    assert document(session, estado=state.value).estado == state.value


def test_document_defaults(session: Session) -> None:
    doc = document(session)
    session.refresh(doc)
    assert doc.estado == "DETECTADO"
    assert doc.tags == []
    assert doc.datas == []
    assert doc.criado_em is not None and doc.atualizado_em is not None
    assert doc.owner_id is None and doc.departamento_id is None and doc.caminho is None


def test_unknown_state_is_rejected(session: Session) -> None:
    with rejected(session):
        document(session, estado="PENDENTE")


def test_origin_pasta_is_rejected_and_watched_accepted(session: Session) -> None:
    with rejected(session):
        document(session, origem="pasta")
    for index, origin in enumerate(("telegram", "watched", "scan")):
        document(session, sha=f"{index:064x}", origem=origin)


@pytest.mark.parametrize("sha", ["abc", "A" * 64, "g" * 64, "a" * 63])
def test_sha256_must_be_lowercase_hex(session: Session, sha: str) -> None:
    with rejected(session):
        document(session, sha=sha)


def test_arrays_round_trip(session: Session) -> None:
    doc = document(session, tags=["nota", "2026"], datas=[date(2026, 1, 31)])
    session.expire(doc)
    assert doc.tags == ["nota", "2026"]
    assert doc.datas == [date(2026, 1, 31)]


def test_role_is_restricted(session: Session) -> None:
    depto = Department(nome="Fiscal", pasta="01 Fiscal")
    session.add(depto)
    session.flush()
    with rejected(session, match="ck_users_papel"):
        session.add(User(telegram_id=1, nome="X", papel="root", departamento_id=depto.id))
        session.flush()


def test_admin_may_have_no_department(session: Session) -> None:
    session.add(User(telegram_id=2, nome="Admin bootstrap", papel="admin"))
    session.flush()


@pytest.mark.parametrize("papel", ["operator", "reviewer"])
def test_non_admin_without_department_is_rejected(session: Session, papel: str) -> None:
    with rejected(session, match="ck_users_departamento_required"):
        session.add(User(telegram_id=4, nome="Sem depto", papel=papel))
        session.flush()


@pytest.mark.parametrize("papel", ["operator", "reviewer"])
def test_non_admin_with_department_is_accepted(session: Session, papel: str) -> None:
    depto = Department(nome="Fiscal", pasta="01 Fiscal")
    session.add(depto)
    session.flush()
    session.add(User(telegram_id=5, nome="Com depto", papel=papel, departamento_id=depto.id))
    session.flush()


def test_user_department_is_checked_by_fk(session: Session) -> None:
    with rejected(session):
        session.add(User(telegram_id=3, nome="FK", papel="operator", departamento_id=-1))
        session.flush()


def test_archive_mode_and_review_state_are_restricted(session: Session) -> None:
    doc = document(session)
    session.add(
        ArchiveOperation(document_id=doc.id, origem="a", destino="b", ator="u", modo="auto")
    )
    session.add(ReviewCase(document_id=doc.id, motivo="texto insuficiente"))
    session.flush()
    with rejected(session):
        session.add(
            ArchiveOperation(document_id=doc.id, origem="a", destino="b", ator="u", modo="move")
        )
        session.flush()
    with rejected(session):
        session.add(ReviewCase(document_id=doc.id, motivo="x", estado="fechado"))
        session.flush()


def test_review_case_defaults_to_open(session: Session) -> None:
    doc = document(session)
    case = ReviewCase(document_id=doc.id, motivo="dois departamentos plausíveis")
    session.add(case)
    session.flush()
    session.refresh(case)
    assert case.estado == "aberto"
    assert case.departamento_sugerido_id is None and case.decidido_por is None


# --- audit --------------------------------------------------------------------------


def test_record_event_flushes_without_commit(
    session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    def no_commit() -> None:
        raise AssertionError("record_event must not commit; the caller owns the transaction")

    monkeypatch.setattr(session, "commit", no_commit)
    event = record_event(
        session,
        ator="telegram:1",
        acao="archive",
        alvo_tipo="document",
        alvo_id=7,
        sha256=SHA_A,
        resultado="ok",
        detalhes={"destino": "ALFA/Fiscal"},
    )
    assert event.id is not None
    assert event.alvo_id == "7"
    session.expire(event)
    assert event.detalhes == {"destino": "ALFA/Fiscal"}
    assert event.timestamp is not None


def test_record_event_refuses_unknown_result(session: Session) -> None:
    with pytest.raises(ValueError, match="resultado"):
        record_event(session, ator="x", acao="y", alvo_tipo="z", resultado="maybe")


def test_audit_rejects_update(session: Session) -> None:
    event = record_event(session, ator="x", acao="y", alvo_tipo="z", resultado="ok")
    with rejected(session, DBAPIError, "append-only"):
        session.execute(
            text("UPDATE audit_events SET ator = 'forged' WHERE id = :i"), {"i": event.id}
        )
    assert session.scalar(select(AuditEvent.ator).where(AuditEvent.id == event.id)) == "x"


def test_audit_rejects_update_through_orm(session: Session) -> None:
    event = record_event(session, ator="x", acao="y", alvo_tipo="z", resultado="ok")
    with rejected(session, DBAPIError, "append-only"):
        event.ator = "forged"
        session.flush()


def test_audit_rejects_delete(session: Session) -> None:
    event = record_event(session, ator="x", acao="y", alvo_tipo="z", resultado="ok")
    with rejected(session, DBAPIError, "append-only"):
        session.execute(text("DELETE FROM audit_events WHERE id = :i"), {"i": event.id})
    assert session.get(AuditEvent, event.id) is not None


@pytest.mark.parametrize(
    "statement",
    ["UPDATE audit_events SET ator = 'forged' WHERE false", "DELETE FROM audit_events WHERE false"],
)
def test_audit_rejects_update_and_delete_matching_no_row(session: Session, statement: str) -> None:
    with rejected(session, DBAPIError, "append-only"):
        session.execute(text(statement))


def test_audit_rejects_truncate(session: Session) -> None:
    record_event(session, ator="x", acao="y", alvo_tipo="z", resultado="ok")
    with rejected(session, DBAPIError, "append-only"):
        session.execute(text("TRUNCATE audit_events"))


# --- migration up and down -----------------------------------------------------------


def _audit_objects(url: URL) -> tuple[int, int]:
    engine = create_engine(url)
    with engine.connect() as conn:
        triggers = conn.exec_driver_sql(
            "SELECT count(*) FROM pg_trigger WHERE NOT tgisinternal"
        ).scalar_one()
        functions = conn.exec_driver_sql(
            "SELECT count(*) FROM pg_proc WHERE proname = 'audit_events_reject_mutation'"
        ).scalar_one()
    engine.dispose()
    return triggers, functions


def _user_tables(url: URL) -> set[str]:
    engine = create_engine(url)
    names = set(inspect(engine).get_table_names())
    engine.dispose()
    return names


def test_migration_up_down_up_on_empty_database(
    empty_database: URL, alembic_config: Callable[[URL], Config]
) -> None:
    config = alembic_config(empty_database)

    command.upgrade(config, "head")
    assert EXPECTED_TABLES <= _user_tables(empty_database)
    assert _audit_objects(empty_database) == (2, 1)

    # TRUNCATE is rejected even on an empty audit table.
    engine = create_engine(empty_database)
    with engine.begin() as conn, pytest.raises(DBAPIError, match="append-only"):
        conn.exec_driver_sql("TRUNCATE audit_events")
    engine.dispose()

    command.downgrade(config, "base")
    assert _user_tables(empty_database) <= {"alembic_version"}
    assert _audit_objects(empty_database) == (0, 0)

    command.upgrade(config, "head")
    assert EXPECTED_TABLES <= _user_tables(empty_database)
    assert _audit_objects(empty_database) == (2, 1)
