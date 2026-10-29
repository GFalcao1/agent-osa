"""The python-telegram-bot adapter: updates in, handler calls out, in worker threads.

Long polling (SPEC §12). Each update gets a ``request_id`` in the logs. The handlers are
synchronous and may take minutes (extraction, the model), so they run in a thread while
the event loop keeps polling; the transport port they use schedules the bot's coroutines
back on the loop. Nothing here decides anything: see the handlers and the courier.
"""

from __future__ import annotations

import asyncio
import datetime
import uuid
from collections.abc import Coroutine, Sequence
from typing import Any

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.error import BadRequest
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

from osa.core.logging import configure_logging, get_logger, log_context
from osa.core.settings import Settings
from osa.telegram import auth, jobs
from osa.telegram.handlers import admin, owners, review, upload
from osa.telegram.ports import (
    BotContext,
    BotPort,
    BotState,
    Callback,
    CourierPort,
    IncomingCommand,
    IncomingDocument,
    IncomingText,
    Keyboard,
)

log = get_logger("osa.telegram")

# 9h in this machine's time zone (the job queue would otherwise read a naive time as UTC).
REMINDER_TIME = datetime.time(hour=9, minute=0, tzinfo=datetime.datetime.now().astimezone().tzinfo)
COMMANDS = ("start", "status", "desfazer", "pendentes")
CALL_TIMEOUT = 120.0


class PtbBot:
    """``BotPort`` over a running ``telegram.Bot``, callable from worker threads."""

    def __init__(self, application: Application[Any, Any, Any, Any, Any, Any]) -> None:
        self._application = application
        self._loop = asyncio.get_running_loop()

    def _run[T](self, coroutine: Coroutine[Any, Any, T]) -> T:
        future = asyncio.run_coroutine_threadsafe(coroutine, self._loop)
        return future.result(timeout=CALL_TIMEOUT)

    def send(self, chat_id: int, text: str, buttons: Keyboard = ()) -> int:
        message = self._run(
            self._application.bot.send_message(
                chat_id=chat_id, text=text, reply_markup=_markup(buttons)
            )
        )
        return int(message.message_id)

    def edit(self, chat_id: int, message_id: int, text: str, buttons: Keyboard = ()) -> None:
        try:
            self._run(
                self._application.bot.edit_message_text(
                    chat_id=chat_id, message_id=message_id, text=text, reply_markup=_markup(buttons)
                )
            )
        except BadRequest as error:  # the same text and buttons again: not an error
            if "not modified" not in str(error).lower():
                raise

    def answer(self, callback_id: str, text: str | None = None) -> None:
        try:
            self._run(
                self._application.bot.answer_callback_query(
                    callback_query_id=callback_id, text=text
                )
            )
        except BadRequest as error:  # the query expired while the handler worked: not fatal
            log.warning("telegram.answer_late", error=str(error))

    def download(self, file_id: str) -> bytes:
        file = self._run(self._application.bot.get_file(file_id))
        return bytes(self._run(file.download_as_bytearray()))


def _markup(buttons: Keyboard) -> InlineKeyboardMarkup | None:
    if not buttons:
        return None
    return InlineKeyboardMarkup(
        [[InlineKeyboardButton(b.text, callback_data=b.data) for b in row] for row in buttons]
    )


def build_application(
    settings: Settings, courier: CourierPort, *, bot: BotPort | None = None
) -> Application[Any, Any, Any, Any, Any, Any]:
    """The application with every handler and the daily reminder; ``bot`` only for tests."""
    token = settings.telegram_bot_token
    if token is None:
        raise ValueError("OSA_TELEGRAM_BOT_TOKEN não configurado")
    state = BotState()

    async def post_init(application: Application[Any, Any, Any, Any, Any, Any]) -> None:
        application.bot_data["ctx"] = BotContext(
            courier=courier,
            bot=bot if bot is not None else PtbBot(application),
            state=state,
            reviewers_chat_id=settings.telegram_reviewers_chat_id,
            effects_enabled=settings.enable_file_effects,
            sensitive_extensions=tuple(settings.sensitive_extensions),
        )
        if application.job_queue is not None:
            application.job_queue.run_daily(_reminder, time=REMINDER_TIME, name="lembrete")
        log.info("telegram.ready", effects=settings.enable_file_effects)

    application = (
        Application.builder()
        .token(token.get_secret_value())
        .concurrent_updates(True)
        .post_init(post_init)
        .build()
    )
    private = filters.ChatType.PRIVATE
    application.add_handler(MessageHandler(filters.Document.ALL & private, _on_document))
    application.add_handler(MessageHandler(filters.Document.ALL & ~private, _on_group_document))
    application.add_handler(MessageHandler(filters.PHOTO, _on_photo))
    for name in COMMANDS:
        application.add_handler(CommandHandler(name, _on_command))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND & private, _on_text))
    application.add_handler(CallbackQueryHandler(_on_callback))
    # Everything else (stickers, voice, text in groups): known users are ignored, unknown
    # ids are audited (SPEC §3), nobody gets a reply.
    application.add_handler(MessageHandler(filters.ALL, _on_other))
    return application


