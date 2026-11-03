"""``BotPort`` for a process without an event loop of its own: the worker (TASK-111).

The worker posts review cases and notices to the same bot the users talk to; the button
presses arrive at the bot process, which rebuilds a review draft from the case after a
restart, so a case posted here is decided there. Each call runs to completion on a private
event loop; nothing here polls for updates.
"""

from __future__ import annotations

import asyncio
from collections.abc import Coroutine
from typing import Any

from telegram import Bot, InputFile
from telegram.error import BadRequest

from osa.telegram.app import CALL_TIMEOUT, UPLOAD_BASE_S, UPLOAD_BYTES_PER_S, keyboard_markup
from osa.telegram.ports import Keyboard


class SyncBot:
    def __init__(self, token: str, *, bot: Bot | None = None) -> None:
        self._loop = asyncio.new_event_loop()
        self._bot = bot if bot is not None else Bot(token)
        self._ready = False  # initialized on first use: the worker never waits for Telegram

    def _run[T](self, coroutine: Coroutine[Any, Any, T], timeout: float = CALL_TIMEOUT) -> T:
        if not self._ready:
            self._loop.run_until_complete(asyncio.wait_for(self._bot.initialize(), CALL_TIMEOUT))
            self._ready = True
        return self._loop.run_until_complete(asyncio.wait_for(coroutine, timeout))

    def send(self, chat_id: int, text: str, buttons: Keyboard = ()) -> int:
        message = self._run(
            self._bot.send_message(
                chat_id=chat_id, text=text, reply_markup=keyboard_markup(buttons)
            )
        )
        return int(message.message_id)

    def edit(self, chat_id: int, message_id: int, text: str, buttons: Keyboard = ()) -> None:
        try:
            self._run(
                self._bot.edit_message_text(
                    chat_id=chat_id, message_id=message_id, text=text,
                    reply_markup=keyboard_markup(buttons),
                )
            )  # fmt: skip
        except BadRequest as error:
            if "not modified" not in str(error).lower():
                raise

    def answer(self, callback_id: str, text: str | None = None) -> None:
        raise NotImplementedError("o worker não recebe botões")

    def download(self, file_id: str) -> bytes:
        raise NotImplementedError("o worker não baixa arquivos do Telegram")

    def send_document(
        self, chat_id: int, data: bytes, filename: str, caption: str | None = None
    ) -> int:
        write_timeout = UPLOAD_BASE_S + len(data) / UPLOAD_BYTES_PER_S
        message = self._run(
            self._bot.send_document(
                chat_id=chat_id, document=InputFile(data, filename=filename), caption=caption,
                disable_content_type_detection=True, write_timeout=write_timeout,
            ),
            timeout=write_timeout + CALL_TIMEOUT,
        )  # fmt: skip
        return int(message.message_id)

    def close(self) -> None:
        try:
            if self._ready:
                self._loop.run_until_complete(self._bot.shutdown())
        finally:
            self._loop.close()
