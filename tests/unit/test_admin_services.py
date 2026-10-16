"""Pure admin rules: folder names, root validation and the write probe (no database)."""

from __future__ import annotations

import os
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy.exc import (
    DBAPIError,
    IntegrityError,
    InterfaceError,
    OperationalError,
    ProgrammingError,
    SQLAlchemyError,
)
from typer.testing import CliRunner

from osa import cli
from osa.admin import export, services
from osa.db.session import DatabaseNotConfigured
from osa.domain.errors import Conflict, UnsafePath


@pytest.mark.parametrize("name", ["Alfa Ltda", "Contábil", "Fiscal 2026", "a.b", "Matriz (SP)"])
def test_validate_folder_name_accepts_safe_names(name: str) -> None:
    assert services.validate_folder_name(name) == name


@pytest.mark.parametrize(
    "name",
    [
        "",
        " ",
        ".",
        "..",
        "a/b",
        "a\\b",
        "a\x00b",
        "a\nb",
        " lead",
        "trail ",
        "trail.",
        ".lead",
        "a:b",
        'a"b',
        "a<b",
        "a>b",
        "a|b",
        "a?b",
        "a*b",
        "CON",
        "nul",
        "Com1",
        "lpt9.txt",
        "x" * 201,
    ],
)
def test_validate_folder_name_refuses_unsafe_names(name: str) -> None:
    with pytest.raises(ValueError):
        services.validate_folder_name(name)


def test_validate_folder_name_normalizes_to_nfc() -> None:
    assert services.validate_folder_name("Café") == "Café"


def test_folder_key_ignores_case_and_unicode_form() -> None:
    assert services.folder_key("Alfa") == services.folder_key("ALFA")
    assert services.folder_key("Café") == services.folder_key("CAFÉ")
    assert services.folder_key("Straße") == services.folder_key("STRASSE")
    assert services.folder_key("Alfa") != services.folder_key("Beta")


def test_derive_folder_name_strips_forbidden_characters_and_keeps_accents() -> None:
    assert services.derive_folder_name("  Alfa   Ltda. / Matriz ") == "Alfa Ltda. Matriz"
    assert services.derive_folder_name('Açaí: "Norte"?') == "Açaí Norte"
    assert services.derive_folder_name("Café Central.") == "Café Central"


def test_derive_folder_name_refuses_names_with_nothing_left() -> None:
    with pytest.raises(ValueError):
        services.derive_folder_name(" /?*. ")


def _mounted(_path: Path) -> bool:
    return True


def _not_mounted(_path: Path) -> bool:
    return False


def test_validate_root_accepts_existing_writable_directory(tmp_path: Path) -> None:
    root = tmp_path / "raiz-ficticia"
    root.mkdir()

    assert services.validate_root_path(str(root), is_mount_point=_mounted) == root
    assert list(root.iterdir()) == []


@pytest.mark.parametrize("raw", ["", "   ", "relativo/raiz", "raiz", "~/raiz"])
def test_validate_root_refuses_non_absolute_paths(raw: str) -> None:
    with pytest.raises(UnsafePath, match="absoluto"):
        services.validate_root_path(raw, is_mount_point=_mounted)


def test_validate_root_refuses_missing_directory_with_message(tmp_path: Path) -> None:
    with pytest.raises(UnsafePath, match="não existe"):
        services.validate_root_path(str(tmp_path / "inexistente"), is_mount_point=_mounted)


def test_validate_root_refuses_a_file(tmp_path: Path) -> None:
    file = tmp_path / "arquivo.txt"
    file.write_text("ficticio")

    with pytest.raises(UnsafePath, match="diretório"):
        services.validate_root_path(str(file), is_mount_point=_mounted)


def test_validate_root_refuses_dotdot_and_symlinks(tmp_path: Path) -> None:
    real = tmp_path / "real"
    real.mkdir()
    link = tmp_path / "link"
    link.symlink_to(real, target_is_directory=True)

    with pytest.raises(UnsafePath, match="link simbólico"):
        services.validate_root_path(str(link), is_mount_point=_mounted)
    with pytest.raises(UnsafePath, match="link simbólico"):
        services.validate_root_path(str(real / ".." / "real"), is_mount_point=_mounted)


def test_validate_root_refuses_path_outside_a_mount_point(tmp_path: Path) -> None:
    with pytest.raises(UnsafePath, match="ponto de montagem"):
        services.validate_root_path(str(tmp_path), is_mount_point=_not_mounted)


