"""``osa-worker``: the second process of an installation (SPEC §12, TASK-111).

Every ``poll_interval_seconds`` the worker runs one watch cycle per active watched folder,
then a part of the periodic rescan (each active root whose last scan is older than
``rescan_interval_hours``, at most ``scan_documents_per_cycle`` documents to the model per
tick, resumable), then the daily reminder of open reviews when it is due. Notices (a review
to decide, a file left at origin) go to the reviewers through the same bot the users talk
to; without a bot token they are only logged. A failure in one folder or root is logged and
the others go on; nothing here moves a file except the courier's archiving.
"""

from __future__ import annotations

import signal
import threading
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime, time, timedelta
from types import FrameType
from typing import Any, Protocol

from sqlalchemy.orm import Session

from osa.core.logging import configure_logging, get_logger, log_context
from osa.core.settings import Settings
from osa.courier.pipeline import ConfigurationError
from osa.courier.ports import ClassifyPort, ContentPort, FsPort
from osa.courier.store import SqlStore
from osa.db.audit import RESULT_FAILED, RESULT_OK
from osa.worker.scan import ScanOptions, scan_root
from osa.worker.watch import (
    CycleSummary,
    Notice,
    WatchOptions,
    mark_delivered,
    pending_notices,
    watch_cycle,
)

log = get_logger("osa.worker")

ACTOR = "worker"
ACAO_LEMBRETE = "reminder.daily"
REMINDER_AT = time(9, 0)
NOTICE_TEXTS = {
    "nao_suportado": (
        "Arquivo da pasta monitorada ficou na origem (não suportado): {caminho} — {motivo}."
    ),
    "duplicata": (
        "Arquivo da pasta monitorada já está no acervo ({motivo}); ficou na origem: {caminho}."
    ),
    "falhou": "Arquivo da pasta monitorada falhou e ficou na origem: {caminho} — {motivo}.",
    "sensivel": (
        "Certificado ou chave na pasta monitorada ficou na origem e não foi lido: {caminho}."
    ),
    "falhou_movido": "Arquivo da pasta monitorada foi movido, mas falhou depois: {motivo}.",
    "perdido": (
        "Arquivo saiu da pasta monitorada enquanto era processado ({motivo}); "
        "o administrador precisa conferir: {caminho}."
    ),
}


class Notifier(Protocol):
    def send(self, notice: Notice) -> bool:
        """Deliver one notice; True when delivered (else it stays in the outbox)."""
        ...

    def remind(self) -> bool:
        """Send the daily reminder of open reviews; True when something was sent."""
        ...


class LogNotifier:
    """Without a bot token: notices only go to the log (and count as delivered)."""

    def send(self, notice: Notice) -> bool:
        log.info("worker.notice", tipo=notice.kind, caminho=notice.caminho)
        return True

    def remind(self) -> bool:
        return False


class TelegramNotifier:
    """Notices through the bot: a review case with its buttons, or one sentence."""

    def __init__(self, ctx: Any) -> None:
        self.ctx = ctx

    def _recipients(self, department_id: int | None) -> list[int]:
        """The reviewers' group, else the department's reviewers, else the administrators."""
        if self.ctx.reviewers_chat_id is not None:
            return [self.ctx.reviewers_chat_id]
        users = list(self.ctx.courier.users_of_department(department_id)) if department_id else []
        if not users:
            users = list(self.ctx.courier.admins())
        return [user.telegram_id for user in users]

    def send(self, notice: Notice) -> bool:
        from osa.telegram.handlers import review

        try:
            if notice.kind == "revisao" and notice.reviewed is not None:
                department = notice.reviewed.departamento_sugerido_id
                for chat_id in self._recipients(department):
                    review.post_case(
                        self.ctx, notice.reviewed, declared_owner=None, fallback_chat_id=chat_id
                    )
                with self.ctx.state.lock:  # the bot process keeps the drafts, not the worker
                    self.ctx.state.drafts.pop(notice.reviewed.case_id, None)
                return True
            template = NOTICE_TEXTS.get(notice.kind)
            if template is None:
                return True  # nothing to say (a review case already decided)
            text = template.format(caminho=notice.caminho, motivo=notice.motivo or "sem motivo")
            for chat_id in self._recipients(None):
                self.ctx.bot.send(chat_id, text)
            return True
        except Exception:  # stays in the outbox: the next tick tries again
            log.exception("worker.notice_failed", tipo=notice.kind)
            return False

    def remind(self) -> bool:
        from osa.telegram import jobs

        return jobs.daily_reminder(self.ctx)


