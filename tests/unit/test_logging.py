from __future__ import annotations

import io
import json
import typing
from collections.abc import Iterator
from typing import Any

import pytest
import structlog
from pydantic import PostgresDsn, SecretStr, TypeAdapter
from pydantic_core import MultiHostUrl, Url

from osa.core.logging import (
    bind_document_id,
    bind_request_id,
    clear_context,
    configure_logging,
    get_logger,
    log_context,
)


@pytest.fixture
def stream() -> Iterator[io.StringIO]:
    buffer = io.StringIO()
    configure_logging(stream=buffer)
    yield buffer
    clear_context()


def records(stream: io.StringIO) -> list[dict[str, Any]]:
    return [json.loads(line) for line in stream.getvalue().splitlines()]


def test_json_has_required_fields_and_bound_ids(stream: io.StringIO) -> None:
    bind_request_id("req-1")
    bind_document_id("doc-1")
    get_logger().info("document_received", size=3)

    (record,) = records(stream)
    assert record["event"] == "document_received"
    assert record["level"] == "info"
    assert record["timestamp"]
    assert record["request_id"] == "req-1"
    assert record["document_id"] == "doc-1"
    assert record["size"] == 3


def test_context_manager_unbinds(stream: io.StringIO) -> None:
    with log_context(request_id="req-2"):
        get_logger().info("inside")
    get_logger().info("outside")

    inside, outside = records(stream)
    assert inside["request_id"] == "req-2"
    assert "request_id" not in outside


def test_outer_binding_survives_nested_log_context(stream: io.StringIO) -> None:
    bind_request_id("outer")
    with log_context(request_id="inner", document_id="doc-9"):
        get_logger().info("inside")
    get_logger().info("after")

    inside, after = records(stream)
    assert inside["request_id"] == "inner"
    assert inside["document_id"] == "doc-9"
    assert after["request_id"] == "outer"
    assert "document_id" not in after


def test_get_logger_is_typed() -> None:
    hints = typing.get_type_hints(get_logger)

    assert hints["return"] is structlog.typing.FilteringBoundLogger


def test_secrets_are_redacted(stream: io.StringIO) -> None:
    dsn = TypeAdapter(PostgresDsn).validate_python(
        "postgresql://osa:fictional-pass-7@db.example.test:5432/osa"
    )
    deep: Any = "fictional-deep-8"
    for _ in range(10):
        deep = {"level": [deep]}
    log = get_logger()
    log.info(
        "config",
        telegram_bot_token="fictional-token-1",
        db_password="fictional-pass-2",
        external_llm_api_key="fictional-key-3",
        wrapped=SecretStr("fictional-secret-4"),
        nested={"api_key": "fictional-key-5", "items": [SecretStr("fictional-secret-6")]},
        url=dsn,
        core_urls=[
            MultiHostUrl("postgresql://osa:fictional-pass-9@db.example.test/osa"),
            Url("https://osa:fictional-pass-10@api.example.test/"),
        ],
        deep=deep,
    )

    text = stream.getvalue()
    for leaked in (
        "token-1",
        "pass-2",
        "key-3",
        "secret-4",
        "key-5",
        "secret-6",
        "pass-7",
        "deep-8",
        "pass-9",
        "pass-10",
    ):
        assert leaked not in text
    assert records(stream)[0]["telegram_bot_token"] == "[REDACTED]"


def test_plain_string_url_passwords_are_redacted(stream: io.StringIO) -> None:
    dsn = "postgresql://osa:fictional-pass-11@db.example.test/osa"
    get_logger().info(
        f"connecting to {dsn}",
        database_url=dsn,
        nested={"targets": [("redis://:fictional-pass-12@cache.example.test:6379/0",)]},
    )

    text = stream.getvalue()
    assert "fictional-pass-11" not in text
    assert "fictional-pass-12" not in text
    (record,) = records(stream)
    assert record["database_url"] == "postgresql://osa:[REDACTED]@db.example.test/osa"
    assert record["event"] == "connecting to postgresql://osa:[REDACTED]@db.example.test/osa"
    assert record["nested"]["targets"][0][0] == "redis://:[REDACTED]@cache.example.test:6379/0"


def test_url_passwords_in_exception_text_are_redacted(stream: io.StringIO) -> None:
    try:
        raise ConnectionError("cannot reach postgresql://osa:fictional-pass-13@db.example.test/osa")
    except ConnectionError:
        get_logger().exception("db_failed")

    text = stream.getvalue()
    assert "fictional-pass-13" not in text
    (record,) = records(stream)
    assert "postgresql://osa:[REDACTED]@db.example.test/osa" in record["exception"]


