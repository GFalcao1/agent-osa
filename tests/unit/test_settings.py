from __future__ import annotations

import os
import socket
import subprocess
import sys
import tomllib
from pathlib import Path

import pytest
from pydantic import SecretStr, ValidationError

from osa.core.settings import Settings

# Repository files are read from here, not from the working directory, so the
# suite does not depend on where pytest was started.
REPO_ROOT = Path(__file__).resolve().parents[2]

PRODUCTION = {
    "environment": "production",
    "database_url": "postgresql://osa:fictional-db-pass@db.example.test:5432/osa",
    "telegram_bot_token": "fictional-telegram-token",
    "quarantine_dir": "/fictional/quarantine",
}


def test_defaults_are_safe_and_match_v1_table() -> None:
    s = Settings()

    assert s.environment == "development"
    assert s.enable_file_effects is False
    assert s.external_llm_enabled is False
    assert s.database_url is None
    assert s.telegram_bot_token is None
    assert s.telegram_reviewers_chat_id is None
    assert s.external_llm_api_key is None
    assert s.ollama_base_url == "http://127.0.0.1:11434"
    assert s.ollama_model == "gemma3:12b"
    assert s.ollama_model_digest is None
    assert s.poll_interval_seconds == 45
    assert s.stable_cycles == 2
    assert s.min_file_age_seconds == 60
    assert s.max_text_chars == 12000
    assert s.max_image_pages == 5
    assert s.sensitive_extensions == (".pfx", ".p12", ".pem", ".key", ".cer", ".crt")
    assert s.quarantine_dir is None
    assert s.telegram_send_limit_mb == 50


def test_valid_production_is_accepted() -> None:
    assert Settings(**PRODUCTION).environment == "production"  # type: ignore[arg-type]


@pytest.mark.parametrize("missing", ["database_url", "telegram_bot_token", "quarantine_dir"])
def test_production_requires_critical_settings(missing: str) -> None:
    values = {k: v for k, v in PRODUCTION.items() if k != missing}

    with pytest.raises(ValidationError, match=missing):
        Settings(**values)  # type: ignore[arg-type]


def test_production_without_anything_fails() -> None:
    with pytest.raises(ValidationError, match="production requires"):
        Settings(environment="production")


def test_external_llm_requires_key() -> None:
    with pytest.raises(ValidationError, match="external_llm_api_key"):
        Settings(external_llm_enabled=True)
    ok = Settings(external_llm_enabled=True, external_llm_api_key="fictional-llm-key")  # type: ignore[arg-type]
    assert isinstance(ok.external_llm_api_key, SecretStr)


def test_secrets_never_appear_in_repr_str_or_errors() -> None:
    secrets = ["fictional-llm-key", "fictional-telegram-token", "fictional-db-pass"]
    s = Settings(**PRODUCTION, external_llm_enabled=True, external_llm_api_key=secrets[0])  # type: ignore[arg-type]
    for text in (repr(s), str(s), s.model_dump_json()):
        for secret in secrets:
            assert secret not in text
    with pytest.raises(ValidationError) as error:
        Settings(
            environment="production",
            telegram_bot_token=secrets[1],
            database_url="not-a-dsn",
        )
    for secret in secrets:
        assert secret not in str(error.value)
        assert secret not in error.value.json()
    assert "not-a-dsn" not in str(error.value)


@pytest.mark.parametrize(
    "overrides",
    [
        {"telegram_bot_token": ""},
        {"telegram_bot_token": "   "},
        {"external_llm_enabled": True, "external_llm_api_key": ""},
        {"external_llm_enabled": True, "external_llm_api_key": "  "},
    ],
    ids=["empty-token", "blank-token", "empty-api-key", "blank-api-key"],
)
def test_empty_secrets_do_not_satisfy_requirements(overrides: dict[str, object]) -> None:
    values = {**PRODUCTION, **overrides}
    field = next(k for k in overrides if k != "external_llm_enabled")

    with pytest.raises(ValidationError, match=field):
        Settings(**values)  # type: ignore[arg-type]


