"""The Telegram bot (SPEC §3, §5-A, §5-D, §11): the courier exposed to the allowlist.

The bot never decides anything by itself: uploads, owner requests, reviews and undo all go
through ``osa.courier``. The handlers are synchronous and speak to two ports (the Telegram
transport and the courier), so they are tested with fakes; ``osa.telegram.app`` is the thin
adapter over python-telegram-bot that runs them in worker threads.
"""
