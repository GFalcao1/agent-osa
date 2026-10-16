from __future__ import annotations

import pytest
from typer.testing import CliRunner

from osa import __version__
from osa.cli import app

runner = CliRunner()


def test_version_exits_zero() -> None:
    result = runner.invoke(app, ["--version"])

    assert result.exit_code == 0
    assert __version__ in result.output


def test_config_check_valid_defaults() -> None:
    result = runner.invoke(app, ["config", "check"])

    assert result.exit_code == 0
    assert "configuration ok" in result.output


def test_config_check_invalid_exits_one_without_secret(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OSA_ENVIRONMENT", "production")
    monkeypatch.setenv("OSA_TELEGRAM_BOT_TOKEN", "fictional-token")

    result = runner.invoke(app, ["config", "check"])

    assert result.exit_code == 1
    assert "database_url" in result.output
    assert "fictional-token" not in result.output


def test_config_check_names_legacy_variable(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OSA_DRY_RUN", "fictional-legacy-value")

    result = runner.invoke(app, ["config", "check"])

    assert result.exit_code == 1
    assert "OSA_DRY_RUN" in result.output
    assert "fictional-legacy-value" not in result.output


@pytest.mark.parametrize(
    "url",
    [
        "http://u:fictional-secret@h.test",
        "http://user:fictional-secret@exa\uff0fmple.test",
    ],
)
def test_config_check_never_prints_url_credentials(
    monkeypatch: pytest.MonkeyPatch, url: str
) -> None:
    monkeypatch.setenv("OSA_OLLAMA_BASE_URL", url)

    result = runner.invoke(app, ["config", "check"])

    assert result.exit_code == 1
    assert "ollama_base_url" in result.output
    assert "fictional-secret" not in result.output
