"""Allowlist and roles (SPEC §3): an unknown Telegram id is ignored in silence and audited."""

from __future__ import annotations

from osa.db.audit import RESULT_DENIED
from osa.telegram.ports import Actor, BotContext

ACAO_DESCONHECIDO = "telegram.unknown"


def identify(ctx: BotContext, telegram_id: int, chat_id: int, *, kind: str) -> Actor | None:
    """The allowlisted actor behind ``telegram_id``, or ``None`` after auditing the attempt.

    No reply ever goes to an unknown id: the bot must not confirm it exists.
    """
    actor = ctx.courier.actor(telegram_id)
    if actor is None:
        ctx.courier.audit(
            ator=f"telegram:{telegram_id}",
            acao=ACAO_DESCONHECIDO,
            alvo_tipo="telegram_update",
            resultado=RESULT_DENIED,
            detalhes={"telegram_id": telegram_id, "chat_id": chat_id, "tipo": kind},
        )
    return actor


def may_decide(actor: Actor, suggested_department_id: int | None) -> bool:
    """SPEC §5-D: a case with a department is for that department's reviewers; else any.

    The administrator decides every case; an operator who is not a reviewer decides none.
    """
    if actor.is_admin:
        return True
    if not actor.is_reviewer:
        return False
    if suggested_department_id is None:
        return True
    return actor.departamento_id == suggested_department_id
