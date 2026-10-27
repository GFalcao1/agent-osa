"""Validated, side-effect-free application configuration (contract v1)."""

from __future__ import annotations

import os
import re
from collections.abc import Sequence
from pathlib import Path
from typing import Annotated, Any, Literal, cast
from urllib.parse import urlsplit

from dotenv import dotenv_values
from pydantic import (
    Field,
    PostgresDsn,
    SecretStr,
    StringConstraints,
    ValidationError,
    field_validator,
    model_validator,
)
from pydantic_core import InitErrorDetails, PydanticCustomError
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict
from pydantic_settings.sources import DotenvType

from osa.core.redact import scrub_url_passwords

Environment = Literal["development", "test", "production"]

ENV_PREFIX = "OSA_"

_EXTENSION = re.compile(r"^\.[a-z0-9]+$")

# Document content goes to the model server (ADR-0002, ADR-0012): only this machine qualifies.
LOOPBACK_HOSTS: frozenset[str] = frozenset({"127.0.0.1", "::1", "localhost"})


def is_loopback_url(url: str) -> bool:
    """True for an http(s) URL on 127.0.0.1, ::1 or localhost, without userinfo, query or fragment.

    The model provider re-checks every request with this, so scheme and userinfo are checked
    here too, not only in ``Settings``.
    """
    if "?" in url or "#" in url:
        return False
    try:
        parts = urlsplit(url)
        hostname = parts.hostname
        _ = parts.port
    except ValueError:
        return False
    if parts.scheme not in ("http", "https") or "@" in parts.netloc:
        return False
    return hostname in LOOPBACK_HOSTS


def _sanitized_errors(error: ValidationError) -> list[InitErrorDetails]:
    """Re-raisable error details without input, ctx, or URL passwords in msg."""
    details: list[InitErrorDetails] = []
    for item in error.errors(include_input=False, include_url=False, include_context=False):
        details.append(
            cast(
                InitErrorDetails,
                {
                    # Same type and message, rebuilt without ctx (which may hold
                    # raw parser errors) and with URL passwords masked.
                    "type": PydanticCustomError(item["type"], scrub_url_passwords(item["msg"])),
                    "loc": item["loc"],
                    "input": None,
                },
            )
        )
    return details


# Retired v0 settings. Their presence anywhere is an error: an old installation
# must not start silently under the wrong contract. Only names are reported.
LEGACY_SETTING_NAMES: frozenset[str] = frozenset(
    {
        "dry_run",
        "enable_file_moves",
        "email_provider",
        "smtp_host",
        "smtp_username",
        "smtp_password",
        "inbox_root",
        "companies_root",
        "allow_external_llm",
    }
)


class LegacySettingError(ValueError):
    """Raised when a retired v0 setting is present; carries names, never values."""


def _env_file_paths(env_file: DotenvType | None) -> list[Path]:
    if env_file is None:
        return []
    if isinstance(env_file, (str, os.PathLike)):
        return [Path(env_file)]
    if isinstance(env_file, Sequence):
        return [Path(item) for item in env_file]
    return []


def _missing(value: object) -> bool:
    """True for an unset value or a SecretStr whose secret is empty or blank."""
    if value is None:
        return True
    return isinstance(value, SecretStr) and not value.get_secret_value().strip()


def find_legacy_settings(explicit: dict[str, Any], env_file: DotenvType | None) -> list[str]:
    """Return the OSA_* names of retired settings present in any source."""
    found: set[str] = set()
    for key in explicit:
        if key.lower() in LEGACY_SETTING_NAMES:
            found.add(ENV_PREFIX + key.upper())
    sources: list[Any] = [os.environ]
    for path in _env_file_paths(env_file):
        if path.is_file():
            sources.append(dotenv_values(path))
    for source in sources:
        for key in source:
            upper = key.upper()
            if upper.startswith(ENV_PREFIX) and upper[len(ENV_PREFIX) :].lower() in (
                LEGACY_SETTING_NAMES
            ):
                found.add(upper)
    return sorted(found)


