"""The "owner not found" dialog (SPEC §4): only the administrator creates an owner.

The request goes to every active administrator with two buttons; the first answer wins.
Confirmation creates the owner and its template tree through the courier and resumes the
upload that was waiting; refusal creates nothing. The request lives in memory until then.
"""

from __future__ import annotations

import secrets

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
    for admin in admins:
        message_id = ctx.bot.send(admin.telegram_id, text, buttons)
        pending.admin_messages.append((admin.telegram_id, message_id))
    with ctx.state.lock:
        ctx.state.owner_requests[token] = pending
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
        ctx.bot.answer(cb.callback_id, texts.DIALOG_LOST)
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
    except DomainError as error:
        ctx.bot.answer(cb.callback_id)
        _tell_admins(ctx, pending, f"Dono «{name}» não criado: {error}")
        ctx.bot.send(pending.requester_chat_id, f"O dono «{name}» não foi criado: {error}")
        return
    ctx.bot.answer(cb.callback_id)
    _tell_admins(ctx, pending, f"Dono «{owner.nome_fantasia}» criado por {actor.nome}.")
    ctx.bot.send(pending.requester_chat_id, texts.OWNER_CREATED.format(nome=owner.nome_fantasia))
    if pending.upload is not None:
        from osa.telegram.handlers.upload import run_upload

        run_upload(ctx, pending.upload, owner)


def _tell_admins(ctx: BotContext, pending: PendingOwnerRequest, text: str) -> None:
    for chat_id, message_id in pending.admin_messages:
        ctx.bot.edit(chat_id, message_id, text)
