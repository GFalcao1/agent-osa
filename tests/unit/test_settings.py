from __future__ import annotations

import importlib
import socket
import tomllib
from pathlib import Path

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


def test_smtp_requires_credentials() -> None:
    with pytest.raises(ValidationError, match="smtp credentials"):
        Settings(email_provider="smtp")


def test_test_environment_without_document_roots_is_valid() -> None:
    settings = Settings(environment="test")

    assert settings.environment == "test"


def test_st001_1_production_without_locations_is_valid() -> None:
    settings = Settings(environment="production")

    assert settings.environment == "production"


@pytest.mark.parametrize(
    ("variable", "value"),
    [
        ("OSA_INBOX_ROOT", ""),
        ("OSA_INBOX_ROOT", "/fictional/inbox"),
        ("OSA_COMPANIES_ROOT", ""),
        ("OSA_COMPANIES_ROOT", "/fictional/companies"),
    ],
)
def test_st001_1_rejects_legacy_roots_from_environment(
    monkeypatch: pytest.MonkeyPatch, variable: str, value: str
) -> None:
    monkeypatch.setenv(variable, value)

    with pytest.raises(ValidationError, match="legacy document-root configuration"):
        Settings()


def test_st001_1_rejects_legacy_roots_passed_explicitly() -> None:
    with pytest.raises(ValidationError, match="legacy document-root configuration"):
        Settings(inbox_root="/fictional/inbox")


def test_st001_1_defaults_are_restrictive_without_environment() -> None:
    settings = Settings()

    assert settings.dry_run is True
    assert settings.enable_file_moves is False
    assert settings.email_provider == "development"
    assert settings.allow_external_llm is False


def test_st001_1_rejects_invalid_values_and_redacts_smtp_password() -> None:
    with pytest.raises(ValidationError, match="enable_file_moves"):
        Settings(enable_file_moves="not-a-boolean")
    with pytest.raises(ValidationError, match="database_url"):
        Settings(database_url="not-a-dsn")
    with pytest.raises(ValidationError) as error:
        Settings(
            email_provider="smtp",
            smtp_host="smtp.example.test",
            smtp_password=SecretStr("fictional-secret"),
        )

    assert "fictional-secret" not in str(error.value)


def test_st001_1_settings_repr_redacts_database_password() -> None:
    password = "fictional-database-password"
    settings = Settings(
        database_url=f"postgresql://osa:{password}@db.example.test:5432/osa",
    )

    assert password not in repr(settings)


def test_st001_1_smtp_validation_error_redacts_text_password() -> None:
    password = "fictional-smtp-password"

    with pytest.raises(ValidationError) as error:
        Settings(
            email_provider="smtp",
            smtp_host="smtp.example.test",
            smtp_password=password,
        )

    assert password not in str(error.value)
    assert password not in error.value.json()


def test_st001_2_package_import_is_side_effect_free() -> None:
    package = importlib.import_module("osa")

    assert package.__version__ == "0.1.0"


def test_st001_2_package_metadata_and_ignore_rules_are_safe() -> None:
    project = tomllib.loads(Path("pyproject.toml").read_text())
    ignored_paths = Path(".gitignore").read_text()

    assert project["project"]["requires-python"] == ">=3.12"
    assert "pydantic-settings>=2.7,<3" in project["project"]["dependencies"]
    assert "pytest>=8.3,<9" in project["dependency-groups"]["dev"]
    assert ".env" in ignored_paths
    assert "tasks/" in ignored_paths


def test_st001_3_ignores_dotenv_files_in_portable_tests(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    (tmp_path / ".env").write_text("OSA_DRY_RUN=false\n")
    monkeypatch.chdir(tmp_path)

    assert Settings().dry_run is True


def test_st001_3_blocks_socket_connection_paths() -> None:
    with socket.socket() as client:
        with pytest.raises(AssertionError, match="network access"):
            client.connect(("127.0.0.1", 1))
    with pytest.raises(AssertionError, match="network access"):
        socket.create_connection(("127.0.0.1", 1))


def test_st001_3_declares_native_and_live_markers() -> None:
    project = tomllib.loads(Path("pyproject.toml").read_text())
    markers = project["tool"]["pytest"]["ini_options"].get("markers", [])

    assert any(marker.startswith("native:") for marker in markers)
    assert any(marker.startswith("live:") for marker in markers)


def test_settings_representation_redacts_secret() -> None:
    settings = Settings(
        email_provider="smtp",
        smtp_host="smtp.example.test",
        smtp_username="osa",
        smtp_password=SecretStr("very-secret"),
    )

    assert "very-secret" not in repr(settings)
