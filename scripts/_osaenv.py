"""Helper of ``backup.sh`` and ``restore.sh`` (TASK-112), run with the installation's Python.

The shell scripts never parse the configuration or a URL themselves:

- ``backup-env``: reads ``OSA_ENV_FILE`` (the installation's ``.env``, parsed as the app parses
  it) and the environment, checks that ``OSA_BACKUP_DIR`` is on a network share (``smbfs``/
  ``cifs``) and outside every registered root, and prints ``export`` lines for the ``PG*``
  variables of ``OSA_DATABASE_URL`` (the password never goes on a command line).
- ``pg-env URL``: the ``PG*`` exports of one URL.
- ``check-empty URL``: exit 0 only when the database has no relation outside the system schemas.
- ``check-restored URL``: the append-only triggers exist and are enabled; prints the migration.
- ``audit-compare RESTORED SOURCE``: every restored audit row equals the source row with the
  same id (the trail only grows: the source may have more, never different).

Errors are one line on stderr and never repeat a URL or a password.
"""

from __future__ import annotations

import hashlib
import os
import shlex
import subprocess
import sys
from pathlib import Path
from typing import Any

PG_VARIABLES = ("PGHOST", "PGPORT", "PGUSER", "PGPASSWORD", "PGDATABASE")
NETWORK_FS = frozenset({"smbfs", "cifs", "smb3", "nfs", "afpfs"})
AUDIT_ROW = (
    "SELECT id, md5(concat_ws('|', id::text, to_char(timestamp AT TIME ZONE 'UTC', "
    "'YYYY-MM-DD\"T\"HH24:MI:SS.US'), ator, acao, alvo_tipo, coalesce(alvo_id, ''), "
    "coalesce(sha256, ''), resultado, detalhes::text)) FROM audit_events"
)


USAGE = (
    "uso: _osaenv.py backup-env | pg-env URL | check-empty URL | check-restored URL | "
    "audit-compare RESTAURADO ORIGEM"
)


class Refused(Exception):
    """A check failed; the message is safe to print."""


def _url(raw: str) -> Any:
    from sqlalchemy.engine import make_url

    try:
        url = make_url(raw)
    except Exception:
        raise Refused("URL de banco inválida") from None
    if url.get_backend_name() not in {"postgresql", "postgres"} or url.query or not url.host:
        raise Refused("URL de banco inválida (só postgresql, com host e sem parâmetros)")
    if not url.database or not url.username:
        raise Refused("URL de banco sem banco ou sem usuário")
    return url


def pg_exports(raw: str) -> str:
    url = _url(raw)
    values = {
        "PGHOST": url.host,
        "PGPORT": str(url.port or 5432),
        "PGUSER": url.username,
        "PGPASSWORD": url.password or "",
        "PGDATABASE": url.database,
    }
    return "\n".join(f"export {name}={shlex.quote(str(value))}" for name, value in values.items())


def _engine(raw: str) -> Any:
    from sqlalchemy import create_engine

    url = _url(raw).set(drivername="postgresql+psycopg")
    return create_engine(url)


def mount_fstype(path: Path, mount_output: str | None = None) -> str | None:
    """The filesystem type of the mount that holds ``path`` (longest mount point that matches).

    Reads ``mount`` output: ``X on /mnt (smbfs, ...)`` (macOS) or ``X on /mnt type cifs (...)``
    (Linux). ``None`` when nothing matches.
    """
    text = mount_output if mount_output is not None else subprocess.run(
        ["mount"], capture_output=True, text=True, check=False
    ).stdout  # fmt: skip
    target = str(path)
    best: tuple[int, str] | None = None
    for line in text.splitlines():
        if " on " not in line:
            continue
        rest = line.split(" on ", 1)[1]
        if " type " in rest:
            point, _, tail = rest.partition(" type ")
            fstype = tail.split()[0]
        elif " (" in rest:
            point, _, tail = rest.rpartition(" (")
            fstype = tail.split(",")[0].rstrip(")").strip()
        else:
            continue
        inside = target == point or target.startswith(point.rstrip("/") + "/")
        if inside and (best is None or len(point) > best[0]):
            best = (len(point), fstype)
    return best[1] if best else None


def _inside(path: str, root: str) -> bool:
    from osa.fs.names import component_key

    key = [component_key(p) for p in Path(path).parts]
    root_key = [component_key(p) for p in Path(root).parts]
    return key[: len(root_key)] == root_key