def test_url_without_password_is_unchanged(stream: io.StringIO) -> None:
    get_logger().info("ok", url="http://127.0.0.1:11434/api", other="a:b@c")

    (record,) = records(stream)
    assert record["url"] == "http://127.0.0.1:11434/api"
    assert record["other"] == "a:b@c"


MULTI_HOST_DSN = (
    "postgresql://alice:fictional-first@db1.test:5432,bob:fictional-second@db2.test:5432/db"
)
# Fail-closed: everything between the first ``:`` and the last ``@`` of the authority is
# masked, so intermediate hosts of a multi-host DSN are hidden too (accepted trade-off).
MULTI_HOST_MASKED = "postgresql://alice:[REDACTED]@db2.test:5432/db"


def test_multi_host_dsn_passwords_are_redacted_in_field_and_event(stream: io.StringIO) -> None:
    from osa.core.settings import Settings

    settings = Settings(database_url=MULTI_HOST_DSN)  # type: ignore[arg-type]
    get_logger().info(f"connecting to {settings.database_url}", dsn=str(settings.database_url))

    text = stream.getvalue()
    assert "fictional-first" not in text
    assert "fictional-second" not in text
    (record,) = records(stream)
    assert record["dsn"] == MULTI_HOST_MASKED
    assert record["event"] == f"connecting to {MULTI_HOST_MASKED}"


def test_multi_host_dsn_passwords_are_redacted_in_exception_text(stream: io.StringIO) -> None:
    try:
        raise ConnectionError(f"cannot reach {MULTI_HOST_DSN}")
    except ConnectionError:
        get_logger().exception("db_failed")

    text = stream.getvalue()
    assert "fictional-first" not in text
    assert "fictional-second" not in text
    (record,) = records(stream)
    assert MULTI_HOST_MASKED in record["exception"]


def test_three_hosts_with_mixed_credentials(stream: io.StringIO) -> None:
    dsn = "postgresql://a:fictional-p1@h1.test,h2.test:5433,c:fictional-p3@h3.test/db?x=1"
    get_logger().info("mixed", dsn=dsn, user_only="postgresql://u@h1.test,v:fictional-p4@h2.test")

    text = stream.getvalue()
    for secret in ("fictional-p1", "fictional-p3", "fictional-p4"):
        assert secret not in text
    (record,) = records(stream)
    assert record["dsn"] == "postgresql://a:[REDACTED]@h3.test/db?x=1"
    assert record["user_only"] == "postgresql://u@h1.test,v:[REDACTED]@h2.test"


COMMA_PASSWORD_URL = "https://alice:fictional-first,fictional-second@api.example.test/"
COMMA_PASSWORD_MASKED = "https://alice:[REDACTED]@api.example.test/"


def test_password_with_comma_is_redacted_in_field(stream: io.StringIO) -> None:
    get_logger().info("calling", url=COMMA_PASSWORD_URL)

    text = stream.getvalue()
    assert "fictional-first" not in text
    assert "fictional-second" not in text
    (record,) = records(stream)
    assert record["url"] == COMMA_PASSWORD_MASKED


def test_password_with_comma_is_redacted_in_exception_text(stream: io.StringIO) -> None:
    try:
        raise ConnectionError(f"cannot reach {COMMA_PASSWORD_URL}")
    except ConnectionError:
        get_logger().exception("api_failed")

    text = stream.getvalue()
    assert "fictional-first" not in text
    assert "fictional-second" not in text
    (record,) = records(stream)
    assert COMMA_PASSWORD_MASKED in record["exception"]


@pytest.mark.parametrize(
    ("url", "masked"),
    [
        ("postgresql://u:fictional-p@ss@h.test/db", "postgresql://u:[REDACTED]@h.test/db"),
        ("redis://:fictional-a,b@cache.test:6379/0", "redis://:[REDACTED]@cache.test:6379/0"),
    ],
)
def test_unencoded_at_and_comma_in_password_are_redacted(
    stream: io.StringIO, url: str, masked: str
) -> None:
    get_logger().info("ok", url=url)

    text = stream.getvalue()
    assert "fictional-" not in text
    (record,) = records(stream)
    assert record["url"] == masked


@pytest.mark.parametrize(
    "url",
    [
        "http://host.test:8080/x",
        "http://user@host.test/",
        "postgresql://db1,db2/db",
        "postgresql://db1.test:5432,db2.test:5432/db",
        "postgresql://alice@db1.test,bob@db2.test/db",
        "https://api.example.test/path?q=a:b@c#frag",
        "http://127.0.0.1:11434/api",
    ],
)
def test_urls_without_password_are_unchanged(stream: io.StringIO, url: str) -> None:
    get_logger().info("ok", url=url)

    (record,) = records(stream)
    assert record["url"] == url
