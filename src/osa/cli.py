"""Command line entrypoint.

``config check`` reads configuration only. ``admin`` and ``panel`` manage the registry;
``process``, ``undo``, ``scan`` and ``search`` drive the courier and the index: heavy
imports stay inside the commands so ``--version`` and ``config check`` never touch the
database or the web stack. Errors are one line and never carry a secret or a DSN.
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


def _default_model(settings: Any) -> tuple[str, str | None]:
    """The effective model: the registry row when a database is configured, else the env."""
    if settings.database_url is None:
        return settings.ollama_model, settings.ollama_model_digest
    from osa.admin import services

    config = _in_transaction(lambda session: services.effective_model_config(session, settings))
    return config.nome, config.digest


@app.command("eval")
def eval_command(
    corpus: Path = typer.Option(..., "--corpus", help="Corpus directory (corpus.json inside)."),
    out: Path | None = typer.Option(
        None, "--out", help="Write reports here (new folder, mode 0600); default: stdout only."
    ),
    model: list[str] | None = typer.Option(
        None, "--model", help="Model to evaluate; repeat to compare. Default: the configured one."
    ),
    digest: list[str] | None = typer.Option(
        None, "--digest", help="Digest of the Nth --model (same order)."
    ),
    meta: float = typer.Option(0.90, "--meta", min=0.0, max=1.0, help="Department accuracy goal."),
) -> None:
    """Evaluate the model(s) on a corpus: accuracy, review rate, latency, improper effects.

    Exit 0: every model met the goal. 2: one missed it. 1: corpus refused or a model could
    not be evaluated. Nothing is saved to the registry.
    """
    from osa.core.logging import configure_logging
    from osa.domain.errors import DomainError
    from osa.extract.model import ExtractLimits
    from osa.llm import eval as harness
    from osa.llm.ollama import literal_loopback_url

    configure_logging("WARNING")  # stdout is the report: only warnings, as JSON on stderr
    settings = _settings()
    try:
        loaded = harness.load_corpus(corpus, sensitive_extensions=settings.sensitive_extensions)
        if not loaded.ficticio and out is None:
            raise ValueError("corpus real exige --out, fora de qualquer repositório")
        specs = harness.pair_models(
            model, digest, ("", None) if model else _default_model(settings)
        )
        # Before the first byte: where the documents are going (the default URL can be a tunnel).
        target_url = literal_loopback_url(settings.ollama_base_url)
        for name, pinned in specs:
            typer.echo(
                f"avaliando {name} (digest {pinned or 'não fixado'}) em {target_url}: "
                f"{len(loaded.entries)} documentos",
                err=True,
            )
        comparison = harness.run_comparison(
            loaded,
            specs,
            meta=meta,
            make_target=lambda name, pinned: harness.make_target(settings, name, pinned),
            limits=ExtractLimits.from_settings(settings),
            extractor=harness.extract_document,
        )
        folder = harness.write_reports(comparison, out) if out is not None else None
    except (DomainError, ValueError) as error:
        _fail(str(error))
    for report in comparison.reports:
        if report.error:
            typer.echo(f"erro: {report.model}: {report.error}", err=True)
    if any(report.results for report in comparison.reports):
        for report in comparison.reports:
            typer.echo(report.to_markdown())
        if len(comparison.reports) > 1:
            typer.echo(comparison.to_markdown())
    if folder is not None:
        typer.echo(f"relatórios em {folder}")
    raise typer.Exit(comparison.exit_code)


def _origin(value: str) -> Any:
    from osa.domain.document import Origin

    try:
        return Origin(value)
    except ValueError:
        _fail("origem deve ser telegram, watched ou scan")


def _describe_outcome(outcome: Any) -> str:
    from osa.courier import pipeline

    match outcome:
        case pipeline.Archived(simulated=True, destino=destino):
            return f"simulado (efeitos desligados): destino previsto {destino}"
        case pipeline.Archived(destino=destino, operation_id=operation_id):
            return f"arquivado em {destino} (operação {operation_id})"
        case pipeline.Reviewed(case_id=case_id, motivo=motivo):
            return f"em revisão (caso {case_id}): {motivo}"
        case pipeline.Duplicate(caminho=caminho, estado=estado):
            return f"duplicata: já consta no índice ({estado}) em {caminho or 'origem'}"
        case pipeline.NotSupported(motivo=motivo):
            return f"não suportado: {motivo}"
        case pipeline.Failed(motivo=motivo):
            return f"FALHOU: {motivo}"
    return str(outcome)


@app.command("process")
def process_command(
    file: Path = typer.Argument(..., help="File to process (fictitious data in development)."),
    dono: str = typer.Option(..., "--dono", help="Declared owner: a registered alias or CNPJ."),
    origem: str = typer.Option("telegram", "--origem", help="telegram, watched or scan."),
) -> None:
    """Run the courier on one file: quarantine, hash, extraction, envelope, decision, archive.

    With OSA_ENABLE_FILE_EFFECTS=true the file is copied to the quarantine of the owner's
    root and archived by rename; otherwise everything is decided and recorded as simulated.
    Exit 0 for any decided outcome, 1 for FALHOU, a refused file or a configuration error.
    """
    from osa.admin import services
    from osa.courier import owners, pipeline
    from osa.courier.intake import Intake, stage_upload
    from osa.courier.ports import LocalContent, LocalFs
    from osa.courier.store import SqlStore
    from osa.db.session import DatabaseNotConfigured
    from osa.domain.errors import DomainError
    from osa.extract.model import ExtractLimits
    from osa.llm.provider import build_local_provider

    settings = _settings()
    origin = _origin(origem)
    fs = LocalFs()
    content = LocalContent(ExtractLimits.from_settings(settings))
    try:
        source = file.resolve(strict=True)
        if file.is_symlink() or not source.is_file():
            _fail("arquivo não existe ou não é um arquivo comum")
        with _session_factory()() as session:
            owner = owners.resolve_declared_owner(session, dono)
            if owner is None:
                _fail("dono não encontrado no cadastro (alias ou CNPJ exato)")
            root = owners.upload_root(session, owner, fs=fs)
            config = services.effective_model_config(session, settings)
            if settings.enable_file_effects:
                with open(source, "rb") as handle:
                    staged = stage_upload(
                        Path(root.caminho),
                        handle.read(),
                        source.name,
                        sensitive_extensions=settings.sensitive_extensions,
                        effects_enabled=True,
                    )
                typer.echo(f"gravado na quarentena: {staged.relative_path}", err=True)
                location, relative = staged.path, staged.relative_path
            else:
                location, relative = source, None
            intake = Intake(
                root.id, location, relative, source.name, origin, declared_owner=owner.id
            )
            with build_local_provider(settings, config) as llm:
                outcome = pipeline.process(
                    intake,
                    store=SqlStore(session),
                    fs=fs,
                    content=content,
                    llm=llm,
                    actor="cli:process",
                    effects_enabled=settings.enable_file_effects,
                )
    except (DatabaseNotConfigured, DomainError, ValueError) as error:
        _fail(str(error))
    except OSError:
        _fail("arquivo não existe ou não pode ser lido")
    except OperationalError:
        _fail("banco de dados inacessível: confira OSA_DATABASE_URL e se o PostgreSQL está de pé")
    except SQLAlchemyError:
        _fail("erro de banco de dados ou esquema não migrado: rode `alembic upgrade head`")
    typer.echo(f"documento {outcome.document_id} [{outcome.estado}]: {_describe_outcome(outcome)}")
    if isinstance(outcome, pipeline.Failed):
        raise typer.Exit(1)


@app.command("undo")
def undo_command(
    operation_id: int = typer.Argument(..., help="Id of the archive operation to undo."),
) -> None:
    """Move an archived file back to where it came from (checked by its sha256)."""
    from osa.courier.ports import LocalFs
    from osa.courier.store import SqlStore
    from osa.courier.undo import undo_operation

    settings = _settings()
    result = _in_transaction(
        lambda session: undo_operation(
            operation_id,
            store=SqlStore(session),
            fs=LocalFs(),
            actor="cli:undo",
            effects_enabled=settings.enable_file_effects,
        )
    )
    if result.simulated:
        typer.echo(f"simulado (efeitos desligados): o arquivo voltaria para {result.destino}")
    else:
        typer.echo(
            f"desfeito: documento {result.document_id} de volta em {result.destino} "
            f"(operação {result.undo_operation_id})"
        )


def _parse_date(value: str | None, label: str) -> Any:
    from datetime import date

    if value is None:
        return None
    try:
        return date.fromisoformat(value)
    except ValueError:
        _fail(f"{label} deve ser uma data AAAA-MM-DD")


@app.command("scan")
def scan_command(
    raiz: list[int] | None = typer.Option(
        None, "--raiz", help="Root id to scan; repeat for several. Default: every active root."
    ),
    desde: str | None = typer.Option(
        None, "--desde", help="Only files modified on or after this date (AAAA-MM-DD)."
    ),
    reiniciar: bool = typer.Option(
        False, "--reiniciar", help="Start over instead of resuming an interrupted scan."
    ),
) -> None:
    """Index what is already in the roots (owner and department from the path); moves nothing.

    Resumable: an interrupted scan continues from the last committed file. Exit 0 when every
    root finished, 1 when a root could not be finished or the configuration is refused.
    """
    from datetime import UTC, datetime, time

    from osa.admin import services
    from osa.core.logging import configure_logging
    from osa.courier.pipeline import ConfigurationError
    from osa.courier.ports import LocalContent, LocalFs
    from osa.courier.store import SqlStore
    from osa.db.session import DatabaseNotConfigured
    from osa.domain.errors import Conflict, DomainError
    from osa.extract.model import ExtractLimits
    from osa.llm.provider import build_local_provider
    from osa.worker.scan import ScanInterrupted, ScanOptions, scan_root

    settings = _settings()
    configure_logging("WARNING")
    since_date = _parse_date(desde, "--desde")
    since = (
        datetime.combine(since_date, time.min).astimezone().astimezone(UTC)
        if since_date is not None
        else None
    )
    options = ScanOptions(since=since, resume=not reiniciar)
    fs = LocalFs()
    content = LocalContent(ExtractLimits.from_settings(settings))
    interrupted = False
    try:
        with _session_factory()() as session:
            store = SqlStore(session)
            roots = (
                [store.root(root_id) for root_id in raiz] if raiz else list(store.active_roots())
            )
            if not roots:
                _fail("nenhuma raiz ativa no cadastro")
            config = services.effective_model_config(session, settings)
            with build_local_provider(settings, config) as llm:
                for root in roots:
                    typer.echo(f"raiz {root.id} ({root.caminho}): varrendo", err=True)
                    try:
                        summary = scan_root(
                            root.id,
                            store=store,
                            fs=fs,
                            content=content,
                            llm=llm,
                            actor="cli:scan",
                            options=options,
                        )
                    except ScanInterrupted as error:
                        interrupted = True
                        typer.echo(f"raiz {root.id}: interrompida: {error}", err=True)
                        continue
                    except Conflict as error:
                        interrupted = True
                        typer.echo(f"raiz {root.id}: recusada: {error}", err=True)
                        continue
                    typer.echo(f"raiz {root.id}: {_describe_scan(summary)}")
    except ConfigurationError as error:
        _fail(f"configuração recusada: {error}")
    except (DatabaseNotConfigured, DomainError, ValueError) as error:
        _fail(str(error))
    except OperationalError:
        _fail("banco de dados inacessível: confira OSA_DATABASE_URL e se o PostgreSQL está de pé")
    except SQLAlchemyError:
        _fail("erro de banco de dados ou esquema não migrado: rode `alembic upgrade head`")
    if interrupted:
        raise typer.Exit(1)


def _describe_scan(summary: Any) -> str:
    parts = [
        f"{summary.arquivos} arquivos",
        f"{summary.novos} novos",
        f"{summary.vistos} já indexados",
        f"{summary.reprocessados} reprocessados",
        f"{summary.movidos} movidos",
        f"{summary.duplicatas} duplicatas",
        f"{summary.fora_do_padrao} fora do padrão",
        f"{summary.sensiveis} sensíveis",
        f"{summary.nao_suportados} não suportados",
        f"{summary.falhas} falhas",
        f"{summary.ausentes} ausentes",
    ]
    if summary.ignorados_por_data:
        parts.append(f"{summary.ignorados_por_data} anteriores a --desde")
    if summary.pastas_ilegiveis:
        parts.append(f"{summary.pastas_ilegiveis} pastas ilegíveis")
    prefix = "retomada e concluída" if summary.retomado else "concluída"
    return f"{prefix}: " + ", ".join(parts)


@app.command("search")
def search_command(
    texto: str = typer.Argument("", help="Keywords (every one must match); may be empty."),
    dono: str | None = typer.Option(None, "--dono", help="Owner: alias, CNPJ, name or folder."),
    departamento: str | None = typer.Option(None, "--departamento", help="Template name."),
    tipo: str | None = typer.Option(None, "--tipo", help="Document type (contrato, boleto)."),
    de: str | None = typer.Option(None, "--de", help="Period start (AAAA-MM-DD)."),
    ate: str | None = typer.Option(None, "--ate", help="Period end (AAAA-MM-DD)."),
    max_hits: int = typer.Option(5, "--max", min=1, max=50, help="Hits to show."),
) -> None:
    """Search the index (full text in Portuguese plus filters); no model, no file is read.

    Exit 0 with hits, 2 with none (or an owner/department the registry does not know).
    """
    from osa.db.session import DatabaseNotConfigured
    from osa.domain.errors import DomainError
    from osa.llm.envelopes import QueryEnvelope
    from osa.search.service import SearchLimits, filters_for, run_search

    settings = _settings()
    words = [word for word in texto.split() if word]
    if len(words) > 10:
        _fail("no máximo 10 palavras-chave")
    try:
        envelope = QueryEnvelope(
            dono=dono,
            departamento=departamento,
            tipo_documental=tipo,
            periodo_inicio=_parse_date(de, "--de"),
            periodo_fim=_parse_date(ate, "--ate"),
            palavras_chave=words,
        )
    except ValidationError as error:
        _fail("; ".join(item["msg"] for item in error.errors(include_url=False)))
    limits = SearchLimits(
        max_hits=max_hits, sensitive_extensions=tuple(settings.sensitive_extensions)
    )
    try:
        with _session_factory()() as session:
            filters = filters_for(session, envelope)
            if filters.owner_unresolved is not None:
                typer.echo(f"dono não encontrado no cadastro: {filters.owner_unresolved}")
                raise typer.Exit(2)
            if filters.department_unresolved is not None:
                typer.echo(
                    f"departamento não encontrado no template: {filters.department_unresolved}"
                )
                raise typer.Exit(2)
            hits = run_search(session, filters, limits)
    except (DatabaseNotConfigured, DomainError, ValueError) as error:
        _fail(str(error))
    except OperationalError:
        _fail("banco de dados inacessível: confira OSA_DATABASE_URL e se o PostgreSQL está de pé")
    except SQLAlchemyError:
        _fail("erro de banco de dados ou esquema não migrado: rode `alembic upgrade head`")
    if not hits:
        typer.echo("nenhum documento encontrado")
        raise typer.Exit(2)
    for hit in hits:
        flags = " [sensível]" if hit.sensivel else ""
        tipo_text = hit.tipo_documental or "tipo desconhecido"
        ano_text = f" {hit.ano}" if hit.ano is not None else ""
        typer.echo(f"{hit.document_id} [{hit.estado}] {hit.caminho}{flags}")
        typer.echo(f"    {tipo_text}{ano_text}: {(hit.resumo or '').strip()[:120]}")


@app.command("worker")
def worker_command() -> None:
    """Run the worker (watched folders, periodic rescan, daily reminder) until stopped.

    Same as the ``osa-worker`` entrypoint. Without OSA_TELEGRAM_BOT_TOKEN the notices to the
    reviewers are only logged.
    """
    from osa.core.logging import configure_logging
    from osa.worker.app import run

    settings = _settings()
    if settings.database_url is None:
        _fail("database is not configured: set OSA_DATABASE_URL")
    configure_logging(
        service="worker", log_file=str(settings.log_file) if settings.log_file else None
    )
    typer.echo(
        "worker em polling a cada "
        f"{settings.poll_interval_seconds} s (efeitos de arquivo "
        f"{'ligados' if settings.enable_file_effects else 'desligados'})",
        err=True,
    )
    run(settings)


@app.command("bot")
def bot_command() -> None:
    """Run the Telegram bot (long polling) until stopped; same as the ``osa-bot`` entrypoint."""
    from osa.core.logging import configure_logging
    from osa.telegram.app import run

    settings = _settings()
    if settings.telegram_bot_token is None:
        _fail("OSA_TELEGRAM_BOT_TOKEN não configurado")
    if settings.database_url is None:
        _fail("database is not configured: set OSA_DATABASE_URL")
    configure_logging(service="bot", log_file=str(settings.log_file) if settings.log_file else None)
    typer.echo(
        "bot do Telegram em long polling "
        f"(efeitos de arquivo {'ligados' if settings.enable_file_effects else 'desligados'})",
        err=True,
    )
    run(settings)


def main() -> None:
    app()