def _ctx(context: ContextTypes.DEFAULT_TYPE) -> BotContext:
    ctx: BotContext = context.bot_data["ctx"]
    return ctx


async def _in_thread(work: Any, *args: Any, **kwargs: Any) -> Any:
    with log_context(request_id=uuid.uuid4().hex):
        return await asyncio.to_thread(work, *args, **kwargs)


async def _on_document(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    message, user, chat = update.effective_message, update.effective_user, update.effective_chat
    if message is None or message.document is None or user is None or chat is None:
        return
    document = message.document
    event = IncomingDocument(
        chat_id=chat.id,
        from_id=user.id,
        message_id=message.message_id,
        file_id=document.file_id,
        file_name=document.file_name or "arquivo",
        file_size=document.file_size,
        caption=message.caption,
    )
    await _in_thread(upload.handle_document, _ctx(context), event)


async def _on_photo(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    message, user, chat = update.effective_message, update.effective_user, update.effective_chat
    if message is None or user is None or chat is None:
        return
    actor = await _in_thread(auth.identify, _ctx(context), user.id, chat.id, kind="photo")
    if actor is None:
        return
    await message.reply_text(
        "Envie a imagem como arquivo (anexo), não como foto: assim ela chega sem compressão."
    )


async def _on_group_document(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    message, user, chat = update.effective_message, update.effective_user, update.effective_chat
    if message is None or user is None or chat is None:
        return
    actor = await _in_thread(auth.identify, _ctx(context), user.id, chat.id, kind="group_document")
    if actor is None:
        return
    await message.reply_text("Envie documentos na conversa privada com o bot, não no grupo.")


async def _on_other(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user, chat = update.effective_user, update.effective_chat
    if user is None or chat is None:
        return
    await _in_thread(auth.identify, _ctx(context), user.id, chat.id, kind="other")


async def _on_text(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    message, user, chat = update.effective_message, update.effective_user, update.effective_chat
    if message is None or message.text is None or user is None or chat is None:
        return
    event = IncomingText(chat.id, user.id, message.message_id, message.text)
    await _in_thread(upload.handle_text, _ctx(context), event)


async def _on_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    message, user, chat = update.effective_message, update.effective_user, update.effective_chat
    if message is None or message.text is None or user is None or chat is None:
        return
    head, *args = message.text.split()
    name = head.lstrip("/").split("@", 1)[0]
    event = IncomingCommand(chat.id, user.id, message.message_id, name, tuple(args))
    await _in_thread(admin.handle_command, _ctx(context), event)


async def _on_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    if query is None or query.data is None or query.message is None:
        return
    event = Callback(
        callback_id=query.id,
        chat_id=query.message.chat.id,
        from_id=query.from_user.id,
        message_id=query.message.message_id,
        data=query.data,
    )
    await _in_thread(dispatch_callback, _ctx(context), event)


def dispatch_callback(ctx: BotContext, event: Callback) -> None:
    """Route a button press by its prefix (shared with the tests)."""
    if event.data.startswith(upload.PREFIX):
        upload.handle_upload_callback(ctx, event)
    elif event.data.startswith(owners.PREFIX):
        owners.handle_owner_callback(ctx, event)
    elif event.data.startswith(review.PREFIX):
        review.handle_review_callback(ctx, event)
    else:
        ctx.bot.answer(event.callback_id)


async def _reminder(context: ContextTypes.DEFAULT_TYPE) -> None:
    await _in_thread(jobs.daily_reminder, _ctx(context))


def run(settings: Settings) -> None:
    """Build the real courier and poll until stopped."""
    from osa.db.session import get_sessionmaker
    from osa.telegram.courier import RealCourier

    courier = RealCourier(get_sessionmaker(), settings)
    application = build_application(settings, courier)
    application.run_polling(allowed_updates=["message", "callback_query"])


def main() -> None:
    """Entrypoint ``osa-bot``."""
    configure_logging()
    settings = Settings()
    if settings.telegram_bot_token is None:
        raise SystemExit("erro: OSA_TELEGRAM_BOT_TOKEN não configurado")
    run(settings)


__all__: Sequence[str] = ("build_application", "dispatch_callback", "main", "run")
