"""Delivery of a found document (SPEC §5-B step 4): the file, its path, or a refusal.

The courier decides (``prepare_send``): the sensitive class never goes as an attachment and
reaches only the administrator, as a path; above the send limit the path goes instead of the
file; a file that changed on the share since it was indexed is refused. The handler only
carries the decision to Telegram and has the file send audited after the attempt.
"""

from __future__ import annotations

from osa.core.logging import get_logger
from osa.domain.errors import DomainError
from osa.telegram import texts
from osa.telegram.ports import Actor, BotContext, FileToSend, PathToShow, SendRefused

log = get_logger("osa.telegram.send")


def send_document(
    ctx: BotContext, actor: Actor, chat_id: int, document_id: int, consulta: dict[str, object]
) -> None:
    try:
        plan = ctx.courier.prepare_send(document_id, actor=actor, consulta=consulta)
    except DomainError as error:  # the document vanished from the index
        ctx.bot.send(chat_id, texts.SEND_REFUSED.format(motivo=error))
        return
    except Exception:  # the index or the audit is down: nothing was sent
        log.exception("telegram.prepare_send_failed")
        ctx.bot.send(chat_id, texts.SEND_FAILED)
        return
    match plan:
        case SendRefused(motivo=motivo):
            ctx.bot.send(chat_id, texts.SEND_REFUSED.format(motivo=motivo))
        case PathToShow(caminho=caminho, motivo=motivo):
            ctx.bot.send(chat_id, texts.SEND_PATH.format(motivo=motivo, caminho=caminho))
        case FileToSend(nome=nome, data=data):
            try:
                ctx.bot.send_document(chat_id, data, nome)
            except Exception as error:  # the transport failed: recorded, the user is told
                log.exception("telegram.send_failed")
                ctx.courier.record_send(
                    document_id, actor=actor, consulta=consulta, ok=False,
                    erro=type(error).__name__,
                )  # fmt: skip
                ctx.bot.send(chat_id, texts.SEND_FAILED)
                return
            try:
                ctx.courier.record_send(document_id, actor=actor, consulta=consulta, ok=True)
            except Exception:  # the file left; the attempt is already on record
                log.exception("telegram.record_send_failed")
