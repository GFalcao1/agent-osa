"""Integration fixtures: a disposable PostgreSQL given by OSA_TEST_DATABASE_URL.

The portable root fixture clears ``OSA_*`` and blocks sockets for every test, so the
URL is captured at import time and the real socket functions are restored here, only
for tests under ``tests/integration``.
"""

from __future__ import annotations

import os
import re
import secrets
import socket
from collections.abc import Callable, Iterator
from contextlib import ExitStack
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import Engine, create_engine
from sqlalchemy.engine import URL, make_url
from sqlalchemy.exc import ArgumentError
from sqlalchemy.orm import Session

ENV_NAME = "OSA_TEST_DATABASE_URL"
_RAW_URL = os.environ.get(ENV_NAME)
_REAL_CREATE_CONNECTION = socket.create_connection
_REAL_CONNECT = socket.socket.connect
_REAL_GETADDRINFO = socket.getaddrinfo
_ROOT = Path(__file__).resolve().parents[2]
_MIGRATION_DB = re.compile(r"^osa_test_mig_[0-9a-f]{12}$")
_TEST_DB = re.compile(r"^osa_test(_[0-9a-z_]+)?$")


def pytest_collection_modifyitems(items: list[pytest.Item]) -> None:
    here = Path(__file__).resolve().parent
    for item in items:
        if here in Path(str(item.fspath)).resolve().parents:
            item.add_marker(pytest.mark.integration)


_CONNECT_KEYS = frozenset({"host", "port", "user", "password", "dbname"})


def _refuse(reason: str) -> ValueError:
    # Never include the URL: it carries the password.
    return ValueError(f"refusing to run: {reason} ({ENV_NAME})")


def checked_test_url(raw: str | URL, database: re.Pattern[str] = _TEST_DB) -> URL:
    """The test URL, only if every effective connection target is the disposable DB.

    No query parameters at all: libpq would let ``?dbname=``, ``?host=``, ``?service=``
    and friends override the path. The dialect's own connect arguments are checked
    too, so what psycopg receives is what was validated. Builds no connection.
    """
    try:
        url = make_url(raw)
    except (ArgumentError, ValueError):
        raise _refuse("unparseable database URL") from None
    if url.drivername in {"postgres", "postgresql"}:
        url = url.set(drivername="postgresql+psycopg")
    if url.drivername != "postgresql+psycopg":
        raise _refuse("database URL must use postgresql")
    if url.query:
        raise _refuse("database URL must not carry query parameters")
    if not url.host:
        raise _refuse("database URL must name a host")
    if not database.fullmatch(url.database or ""):
        raise _refuse("database name must be osa_test or osa_test_<suffix>")
    _, kwargs = url.get_dialect()().create_connect_args(url)
    if (
        set(kwargs) - _CONNECT_KEYS
        or kwargs.get("dbname") != url.database
        or kwargs.get("host") != url.host
    ):
        raise _refuse("effective connection arguments differ from the validated URL")
    return url


def _alembic_config(url: URL) -> Config:
    config = Config(str(_ROOT / "alembic.ini"))
    config.attributes["url"] = url.render_as_string(hide_password=False)
    return config


@pytest.fixture(scope="session")
def test_url_guard() -> Callable[[str], URL]:
    return checked_test_url


@pytest.fixture(scope="session")
def alembic_config() -> Callable[[URL], Config]:
    return _alembic_config


@pytest.fixture(autouse=True)
def real_sockets(isolate_osa_environment: None, monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """Undo the portable network block; depends on it so the order is guaranteed."""
    monkeypatch.setattr(socket, "create_connection", _REAL_CREATE_CONNECTION)
    monkeypatch.setattr(socket.socket, "connect", _REAL_CONNECT)
    monkeypatch.setattr(socket, "getaddrinfo", _REAL_GETADDRINFO)
    yield


@pytest.fixture(scope="session")
def test_url() -> URL:
    if not _RAW_URL:
        pytest.skip(f"{ENV_NAME} is not set; the integration suite needs a disposable PostgreSQL")
    try:
        return checked_test_url(_RAW_URL)
    except ValueError as error:
        pytest.exit(str(error), 2)


@pytest.fixture(scope="session")
def engine(test_url: URL) -> Iterator[Engine]:
    engine = create_engine(test_url, pool_pre_ping=True)
    command.upgrade(_alembic_config(test_url), "head")
    yield engine
    engine.dispose()


@pytest.fixture
def session(engine: Engine) -> Iterator[Session]:
    """Session on one outer transaction; everything is rolled back after the test.

    Tables are never truncated: the audit trigger forbids it.
    """
    connection = engine.connect()
    outer = connection.begin()
    db_session = Session(bind=connection, join_transaction_mode="create_savepoint")
    try:
        yield db_session
    finally:
        db_session.close()
        outer.rollback()
        connection.close()


@pytest.fixture
def empty_database(test_url: URL) -> Iterator[URL]:
    """A brand-new empty database created by this fixture and dropped afterwards."""
    name = f"osa_test_mig_{secrets.token_hex(6)}"
    assert _MIGRATION_DB.fullmatch(name)
    url = checked_test_url(test_url.set(database=name), _MIGRATION_DB)
    with ExitStack() as stack:
        admin = create_engine(test_url, isolation_level="AUTOCOMMIT")
        stack.callback(admin.dispose)
        with admin.connect() as conn:
            conn.exec_driver_sql(f'CREATE DATABASE "{name}"')

        def drop() -> None:
            assert _MIGRATION_DB.fullmatch(name)
            with admin.connect() as conn:
                conn.exec_driver_sql(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)')

        stack.callback(drop)  # runs before admin.dispose (LIFO)
        probe = create_engine(url)
        try:
            with probe.connect() as conn:
                tables = conn.exec_driver_sql(
                    "SELECT count(*) FROM information_schema.tables "
                    "WHERE table_schema NOT IN ('pg_catalog', 'information_schema')"
                ).scalar_one()
        finally:
            probe.dispose()
        assert tables == 0, "migration database must start empty"
        yield url
