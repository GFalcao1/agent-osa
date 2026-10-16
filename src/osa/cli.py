"""Command line entrypoint.

``config check`` reads configuration only. ``admin`` and ``panel`` manage the registry:
heavy imports stay inside the commands so ``--version`` and ``config check`` never touch
the database or the web stack. Errors are one line and never carry a secret or a DSN.
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import Any, NoReturn

import typer
from pydantic import ValidationError
from sqlalchemy.exc import IntegrityError, OperationalError, SQLAlchemyError
from sqlalchemy.orm import Session

from osa import __version__


def _typer(**kwargs: Any) -> typer.Typer:
    """No rich tracebacks and never frame locals: an unexpected error must not print a DSN."""
    return typer.Typer(
        no_args_is_help=True,
        pretty_exceptions_enable=False,
        pretty_exceptions_show_locals=False,
        **kwargs,
    )


app = _typer(name="osa", help="Agent O.S.A.", add_completion=False)
config_app = _typer(help="Configuration commands.")
admin_app = _typer(help="Registry administration.")
app.add_typer(config_app, name="config")
app.add_typer(admin_app, name="admin")


def _version(value: bool) -> None:
    if value:
        typer.echo(f"osa {__version__}")
        raise typer.Exit(0)


@app.callback()
def root(
    version: bool = typer.Option(
        False, "--version", callback=_version, is_eager=True, help="Show version and exit."
    ),
) -> None:
    del version


@config_app.command("check")
def config_check() -> None:
    """Validate configuration (env and .env only; no network, DB or filesystem access)."""
    from osa.core.settings import Settings

    try:
        settings = Settings()
    except ValidationError as error:
        typer.echo("configuration invalid:", err=True)
        for item in error.errors(include_input=False, include_url=False):
            where = ".".join(str(part) for part in item["loc"]) or "settings"
            typer.echo(f"  - {where}: {item['msg']}", err=True)
        raise typer.Exit(1) from None
    typer.echo(f"configuration ok (environment={settings.environment})")


def _fail(message: str) -> NoReturn:
    typer.echo(f"erro: {message}", err=True)
    raise typer.Exit(1)


def _session_factory() -> Callable[[], Any]:
    from osa.db.session import get_sessionmaker

    return get_sessionmaker()


def _settings() -> Any:
    from osa.core.settings import Settings

    try:
        return Settings()
    except ValidationError as error:
        typer.echo("configuration invalid:", err=True)
        for item in error.errors(include_input=False, include_url=False):
            where = ".".join(str(part) for part in item["loc"]) or "settings"
            typer.echo(f"  - {where}: {item['msg']}", err=True)
        raise typer.Exit(1) from None


def _in_transaction[T](work: Callable[[Session], T]) -> T:
    """Run ``work`` in one transaction: commit on success, roll back and report on refusal."""
    from osa.db.session import DatabaseNotConfigured
    from osa.domain.errors import DomainError

    try:
        with _session_factory()() as session:
            result = work(session)
            session.commit()
            return result
    except (DatabaseNotConfigured, DomainError, ValueError) as error:
        _fail(str(error))
    except IntegrityError:
        _fail("conflito com o cadastro existente")
    except OperationalError:
        _fail("banco de dados inacessível: confira OSA_DATABASE_URL e se o PostgreSQL está de pé")
    except SQLAlchemyError:
        _fail("erro de banco de dados ou esquema não migrado: rode `alembic upgrade head`")


@admin_app.command("bootstrap")
def admin_bootstrap(
    telegram_id: int = typer.Option(..., "--telegram-id", help="Telegram user id of the admin."),
    nome: str = typer.Option(..., "--nome", help="Admin display name."),
) -> None:
    """Create the first administrator (only once; the panel never creates one)."""
    from osa.admin import services

    admin = _in_transaction(
        lambda session: services.bootstrap_admin(
            session, telegram_id=telegram_id, nome=nome, actor="cli:bootstrap"
        )
    )
    del admin
    typer.echo(f"administrador criado (telegram_id={telegram_id})")


@admin_app.command("export")
def admin_export(
    out: Path | None = typer.Option(
        None, "--out", help="Write here (new file, mode 0600); default: stdout."
    ),
) -> None:
    """Export the registry, users included, as YAML (never tokens or keys)."""
    from osa.admin import export

    text = _in_transaction(export.export_yaml)
    if out is None:
        typer.echo(text, nl=False)
        return
    try:
        export.write_export_file(out, text)
    except FileExistsError:
        _fail("arquivo de saída já existe: não sobrescrevo")
    except OSError:
        _fail("não foi possível criar o arquivo de saída")
    typer.echo(f"cadastro exportado para {out}")


@admin_app.command("import")
def admin_import(
    file: Path = typer.Argument(..., help="YAML written by `osa admin export`."),
) -> None:
    """Import a YAML registry into an empty registry (admin already bootstrapped)."""
    from osa.admin import export, services

    try:
        registry = export.parse_registry_yaml(export.read_registry_file(file))
    except ValueError as error:
        _fail(str(error))
    settings = _settings()
    summary = _in_transaction(
        lambda session: export.import_registry(
            session,
            registry,
            settings=settings,
            is_mount_point=services.default_is_mount_point,
            actor="cli:import",
        )
    )
    typer.echo(
        "cadastro importado: "
        f"{summary.raizes} raízes, {summary.departamentos} departamentos, "
        f"{summary.donos} donos, {summary.usuarios} usuários"
    )


@app.command("panel")
def panel_command(
    host: str = typer.Option("127.0.0.1", "--host", help="Only 127.0.0.1 is accepted."),
    port: int = typer.Option(8765, "--port", help="Panel port (1024-65535)."),
) -> None:
    """Serve the local configuration panel (no authentication: loopback only)."""
    from osa.db.session import DatabaseNotConfigured
    from osa.panel import app as panel

    try:
        host = panel.validate_bind_host(host)
        port = panel.validate_port(port)
    except ValueError as error:
        _fail(str(error))
    settings = _settings()
    typer.echo(f"painel em http://{host}:{port} (sem autenticação própria; só nesta máquina)")
    try:
        panel.serve(host, port, settings=settings)
    except DatabaseNotConfigured as error:
        _fail(str(error))


def main() -> None:
    app()