@pytest.mark.parametrize(
    ("variable", "extra"),
    [
        ("OSA_TELEGRAM_BOT_TOKEN", {}),
        ("OSA_EXTERNAL_LLM_API_KEY", {"OSA_EXTERNAL_LLM_ENABLED": "true"}),
    ],
)
def test_empty_secret_env_vars_do_not_satisfy_requirements(
    monkeypatch: pytest.MonkeyPatch, variable: str, extra: dict[str, str]
) -> None:
    for key, value in PRODUCTION.items():
        monkeypatch.setenv(f"OSA_{key.upper()}", value)
    for key, value in extra.items():
        monkeypatch.setenv(key, value)
    monkeypatch.setenv(variable, "")

    with pytest.raises(ValidationError, match=variable.removeprefix("OSA_").lower()):
        Settings()


def test_rejects_malformed_values() -> None:
    with pytest.raises(ValidationError, match="database_url"):
        Settings(database_url="not-a-dsn")
    with pytest.raises(ValidationError, match="enable_file_effects"):
        Settings(enable_file_effects="not-a-boolean")  # type: ignore[arg-type]
    with pytest.raises(ValidationError, match="poll_interval_seconds"):
        Settings(poll_interval_seconds=10)
    with pytest.raises(ValidationError, match="poll_interval_seconds"):
        Settings(poll_interval_seconds=61)
    with pytest.raises(ValidationError, match="quarantine_dir"):
        Settings(quarantine_dir="relative/dir")  # type: ignore[arg-type]
    with pytest.raises(ValidationError, match="ollama_base_url"):
        Settings(ollama_base_url="ftp://x")


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("sensitive_extensions", ""),
        ("sensitive_extensions", " , "),
        ("sensitive_extensions", ".p fx"),
        ("ollama_model", ""),
        ("ollama_base_url", "http://"),
        ("ollama_base_url", "https:///path"),
        ("ollama_model", "   "),
        ("ollama_base_url", "http://:11434"),
        ("ollama_base_url", "http://127.0.0.1:notaport"),
        ("ollama_base_url", "http://127.0.0.1:99999"),
        ("ollama_base_url", "HTTP://127.0.0.1"),
        ("sensitive_extensions", "."),
        ("sensitive_extensions", "pfx/key"),
        ("sensitive_extensions", ".p*x"),
        ("sensitive_extensions", "..pfx"),
    ],
)
def test_rejects_empty_or_hollow_values(field: str, value: str) -> None:
    with pytest.raises(ValidationError, match=field):
        Settings(**{field: value})  # type: ignore[arg-type]


@pytest.mark.parametrize(
    "url",
    [
        "http://127.0.0.1:11434",
        "http://127.0.0.1:54321/",
        "https://127.0.0.1",
        "http://localhost:11434",
        "http://LOCALHOST:11434",
        "http://[::1]:11434",
    ],
)
def test_ollama_url_accepts_loopback_hosts(url: str) -> None:
    assert Settings(ollama_base_url=url).ollama_base_url == url


@pytest.mark.parametrize(
    "url",
    [
        "http://host.example.test:11434",
        "http://192.0.2.10:11434",
        "http://10.0.0.5:11434",
        "http://0.0.0.0:11434",
        "http://127.0.0.2:11434",
        "http://[::ffff:127.0.0.1]:11434",
        "http://localhost.example.test:11434",
        "http://localhost.:11434",
        "http://127.0.0.1.example.test:11434",
        "http://macmini.local:11434",
    ],
)
def test_ollama_url_refuses_non_loopback_hosts(url: str) -> None:
    # Document content goes to this URL (ADR-0002): only this machine qualifies.
    with pytest.raises(ValidationError, match="ollama_base_url") as error:
        Settings(ollama_base_url=url)

    assert "loopback" in str(error.value)


