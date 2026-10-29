"""The "owner not found" dialog (SPEC §4): only the administrator creates an owner.

The request goes to every active administrator with two buttons; the first answer wins.
Confirmation creates the owner and its template tree through the courier and resumes the
upload that was waiting; refusal creates nothing. The request lives in memory until then.
"""

from __future__ import annotations

import secrets

from osa.core.logging import get_logger
from osa.courier.owners import OwnerRequest
from osa.domain.errors import DomainError
from osa.telegram import texts
from osa.telegram.auth import identify
from osa.telegram.ports import (
    Actor,
    BotContext,
    Button,
    Callback,
    PendingOwnerRequest,
    PendingUpload,
)

PREFIX = "ow:"
log = get_logger("osa.telegram.owners")


def request_owner(
    ctx: BotContext,
    name: str,
    requester: Actor,
    requester_chat_id: int,
    upload: PendingUpload | None,
) -> str | None:
    """Ask the administrators; returns the token of the pending request, or ``None``."""
    admins = list(ctx.courier.admins())
    if not admins:
        return None
    token = secrets.token_urlsafe(6)
    pending = PendingOwnerRequest(
        token=token,
        request=OwnerRequest(nome=name, requested_by=requester.user_id),
        requester=requester,
        requester_chat_id=requester_chat_id,
        upload=upload,
    )
    text = (
        f"{requester.nome} pediu um dono novo: «{name}».\n"
        "Criar o dono com a estrutura padrão de pastas?"
    )
    buttons = [
        [Button("Criar dono", f"{PREFIX}{token}:y"), Button("Recusar", f"{PREFIX}{token}:n")]
    ]
    # Registered before the first send: an admin may press the button before the loop ends.
    with ctx.state.lock:
        ctx.state.owner_requests[token] = pending
    for admin in admins:
        try:
            message_id = ctx.bot.send(admin.telegram_id, text, buttons)
        except Exception:  # an admin who never opened the bot cannot be messaged
            log.exception("telegram.admin_unreachable", admin=admin.user_id)
            continue
        pending.admin_messages.append((admin.telegram_id, message_id))
    if not pending.admin_messages:
        with ctx.state.lock:
            ctx.state.owner_requests.pop(token, None)
        return None
    return token


def handle_owner_callback(ctx: BotContext, cb: Callback) -> None:
    actor = identify(ctx, cb.from_id, cb.chat_id, kind="callback")
    if actor is None:
        return
    if not actor.is_admin:
        ctx.bot.answer(cb.callback_id, texts.ONLY_ADMIN)
        return
    token, _, answer = cb.data[len(PREFIX) :].partition(":")
    with ctx.state.lock:
        pending = ctx.state.owner_requests.pop(token, None)
    if pending is None:
        ctx.bot.answer(cb.callback_id, texts.REQUEST_ANSWERED)
        return
    name = pending.request.nome
    if answer != "y":
        ctx.courier.refuse_owner(pending.request, admin=actor)
        ctx.bot.answer(cb.callback_id)
        _tell_admins(ctx, pending, f"Dono «{name}» recusado por {actor.nome}.")
        ctx.bot.send(pending.requester_chat_id, texts.OWNER_REQUEST_REFUSED.format(nome=name))
        return
    try:
        owner = ctx.courier.confirm_owner(pending.request, admin=actor)
    except (DomainError, ValueError, OSError) as error:
        ctx.bot.answer(cb.callback_id)
        created = ctx.courier.resolve_owner(name)
        if created is not None:
            # The owner was committed before its tree (SPEC §10): say exactly that.
            text = texts.OWNER_TREE_FAILED.format(nome=created.nome_fantasia, erro=error)
        else:
            text = f"O dono «{name}» não foi criado: {error}"
        _tell_admins(ctx, pending, text)
        ctx.bot.send(pending.requester_chat_id, text)
        if pending.upload is not None:
            _drop_upload(ctx, pending.upload)
        return
    ctx.bot.answer(cb.callback_id)
    _tell_admins(ctx, pending, f"Dono «{owner.nome_fantasia}» criado por {actor.nome}.")
    ctx.bot.send(pending.requester_chat_id, texts.OWNER_CREATED.format(nome=owner.nome_fantasia))
    if pending.upload is not None:
        from osa.telegram.handlers.upload import run_upload

        run_upload(ctx, pending.upload, owner)


def _drop_upload(ctx: BotContext, upload: PendingUpload) -> None:
    with ctx.state.lock:
        ctx.state.uploads.pop((upload.chat_id, upload.actor.user_id), None)


def _tell_admins(ctx: BotContext, pending: PendingOwnerRequest, text: str) -> None:
    """Best effort: one admin's deleted message must not stop the others or the operator."""
    for chat_id, message_id in pending.admin_messages:
        try:
            ctx.bot.edit(chat_id, message_id, text)
        except Exception:
            log.exception("telegram.admin_edit_failed", chat_id=chat_id)
