"""Arquivos de implantação no Mac mini (TASK-112): launchd, rotação e scripts, sem rodar nada."""

from __future__ import annotations

import plistlib
import re
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
LAUNCHD = ROOT / "deploy" / "launchd"
SECRET_NAMES = re.compile(r"(TOKEN|PASSWORD|SENHA|SECRET|KEY|DATABASE_URL)", re.IGNORECASE)


def load(name: str) -> dict[str, object]:
    with (LAUNCHD / f"{name}.plist").open("rb") as handle:
        return plistlib.load(handle)


@pytest.mark.parametrize("service", ["bot", "worker"])
def test_bot_and_worker_start_with_the_machine_and_restart_after_a_kill(service: str) -> None:
    plist = load(f"com.osa.{service}")

    assert plist["Label"] == f"com.osa.{service}"
    assert plist["ProgramArguments"] == [f"/opt/osa/.venv/bin/osa-{service}"]
    assert plist["RunAtLoad"] is True and plist["KeepAlive"] is True
    assert int(plist["ThrottleInterval"]) >= 10  # type: ignore[call-overload]
    assert plist["UserName"] == "osa"  # the service account, never a person's
    assert plist["WorkingDirectory"] == "/opt/osa"  # where .env is read
    assert plist["Umask"] == 0o077  # logs and files private to the service account
    env = plist["EnvironmentVariables"]
    assert isinstance(env, dict) and env["OSA_LOG_FILE"] == f"/usr/local/var/log/osa/{service}.log"
    assert plist["StandardErrorPath"] == f"/usr/local/var/log/osa/{service}.stderr.log"


def test_the_entrypoints_the_plists_run_exist() -> None:
    project = (ROOT / "pyproject.toml").read_text()
    assert 'osa-bot = "osa.telegram.app:main"' in project
    assert 'osa-worker = "osa.worker.app:main"' in project


def test_backup_runs_nightly_and_does_not_stay_alive() -> None:
    plist = load("com.osa.backup")

    assert plist["StartCalendarInterval"] == {"Hour": 2, "Minute": 30}
    assert "KeepAlive" not in plist
    # the script itself, never "source .env" in a shell (python-dotenv parses it, as the app)
    assert plist["ProgramArguments"] == ["/opt/osa/scripts/backup.sh"]
    env = plist["EnvironmentVariables"]
    assert isinstance(env, dict) and env["OSA_ENV_FILE"] == "/opt/osa/.env"
    assert env["OSA_PYTHON"] == "/opt/osa/.venv/bin/python" and plist["Umask"] == 0o077


@pytest.mark.parametrize("name", ["com.osa.bot", "com.osa.worker", "com.osa.backup"])
def test_no_secret_lives_in_a_plist(name: str) -> None:
    plist = load(name)
    env = plist.get("EnvironmentVariables", {})
    assert isinstance(env, dict)
    assert not [key for key in env if SECRET_NAMES.search(key)]
    text = (LAUNCHD / f"{name}.plist").read_text()
    assert "postgresql://" not in text and "bot" + "token" not in text.lower()


def test_every_log_the_plists_write_is_rotated() -> None:
    rotated = {
        line.split()[0]
        for line in (ROOT / "deploy" / "newsyslog" / "osa.conf").read_text().splitlines()
        if line.strip() and not line.startswith("#")
    }
    written = {
        str(load(name)[key])
        for name in ("com.osa.bot", "com.osa.worker", "com.osa.backup")
        for key in ("StandardOutPath", "StandardErrorPath")
    } | {
        str(load(name)["EnvironmentVariables"]["OSA_LOG_FILE"])  # type: ignore[index]
        for name in ("com.osa.bot", "com.osa.worker")
    }
    assert written <= rotated


@pytest.mark.parametrize("script", ["backup.sh", "restore.sh"])
def test_scripts_are_executable_strict_bash(script: str) -> None:
    path = ROOT / "scripts" / script
    assert path.stat().st_mode & 0o111
    text = path.read_text()
    assert text.startswith("#!/usr/bin/env bash") and "set -euo pipefail" in text
    assert "umask 077" in text
    assert subprocess.run(["bash", "-n", str(path)], check=False).returncode == 0


HELPER = ROOT / "scripts" / "_osaenv.py"


def helper(*args: str) -> subprocess.CompletedProcess[str]:
    import sys

    return subprocess.run(
        [sys.executable, str(HELPER), *args], capture_output=True, text=True, check=False
    )


def test_the_helper_hands_the_password_over_an_environment_variable() -> None:
    done = helper(
        "pg-env", "postgresql+psycopg://osa:s%40nha%20ficticia@db.example.invalid:6543/osa_x"
    )
    assert done.returncode == 0, done.stderr
    assert "export PGPASSWORD='s@nha ficticia'" in done.stdout
    assert "export PGHOST=db.example.invalid" in done.stdout and "export PGPORT=6543" in done.stdout


@pytest.mark.parametrize(
    "url",
    [
        "postgresql://h/db?host=evil",  # libpq could be redirected
        "mysql://u:p@h/db",
        "postgresql://u@h/",  # no database
        "postgresql://osa:segredo-ficticio@h:porta-errada/db",  # unparseable port
    ],
)
def test_the_helper_refuses_bad_urls_without_repeating_them(url: str) -> None:
    done = helper("pg-env", url)
    assert done.returncode != 0 and done.stdout == ""
    assert "gredo" not in done.stderr and "evil" not in done.stderr


def test_a_password_with_url_characters_is_read_as_the_app_reads_it() -> None:
    done = helper("pg-env", "postgresql://osa:se#gredo/x?y@h:5432/db")
    assert done.returncode == 0, done.stderr
    assert (
        "export PGPASSWORD='se#gredo/x?y'" in done.stdout and "export PGDATABASE=db" in done.stdout
    )


