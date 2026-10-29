"""Allowlist e papéis do bot: desconhecido é ignorado e auditado; quem decide cada caso."""

from __future__ import annotations

from typing import Any

import pytest

from osa.telegram.auth import ACAO_DESCONHECIDO, identify, may_decide
from osa.telegram.ports import Actor, BotContext, BotState

ADMIN = Actor(1, 1001, "Admin Ficticio", "admin", None)
REVIEWER_FISCAL = Actor(2, 2002, "Maria Ficticia", "reviewer", 10)
REVIEWER_PESSOAL = Actor(3, 3003, "João Ficticio", "reviewer", 20)
OPERATOR = Actor(4, 4004, "Ana Ficticia", "operator", 10)


class _Courier:
    def __init__(self, known: dict[int, Actor]) -> None:
        self.known = known
        self.events: list[dict[str, Any]] = []

    def actor(self, telegram_id: int) -> Actor | None:
        return self.known.get(telegram_id)

    def audit(self, **event: Any) -> None:
        self.events.append(event)


class _Bot:
    def __init__(self) -> None:
        self.sent: list[tuple[int, str]] = []

    def send(self, chat_id: int, text: str, buttons: Any = ()) -> int:
        self.sent.append((chat_id, text))
        return 1

    def edit(self, *args: Any, **kwargs: Any) -> None:
        raise AssertionError("not expected")

    def answer(self, *args: Any, **kwargs: Any) -> None:
        raise AssertionError("not expected")

    def download(self, file_id: str) -> bytes:
        raise AssertionError("not expected")


def _ctx(courier: _Courier, bot: _Bot) -> BotContext:
    return BotContext(courier, bot, BotState(), None, False)  # type: ignore[arg-type]


def test_known_user_is_identified() -> None:
    courier, bot = _Courier({1001: ADMIN}), _Bot()
    assert identify(_ctx(courier, bot), 1001, 1001, kind="text") == ADMIN
    assert courier.events == [] and bot.sent == []


def test_unknown_id_gets_no_reply_and_is_audited() -> None:
    courier, bot = _Courier({1001: ADMIN}), _Bot()

    assert identify(_ctx(courier, bot), 9999, 9999, kind="document") is None

    assert bot.sent == []
    [event] = courier.events
    assert (event["ator"], event["acao"], event["resultado"]) == (
        "telegram:9999",
        ACAO_DESCONHECIDO,
        "denied",
    )
    assert event["detalhes"] == {"telegram_id": 9999, "chat_id": 9999, "tipo": "document"}


@pytest.mark.parametrize(
    ("actor", "department", "expected"),
    [
        (ADMIN, 10, True),
        (ADMIN, None, True),
        (REVIEWER_FISCAL, 10, True),
        (REVIEWER_FISCAL, 20, False),
        (REVIEWER_FISCAL, None, True),
        (REVIEWER_PESSOAL, 10, False),
        (OPERATOR, 10, False),
        (OPERATOR, None, False),
    ],
)
def test_who_may_decide_a_case(actor: Actor, department: int | None, expected: bool) -> None:
    assert may_decide(actor, department) is expected


def test_actor_string_and_roles() -> None:
    assert ADMIN.actor == "telegram:1001"
    assert ADMIN.is_admin and ADMIN.is_reviewer
    assert REVIEWER_FISCAL.is_reviewer and not REVIEWER_FISCAL.is_admin
    assert not OPERATOR.is_reviewer
