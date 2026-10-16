"""Command line entrypoint. ``config check`` reads configuration only."""

from __future__ import annotations

import typer
from pydantic import ValidationError

from osa import __version__

app = typer.Typer(name="osa", help="Agent O.S.A.", no_args_is_help=True, add_completion=False)
config_app = typer.Typer(help="Configuration commands.", no_args_is_help=True)
app.add_typer(config_app, name="config")


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


def main() -> None:
    app()