def test_the_mount_type_comes_from_the_longest_matching_mount_point() -> None:
    import importlib.util

    spec = importlib.util.spec_from_file_location("osaenv", HELPER)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    macos = (
        "/dev/disk3s1s1 on / (apfs, sealed, local, read-only, journaled)\n"
        "/dev/disk3s5 on /System/Volumes/Data (apfs, local, journaled, nobrowse)\n"
        "//osa@srv/acervo on /Volumes/acervo (smbfs, nodev, nosuid, mounted by osa)\n"
    )
    linux = "//srv/acervo on /mnt/acervo type cifs (rw,relatime)\n/dev/sda1 on / type ext4 (rw)\n"
    assert module.mount_fstype(Path("/Volumes/acervo/Backup OSA"), macos) == "smbfs"
    assert module.mount_fstype(Path("/opt/osa/backups"), macos) == "apfs"  # the Mac's own disk
    assert module.mount_fstype(Path("/mnt/acervo/b"), linux) == "cifs"
    assert module.mount_fstype(Path("/var/tmp"), linux) == "ext4"
    assert module.mount_fstype(Path("/Volumes/acervo2"), macos) == "apfs"  # not a prefix match
    # systemd automount: the autofs trigger and the cifs mount stacked on the same point; the
    # later line is the one on top, the share itself
    automount = (
        "/dev/sda2 on / type ext4 (rw)\n"
        "systemd-1 on /mnt/acervo type autofs (rw,relatime,fd=60,timeout=0)\n"
        "//srv/acervo on /mnt/acervo type cifs (rw,relatime,vers=3.1.1)\n"
    )
    assert module.mount_fstype(Path("/mnt/acervo/Backup OSA"), automount) == "cifs"
    # the trigger alone (share not mounted yet) is not a network share
    trigger = "/dev/sda2 on / type ext4 (rw)\nsystemd-1 on /mnt/acervo type autofs (rw)\n"
    assert module.mount_fstype(Path("/mnt/acervo/b"), trigger) == "autofs"


# --- /status e campos do log ---------------------------------------------------------------------


def test_status_shows_queue_reviews_model_space_last_scan_and_version() -> None:
    from datetime import UTC, datetime
    from types import SimpleNamespace

    from osa import __version__
    from osa.telegram.handlers.admin import send_status
    from osa.telegram.ports import Actor, RootSpace, StatusInfo

    sent: list[str] = []
    status = StatusInfo(
        {"ARQUIVADO": 12, "EM_REVISAO": 3, "FALHOU": 1}, 3, "gemma3:12b", "sha256:b92b2185aaaa",
        False, (RootSpace("/Volumes/acervo", 50 * 2**30, datetime(2026, 11, 3, 2, 0, tzinfo=UTC)),),
        versao=__version__,
    )  # fmt: skip
    ctx = SimpleNamespace(
        courier=SimpleNamespace(status=lambda: status),
        bot=SimpleNamespace(send=lambda chat, text, buttons=(): sent.append(text) or 1),
    )
    send_status(ctx, Actor(1, 1001, "Admin", "admin", None), 1001)  # type: ignore[arg-type]

    [text] = sent
    expected = (
        "12 arquivados", "3 em revisão", "1 falharam", "Revisões abertas: 3", "gemma3:12b",
        "50.0 GB livres", "último scan", f"Versão: osa {__version__}",
    )  # fmt: skip
    for part in expected:
        assert part in text, part


def test_every_log_line_names_its_service_and_version() -> None:
    import io
    import json

    from osa.core.logging import configure_logging, get_logger, log_context
    from osa.core.version import full_version

    buffer = io.StringIO()
    configure_logging(stream=buffer, service="worker")
    try:
        with log_context(request_id="r-1"):
            get_logger("t").info("evento.teste")
    finally:
        configure_logging()
    record = json.loads(buffer.getvalue().splitlines()[-1])
    assert (record["servico"], record["versao"], record["request_id"]) == (
        "worker", full_version(), "r-1"
    )  # fmt: skip


def test_the_log_file_follows_its_path_after_a_rotation(tmp_path: Path) -> None:
    import os

    from osa.core.logging import ReopeningFile

    path = tmp_path / "bot.log"
    log = ReopeningFile(str(path))
    log.write("antes\n")
    os.rename(path, tmp_path / "bot.log.0")  # newsyslog renames; nobody signals the process
    log.write("depois\n")
    log.flush()

    assert (tmp_path / "bot.log.0").read_text() == "antes\n"
    assert path.read_text() == "depois\n"
    assert oct(path.stat().st_mode & 0o777) == "0o600"


def test_library_warnings_go_through_the_same_json_line() -> None:
    import io
    import json
    import logging

    from osa.core.logging import configure_logging

    buffer = io.StringIO()
    configure_logging(stream=buffer, service="bot")
    try:
        logging.getLogger("httpx").warning("conexão recusada")
        logging.getLogger("httpx").info("detalhe que não interessa")
    finally:
        configure_logging()
    lines = [json.loads(line) for line in buffer.getvalue().splitlines()]
    assert [(r["event"], r["biblioteca"], r["servico"]) for r in lines] == [
        ("conexão recusada", "httpx", "bot")
    ]


def test_the_version_names_the_installed_commit_when_there_is_one() -> None:
    from osa import __version__
    from osa.core.version import full_version

    version = full_version()
    assert version == __version__ or (
        version.startswith(__version__ + "+") and len(version.split("+")[1]) == 7
    )
