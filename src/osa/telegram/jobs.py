"""The daily reminder of open reviews (SPEC §5-D), sent by the worker since TASK-111.

The bot process only answers users; scheduled work lives in ``osa-worker``, so the reminder
goes out once even when the bot restarts.
"""

from __future__ import annotations

import datetime

from osa.telegram.handlers.admin import pending_text
from osa.telegram.ports import BotContext

# 9h in this machine's time zone.
REMINDER_TIME = datetime.time(hour=9, minute=0, tzinfo=datetime.datetime.now().astimezone().tzinfo)


def daily_reminder(ctx: BotContext) -> bool:
    """Send the open cases to the reviewers' group; nothing is sent when there are none."""
    if ctx.reviewers_chat_id is None:
        return False
    cases = list(ctx.courier.open_cases())
    if not cases:
        return False
    ctx.bot.send(ctx.reviewers_chat_id, pending_text(ctx, cases))
    return True