def test_ollama_url_refusal_never_echoes_the_host() -> None:
    with pytest.raises(ValidationError) as error:
        Settings(ollama_base_url="http://fictional-remote-host.test:11434")

    assert "fictional-remote-host" not in str(error.value)


def test_ollama_model_is_stripped() -> None:
    assert Settings(ollama_model="  gemma3:12b  ").ollama_model == "gemma3:12b"


@pytest.mark.parametrize(
    "url",
    [
        "http://user:fictional-secret@exa\uff0fmple.test",
        "http://user:fictional-secret@127.0.0.1:11434",
        "http://fictional-secret@localhost",
        "http://user:fictional-secret@127.0.0.1:notaport",
        "http://user:fictional-secret@host.example.test:11434",
        "http://user:fictional-secret@[::1",
    ],
)
def test_ollama_url_errors_never_echo_credentials(url: str) -> None:
    with pytest.raises(ValidationError) as error:
        Settings(ollama_base_url=url)

    assert "ollama_base_url" in str(error.value)
    for text in (str(error.value), error.value.json(), repr(error.value.errors())):
        assert "fictional-secret" not in text


def test_sensitive_extensions_empty_env_var_keeps_default(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # env_ignore_empty: an empty variable falls back to the safe default rather than
    # silently disabling the sensitive class.
    monkeypatch.setenv("OSA_SENSITIVE_EXTENSIONS", "")

    assert Settings().sensitive_extensions == (".pfx", ".p12", ".pem", ".key", ".cer", ".crt")


def test_sensitive_extensions_from_env_are_normalised(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OSA_SENSITIVE_EXTENSIONS", "PFX, .key")

    assert Settings().sensitive_extensions == (".pfx", ".key")


@pytest.mark.parametrize(
    "variable",
    [
        "OSA_DRY_RUN",
        "OSA_ENABLE_FILE_MOVES",
        "OSA_EMAIL_PROVIDER",
        "OSA_SMTP_HOST",
        "OSA_SMTP_USERNAME",
        "OSA_SMTP_PASSWORD",
        "OSA_INBOX_ROOT",
        "OSA_COMPANIES_ROOT",
    ],
)
def test_legacy_env_var_is_rejected_naming_variable_not_value(
    monkeypatch: pytest.MonkeyPatch, variable: str
) -> None:
    monkeypatch.setenv(variable, "fictional-legacy-value")

    with pytest.raises(ValidationError) as error:
        Settings()

    assert variable in str(error.value)
    assert "fictional-legacy-value" not in str(error.value)
    assert "fictional-legacy-value" not in error.value.json()


def test_legacy_variable_in_dotenv_file_is_rejected(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    env_file = tmp_path / ".env"
    env_file.write_text("OSA_DRY_RUN=fictional-legacy-value\n")
    monkeypatch.setitem(Settings.model_config, "env_file", str(env_file))

    with pytest.raises(ValidationError) as error:
        Settings()

    assert "OSA_DRY_RUN" in str(error.value)
    assert "fictional-legacy-value" not in str(error.value)


def test_legacy_variable_in_explicit_env_file_kwarg_is_rejected(tmp_path: Path) -> None:
    env_file = tmp_path / "custom.env"
    env_file.write_text("OSA_DRY_RUN=fictional-legacy-value\n")

    with pytest.raises(ValidationError) as error:
        Settings(_env_file=env_file)  # type: ignore[call-arg]

    assert "OSA_DRY_RUN" in str(error.value)
    assert "fictional-legacy-value" not in str(error.value)


def test_legacy_detection_handles_tuple_env_file(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    clean = tmp_path / "a.env"
    clean.write_text("OSA_ENVIRONMENT=test\n")
    legacy = tmp_path / "b.env"
    legacy.write_text("OSA_INBOX_ROOT=/fictional/inbox\n")
    monkeypatch.setitem(Settings.model_config, "env_file", (clean, legacy))

    with pytest.raises(ValidationError) as error:
        Settings()

    assert "OSA_INBOX_ROOT" in str(error.value)
    assert "/fictional/inbox" not in str(error.value)


def test_legacy_setting_passed_as_kwarg_is_rejected() -> None:
    with pytest.raises(ValidationError) as error:
        Settings(inbox_root="/fictional/inbox")  # type: ignore[call-arg]

    assert "OSA_INBOX_ROOT" in str(error.value)
    assert "/fictional/inbox" not in str(error.value)


def test_dotenv_is_ignored_in_portable_tests(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    (tmp_path / ".env").write_text("OSA_ENVIRONMENT=test\nOSA_DRY_RUN=false\n")
    monkeypatch.chdir(tmp_path)

    assert Settings().environment == "development"


_NO_NETWORK_PREAMBLE = """
import socket

def _blocked(*args, **kwargs):
    raise RuntimeError("network access attempted during import")

class _BlockedSocket(socket.socket):
    def __init__(self, *args, **kwargs):
        _blocked()

socket.socket = _BlockedSocket
socket.getaddrinfo = _blocked
socket.create_connection = _blocked
"""


def _run_without_network(code: str, cwd: Path) -> subprocess.CompletedProcess[str]:
    env = {k: v for k, v in os.environ.items() if not k.startswith("OSA_")}
    return subprocess.run(
        [sys.executable, "-c", _NO_NETWORK_PREAMBLE + code],
        cwd=cwd,
        env=env,
        capture_output=True,
        text=True,
        timeout=60,
    )


def test_package_import_is_side_effect_free(tmp_path: Path) -> None:
    # Fresh interpreter: osa is not yet imported, sockets are blocked, no OSA_*
    # variables and no .env in cwd.
    result = _run_without_network(
        "import osa, osa.cli, osa.core.logging\nassert osa.__version__ == '0.1.0'\n",
        tmp_path,
    )
    assert result.returncode == 0, result.stderr
    assert not list(tmp_path.iterdir())

    # Control: the harness really detects a network attempt.
    for probe in ("socket.getaddrinfo('example.test', 80)", "socket.socket()"):
        control = _run_without_network(probe + "\n", tmp_path)
        assert control.returncode != 0
        assert "network access attempted" in control.stderr


def test_package_metadata_and_ignore_rules_are_safe() -> None:
    project = tomllib.loads((REPO_ROOT / "pyproject.toml").read_text())
    ignored_paths = (REPO_ROOT / ".gitignore").read_text()

    assert project["project"]["requires-python"] == ">=3.12"
    assert any(d.startswith("structlog") for d in project["project"]["dependencies"])
    assert any(d.startswith("typer") for d in project["project"]["dependencies"])
    assert any(d.startswith("python-dotenv") for d in project["project"]["dependencies"])
    assert project["project"]["scripts"]["osa"] == "osa.cli:main"
    assert ".env" in ignored_paths


def test_env_example_lists_every_setting_with_default() -> None:
    lines = (REPO_ROOT / ".env.example").read_text().splitlines()
    active = {
        line.split("=", 1)[0].removeprefix("# ").strip()
        for line in lines
        if "=" in line and line.lstrip("# ").startswith("OSA_")
    }

    assert active == {f"OSA_{name.upper()}" for name in Settings.model_fields}


def test_env_example_production_block_lists_only_required_settings() -> None:
    lines = (REPO_ROOT / ".env.example").read_text().splitlines()
    start = next(i for i, line in enumerate(lines) if line.startswith("# Required in production"))
    block = []
    for line in lines[start + 1 :]:
        if not line.strip():
            break
        block.append(line.lstrip("# ").split("=", 1)[0])

    assert block == ["OSA_DATABASE_URL", "OSA_TELEGRAM_BOT_TOKEN", "OSA_QUARANTINE_DIR"]


def test_dotenv_example_values_parse(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    from dotenv import dotenv_values

    for key, value in dotenv_values(".env.example").items():
        if value is not None:
            monkeypatch.setenv(key, value)

    assert Settings().enable_file_effects is False


def test_socket_connection_paths_are_blocked() -> None:
    with socket.socket() as client:
        with pytest.raises(AssertionError, match="network access"):
            client.connect(("127.0.0.1", 1))
    with pytest.raises(AssertionError, match="network access"):
        socket.create_connection(("127.0.0.1", 1))
    with pytest.raises(AssertionError, match="network access"):
        socket.getaddrinfo("example.test", 80)


def test_declares_native_and_live_markers() -> None:
    project = tomllib.loads((REPO_ROOT / "pyproject.toml").read_text())
    markers = project["tool"]["pytest"]["ini_options"].get("markers", [])

    assert any(marker.startswith("native:") for marker in markers)
    assert any(marker.startswith("live:") for marker in markers)


def test_reraised_errors_drop_context_and_mask_url_passwords() -> None:
    from pydantic_core import InitErrorDetails, PydanticCustomError

    from osa.core.settings import _sanitized_errors

    raw = ValidationError.from_exception_data(
        "Settings",
        [
            InitErrorDetails(
                type=PydanticCustomError(
                    "value_error", "bad http://u:fictional-secret@h.test {detail}", {"detail": "x"}
                ),
                loc=("ollama_base_url",),
                input="fictional-input",
            )
        ],
    )

    clean = ValidationError.from_exception_data("Settings", _sanitized_errors(raw))

    for text in (str(clean), clean.json()):
        assert "fictional-secret" not in text
        assert "fictional-input" not in text
    assert "ctx" not in clean.errors()[0]


def test_reraised_errors_mask_multi_host_url_passwords() -> None:
    from pydantic_core import InitErrorDetails, PydanticCustomError

    from osa.core.settings import _sanitized_errors

    dsn = "postgresql://alice:fictional-first@db1.test:5432,bob:fictional-second@db2.test:5432/db"
    raw = ValidationError.from_exception_data(
        "Settings",
        [
            InitErrorDetails(
                type=PydanticCustomError("value_error", f"bad {dsn}"),
                loc=("database_url",),
                input=dsn,
            )
        ],
    )

    clean = ValidationError.from_exception_data("Settings", _sanitized_errors(raw))

    for text in (str(clean), clean.json()):
        assert "fictional-first" not in text
        assert "fictional-second" not in text
    assert "postgresql://alice:[REDACTED]@db2.test:5432/db" in clean.errors()[0]["msg"]


def test_reraised_errors_mask_url_password_containing_comma() -> None:
    from pydantic_core import InitErrorDetails, PydanticCustomError

    from osa.core.settings import _sanitized_errors

    url = "https://alice:fictional-first,fictional-second@api.example.test/"
    raw = ValidationError.from_exception_data(
        "Settings",
        [
            InitErrorDetails(
                type=PydanticCustomError("value_error", f"bad {url}"),
                loc=("ollama_base_url",),
                input=url,
            )
        ],
    )

    clean = ValidationError.from_exception_data("Settings", _sanitized_errors(raw))

    for text in (str(clean), clean.json()):
        assert "fictional-first" not in text
        assert "fictional-second" not in text
    assert "https://alice:[REDACTED]@api.example.test/" in clean.errors()[0]["msg"]


def test_multi_host_dsn_is_accepted_and_hidden() -> None:
    settings = Settings(
        database_url="postgresql://alice:fictional-first@db1.test:5432,bob:fictional-second@db2.test:5432/db"  # type: ignore[arg-type]
    )
    assert settings.database_url is not None
    for text in (repr(settings), str(settings), settings.model_dump_json()):
        assert "fictional-first" not in text
        assert "fictional-second" not in text


@pytest.mark.parametrize(
    "url",
    ["http://127.0.0.1:11434/?x=1", "http://127.0.0.1:11434#frag", "http://127.0.0.1:11434/?"],
)
def test_ollama_base_url_refuses_query_and_fragment(url: str) -> None:
    with pytest.raises(ValidationError):
        Settings(_env_file=None, ollama_base_url=url)
