"""``scripts/backup.sh`` e ``scripts/restore.sh`` contra o PostgreSQL descartável (TASK-112).

Precisa das ferramentas cliente do PostgreSQL no PATH com a mesma versão principal do servidor
(``pg_dump`` recusa servidor mais novo); sem elas o teste é pulado com o motivo e o requisito
de backup da homologação fica PENDENTE.
"""

from __future__ import annotations

import os
import re
import secrets
import shutil
import subprocess
import sys
import time
from collections.abc import Iterator
from pathlib import Path

import pytest
from sqlalchemy import create_engine, func, select, text
from sqlalchemy.engine import URL
from sqlalchemy.orm import Session

from osa.admin import services
from osa.db.models import AuditEvent

ROOT = Path(__file__).resolve().parents[2]
BACKUP = ROOT / "scripts" / "backup.sh"
RESTORE = ROOT / "scripts" / "restore.sh"


def _major(tool: str) -> int | None:
    found = shutil.which(tool)
    if found is None:
        return None
    out = subprocess.run([found, "--version"], capture_output=True, text=True, check=False).stdout
    match = re.search(r"(\d+)(?:\.\d+)?", out)
    return int(match.group(1)) if match else None


@pytest.fixture
def tools(migrated_database: URL) -> None:
    engine = create_engine(migrated_database)
    try:
        with engine.connect() as connection:
            server = int(connection.execute(text("SHOW server_version_num")).scalar_one()) // 10000
    finally:
        engine.dispose()
    for tool in ("pg_dump", "pg_restore", "psql"):
        major = _major(tool)
        if major is None or major < server:
            pytest.skip(f"{tool} {major or 'ausente'} não serve para o servidor {server}: PENDENTE")


def _run(script: Path, *args: str, **env: str) -> subprocess.CompletedProcess[str]:
    base = {k: v for k, v in os.environ.items() if not k.startswith(("OSA_", "PG"))}
    base["OSA_PYTHON"] = sys.executable  # the installation's Python (the osa package inside)
    return subprocess.run(  # noqa: S603  # fixed scripts, fictitious data
        ["bash", str(script), *args], env={**base, **env}, capture_output=True, text=True,
        timeout=120, check=False,
    )  # fmt: skip


def _dsn(url: URL) -> str:
    return url.render_as_string(hide_password=False).replace("postgresql+psycopg", "postgresql")


def _seed(url: URL) -> int:
    engine = create_engine(url)
    try:
        with Session(engine) as session:
            services.bootstrap_admin(session, telegram_id=1001, nome="Admin", actor="test:boot")
            services.create_department(session, nome="Fiscal", actor="test:setup")
            services.create_owner(session, nome_fantasia="Alfa Comércio", actor="test:setup")
            session.commit()
            return int(session.scalar(select(func.count()).select_from(AuditEvent)) or 0)
    finally:
        engine.dispose()


def test_backup_writes_a_private_dump_and_applies_the_retention(
    tools: None, migrated_database: URL, tmp_path: Path
) -> None:
    _seed(migrated_database)
    folder = tmp_path / "backup"
    folder.mkdir()
    old = folder / "osa-20240101T000000Z.dump"
    old.write_bytes(b"antigo")
    stamp = time.time() - 40 * 86400
    os.utime(old, (stamp, stamp))
    other = folder / "planilha-do-financeiro.xlsx"  # not the script's: never touched
    other.write_bytes(b"x")
    os.utime(other, (stamp, stamp))

    done = _run(BACKUP, OSA_DATABASE_URL=_dsn(migrated_database), OSA_BACKUP_DIR=str(folder),
                OSA_BACKUP_ALLOW_LOCAL="1")  # fmt: skip

    assert done.returncode == 0, done.stderr
    dump = Path(done.stdout.strip())
    assert dump.parent == folder and dump.name.startswith("osa-") and dump.stat().st_size > 0
    assert oct(dump.stat().st_mode & 0o777) == "0o600"
    sidecar = Path(f"{dump}.sha256")
    assert sidecar.read_text().split()[1] == dump.name
    assert not old.exists() and other.exists()  # only the script's own dumps, by exact name
    assert not list(folder.glob(".osa-backup-*"))
    assert "retenção:" in done.stderr
    password = migrated_database.password
    assert password and str(password) not in done.stdout + done.stderr  # never shown


def test_backup_refuses_this_machines_own_disk(
    tools: None, migrated_database: URL, tmp_path: Path
) -> None:
    done = _run(BACKUP, OSA_DATABASE_URL=_dsn(migrated_database), OSA_BACKUP_DIR=str(tmp_path))

    assert done.returncode != 0 and "não está num share de rede" in done.stderr
    assert list(tmp_path.iterdir()) == []


