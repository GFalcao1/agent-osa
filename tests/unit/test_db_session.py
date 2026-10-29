from __future__ import annotations

import importlib.util
import io
import os
import re
import socket
import subprocess
import sys
from pathlib import Path
from types import ModuleType

import pytest
from alembic.operations import Operations
from alembic.runtime.migration import MigrationContext
from sqlalchemy.dialects import postgresql
from sqlalchemy.engine import make_url
from sqlalchemy.schema import CreateIndex, CreateTable

from osa.core.settings import Settings
from osa.db import session as db_session
from osa.db.models import Base
from osa.domain.document import ArchiveMode, DocumentState, Origin, ReviewState
from osa.domain.owner import ALIAS_PATTERN, CNPJ_PATTERN
from osa.domain.user import Role

MIGRATION_0001 = (
    Path(__file__).resolve().parents[2] / "src/osa/db/migrations/versions/0001_v1_schema.py"
)

MIGRATION_0002 = (
    Path(__file__).resolve().parents[2]
    / "src/osa/db/migrations/versions/0002_installation_settings.py"
)

MIGRATION_0003 = (
    Path(__file__).resolve().parents[2] / "src/osa/db/migrations/versions/0003_courier.py"
)

FICTIONAL_PASSWORD = "fictional-pw-123"
URL = f"postgresql://osa:{FICTIONAL_PASSWORD}@db.example.invalid:5432/osa_fictional"


@pytest.fixture(autouse=True)
def clear_caches() -> None:
    db_session.get_engine.cache_clear()
    db_session.get_sessionmaker.cache_clear()


def test_importing_the_db_package_never_opens_a_connection() -> None:
    """With a URL configured, importing session, models and audit must not call psycopg."""
    code = (
        "import psycopg\n"
        "def boom(*a, **k): raise SystemExit('psycopg connect called on import')\n"
        "psycopg.connect = boom\n"
        "psycopg.Connection.connect = classmethod(boom)\n"
        "import osa.db.session, osa.db.models, osa.db.audit\n"
    )
    env = {**os.environ, "OSA_DATABASE_URL": URL}
    result = subprocess.run(
        [sys.executable, "-c", code], capture_output=True, text=True, check=False, env=env
    )
    assert result.returncode == 0, result.stderr


def test_missing_url_fails_clearly(monkeypatch: pytest.MonkeyPatch) -> None:
    with pytest.raises(db_session.DatabaseNotConfigured, match="OSA_DATABASE_URL"):
        db_session.get_engine()


