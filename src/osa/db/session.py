"""Lazy engine and session factory built from Settings; nothing connects on import."""

from __future__ import annotations

from functools import lru_cache

from sqlalchemy import Engine, create_engine
from sqlalchemy.engine import URL, make_url
from sqlalchemy.exc import ArgumentError
from sqlalchemy.orm import Session, sessionmaker

from osa.core.settings import Settings

_DRIVER = "postgresql+psycopg"
_PLAIN_SCHEMES = frozenset({"postgres", "postgresql"})
# SQLAlchemy masks only the userinfo password; these would show up in str(url).
_SECRET_QUERY_KEYS = frozenset({"password", "sslpassword"})


class DatabaseNotConfigured(RuntimeError):
    """OSA_DATABASE_URL is not set; the message never carries connection data."""


def database_url(settings: Settings | None = None) -> URL:
    """SQLAlchemy URL for the configured database, forced onto the psycopg 3 driver."""
    settings = settings or Settings()
    if settings.database_url is None:
        raise DatabaseNotConfigured("database is not configured: set OSA_DATABASE_URL")
    try:
        url = make_url(str(settings.database_url))
    except (ArgumentError, ValueError):
        # The original error repeats the URL (with its password); never chain it.
        raise DatabaseNotConfigured(
            "OSA_DATABASE_URL is not a valid single-host PostgreSQL URL"
        ) from None
    if _SECRET_QUERY_KEYS & {key.lower() for key in url.query}:
        raise DatabaseNotConfigured(
            "OSA_DATABASE_URL must carry the password in the userinfo, not in the query"
        )
    if url.drivername in _PLAIN_SCHEMES:
        url = url.set(drivername=_DRIVER)
    return url


def make_engine(url: URL | str) -> Engine:
    """Engine with pre-ping; creating it opens no connection."""
    return create_engine(url, pool_pre_ping=True)


@lru_cache(maxsize=1)
def get_engine() -> Engine:
    return make_engine(database_url())


@lru_cache(maxsize=1)
def get_sessionmaker() -> sessionmaker[Session]:
    return sessionmaker(get_engine(), expire_on_commit=False)