def test_backup_refuses_a_folder_inside_a_root(
    tools: None, migrated_database: URL, tmp_path: Path
) -> None:
    root = tmp_path / "raiz"
    (root / "Backup OSA").mkdir(parents=True)
    engine = create_engine(migrated_database)
    try:
        with Session(engine) as session:
            services.bootstrap_admin(session, telegram_id=1001, nome="Admin", actor="test:boot")
            services.create_root(
                session, caminho=str(root.resolve()), is_mount_point=lambda _p: True,
                actor="test:setup",
            )  # fmt: skip
            session.commit()
    finally:
        engine.dispose()

    done = _run(BACKUP, OSA_DATABASE_URL=_dsn(migrated_database),
                OSA_BACKUP_DIR=str(root / "Backup OSA"), OSA_BACKUP_ALLOW_LOCAL="1")  # fmt: skip

    assert done.returncode != 0 and "dentro de uma raiz" in done.stderr
    assert list((root / "Backup OSA").iterdir()) == []


def test_backup_reads_the_installation_env_file_as_the_app_does(
    tools: None, migrated_database: URL, tmp_path: Path
) -> None:
    folder = tmp_path / "Backup OSA com espaço"  # a value the shell "source" would break
    folder.mkdir()
    env_file = tmp_path / ".env"
    env_file.write_text(
        f"OSA_DATABASE_URL={_dsn(migrated_database)}\nOSA_BACKUP_DIR={folder}\n"
        "OSA_BACKUP_ALLOW_LOCAL=1\n"
    )

    done = _run(BACKUP, OSA_ENV_FILE=str(env_file))

    assert done.returncode == 0, done.stderr
    assert Path(done.stdout.strip()).parent == folder


@pytest.fixture
def target_database(test_url: URL) -> Iterator[URL]:
    """A second brand-new empty database (the restore target), dropped afterwards."""
    name = f"osa_test_mig_{secrets.token_hex(6)}"
    admin = create_engine(test_url, isolation_level="AUTOCOMMIT")
    try:
        with admin.connect() as connection:
            connection.exec_driver_sql(f'CREATE DATABASE "{name}"')
        yield test_url.set(database=name)
    finally:
        with admin.connect() as connection:
            connection.exec_driver_sql(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)')
        admin.dispose()


def test_restore_rebuilds_an_empty_database_and_the_audit_matches_the_source(
    tools: None, migrated_database: URL, target_database: URL, tmp_path: Path
) -> None:
    empty_database = target_database
    events = _seed(migrated_database)
    folder = tmp_path / "backup"
    folder.mkdir()
    dump = _run(BACKUP, OSA_DATABASE_URL=_dsn(migrated_database), OSA_BACKUP_DIR=str(folder),
                OSA_BACKUP_ALLOW_LOCAL="1").stdout.strip()  # fmt: skip

    done = _run(RESTORE, dump, OSA_RESTORE_URL=_dsn(empty_database),
                OSA_DATABASE_URL=_dsn(migrated_database))  # fmt: skip

    assert done.returncode == 0, done.stderr
    assert "auditoria conferida com a origem" in done.stdout and f"{events} eventos" in done.stdout
    engine = create_engine(empty_database)
    try:
        with engine.connect() as connection:
            assert (
                connection.execute(text("SELECT count(*) FROM audit_events")).scalar_one() == events
            )
            with pytest.raises(Exception, match="append-only|audit"):
                connection.execute(text("DELETE FROM audit_events"))
    finally:
        engine.dispose()

    again = _run(RESTORE, dump, OSA_RESTORE_URL=_dsn(empty_database))
    assert again.returncode != 0 and "não está vazio" in again.stderr


def test_restore_refuses_a_dump_that_does_not_match_its_checksum(
    tools: None, migrated_database: URL, target_database: URL, tmp_path: Path
) -> None:
    _seed(migrated_database)
    folder = tmp_path / "backup"
    folder.mkdir()
    dump = Path(
        _run(BACKUP, OSA_DATABASE_URL=_dsn(migrated_database), OSA_BACKUP_DIR=str(folder),
             OSA_BACKUP_ALLOW_LOCAL="1").stdout.strip()
    )  # fmt: skip
    with dump.open("ab") as handle:
        handle.write(b"corrompido")

    done = _run(RESTORE, str(dump), OSA_RESTORE_URL=_dsn(target_database))

    assert done.returncode != 0 and ".sha256" in done.stderr
