from __future__ import annotations

import pytest
from pydantic import SecretStr, ValidationError

from osa.core.settings import Settings


def test_defaults_are_safe() -> None:
    settings = Settings()

    assert settings.dry_run is True
    assert settings.enable_file_moves is False
    assert settings.email_provider == "development"
    assert settings.allow_external_llm is False


def test_rejects_malformed_boolean() -> None:
    with pytest.raises(ValidationError, match="enable_file_moves"):
        Settings(enable_file_moves="not-a-boolean")


def test_rejects_malformed_database_dsn() -> None:
    with pytest.raises(ValidationError, match="database_url"):
        Settings(database_url="not-a-dsn")


def test_production_requires_document_roots() -> None:
    with pytest.raises(ValidationError, match="document roots"):
        Settings(environment="production")


def test_smtp_requires_credentials() -> None:
    with pytest.raises(ValidationError, match="smtp credentials"):
        Settings(email_provider="smtp")


def test_test_environment_rejects_document_roots() -> None:
    with pytest.raises(ValidationError, match="test environment"):
        Settings(environment="test", inbox_root="/production/inbox")


def test_settings_representation_redacts_secret() -> None:
    settings = Settings(
        email_provider="smtp",
        smtp_host="smtp.example.test",
        smtp_username="osa",
        smtp_password=SecretStr("very-secret"),
    )

    assert "very-secret" not in repr(settings)