@dataclass(slots=True)
class TickSummary:
    folders: list[CycleSummary] = field(default_factory=list)
    scanned_roots: list[int] = field(default_factory=list)
    delivered: int = 0
    reminded: bool = False
    errors: int = 0


class Worker:
    def __init__(
        self,
        settings: Settings,
        sessions: Callable[[], Session],
        *,
        fs: FsPort,
        content: ContentPort,
        llm: ClassifyPort,
        notifier: Notifier,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        self.settings = settings
        self.sessions = sessions
        self.fs, self.content, self.llm = fs, content, llm
        self.notifier = notifier
        self.clock = clock
        self.options = WatchOptions(
            stable_cycles=settings.stable_cycles,
            min_age_s=float(settings.min_file_age_seconds),
            effects_enabled=settings.enable_file_effects,
        )
        self._last_root: int | None = None  # rescan round-robin: never stuck on one root

    def tick(self) -> TickSummary:
        """One round: watch every folder, deliver notices, a part of the rescan, the reminder."""
        tick = TickSummary()
        now = self.clock()
        with self.sessions() as session:
            folder_ids = [f.id for f in SqlStore(session).watched_folders()]
        for folder_id in folder_ids:
            with log_context(watched_folder_id=str(folder_id)), self.sessions() as session:
                try:
                    summary = watch_cycle(
                        folder_id, store=SqlStore(session), fs=self.fs, content=self.content,
                        llm=self.llm, actor=ACTOR, options=self.options, now=now,
                    )  # fmt: skip
                except ConfigurationError:
                    log.exception("worker.configuration_refused")
                    tick.errors += 1
                    break  # the template cannot go to the model: no folder can be processed
                except Exception:  # one folder never starves the others
                    log.exception("worker.folder_failed")
                    tick.errors += 1
                    continue
            tick.folders.append(summary)
        self._deliver(tick)
        self._rescan(tick, now)
        self._remind(tick, now)
        return tick

    def _deliver(self, tick: TickSummary) -> None:
        try:
            with self.sessions() as session:
                store = SqlStore(session)
                for notice in pending_notices(store):
                    if self.notifier.send(notice) and notice.entry_id is not None:
                        mark_delivered(store, notice.entry_id)
                        tick.delivered += 1
        except Exception:
            log.exception("worker.deliver_failed")
            tick.errors += 1

    def _rescan(self, tick: TickSummary, now: datetime) -> None:
        due = now - timedelta(hours=self.settings.rescan_interval_hours)
        try:
            with self.sessions() as session:
                roots = [
                    r.id
                    for r in SqlStore(session).active_roots()
                    if r.scan_iniciado_em is not None
                    or r.ultimo_scan is None
                    or r.ultimo_scan < due
                ]
        except Exception:
            log.exception("worker.rescan_failed")
            tick.errors += 1
            return
        if not roots:
            return
        later = [r for r in roots if self._last_root is None or r > self._last_root]
        root_id = (later or roots)[0]  # one root per tick, in turn
        self._last_root = root_id
        with log_context(root_id=str(root_id)), self.sessions() as session:
            try:
                scan_root(
                    root_id, store=SqlStore(session), fs=self.fs, content=self.content,
                    llm=self.llm, actor=ACTOR,
                    options=ScanOptions(
                        max_indexed=self.settings.scan_documents_per_cycle,
                        max_visited=self.settings.scan_files_per_cycle,
                    ),
                )  # fmt: skip
                tick.scanned_roots.append(root_id)
            except Exception:
                log.exception("worker.rescan_failed")
                tick.errors += 1

    def _remind(self, tick: TickSummary, now: datetime) -> None:
        local = now.astimezone()  # this machine's zone as of now (daylight saving included)
        if local.time() < REMINDER_AT:
            return
        midnight = local.replace(hour=0, minute=0, second=0, microsecond=0)
        try:
            with self.sessions() as session:
                store = SqlStore(session)
                if store.count_events(acao=ACAO_LEMBRETE, since=midnight):
                    return  # already tried today (also across a restart of the worker)
                # The attempt is on record before sending: at most one try a day, never spam.
                store.audit(
                    ator=ACTOR, acao=ACAO_LEMBRETE, alvo_tipo="reminder", alvo_id=None,
                    sha256=None, resultado=RESULT_OK, detalhes={"fase": "tentativa"},
                )  # fmt: skip
                store.commit()
                try:
                    tick.reminded = self.notifier.remind()
                    resultado = RESULT_OK
                except Exception:
                    log.exception("worker.reminder_failed")
                    tick.errors += 1
                    resultado = RESULT_FAILED
                store.audit(
                    ator=ACTOR, acao="reminder.result", alvo_tipo="reminder", alvo_id=None,
                    sha256=None, resultado=resultado, detalhes={"enviado": tick.reminded},
                )  # fmt: skip
                store.commit()
        except Exception:
            log.exception("worker.reminder_failed")
            tick.errors += 1

    def run(self, stop: threading.Event) -> None:
        """Tick until ``stop`` is set; a failed tick is logged and the next one comes.

        Only one worker per installation: a second one refuses to start.
        """
        with self.sessions() as session, SqlStore(session).worker_lock() as held:
            if not held:
                raise SystemExit("erro: outro worker desta instalação já está rodando")
            while not stop.is_set():
                try:
                    self.tick()
                except Exception:
                    log.exception("worker.tick_failed")
                stop.wait(self.settings.poll_interval_seconds)


def build(settings: Settings) -> tuple[Worker, Callable[[], None]]:
    """The real worker and a close function (provider, bot)."""
    from osa.admin import services
    from osa.courier.ports import LocalContent, LocalFs
    from osa.db.session import get_sessionmaker
    from osa.extract.model import ExtractLimits
    from osa.llm.provider import build_local_provider

    sessions = get_sessionmaker()
    with sessions() as session:
        config = services.effective_model_config(session, settings)
    llm = build_local_provider(settings, config)
    closers: list[Callable[[], None]] = [llm.close]
    notifier: Notifier = LogNotifier()
    if settings.telegram_bot_token is not None:
        from osa.telegram.courier import RealCourier
        from osa.telegram.ports import BotContext, BotState
        from osa.telegram.sync_bot import SyncBot

        bot = SyncBot(settings.telegram_bot_token.get_secret_value())
        closers.append(bot.close)
        ctx = BotContext(
            courier=RealCourier(sessions, settings),
            bot=bot,
            state=BotState(),
            reviewers_chat_id=settings.telegram_reviewers_chat_id,
            effects_enabled=settings.enable_file_effects,
            sensitive_extensions=tuple(settings.sensitive_extensions),
        )
        notifier = TelegramNotifier(ctx)
    content = LocalContent(ExtractLimits.from_settings(settings))
    worker = Worker(settings, sessions, fs=LocalFs(), content=content, llm=llm, notifier=notifier)

    def close() -> None:
        for closer in reversed(closers):
            try:
                closer()
            except Exception:
                log.exception("worker.close_failed")

    return worker, close


def run(settings: Settings) -> None:
    """Run until SIGINT or SIGTERM; the current tick finishes first."""
    stop = threading.Event()

    def on_signal(signum: int, frame: FrameType | None) -> None:
        del frame
        log.info("worker.stopping", signal=signum)
        stop.set()

    signal.signal(signal.SIGINT, on_signal)
    signal.signal(signal.SIGTERM, on_signal)
    worker, close = build(settings)
    log.info(
        "worker.ready",
        effects=settings.enable_file_effects,
        poll_s=settings.poll_interval_seconds,
        telegram=settings.telegram_bot_token is not None,
    )
    try:
        worker.run(stop)
    finally:
        close()


def main() -> None:
    """Entrypoint ``osa-worker``."""
    configure_logging()
    settings = Settings()
    if settings.database_url is None:
        raise SystemExit("erro: database is not configured: set OSA_DATABASE_URL")
    run(settings)
