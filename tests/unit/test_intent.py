"""Intenção, entrega e respostas da consulta (TASK-110), sem banco, modelo nem Telegram.

O despacho (``osa.search.intent``) é puro; os handlers rodam sobre um correio e um
transporte falsos. A auditoria real do envio e o modelo recebendo só o texto ficam provados em
``tests/integration/test_query_flow.py``, com o correio real.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import date
from typing import Any

import pytest

from osa.domain.errors import NotFound
from osa.llm.envelopes import ACOES, IntentEnvelope, QueryEnvelope
from osa.llm.provider import ExternalNotConfigured, LLMError, ModelUnavailable, QueryTooLong
from osa.search import intent
from osa.telegram import texts
from osa.telegram.app import dispatch_callback
from osa.telegram.handlers import query
from osa.telegram.ports import (
    Actor,
    BotContext,
    BotState,
    Button,
    Callback,
    DepartmentInfo,
    FileToSend,
    HitInfo,
    IncomingText,
    Keyboard,
    OwnerInfo,
    PathToShow,
    RootSpace,
    SearchResult,
    SendPlan,
    SendRefused,
    StatusInfo,
    Understood,
)

ADMIN = Actor(1, 1001, "Admin Ficticio", "admin", None)
ANA = Actor(4, 4004, "Ana Ficticia", "operator", 10)
PDF = b"%PDF-1.4 contrato ficticio"


def intent_of(acao: str = "buscar", **values: Any) -> IntentEnvelope:
    base: dict[str, Any] = {
        "acao": acao,
        "dono": None,
        "departamento": None,
        "tipo_documental": None,
        "periodo_inicio": None,
        "periodo_fim": None,
        "palavras_chave": [],
    }
    return IntentEnvelope(**{**base, **values})


CONTRATO_ALFA_2025 = intent_of(
    dono="Alfa",
    tipo_documental="contrato",
    periodo_inicio=date(2025, 1, 1),
    periodo_fim=date(2025, 12, 31),
)


# --- despacho -------------------------------------------------------------------------------


def test_contract_request_becomes_a_search_with_owner_type_and_period() -> None:
    """The dispatch of the envelope; that the model writes this envelope for "manda o contrato
    da Alfa de 2025" is ``tests/live/test_intent_live.py`` (PENDENTE without an Ollama)."""
    plan = intent.plan(CONTRATO_ALFA_2025)

    assert isinstance(plan, intent.Search)
    assert plan.query == QueryEnvelope(
        dono="Alfa",
        departamento=None,
        tipo_documental="contrato",
        periodo_inicio=date(2025, 1, 1),
        periodo_fim=date(2025, 12, 31),
        palavras_chave=[],
    )


@pytest.mark.parametrize(
    ("acao", "expected"),
    [
        ("listar_donos", intent.ListOwners),
        ("listar_departamentos", intent.ListDepartments),
        ("status", intent.Status),
        ("ajuda", intent.Help),
    ],
)
def test_every_other_action_has_its_own_plan(acao: str, expected: type) -> None:
    assert isinstance(intent.plan(intent_of(acao)), expected)


def test_actions_of_the_envelope_and_of_the_dispatch_are_the_same() -> None:
    assert {a.value for a in intent.Action} == set(ACOES)


def test_a_search_with_no_filter_asks_what_to_look_for_instead_of_listing_everything() -> None:
    assert isinstance(intent.plan(intent_of("buscar")), intent.Help)
    assert isinstance(intent.plan(intent_of(palavras_chave=["boleto"])), intent.Search)


# --- entrega --------------------------------------------------------------------------------

MB = 1024 * 1024


@pytest.mark.parametrize(
    ("sensitive", "size", "admin", "how"),
    [
        (False, 1 * MB, False, "arquivo"),
        (False, 50 * MB, False, "arquivo"),
        (False, 50 * MB + 1, False, "caminho"),
        (False, 51 * MB, True, "caminho"),
        (True, 10, True, "caminho"),
        (True, 10, False, "recusado"),
        (True, 99 * MB, False, "recusado"),
    ],
)
def test_delivery_by_class_size_and_role(sensitive: bool, size: int, admin: bool, how: str) -> None:
    decision = intent.delivery(sensitive=sensitive, size=size, limit_bytes=50 * MB, is_admin=admin)
    assert decision.how == how
    assert (decision.motivo is None) is (how == "arquivo")


