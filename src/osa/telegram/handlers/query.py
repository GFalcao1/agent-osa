"""Free text in a private chat: the owner of a pending upload, or a request (TASK-110).

A request goes to the model as text and nothing else (SPEC §5-B step 2): the intent envelope
comes back, ``osa.search.intent`` turns it into a plan and the code runs it. Results, names
and file contents never go to the model. A search offers up to five documents as buttons;
more than five asks for a narrower request showing the filters understood; none says so,
with the same filters, and never suggests a file that does not exist.
"""

from __future__ import annotations

from osa.core.logging import get_logger
from osa.domain.errors import DomainError
from osa.llm.provider import ExternalNotConfigured, InvalidDepartments, LLMError, QueryTooLong
from osa.search import intent
from osa.telegram import texts
from osa.telegram.auth import identify
from osa.telegram.handlers import admin
from osa.telegram.handlers import send as send_handler
from osa.telegram.handlers import upload as upload_dialog
from osa.telegram.ports import (
    Actor,
    BotContext,
    Button,
    Callback,
    IncomingText,
    Offer,
    SearchResult,
)

log = get_logger("osa.telegram.query")

PREFIX = "q:"


def handle_text(ctx: BotContext, msg: IncomingText) -> None:
    actor = identify(ctx, msg.from_id, msg.chat_id, kind="text")
    if actor is None:
        return
    with ctx.state.lock:
        pending = ctx.state.uploads.get((msg.chat_id, actor.user_id))
    text = msg.text.strip()
    if pending is not None:
        upload_dialog.continue_with_owner_text(ctx, pending, text)
        return
    if not text:
        return
    handle_request(ctx, actor, msg.chat_id, text)


def handle_request(ctx: BotContext, actor: Actor, chat_id: int, text: str) -> None:
    try:
        envelope = ctx.courier.interpret(text)
    except QueryTooLong:
        ctx.bot.send(chat_id, texts.QUERY_TOO_LONG)
        return
    except (InvalidDepartments, ExternalNotConfigured) as error:
        ctx.bot.send(chat_id, texts.CONFIG_ERROR.format(erro=error))
        return
    except LLMError as error:
        log.warning("telegram.interpret_failed", kind=type(error).__name__)
        ctx.bot.send(chat_id, texts.MODEL_UNAVAILABLE)
        return
    match intent.plan(envelope):
        case intent.Search(query=query):
            try:
                result = ctx.courier.search(query, actor=actor)
            except DomainError as error:
                ctx.bot.send(chat_id, texts.QUERY_FAILED.format(erro=error))
                return
            _reply_with_results(ctx, actor, chat_id, result)
        case intent.ListOwners():
            names = [o.nome_fantasia for o in ctx.courier.owners()]
            ctx.bot.send(chat_id, _listing("Donos cadastrados", names))
        case intent.ListDepartments():
            names = [d.nome for d in ctx.courier.departments()]
            ctx.bot.send(chat_id, _listing("Departamentos", names))
        case intent.Status():
            admin.send_status(ctx, actor, chat_id)
        case intent.Help():
            ctx.bot.send(chat_id, texts.HELP if _asked_for_help(envelope.acao) else texts.ASK_WHAT)


def _asked_for_help(acao: str) -> bool:
    return acao == intent.Action.AJUDA.value


def _listing(title: str, names: list[str]) -> str:
    if not names:
        return f"{title}: nenhum."
    shown = names[: texts.MAX_LISTED_NAMES]
    tail = f"\n… e mais {len(names) - len(shown)}." if len(names) > len(shown) else ""
    return f"{title} ({len(names)}):\n" + "\n".join(f"- {name}" for name in shown) + tail


def _reply_with_results(ctx: BotContext, actor: Actor, chat_id: int, result: SearchResult) -> None:
    filtros = texts.describe_filters(result.filtros)
    hits = result.hits
    key = (chat_id, actor.user_id)
    with ctx.state.lock:
        ctx.state.offers.pop(key, None)
    if not hits:
        ctx.bot.send(chat_id, f"Não encontrei nenhum documento.\nEntendi: {filtros}.")
        return
    if len(hits) > intent.MAX_OFFERED:
        ctx.bot.send(
            chat_id,
            f"Encontrei mais de {intent.MAX_OFFERED} documentos. Refine o pedido (dono, "
            f"departamento, tipo ou período).\nEntendi: {filtros}.",
        )
        return
    if len(hits) == 1:
        (hit,) = hits
        message_id = ctx.bot.send(
            chat_id,
            f"Encontrei 1 documento: {texts.hit_label(hit, 120)}"
            + (f" ({hit.tipo_documental})" if hit.tipo_documental else "")
            + f".\nEntendi: {filtros}.\nEnviar?",
            [[Button("Enviar", f"{PREFIX}s:{hit.document_id}"), Button("Cancelar", f"{PREFIX}x")]],
        )
    else:
        rows = [[Button(texts.hit_label(hit), f"{PREFIX}s:{hit.document_id}")] for hit in hits]
        rows.append([Button("Cancelar", f"{PREFIX}x")])
        message_id = ctx.bot.send(
            chat_id,
            f"Encontrei {len(hits)} documentos. Qual devo enviar?\nEntendi: {filtros}.",
            rows,
        )
    offer = Offer(tuple(hit.document_id for hit in hits), result.filtros.as_audit(), message_id)
    with ctx.state.lock:
        ctx.state.offers[key] = offer


def handle_query_callback(ctx: BotContext, cb: Callback) -> None:
    """A button of a search: send one of the offered documents, or cancel."""
    actor = identify(ctx, cb.from_id, cb.chat_id, kind="callback")
    if actor is None:
        return
    key = (cb.chat_id, actor.user_id)
    action = cb.data[len(PREFIX) :]
    document_id = int(action[2:]) if action.startswith("s:") and action[2:].isdigit() else None
    # Check and take the offer in one step: two presses (handlers run in threads) never send
    # twice, and a button of an older message never touches the newer offer.
    with ctx.state.lock:
        offer = ctx.state.offers.get(key)
        refusal = _refusal(offer, cb.message_id, action, document_id)
        if refusal is None:
            del ctx.state.offers[key]
    if refusal is not None:
        ctx.bot.answer(cb.callback_id, refusal or None)
        return
    assert offer is not None
    if action == "x":
        ctx.bot.answer(cb.callback_id)
        ctx.bot.edit(cb.chat_id, cb.message_id, texts.SEND_CANCELLED)
        return
    assert document_id is not None
    ctx.bot.answer(cb.callback_id)
    ctx.bot.edit(cb.chat_id, cb.message_id, "Enviando…")
    send_handler.send_document(ctx, actor, cb.chat_id, document_id, offer.consulta)


def _refusal(
    offer: Offer | None, message_id: int, action: str, document_id: int | None
) -> str | None:
    """Why a button press is not honoured (``""``: ignore in silence); ``None`` to go on."""
    if offer is None or offer.message_id != message_id:
        return texts.OFFER_LOST
    if action == "x":
        return None
    if document_id is None:
        return ""
    if document_id not in offer.document_ids:
        return texts.NOT_IN_OFFER
    return None