def backup_env() -> str:
    """Validate the backup target and return the ``PG*`` exports of the database."""
    env_file = os.environ.get("OSA_ENV_FILE")
    values: dict[str, str | None] = {}
    if env_file:
        from dotenv import dotenv_values

        if not Path(env_file).is_file():
            raise Refused("OSA_ENV_FILE não existe")
        values = dict(dotenv_values(env_file))
    merged = {**values, **{k: v for k, v in os.environ.items() if k.startswith("OSA_")}}
    url, folder = merged.get("OSA_DATABASE_URL"), merged.get("OSA_BACKUP_DIR")
    if not url:
        raise Refused("OSA_DATABASE_URL não definido")
    if not folder:
        raise Refused("OSA_BACKUP_DIR não definido")
    target = Path(folder)
    if target.is_symlink() or not target.is_dir():
        raise Refused("pasta de backup inexistente ou é link (o share está montado?)")
    real = Path(os.path.realpath(target))
    if merged.get("OSA_BACKUP_ALLOW_LOCAL") != "1":
        fstype = mount_fstype(real)
        if fstype not in NETWORK_FS:
            raise Refused(
                f"a pasta de backup não está num share de rede ({fstype or 'desconhecido'}): "
                "use a pasta do share, nunca o disco desta máquina"
            )
    from sqlalchemy import text

    engine = _engine(url)
    try:
        with engine.connect() as connection:
            roots = [r for (r,) in connection.execute(text("SELECT caminho FROM roots"))]
    except Exception:
        raise Refused("não foi possível ler o cadastro de raízes no banco") from None
    finally:
        engine.dispose()
    if any(_inside(str(real), root) or _inside(root, str(real)) for root in roots):
        raise Refused("a pasta de backup está dentro de uma raiz (ou contém uma): use outra")
    return pg_exports(url) + f"\nexport OSA_BACKUP_DIR={shlex.quote(str(real))}"


def check_empty(raw: str) -> None:
    from sqlalchemy import text

    engine = _engine(raw)
    try:
        with engine.connect() as connection:
            count = connection.execute(
                text(
                    "SELECT count(*) FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace "
                    "WHERE n.nspname NOT IN ('pg_catalog', 'information_schema') "
                    "AND n.nspname NOT LIKE 'pg_toast%' AND n.nspname NOT LIKE 'pg_temp%'"
                )
            ).scalar_one()
    finally:
        engine.dispose()
    if count:
        raise Refused(f"o banco de destino não está vazio ({count} objetos)")


def check_restored(raw: str) -> str:
    from sqlalchemy import text

    engine = _engine(raw)
    try:
        with engine.connect() as connection:
            enabled = connection.execute(
                text(
                    "SELECT count(*) FROM pg_trigger WHERE tgrelid = 'audit_events'::regclass "
                    "AND tgname LIKE 'audit_events_no_%' AND tgenabled <> 'D'"
                )
            ).scalar_one()
            version = connection.execute(text("SELECT version_num FROM alembic_version")).scalar()
            last = connection.execute(
                text("SELECT coalesce(max(id), 0) FROM audit_events")
            ).scalar()
    finally:
        engine.dispose()
    if enabled != 2:
        raise Refused("a auditoria restaurada perdeu a proteção append-only")
    return f"restaurado: migração {version}, auditoria até o evento {last}"


def audit_compare(restored_raw: str, source_raw: str, chunk: int = 1000) -> str:
    from sqlalchemy import text

    restored, source = _engine(restored_raw), _engine(source_raw)
    total = 0
    digest = hashlib.sha256()
    try:
        with restored.connect() as rc, source.connect() as sc:
            rows = rc.execute(text(AUDIT_ROW + " ORDER BY id")).all()
            for start in range(0, len(rows), chunk):
                part = rows[start : start + chunk]
                ids = [row[0] for row in part]
                found = dict(
                    sc.execute(text(AUDIT_ROW + " WHERE id = ANY(:ids)"), {"ids": ids}).all()
                )
                for event_id, row_md5 in part:
                    if found.get(event_id) != row_md5:
                        raise Refused(
                            f"a auditoria restaurada difere da origem no evento {event_id}"
                        )
                    digest.update(row_md5.encode())
                total += len(part)
    finally:
        restored.dispose()
        source.dispose()
    return (
        f"auditoria conferida com a origem: {total} eventos idênticos ({digest.hexdigest()[:12]})"
    )


def main(argv: list[str]) -> int:
    try:
        match argv[1:]:
            case ["backup-env"]:
                print(backup_env())
            case ["pg-env", url]:
                print(pg_exports(url))
            case ["check-empty", url]:
                check_empty(url)
            case ["check-restored", url]:
                print(check_restored(url))
            case ["audit-compare", restored, source]:
                print(audit_compare(restored, source))
            case _:
                print(USAGE, file=sys.stderr)
                return 2
    except Refused as error:
        print(f"erro: {error}", file=sys.stderr)
        return 1
    except Exception as error:  # never the message: it could carry a URL or a password
        print(f"erro: falha inesperada ({type(error).__name__})", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
