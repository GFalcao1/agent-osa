"""Scheduled work of the bot: the daily reminder of open reviews (SPEC §5-D)."""

from __future__ import annotations

from osa.telegram.handlers.admin import pending_text
from osa.telegram.ports import BotContext


def daily_reminder(ctx: BotContext) -> bool:
    """Send the open cases to the reviewers' group; nothing is sent when there are none."""
    if ctx.reviewers_chat_id is None:
        return False
    cases = list(ctx.courier.open_cases())
    if not cases:
        return False
    ctx.bot.send(ctx.reviewers_chat_id, pending_text(ctx, cases))
    return True
