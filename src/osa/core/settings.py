"""Validated, side-effect-free application configuration."""

from __future__ import annotations

from typing import Any, Literal, cast

from pydantic import Field, PostgresDsn, SecretStr, ValidationError, model_validator
from pydantic_core import InitErrorDetails
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

    database_url: PostgresDsn | None = Field(default=None, repr=False)
    # These fields exist only to reject retired settings explicitly. They must
    # never authorize access to a document location.
    inbox_root: str | None = None
    companies_root: str | None = None

    smtp_host: str | None = None
    smtp_username: str | None = None
    smtp_password: SecretStr | None = None

    def __init__(self, **values: Any) -> None:
        """Validate settings without retaining raw inputs in error output."""
        try:
            super().__init__(**values)
        except ValidationError as error:
            raise ValidationError.from_exception_data(
                self.__class__.__name__,
                cast(list[InitErrorDetails], error.errors(include_input=False)),
            ) from None

    @model_validator(mode="before")
    @classmethod
    def reject_legacy_document_roots(cls, values: object) -> object:
        if isinstance(values, dict) and (
            "inbox_root" in values or "companies_root" in values
        ):
            raise ValueError(
                "legacy document-root configuration is not supported; "
                "register authorized locations instead"
            )
        return values

    @model_validator(mode="after")
    def validate_mode_requirements(self) -> Settings:

        if self.enable_file_moves and self.dry_run:
            raise ValueError("enable_file_moves requires dry_run=false")

        if self.email_provider == "smtp" and not (
            self.smtp_host and self.smtp_username and self.smtp_password
        ):
            raise ValueError("smtp credentials are required when email_provider=smtp")

        return self
