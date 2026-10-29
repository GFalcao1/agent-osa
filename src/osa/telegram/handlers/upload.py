"""Upload with a declared owner (SPEC §5-A steps 1 to 3, then the courier).

The owner comes from the caption or from the next message, resolved by the registry; no
match offers the closest registered owners or a request to the administrator. A sensitive
extension is refused before any download; a file above the Bot API limit is sent to the
share instead. The reply never carries document text.
"""

from __future__ import annotations

from osa.core.logging import get_logger
from osa.courier.pipeline import ConfigurationError, Reviewed
from osa.db.audit import RESULT_DENIED
from osa.domain.document import FileClass
from osa.domain.errors import DomainError, SensitiveFile
from osa.extract.classify import class_by_name
from osa.extract.model import DEFAULT_SENSITIVE_EXTENSIONS
from osa.telegram import texts
from osa.telegram.auth import identify
from osa.telegram.handlers import owners as owners_dialog
from osa.telegram.handlers import review as review_dialog
from osa.telegram.ports import (
    Actor,
    BotContext,
    Button,
    Callback,
    IncomingDocument,
    IncomingText,
    OwnerInfo,
    PendingUpload,
)

log = get_logger("osa.telegram.upload")

PREFIX = "up:"
MAX_SUGGESTIONS = 5


def handle_document(ctx: BotContext, msg: IncomingDocument) -> None:
    actor = identify(ctx, msg.from_id, msg.chat_id, kind="document")
    if actor is None:
        return
    if class_by_name(msg.file_name, DEFAULT_SENSITIVE_EXTENSIONS) is FileClass.SENSITIVE:
        ctx.courier.audit(
            ator=actor.actor,
            acao="telegram.upload",
            alvo_tipo="telegram_update",
            resultado=RESULT_DENIED,
            detalhes={"motivo": "classe sensível: recusado antes do download"},
        )
        ctx.bot.send(msg.chat_id, texts.SENSITIVE_REFUSED)
        return
    if msg.file_size is not None and msg.file_size > ctx.download_limit:
        ctx.courier.audit(
            ator=actor.actor,
            acao="telegram.upload",
            alvo_tipo="telegram_update",
            resultado=RESULT_DENIED,
            detalhes={"motivo": "acima do limite de download", "bytes": msg.file_size},
        )
        ctx.bot.send(msg.chat_id, texts.TOO_BIG)
        return
    pending = PendingUpload(msg.chat_id, actor, msg.file_id, msg.file_name, msg.file_size)
    with ctx.state.lock:
        ctx.state.uploads[(msg.chat_id, actor.user_id)] = pending
    caption = (msg.caption or "").strip()
    if not caption:
        ctx.bot.send(msg.chat_id, texts.ASK_OWNER)
        return
    continue_with_owner_text(ctx, pending, caption)


def handle_text(ctx: BotContext, msg: IncomingText) -> bool:
    """A text message answers the owner question of a pending upload; else it is ignored."""
    actor = identify(ctx, msg.from_id, msg.chat_id, kind="text")
    if actor is None:
        return False
    with ctx.state.lock:
        pending = ctx.state.uploads.get((msg.chat_id, actor.user_id))
    if pending is None:
        return False
    continue_with_owner_text(ctx, pending, msg.text.strip())
    return True


def continue_with_owner_text(ctx: BotContext, pending: PendingUpload, text: str) -> None:
    pending.owner_text = text
    owner = ctx.courier.resolve_owner(text)
    if owner is not None:
        run_upload(ctx, pending, owner)
        return
    suggestions = list(ctx.courier.suggest_owners(text, MAX_SUGGESTIONS))
    rows = [[Button(owner.nome_fantasia, f"{PREFIX}o:{owner.id}")] for owner in suggestions]
    rows.append([Button(f"Cadastrar dono «{texts.short(text, 40)}»", f"{PREFIX}new")])
    rows.append([Button("Cancelar", f"{PREFIX}x")])
    ctx.bot.send(pending.chat_id, texts.NO_OWNER_MATCH, rows)


def handle_upload_callback(ctx: BotContext, cb: Callback) -> None:
    """Buttons of the owner question: a suggestion, a request to create, or cancel."""
    actor = identify(ctx, cb.from_id, cb.chat_id, kind="callback")
    if actor is None:
        return
    with ctx.state.lock:
        pending = ctx.state.uploads.get((cb.chat_id, actor.user_id))
    if pending is None:
        ctx.bot.answer(cb.callback_id, texts.DIALOG_LOST)
        return
    action = cb.data[len(PREFIX) :]
    if action == "x":
        _drop(ctx, pending)
        ctx.bot.answer(cb.callback_id)
        ctx.bot.edit(cb.chat_id, cb.message_id, texts.UPLOAD_CANCELLED)
        return
    if action == "new":
        ctx.bot.answer(cb.callback_id)
        name = pending.owner_text or ""
        token = owners_dialog.request_owner(ctx, name, actor, cb.chat_id, pending)
        if token is None:
            ctx.bot.edit(cb.chat_id, cb.message_id, texts.OWNER_REQUEST_NO_ADMIN)
            _drop(ctx, pending)
        else:
            ctx.bot.edit(cb.chat_id, cb.message_id, texts.OWNER_REQUEST_SENT.format(nome=name))
        return
    if action.startswith("o:"):
        try:
            owner = ctx.courier.owner(int(action[2:]))
        except (ValueError, DomainError):
            ctx.bot.answer(cb.callback_id, texts.DIALOG_LOST)
            return
        ctx.bot.answer(cb.callback_id)
        ctx.bot.edit(cb.chat_id, cb.message_id, f"Dono: {owner.nome_fantasia}.")
        run_upload(ctx, pending, owner)
        return
    ctx.bot.answer(cb.callback_id)


def run_upload(ctx: BotContext, pending: PendingUpload, owner: OwnerInfo) -> None:
    """Download, hand to the courier and reply; the pending dialog is over either way."""
    _drop(ctx, pending)
    actor: Actor = pending.actor
    try:
        data = ctx.bot.download(pending.file_id)
    except Exception:  # the transport failed, nothing was processed
        log.exception("telegram.download_failed")
        ctx.bot.send(pending.chat_id, texts.DOWNLOAD_FAILED)
        return
    try:
        outcome = ctx.courier.process_upload(
            data, pending.file_name, owner_id=owner.id, sender=actor
        )
    except SensitiveFile:
        ctx.bot.send(pending.chat_id, texts.SENSITIVE_REFUSED)
        return
    except ConfigurationError as error:
        ctx.bot.send(pending.chat_id, texts.CONFIG_ERROR.format(erro=error))
        return
    except DomainError as error:
        ctx.bot.send(pending.chat_id, f"Não foi possível processar: {error}")
        return
    document = ctx.courier.document(outcome.document_id)
    ctx.bot.send(
        pending.chat_id,
        texts.describe_outcome(outcome, tipo=document.tipo_documental, resumo=document.resumo),
    )
    if isinstance(outcome, Reviewed):
        review_dialog.post_case(
            ctx, outcome, declared_owner=owner, fallback_chat_id=pending.chat_id
        )


def _drop(ctx: BotContext, pending: PendingUpload) -> None:
    with ctx.state.lock:
        ctx.state.uploads.pop((pending.chat_id, pending.actor.user_id), None)