def test_engine_uses_psycopg_driver_and_is_lazy(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OSA_DATABASE_URL", URL)
    engine = db_session.get_engine()
    assert engine.url.drivername == "postgresql+psycopg"
    assert engine.url.host == "db.example.invalid"
    assert engine.pool._pre_ping is True  # type: ignore[attr-defined]
    assert db_session.get_engine() is engine


def test_unparseable_url_fails_without_echoing_it() -> None:
    """Multi-host DSNs pass pydantic but not SQLAlchemy; the error must not leak the URL."""
    settings = Settings(
        database_url=f"postgresql://osa:{FICTIONAL_PASSWORD}@h1.invalid:1,h2.invalid:2/osa_x"  # type: ignore[arg-type]
    )
    with pytest.raises(db_session.DatabaseNotConfigured) as excinfo:
        db_session.database_url(settings)
    assert FICTIONAL_PASSWORD not in str(excinfo.value)
    assert excinfo.value.__cause__ is None
    assert excinfo.value.__suppress_context__ is True


@pytest.mark.parametrize("key", ["password", "sslpassword"])
def test_password_in_query_is_refused_without_echoing_it(key: str) -> None:
    """SQLAlchemy masks only the userinfo password; a query password would leak in str()."""
    settings = Settings(
        database_url=f"postgresql://osa@db.example.invalid/osa_x?{key}={FICTIONAL_PASSWORD}"  # type: ignore[arg-type]
    )
    with pytest.raises(db_session.DatabaseNotConfigured) as excinfo:
        db_session.database_url(settings)
    assert FICTIONAL_PASSWORD not in str(excinfo.value)
    assert excinfo.value.__cause__ is None


def test_userinfo_password_is_masked_everywhere(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OSA_DATABASE_URL", URL)
    url = db_session.database_url()
    engine = db_session.get_engine()
    assert url.password == FICTIONAL_PASSWORD
    for text in (str(url), repr(url), repr(engine), str(engine.url)):
        assert FICTIONAL_PASSWORD not in text


def test_driver_already_explicit_is_kept() -> None:
    url = db_session.database_url(
        Settings(database_url="postgresql+psycopg://u:p@h.example.invalid/osa_x")  # type: ignore[arg-type]
    )
    assert url.drivername == "postgresql+psycopg"


def test_password_never_in_repr_or_messages(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OSA_DATABASE_URL", URL)
    engine = db_session.get_engine()
    assert FICTIONAL_PASSWORD not in repr(engine)
    assert FICTIONAL_PASSWORD not in str(engine.url)
    assert FICTIONAL_PASSWORD not in repr(db_session.get_sessionmaker())
    assert make_url(URL).password == FICTIONAL_PASSWORD  # sanity: the fixture has one


def test_sessionmaker_does_not_connect(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OSA_DATABASE_URL", URL)
    factory = db_session.get_sessionmaker()
    with factory() as session:  # opening a session must not touch the network
        assert session.in_transaction() is False


def test_portable_tests_keep_python_sockets_blocked() -> None:
    """The integration suite restores sockets only for itself.

    This covers Python's socket module only; psycopg talks through libpq (C) and is
    not intercepted, so portable tests must never call ``connect`` on an engine.
    """
    with pytest.raises(AssertionError, match="network access is disabled"):
        socket.create_connection(("127.0.0.1", 9))


# --- migration 0001, rendered offline (no database, no env.py) ---------------------


def _load_migration(path: Path, name: str) -> ModuleType:
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _load_0001() -> ModuleType:
    return _load_migration(MIGRATION_0001, "migration_0001")


def _load_0002() -> ModuleType:
    return _load_migration(MIGRATION_0002, "migration_0002")


def _load_0003() -> ModuleType:
    return _load_migration(MIGRATION_0003, "migration_0003")


def _migration_ddl() -> str:
    """DDL of 0001 to 0003 with a bare context: no target_metadata, no naming convention."""
    buffer = io.StringIO()
    context = MigrationContext.configure(
        dialect_name="postgresql", opts={"as_sql": True, "output_buffer": buffer}
    )
    with Operations.context(context):
        _load_0001().upgrade()
        _load_0002().upgrade()
        _load_0003().upgrade()
    return buffer.getvalue()


_ADDED = re.compile(r"^ALTER TABLE \w+ ADD (CONSTRAINT .*)$")


def _clauses(ddl: str) -> set[str]:
    """Constraint and index clauses, one per line, without trailing commas.

    A constraint added by a later migration (``ALTER TABLE ... ADD CONSTRAINT``) is read
    as the clause it adds, so it compares with the inline clause of ``CREATE TABLE``.
    """
    lines = (line.strip().rstrip(",").rstrip(";").strip() for line in ddl.splitlines())
    keep = ("CONSTRAINT ", "PRIMARY KEY", "UNIQUE", "FOREIGN KEY", "CHECK", "CREATE INDEX")
    found = set()
    for line in lines:
        added = _ADDED.match(line)
        if added:
            line = added.group(1)
        if line.startswith(keep):
            found.add(line)
    return found


def _models_ddl() -> str:
    dialect = postgresql.dialect()  # type: ignore[no-untyped-call]
    parts: list[str] = []
    for table in Base.metadata.sorted_tables:
        parts.append(str(CreateTable(table).compile(dialect=dialect)))
        parts.extend(str(CreateIndex(index).compile(dialect=dialect)) for index in table.indexes)
    return "\n".join(parts)


def test_migration_names_every_constraint_without_env_naming_convention() -> None:
    clauses = _clauses(_migration_ddl())
    unnamed = {
        c for c in clauses if c.startswith(("PRIMARY KEY", "UNIQUE", "FOREIGN KEY", "CHECK"))
    }
    assert unnamed == set()
    names = re.findall(r"CONSTRAINT (\w+) ", "\n".join(clauses))
    assert all(re.fullmatch(r"(pk|uq|fk|ck)_\w+", name) for name in names), names


def test_migration_constraints_and_indexes_match_models() -> None:
    """Names, CHECK expressions and indexes of 0001 to 0003 equal what the models declare."""
    assert _clauses(_migration_ddl()) == _clauses(_models_ddl())


def test_migration_vocabularies_match_domain_enums() -> None:
    """Fails on drift: a new enum value needs a new migration that widens the CHECK."""
    migration = _load_0001()
    assert set(migration.STATES) == {s.value for s in DocumentState}
    assert set(migration.ORIGINS) == {o.value for o in Origin}
    assert set(migration.ROLES) == {r.value for r in Role}
    assert set(migration.MODES) == {m.value for m in ArchiveMode}
    assert set(migration.REVIEW_STATES) == {s.value for s in ReviewState}
    assert migration.ALIAS_PATTERN == ALIAS_PATTERN
    assert migration.CNPJ_PATTERN == CNPJ_PATTERN
    assert _load_0003().SHA256 == migration.SHA256


def test_migration_0003_adds_the_courier_columns_and_nothing_else() -> None:
    """The description of a department and the identity of an archive operation."""
    buffer = io.StringIO()
    context = MigrationContext.configure(
        dialect_name="postgresql", opts={"as_sql": True, "output_buffer": buffer}
    )
    with Operations.context(context):
        _load_0003().upgrade()
    ddl = buffer.getvalue()
    statements = [line.strip() for line in ddl.splitlines() if line.strip().startswith("ALTER")]
    assert statements == [
        "ALTER TABLE departments ADD COLUMN descricao TEXT;",
        "ALTER TABLE archive_operations ADD COLUMN root_id INTEGER NOT NULL;",
        "ALTER TABLE archive_operations ADD COLUMN sha256 VARCHAR(64) NOT NULL;",
        "ALTER TABLE archive_operations ADD CONSTRAINT fk_archive_operations_root_id_roots "
        "FOREIGN KEY(root_id) REFERENCES roots (id);",
        "ALTER TABLE archive_operations ADD CONSTRAINT ck_archive_operations_sha256_hex "
        "CHECK (sha256 ~ '^[0-9a-f]{64}$');",
    ]