def test_validate_root_refuses_nul_byte(tmp_path: Path) -> None:
    with pytest.raises(UnsafePath):
        services.validate_root_path(str(tmp_path) + "\x00", is_mount_point=_mounted)


@pytest.mark.skipif(os.geteuid() == 0, reason="root ignores directory permissions")
def test_validate_root_refuses_directory_without_write_permission(tmp_path: Path) -> None:
    root = tmp_path / "somente-leitura"
    root.mkdir()
    root.chmod(0o500)
    try:
        with pytest.raises(UnsafePath, match="escrita"):
            services.validate_root_path(str(root), is_mount_point=_mounted)
    finally:
        root.chmod(0o700)


def test_default_mount_point_predicate_walks_ancestors_but_not_slash(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    mount = tmp_path / "share"
    inner = mount / "dentro" / "mais"
    inner.mkdir(parents=True)
    monkeypatch.setattr(os.path, "ismount", lambda p: str(p) in {str(mount), "/"})

    assert services.default_is_mount_point(inner) is True
    assert services.default_is_mount_point(mount) is True
    assert services.default_is_mount_point(tmp_path) is False  # "/" alone does not count


def test_probe_leaves_no_file_behind(tmp_path: Path) -> None:
    services.probe_directory_writable(tmp_path)

    assert list(tmp_path.iterdir()) == []


def test_probe_removes_its_file_when_writing_fails_after_creation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def failing_write(fd: int, data: bytes) -> int:
        raise OSError("disco cheio (simulado)")

    monkeypatch.setattr(os, "write", failing_write)

    with pytest.raises(UnsafePath, match="escrita"):
        services.probe_directory_writable(tmp_path)
    assert list(tmp_path.iterdir()) == []


def test_probe_never_touches_foreign_files(tmp_path: Path) -> None:
    other = tmp_path / ".osa-probe-de-outra-pessoa"
    other.write_text("ficticio")

    services.probe_directory_writable(tmp_path)

    assert other.read_text() == "ficticio"


def test_probe_refuses_a_symlink_swapped_in_before_opening(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    target = tmp_path / "destino-do-link"
    target.mkdir()
    root = tmp_path / "raiz"
    root.mkdir()
    real_open = os.open
    swapped = False

    def swapping_open(
        path: str | os.PathLike[str], flags: int, mode: int = 0o777, *, dir_fd: int | None = None
    ) -> int:
        nonlocal swapped
        if not swapped and Path(path) == root:
            swapped = True
            root.rmdir()
            root.symlink_to(target, target_is_directory=True)
        return real_open(path, flags, mode, dir_fd=dir_fd)

    monkeypatch.setattr(os, "open", swapping_open)

    with pytest.raises(UnsafePath):
        services.probe_directory_writable(root)
    assert list(target.iterdir()) == []


def _swap_after_open(
    monkeypatch: pytest.MonkeyPatch, root: Path, swap: Callable[[], None]
) -> list[int]:
    """Run ``swap`` right after the probe opened ``root``: the descriptor is the old dir."""
    real_open = os.open
    opened: list[int] = []

    def open_then_swap(
        path: str | os.PathLike[str], flags: int, mode: int = 0o777, *, dir_fd: int | None = None
    ) -> int:
        fd = real_open(path, flags, mode, dir_fd=dir_fd)
        if not opened and dir_fd is None and Path(path) == root:
            opened.append(fd)
            swap()
        return fd

    monkeypatch.setattr(os, "open", open_then_swap)
    return opened


def test_probe_refuses_a_symlink_swapped_in_after_opening(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root, moved, target = tmp_path / "raiz", tmp_path / "raiz-antiga", tmp_path / "destino"
    root.mkdir()
    target.mkdir()

    def swap() -> None:
        root.rename(moved)
        root.symlink_to(target, target_is_directory=True)

    opened = _swap_after_open(monkeypatch, root, swap)

    with pytest.raises(UnsafePath, match="link simbólico"):
        services.probe_directory_writable(root)
    assert opened, "the swap must happen after the directory was opened"
    assert list(moved.iterdir()) == [] and list(target.iterdir()) == []


def test_probe_refuses_a_directory_replaced_after_opening(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root, moved = tmp_path / "raiz", tmp_path / "raiz-antiga"
    root.mkdir()

    def swap() -> None:
        root.rename(moved)
        root.mkdir()  # same path, no link: only the inode check can tell

    opened = _swap_after_open(monkeypatch, root, swap)

    with pytest.raises(UnsafePath, match="raiz mudou durante a validação$"):
        services.probe_directory_writable(root)
    assert opened
    assert list(moved.iterdir()) == [] and list(root.iterdir()) == []


# --- CLI: bootstrap, export, import (services replaced; no database) ----------------

runner = CliRunner()
PASSWORD = "fictional-pw-not-real"


class FakeSession:
    def __init__(self) -> None:
        self.committed = False
        self.closed = False

    def __enter__(self) -> FakeSession:
        return self

    def __exit__(self, *exc: object) -> None:
        self.closed = True

    def commit(self) -> None:
        self.committed = True


def use_fake_session(monkeypatch: pytest.MonkeyPatch) -> FakeSession:
    fake = FakeSession()
    monkeypatch.setattr(cli, "_session_factory", lambda: lambda: fake)
    return fake


def forbid_database(monkeypatch: pytest.MonkeyPatch) -> None:
    def boom() -> Callable[[], Any]:
        raise AssertionError("the database must not be opened")

    monkeypatch.setattr(cli, "_session_factory", boom)


def test_bootstrap_command_calls_the_service_with_cli_actor_and_commits(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake = use_fake_session(monkeypatch)
    seen: dict[str, Any] = {}

    def fake_bootstrap(session: object, **kwargs: Any) -> None:
        assert session is fake
        seen.update(kwargs)

    monkeypatch.setattr(services, "bootstrap_admin", fake_bootstrap)

    result = runner.invoke(
        cli.app, ["admin", "bootstrap", "--telegram-id", "1001", "--nome", "Admin Ficticio"]
    )

    assert result.exit_code == 0, result.output
    assert seen == {"telegram_id": 1001, "nome": "Admin Ficticio", "actor": "cli:bootstrap"}
    assert fake.committed and fake.closed
    assert "administrador criado" in result.output


def test_bootstrap_command_requires_both_options() -> None:
    assert runner.invoke(cli.app, ["admin", "bootstrap", "--nome", "X"]).exit_code == 2
    assert runner.invoke(cli.app, ["admin", "bootstrap", "--telegram-id", "1"]).exit_code == 2
    assert (
        runner.invoke(
            cli.app, ["admin", "bootstrap", "--telegram-id", "x", "--nome", "X"]
        ).exit_code
        == 2
    )


def test_bootstrap_second_run_fails_with_message_and_no_commit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake = use_fake_session(monkeypatch)

    def refuse(session: object, **kwargs: Any) -> None:
        raise Conflict("já existe um administrador: o bootstrap só roda uma vez")

    monkeypatch.setattr(services, "bootstrap_admin", refuse)

    result = runner.invoke(
        cli.app, ["admin", "bootstrap", "--telegram-id", "1002", "--nome", "Outro"]
    )

    assert result.exit_code == 1
    assert "já existe um administrador" in result.output
    assert "Traceback" not in result.output
    assert not fake.committed


@pytest.mark.parametrize(
    "error",
    [
        DatabaseNotConfigured("database is not configured: set OSA_DATABASE_URL"),
        OperationalError("SELECT 1", {}, Exception(f"password authentication failed {PASSWORD}")),
        IntegrityError("INSERT", {}, Exception(f"duplicate key {PASSWORD}")),
        ProgrammingError("SELECT", {}, Exception(f'relation "users" does not exist {PASSWORD}')),
        InterfaceError("SELECT", {}, Exception(f"connection closed {PASSWORD}")),
        DBAPIError("SELECT", {}, Exception(f"driver failure {PASSWORD}")),
        SQLAlchemyError(f"anything else {PASSWORD}"),
        ValueError("telegram_id deve ser um inteiro positivo"),
    ],
)
def test_cli_errors_are_one_line_without_secrets(
    monkeypatch: pytest.MonkeyPatch, error: Exception
) -> None:
    use_fake_session(monkeypatch)

    def fail(session: object, **kwargs: Any) -> None:
        raise error

    monkeypatch.setattr(services, "bootstrap_admin", fail)

    result = runner.invoke(cli.app, ["admin", "bootstrap", "--telegram-id", "1", "--nome", "X"])

    assert result.exit_code == 1
    assert PASSWORD not in result.output
    assert "Traceback" not in result.output
    assert result.output.strip().startswith("erro:")


def test_cli_names_the_missing_migration_on_schema_errors(monkeypatch: pytest.MonkeyPatch) -> None:
    use_fake_session(monkeypatch)

    def fail(session: object, **kwargs: Any) -> None:
        raise ProgrammingError("SELECT", {}, Exception("relation does not exist"))

    monkeypatch.setattr(services, "bootstrap_admin", fail)

    result = runner.invoke(cli.app, ["admin", "bootstrap", "--telegram-id", "1", "--nome", "X"])

    assert result.exit_code == 1 and "alembic upgrade head" in result.output
    assert len(result.output.strip().splitlines()) == 1


def test_cli_never_renders_rich_tracebacks_or_locals() -> None:
    for typer_app in (cli.app, cli.config_app, cli.admin_app):
        assert typer_app.pretty_exceptions_enable is False
        assert typer_app.pretty_exceptions_show_locals is False


@pytest.mark.parametrize(
    "key", ["sk-fictional-0123456789abcdef", "fictional_api_key_0123456789abcdef"]
)
def test_cli_import_never_prints_a_secret_pasted_as_a_key(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
    key: str,
) -> None:
    forbid_database(monkeypatch)
    caplog.set_level("DEBUG")
    document = tmp_path / "cadastro.yaml"
    document.write_text(f"version: 1\nmodelo:\n  nome: m\n  {key}:\n    {key}: x\n")

    result = runner.invoke(cli.app, ["admin", "import", str(document)])

    assert result.exit_code == 1 and result.output.startswith("erro:")
    assert key not in result.output and key not in caplog.text


def test_cli_reports_missing_database_configuration(monkeypatch: pytest.MonkeyPatch) -> None:
    def not_configured() -> Callable[[], Any]:
        raise DatabaseNotConfigured("database is not configured: set OSA_DATABASE_URL")

    monkeypatch.setattr(cli, "_session_factory", not_configured)

    result = runner.invoke(cli.app, ["admin", "bootstrap", "--telegram-id", "1", "--nome", "X"])

    assert result.exit_code == 1 and "OSA_DATABASE_URL" in result.output


def test_export_prints_yaml_to_stdout(monkeypatch: pytest.MonkeyPatch) -> None:
    use_fake_session(monkeypatch)
    monkeypatch.setattr(export, "export_yaml", lambda session: "version: 1\n")

    result = runner.invoke(cli.app, ["admin", "export"])

    assert result.exit_code == 0 and result.output == "version: 1\n"


def test_export_to_file_is_private_and_never_overwrites(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    use_fake_session(monkeypatch)
    monkeypatch.setattr(export, "export_yaml", lambda session: "version: 1\n")
    target = tmp_path / "cadastro.yaml"

    first = runner.invoke(cli.app, ["admin", "export", "--out", str(target)])
    second = runner.invoke(cli.app, ["admin", "export", "--out", str(target)])

    assert first.exit_code == 0 and oct(target.stat().st_mode & 0o777) == "0o600"
    assert second.exit_code == 1 and "já existe" in second.output
    assert target.read_text() == "version: 1\n"


def test_import_command_reads_validates_and_commits(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    fake = use_fake_session(monkeypatch)
    source = tmp_path / "cadastro.yaml"
    source.write_text("version: 1\n")
    seen: dict[str, Any] = {}

    def fake_import(session: object, registry: export.Registry, **kwargs: Any) -> Any:
        seen.update(kwargs, registry=registry)
        return export.ImportSummary(0, 0, 0, 0, 0, False)

    monkeypatch.setattr(export, "import_registry", fake_import)

    result = runner.invoke(cli.app, ["admin", "import", str(source)])

    assert result.exit_code == 0, result.output
    assert seen["actor"] == "cli:import" and fake.committed
    assert "importado" in result.output


def test_import_command_rejects_secrets_before_opening_the_database(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    forbid_database(monkeypatch)
    source = tmp_path / "ruim.yaml"
    source.write_text("version: 1\nexternal_llm_api_key: fictional-key-not-real\n")

    result = runner.invoke(cli.app, ["admin", "import", str(source)])

    assert result.exit_code == 1
    assert "chave sensível" in result.output
    assert "fictional-key-not-real" not in result.output


def test_import_command_failure_does_not_commit(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    fake = use_fake_session(monkeypatch)
    source = tmp_path / "cadastro.yaml"
    source.write_text("version: 1\n")

    def refuse(*args: Any, **kwargs: Any) -> None:
        raise UnsafePath("raiz não existe")

    monkeypatch.setattr(export, "import_registry", refuse)

    result = runner.invoke(cli.app, ["admin", "import", str(source)])

    assert result.exit_code == 1 and "raiz não existe" in result.output
    assert not fake.committed


def test_import_command_handles_unreadable_file(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    forbid_database(monkeypatch)

    result = runner.invoke(cli.app, ["admin", "import", str(tmp_path / "nada.yaml")])

    assert result.exit_code == 1 and "não encontrado" in result.output
