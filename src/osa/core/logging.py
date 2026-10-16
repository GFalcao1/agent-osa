"""Structured JSON logging with request/document context and secret redaction."""

from __future__ import annotations

import logging
import re
import sys
from collections.abc import Iterator, Mapping, MutableMapping, Sequence
from contextlib import contextmanager
from typing import Any, cast

import structlog
from pydantic import AnyUrl, SecretStr
from pydantic_core import MultiHostUrl, Url
from structlog.contextvars import (
    bind_contextvars,
    bound_contextvars,
    merge_contextvars,
    unbind_contextvars,
)
from structlog.typing import FilteringBoundLogger

from osa.core.redact import REDACTED, scrub_url_passwords

_SENSITIVE_KEY = re.compile(r"token|password|passwd|secret|api[_-]?key|authorization", re.I)
_MAX_DEPTH = 6


def _scrub(value: Any, depth: int = 0) -> Any:
    if isinstance(value, str):
        return scrub_url_passwords(value)
    if depth > _MAX_DEPTH:
        return REDACTED if isinstance(value, (Mapping, list, tuple)) else value
    if isinstance(value, Mapping):
        return {k: _scrub(v, depth + 1) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_scrub(v, depth + 1) for v in value]
    return value


def _is_parsed_url(value: object) -> bool:
    """Pydantic URL objects (core types and their wrappers, e.g. PostgresDsn)."""
    if isinstance(value, (Url, MultiHostUrl, AnyUrl)):
        return True
    # pydantic>=2.10 wraps multi-host DSNs around a core URL without a public base.
    return isinstance(getattr(value, "_url", None), (Url, MultiHostUrl))


def _redact(value: Any, depth: int = 0) -> Any:
    # Parsed URLs (e.g. database_url DSNs) may carry credentials in userinfo.
    if isinstance(value, SecretStr) or _is_parsed_url(value):
        return REDACTED
    if depth > _MAX_DEPTH:
        # Too deep to inspect safely: never emit an uninspected container.
        return REDACTED if isinstance(value, (Mapping, list, tuple)) else value
    if isinstance(value, Mapping):
        return {
            k: REDACTED
            if isinstance(k, str) and _SENSITIVE_KEY.search(k)
            else _redact(v, depth + 1)
            for k, v in value.items()
        }
    if isinstance(value, (list, tuple)):
        return [_redact(v, depth + 1) for v in value]
    return value


def redact_secrets(
    logger: object, method_name: str, event_dict: MutableMapping[str, Any]
) -> MutableMapping[str, Any]:
    """Structlog processor: mask SecretStr values and token/password/api_key keys."""
    del logger, method_name
    for key in list(event_dict):
        if isinstance(key, str) and _SENSITIVE_KEY.search(key):
            event_dict[key] = REDACTED
        else:
            event_dict[key] = _redact(event_dict[key])
    return event_dict


def scrub_url_credentials(
    logger: object, method_name: str, event_dict: MutableMapping[str, Any]
) -> MutableMapping[str, Any]:
    """Structlog processor: mask URL userinfo passwords in every string value.

    Runs after exception formatting so the rendered ``exception`` text is covered.
    """
    del logger, method_name
    for key in list(event_dict):
        event_dict[key] = _scrub(event_dict[key])
    return event_dict


def configure_logging(level: int | str = "INFO", stream: Any = None) -> None:
    """Configure structlog to emit one JSON object per line (default: stderr)."""
    structlog.configure(
        processors=[
            merge_contextvars,
            structlog.processors.add_log_level,
            structlog.processors.TimeStamper(fmt="iso", utc=True),
            structlog.processors.format_exc_info,
            redact_secrets,
            scrub_url_credentials,
            structlog.processors.JSONRenderer(),
        ],
        wrapper_class=structlog.make_filtering_bound_logger(
            logging.getLevelNamesMapping()[level.upper()] if isinstance(level, str) else level
        ),
        logger_factory=structlog.PrintLoggerFactory(file=stream or sys.stderr),
        cache_logger_on_first_use=False,
    )


def get_logger(name: str | None = None) -> FilteringBoundLogger:
    return cast(FilteringBoundLogger, structlog.get_logger(name))


def bind_request_id(request_id: str) -> None:
    bind_contextvars(request_id=request_id)


def bind_document_id(document_id: str) -> None:
    bind_contextvars(document_id=document_id)


def clear_context(keys: Sequence[str] = ("request_id", "document_id")) -> None:
    unbind_contextvars(*keys)


@contextmanager
def log_context(**values: str) -> Iterator[None]:
    """Bind context (e.g. request_id, document_id) for the duration of a block."""
    with bound_contextvars(**values):
        yield