def test_network_path_joins_the_root_unc_and_the_relative_path() -> None:
    assert (
        intent.network_path("\\\\srv\\acervo", "/Volumes/acervo", "Alfa/03 Contratos/c.pdf")
        == "\\\\srv\\acervo\\Alfa\\03 Contratos\\c.pdf"
    )
    assert (
        intent.network_path(None, "/Volumes/acervo/", "Alfa/c.pdf") == "/Volumes/acervo/Alfa/c.pdf"
    )


# --- fakes ----------------------------------------------------------------------------------


class FakeBot:
    def __init__(self) -> None:
        self.sent: list[tuple[int, str, list[list[Button]]]] = []
        self.edits: list[tuple[int, int, str]] = []
        self.answers: list[tuple[str, str | None]] = []
        self.documents: list[tuple[int, bytes, str]] = []
        self.fail_send_document = False

    with_buttons = 0  # id of the last message sent with buttons

    def send(self, chat_id: int, text: str, buttons: Keyboard = ()) -> int:
        self.sent.append((chat_id, text, [list(row) for row in buttons]))
        if buttons:
            self.with_buttons = 100 + len(self.sent)
        return 100 + len(self.sent)

    def edit(self, chat_id: int, message_id: int, text: str, buttons: Keyboard = ()) -> None:
        self.edits.append((chat_id, message_id, text))

    def answer(self, callback_id: str, text: str | None = None) -> None:
        self.answers.append((callback_id, text))

    def download(self, file_id: str) -> bytes:
        raise AssertionError("a consulta nunca baixa nada")

    def send_document(
        self, chat_id: int, data: bytes, filename: str, caption: str | None = None
    ) -> int:
        if self.fail_send_document:
            raise RuntimeError("telegram fora do ar (fictício)")
        self.documents.append((chat_id, data, filename))
        return 900

    def last(self) -> str:
        return self.sent[-1][1]

    def buttons(self) -> list[str]:
        return [b.data for row in self.sent[-1][2] for b in row]


def hit(document_id: int, *, sensivel: bool = False) -> HitInfo:
    return HitInfo(document_id, f"doc-{document_id}.pdf", "Alfa Comércio", None, 2025, "contrato",
                   sensivel)  # fmt: skip


UNDERSTOOD = Understood(1, "Alfa Comércio", None, None, None, None, "contrato",
                        date(2025, 1, 1), date(2025, 12, 31), ())  # fmt: skip


@dataclass
class FakeCourier:
    envelope: IntentEnvelope | Exception = CONTRATO_ALFA_2025
    hits: tuple[HitInfo, ...] = ()
    plans: dict[int, SendPlan] = field(default_factory=dict)
    interpreted: list[str] = field(default_factory=list)
    searches: list[QueryEnvelope] = field(default_factory=list)
    prepared: list[tuple[int, Actor, dict[str, object]]] = field(default_factory=list)
    recorded: list[dict[str, Any]] = field(default_factory=list)
    events: list[dict[str, Any]] = field(default_factory=list)

    def actor(self, telegram_id: int) -> Actor | None:
        return {a.telegram_id: a for a in (ADMIN, ANA)}.get(telegram_id)

    def audit(self, **event: Any) -> None:
        self.events.append(event)

    def interpret(self, text: str) -> IntentEnvelope:
        self.interpreted.append(text)
        if isinstance(self.envelope, Exception):
            raise self.envelope
        return self.envelope

    def search(self, q: QueryEnvelope, *, actor: Actor) -> SearchResult:
        self.searches.append(q)
        return SearchResult(self.hits, UNDERSTOOD)

    def prepare_send(self, document_id: int, *, actor: Actor, consulta: dict[str, object]) -> Any:
        self.prepared.append((document_id, actor, consulta))
        if document_id not in self.plans:
            raise NotFound("documento não encontrado")
        return self.plans[document_id]

    def record_send(self, document_id: int, **values: Any) -> None:
        self.recorded.append({"document_id": document_id, **values})

    def owners(self) -> Sequence[OwnerInfo]:
        return [OwnerInfo(1, "Alfa Comércio", "Alfa Comércio"), OwnerInfo(2, "Beta", "Beta")]

    def departments(self) -> Sequence[DepartmentInfo]:
        return [DepartmentInfo(10, "Fiscal", True), DepartmentInfo(30, "Contratos", False)]

    def status(self) -> StatusInfo:
        return StatusInfo({"ARQUIVADO": 3}, 0, "modelo-ficticio:1b", None, False,
                          (RootSpace("/srv/raiz", 2**30),))  # fmt: skip