class Settings(BaseSettings):
    """Configuration loaded from explicit values, environment, then ``.env``.

    Constructing this object only validates configuration. It does not connect to
    the database, filesystem, Telegram, or any model provider.
    """

    model_config = SettingsConfigDict(
        env_file=".env",
        env_prefix=ENV_PREFIX,
        env_ignore_empty=True,
        extra="ignore",
    )

    environment: Environment = "development"
    enable_file_effects: bool = False

    database_url: PostgresDsn | None = Field(default=None, repr=False, exclude=True)
    telegram_bot_token: SecretStr | None = None
    telegram_reviewers_chat_id: int | None = None

    ollama_base_url: str = "http://127.0.0.1:11434"
    ollama_model: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1)] = (
        "gemma3:12b"
    )
    ollama_model_digest: str | None = None

    external_llm_enabled: bool = False
    external_llm_api_key: SecretStr | None = None

    poll_interval_seconds: int = Field(default=45, ge=30, le=60)
    stable_cycles: int = Field(default=2, ge=1)
    min_file_age_seconds: int = Field(default=60, ge=0)
    max_text_chars: int = Field(default=12000, ge=1)
    max_image_pages: int = Field(default=5, ge=1)
    sensitive_extensions: Annotated[tuple[str, ...], NoDecode, Field(min_length=1)] = (
        ".pfx",
        ".p12",
        ".pem",
        ".key",
        ".cer",
        ".crt",
    )
    quarantine_dir: Path | None = None
    telegram_send_limit_mb: int = Field(default=50, ge=1)

    def __init__(self, **values: Any) -> None:
        """Validate settings without retaining raw inputs in error output."""
        try:
            env_file = values.get("_env_file", self.model_config.get("env_file"))
            legacy = find_legacy_settings(values, env_file)
            if legacy:
                raise LegacySettingError(
                    "retired v0 setting(s) present: "
                    + ", ".join(legacy)
                    + "; remove them and use the v1 contract (see .env.example)"
                )
            super().__init__(**values)
        except LegacySettingError as error:
            raise ValidationError.from_exception_data(
                self.__class__.__name__,
                [
                    cast(
                        InitErrorDetails,
                        {"type": "value_error", "loc": (), "input": None, "ctx": {"error": error}},
                    )
                ],
            ) from None
        except ValidationError as error:
            raise ValidationError.from_exception_data(
                self.__class__.__name__, _sanitized_errors(error)
            ) from None

    @field_validator("sensitive_extensions", mode="before")
    @classmethod
    def parse_extensions(cls, value: object) -> object:
        if isinstance(value, str):
            value = value.split(",")
        if isinstance(value, (list, tuple)):
            items = []
            for item in value:
                if not isinstance(item, str):
                    return value
                text = item.strip().lower()
                if not text:
                    continue
                text = text if text.startswith(".") else "." + text
                if not _EXTENSION.fullmatch(text):
                    raise ValueError(
                        "sensitive_extensions items must be a dot followed by letters/digits"
                    )
                items.append(text)
            return tuple(items)
        return value

    @field_validator("ollama_base_url")
    @classmethod
    def validate_ollama_url(cls, value: str) -> str:
        # Error messages are constants: never echo the value or a parser error,
        # both of which may contain credentials.
        if not value.startswith(("http://", "https://")):
            raise ValueError("ollama_base_url must start with http:// or https://")
        try:
            parts = urlsplit(value)
            hostname = parts.hostname
            username, password = parts.username, parts.password
            _ = parts.port
        except ValueError:
            raise ValueError("ollama_base_url is not a valid URL") from None
        if username is not None or password is not None or "@" in parts.netloc:
            raise ValueError("ollama_base_url must not contain credentials")
        if not hostname:
            raise ValueError("ollama_base_url must include a host")
        if "?" in value or "#" in value:
            raise ValueError("ollama_base_url must not contain a query or a fragment")
        if hostname not in LOOPBACK_HOSTS:
            raise ValueError(
                "ollama_base_url must point to this machine (loopback: 127.0.0.1, ::1 or "
                "localhost): document content never leaves it"
            )
        return value

    @field_validator("quarantine_dir")
    @classmethod
    def validate_quarantine_dir(cls, value: Path | None) -> Path | None:
        if value is not None and not value.is_absolute():
            raise ValueError("quarantine_dir must be an absolute path")
        return value

    @model_validator(mode="after")
    def validate_mode_requirements(self) -> Settings:
        if self.environment == "production":
            missing = [
                name
                for name, value in (
                    ("database_url", self.database_url),
                    ("telegram_bot_token", self.telegram_bot_token),
                    ("quarantine_dir", self.quarantine_dir),
                )
                if _missing(value)
            ]
            if missing:
                raise ValueError("production requires: " + ", ".join(missing))
        if self.external_llm_enabled and _missing(self.external_llm_api_key):
            raise ValueError("external_llm_api_key is required when external_llm_enabled=true")
        return self
