"""Validated, side-effect-free application configuration."""

from __future__ import annotations

from pathlib import Path
from typing import Literal

from pydantic import PostgresDsn, SecretStr, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

Environment = Literal["development", "test", "production"]
EmailProvider = Literal["development", "smtp"]


class Settings(BaseSettings):
    """Configuration loaded from explicit values, environment, then ``.env``.

    Constructing this object only validates configuration. It does not connect to
    the database, filesystem, e-mail provider, or any model provider.
    """

    model_config = SettingsConfigDict(
        env_file=".env",
        env_prefix="OSA_",
        extra="ignore",
    )

    environment: Environment = "development"
    dry_run: bool = True
    enable_file_moves: bool = False
    email_provider: EmailProvider = "development"
    allow_external_llm: bool = False

    database_url: PostgresDsn | None = None
    inbox_root: Path | None = None
    companies_root: Path | None = None

    smtp_host: str | None = None
    smtp_username: str | None = None
    smtp_password: SecretStr | None = None

    @model_validator(mode="after")
    def validate_mode_requirements(self) -> Settings:
        has_document_roots = self.inbox_root is not None or self.companies_root is not None

        if self.environment == "production" and not (
            self.inbox_root is not None and self.companies_root is not None
        ):
            raise ValueError("production requires both document roots")

        if self.environment == "test" and has_document_roots:
            raise ValueError("test environment does not accept document roots")

        if self.enable_file_moves and self.dry_run:
            raise ValueError("enable_file_moves requires dry_run=false")

        if self.enable_file_moves and not (
            self.inbox_root is not None and self.companies_root is not None
        ):
            raise ValueError("enable_file_moves requires both document roots")

        if self.email_provider == "smtp" and not (
            self.smtp_host and self.smtp_username and self.smtp_password
        ):
            raise ValueError("smtp credentials are required when email_provider=smtp")

        return self