@dataclass
class World:
    courier: FakeCourier
    bot: FakeBot
    ctx: BotContext

    def say(self, text: str, *, from_id: int = 4004) -> None:
        query.handle_text(self.ctx, IncomingText(from_id, from_id, 1, text))

    def press(self, data: str, *, from_id: int = 4004, message_id: int | None = None) -> None:
        """Press a button of the last message that had buttons, unless told otherwise."""
        target = message_id if message_id is not None else self.bot.with_buttons
        dispatch_callback(self.ctx, Callback("cb", from_id, from_id, target, data))


@pytest.fixture
def world() -> World:
    courier, bot = FakeCourier(), FakeBot()
    ctx = BotContext(courier, bot, BotState(), None, False)  # type: ignore[arg-type]
    return World(courier, bot, ctx)


# --- respostas por quantidade ------------------------------------------------------------------


def test_no_result_says_not_found_with_the_filters_understood(world: World) -> None:
    world.say("manda o contrato da Alfa de 2025")

    assert "Não encontrei" in world.bot.last()
    assert "dono Alfa Comércio" in world.bot.last() and "tipo contrato" in world.bot.last()
    assert "de 01/01/2025 a 31/12/2025" in world.bot.last()
    assert world.bot.sent[-1][2] == []  # nenhum botão: nunca sugere arquivo inexistente
    assert world.ctx.state.offers == {}


def test_one_result_asks_before_sending(world: World) -> None:
    world.courier.hits = (hit(7),)

    world.say("manda o contrato da Alfa de 2025")

    assert "Encontrei 1 documento" in world.bot.last() and "Enviar?" in world.bot.last()
    assert world.bot.buttons() == ["q:s:7", "q:x"]
    assert world.bot.documents == []  # nada vai antes da confirmação


def test_three_results_become_three_buttons_and_cancel(world: World) -> None:
    world.courier.hits = (hit(1), hit(2), hit(3))

    world.say("contratos da Alfa")

    assert "Encontrei 3 documentos" in world.bot.last()
    assert world.bot.buttons() == ["q:s:1", "q:s:2", "q:s:3", "q:x"]
    offer = world.ctx.state.offers[(4004, ANA.user_id)]
    assert offer.document_ids == (1, 2, 3) and offer.consulta == UNDERSTOOD.as_audit()


def test_seven_results_ask_to_refine_without_buttons(world: World) -> None:
    world.courier.hits = tuple(hit(i) for i in range(1, 7))  # o correio busca 6: "mais de 5"

    world.say("contratos")

    assert "mais de 5" in world.bot.last() and "Refine" in world.bot.last()
    assert "Entendi:" in world.bot.last()
    assert world.bot.sent[-1][2] == [] and world.ctx.state.offers == {}


def test_hit_buttons_carry_no_text_beyond_name_owner_and_year(world: World) -> None:
    world.courier.hits = (hit(1), hit(2, sensivel=True))

    world.say("contratos")

    labels = [b.text for row in world.bot.sent[-1][2] for b in row]
    assert labels[:2] == [
        "doc-1.pdf · Alfa Comércio · 2025",
        "[sensível] doc-2.pdf · Alfa Comércio · 2025",
    ]


# --- envio ----------------------------------------------------------------------------------


def test_pressing_send_delivers_the_file_and_records_it(world: World) -> None:
    world.courier.hits = (hit(7),)
    world.courier.plans[7] = FileToSend("contrato.pdf", PDF, "a" * 64)
    world.say("manda o contrato da Alfa de 2025")

    world.press("q:s:7")

    assert world.bot.documents == [(4004, PDF, "contrato.pdf")]
    [(document_id, actor, consulta)] = world.courier.prepared
    assert (document_id, actor) == (7, ANA) and consulta == UNDERSTOOD.as_audit()
    assert world.courier.recorded == [
        {"document_id": 7, "actor": ANA, "consulta": UNDERSTOOD.as_audit(), "ok": True}
    ]
    assert world.ctx.state.offers == {}  # a oferta acaba no envio


def test_a_failed_send_is_recorded_as_failed_and_the_user_is_told(world: World) -> None:
    world.courier.hits = (hit(7),)
    world.courier.plans[7] = FileToSend("contrato.pdf", PDF, "a" * 64)
    world.bot.fail_send_document = True
    world.say("contrato")

    world.press("q:s:7")

    [record] = world.courier.recorded
    assert record["ok"] is False and record["erro"] == "RuntimeError"
    assert world.bot.last() == texts.SEND_FAILED


def test_path_answer_for_a_big_or_sensitive_file(world: World) -> None:
    world.courier.hits = (hit(8, sensivel=True),)
    world.courier.plans[8] = PathToShow("\\\\srv\\acervo\\Alfa\\cert.pfx", intent.MOTIVO_SENSIVEL)
    world.say("certificado da Alfa", from_id=1001)

    world.press("q:s:8", from_id=1001)

    assert world.bot.documents == []
    assert "\\\\srv\\acervo\\Alfa\\cert.pfx" in world.bot.last()
    assert world.courier.recorded == []  # o caminho é auditado pelo correio, não aqui


def test_refusal_is_shown_and_nothing_is_sent(world: World) -> None:
    world.courier.hits = (hit(8, sensivel=True),)
    world.courier.plans[8] = SendRefused(intent.MOTIVO_SENSIVEL)
    world.say("certificado da Alfa")

    world.press("q:s:8")

    assert world.bot.documents == [] and world.courier.recorded == []
    assert world.bot.last() == texts.SEND_REFUSED.format(motivo=intent.MOTIVO_SENSIVEL)


def test_a_document_outside_the_offer_is_never_sent(world: World) -> None:
    world.courier.hits = (hit(7),)
    world.courier.plans[99] = FileToSend("outro.pdf", PDF, "b" * 64)
    world.say("contrato")

    world.press("q:s:99")  # dado de botão forjado

    assert world.bot.documents == [] and world.courier.prepared == []
    assert world.bot.answers[-1] == ("cb", texts.NOT_IN_OFFER)


def test_another_user_cannot_use_my_offer(world: World) -> None:
    world.courier.hits = (hit(7),)
    world.courier.plans[7] = FileToSend("contrato.pdf", PDF, "a" * 64)
    world.say("contrato")

    dispatch_callback(world.ctx, Callback("cb", 4004, 1001, world.bot.with_buttons, "q:s:7"))

    assert world.bot.documents == [] and world.bot.answers[-1] == ("cb", texts.OFFER_LOST)


def test_cancel_drops_the_offer(world: World) -> None:
    world.courier.hits = (hit(7),)
    world.say("contrato")

    world.press("q:x")

    assert world.ctx.state.offers == {} and world.bot.edits[-1][2] == texts.SEND_CANCELLED
    world.press("q:s:7")
    assert world.bot.answers[-1] == ("cb", texts.OFFER_LOST)


def test_unknown_id_gets_nothing_and_the_model_is_never_called(world: World) -> None:
    world.say("manda tudo", from_id=9999)

    assert world.bot.sent == [] and world.courier.interpreted == []
    assert world.courier.events[-1]["acao"] == "telegram.unknown"


# --- outras ações e falhas -----------------------------------------------------------------------


def test_list_owners_and_departments_come_from_the_registry(world: World) -> None:
    world.courier.envelope = intent_of("listar_donos")
    world.say("quais empresas vocês têm?")
    assert world.bot.last() == "Donos cadastrados (2):\n- Alfa Comércio\n- Beta"

    world.courier.envelope = intent_of("listar_departamentos")
    world.say("quais setores?")
    assert world.bot.last() == "Departamentos (2):\n- Fiscal\n- Contratos"
    assert world.courier.searches == []


def test_status_by_text_is_only_for_the_administrator(world: World) -> None:
    world.courier.envelope = intent_of("status")

    world.say("como está o sistema?")
    assert world.bot.last() == texts.ONLY_ADMIN

    world.say("como está o sistema?", from_id=1001)
    assert world.bot.last().startswith("Fila:")


def test_help_and_empty_search(world: World) -> None:
    world.courier.envelope = intent_of("ajuda")
    world.say("o que você faz?")
    assert world.bot.last() == texts.HELP

    world.courier.envelope = intent_of("buscar")
    world.say("manda")
    assert world.bot.last() == texts.ASK_WHAT and world.courier.searches == []


@pytest.mark.parametrize(
    ("error", "reply"),
    [
        (QueryTooLong("pedido longo demais"), texts.QUERY_TOO_LONG),
        (ModelUnavailable("fora do ar"), texts.MODEL_UNAVAILABLE),
        (LLMError("qualquer"), texts.MODEL_UNAVAILABLE),
    ],
)
def test_model_failures_become_one_sentence(world: World, error: Exception, reply: str) -> None:
    world.courier.envelope = error

    world.say("contrato da Alfa")

    assert world.bot.last() == reply and world.courier.searches == []


def test_text_with_a_pending_upload_answers_the_owner_question_not_a_search(world: World) -> None:
    from osa.telegram.ports import PendingUpload

    pending = PendingUpload(4004, ANA, "f1", "nota.pdf", 10)
    world.ctx.state.uploads[(4004, ANA.user_id)] = pending
    calls: list[str] = []
    world.ctx.courier.resolve_owner = lambda text: calls.append(text)  # type: ignore[attr-defined]
    world.ctx.courier.suggest_owners = lambda text, limit=5: []  # type: ignore[attr-defined]

    world.say("Alfa")

    assert calls == ["Alfa"] and world.courier.interpreted == []


def test_two_quick_presses_send_the_document_once(world: World) -> None:
    world.courier.hits = (hit(7),)
    world.courier.plans[7] = FileToSend("contrato.pdf", PDF, "a" * 64)
    world.say("contrato")

    world.press("q:s:7")
    world.press("q:s:7")

    assert len(world.bot.documents) == 1 and len(world.courier.prepared) == 1
    assert world.bot.answers[-1] == ("cb", texts.OFFER_LOST)


def test_a_button_of_an_older_message_never_touches_the_newer_offer(world: World) -> None:
    world.courier.hits = (hit(1), hit(2))
    world.say("contratos")
    older = world.bot.with_buttons
    world.courier.hits = (hit(3), hit(4))
    world.say("boletos")

    world.press("q:x", message_id=older)
    world.press("q:s:1", message_id=older)

    assert world.ctx.state.offers[(4004, ANA.user_id)].document_ids == (3, 4)
    assert world.courier.prepared == []
    assert world.bot.answers[-2:] == [("cb", texts.OFFER_LOST), ("cb", texts.OFFER_LOST)]


def test_external_provider_without_transport_is_a_configuration_message(world: World) -> None:
    world.courier.envelope = ExternalNotConfigured("provedor externo ligado sem transporte")

    world.say("contrato da Alfa")

    assert world.bot.last() == texts.CONFIG_ERROR.format(
        erro="provedor externo ligado sem transporte"
    )


def test_send_limit_cannot_exceed_what_telegram_accepts() -> None:
    from pydantic import ValidationError

    from osa.core.settings import Settings

    assert Settings(telegram_send_limit_mb=50).telegram_send_limit_mb == 50
    with pytest.raises(ValidationError):
        Settings(telegram_send_limit_mb=51)


def test_a_broken_index_while_preparing_the_send_is_one_sentence(world: World) -> None:
    world.courier.hits = (hit(7),)
    world.courier.plans[7] = RuntimeError("banco fora do ar")  # type: ignore[assignment]
    world.say("contrato")

    def broken(document_id: int, **_: Any) -> Any:
        raise RuntimeError("banco fora do ar")

    world.courier.prepare_send = broken  # type: ignore[method-assign]
    world.press("q:s:7")

    assert world.bot.documents == [] and world.bot.last() == texts.SEND_FAILED
