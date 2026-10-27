"""External provider policy (spec §8, ADR-0002): query only, owners masked, every call audited."""

from __future__ import annotations

import json
import random
import re
import subprocess
import sys
import unicodedata
from collections.abc import Mapping, Sequence
from types import SimpleNamespace
from typing import Any

import pytest

from osa.core.settings import Settings
from osa.domain.owner import is_valid_cnpj
from osa.extract.model import ExtractedContent
from osa.llm.external import (
    AuditRecord,
    ExternalAuditFailed,
    ExternalCallFailed,
    ExternalClassifyRefused,
    ExternalDisabled,
    ExternalLeakBlocked,
    ExternalQueryProvider,
    OwnerNames,
    db_audit_sink,
    find_leak,
    mask_owner_names,
)
from osa.llm.ollama import OllamaProvider
from osa.llm.provider import ExternalNotConfigured, LLMError, QueryTooLong, build_query_provider

DEPARTMENTS = ("Fiscal", "Financeiro", "Jurídico")


def make_cnpj(base12: str) -> str:
    def digit(digits: str, weights: list[int]) -> str:
        rest = sum(int(d) * w for d, w in zip(digits, weights, strict=True)) % 11
        return "0" if rest < 2 else str(11 - rest)

    first = digit(base12, [5, 4, 3, 2, 9, 8, 7, 6, 5, 4, 3, 2])
    return base12 + first + digit(base12 + first, [6, 5, 4, 3, 2, 9, 8, 7, 6, 5, 4, 3, 2])


def masked(c: str) -> str:
    return f"{c[:2]}.{c[2:5]}.{c[5:8]}/{c[8:12]}-{c[12:]}"


CNPJ_ALFA = make_cnpj("112223330001")
CNPJ_BETA = make_cnpj("554443330001")
CNPJ_UNREGISTERED = make_cnpj("998887770001")

OWNERS = (
    OwnerNames(
        id=1,
        rotulo="Alfa Comércio",
        nomes=("Alfa Comércio", "Alfa Comércio e Serviços Ltda", "Alfa", "ACS"),
        cnpjs=(CNPJ_ALFA,),
    ),
    OwnerNames(
        id=2, rotulo="Beta Serviços", nomes=("Beta Serviços", "Beta & Filhos"), cnpjs=(CNPJ_BETA,)
    ),
)

QUERY_REPLY: dict[str, Any] = {
    "dono": None,
    "departamento": None,
    "tipo_documental": "contrato",
    "periodo_inicio": None,
    "periodo_fim": None,
    "palavras_chave": [],
}


class Events:
    def __init__(self) -> None:
        self.order: list[str] = []
        self.records: list[AuditRecord] = []
        self.owner_reads = 0


class FakeTransport:
    rotulo = "transporte-de-teste"

    def __init__(self, events: Events, *, reply: Any = None, retencao_zero: bool = True) -> None:
        self.events = events
        self.reply = QUERY_REPLY if reply is None else reply
        self.retencao_zero = retencao_zero
        self.calls: list[dict[str, Any]] = []

    def complete_json(
        self, *, system: str, user: str, schema: Mapping[str, Any], timeout_s: float
    ) -> str:
        self.events.order.append("transport")
        self.calls.append(
            {"system": system, "user": user, "schema": dict(schema), "timeout_s": timeout_s}
        )
        if isinstance(self.reply, Exception):
            raise self.reply
        return self.reply if isinstance(self.reply, str) else json.dumps(self.reply)

    def payload(self) -> str:
        call = self.calls[-1]
        return "\n".join(
            [call["system"], call["user"], json.dumps(call["schema"], ensure_ascii=False)]
        )


def build(
    *,
    enabled: bool = True,
    owners: Sequence[OwnerNames] = OWNERS,
    reply: Any = None,
    retencao_zero: bool = True,
    fail_sink_on: str | None = None,
) -> tuple[ExternalQueryProvider, FakeTransport, Events]:
    events = Events()
    transport = FakeTransport(events, reply=reply, retencao_zero=retencao_zero)

    def owners_source() -> Sequence[OwnerNames]:
        events.owner_reads += 1
        return owners

    def sink(record: AuditRecord) -> None:
        events.order.append(f"audit:{record.resultado}")
        if fail_sink_on == record.resultado:
            raise RuntimeError("audit store down")
        events.records.append(record)

    provider = ExternalQueryProvider(
        enabled=enabled,
        transport=transport,
        owners_source=owners_source,
        audit=sink,
        actor="cli:teste",
    )
    return provider, transport, events


# --- independent checkers (not the code under test) --------------------------


def fold(text: str) -> str:
    out = []
    for ch in unicodedata.normalize("NFKD", text.casefold()):
        if not unicodedata.combining(ch):
            out.append(ch if ch.isascii() and ch.isalnum() else " ")
    # a letter next to a digit is a word edge too: "alfa2025" still shows "alfa"
    joined = re.sub(r"(?<=[a-z])(?=[0-9])|(?<=[0-9])(?=[a-z])", " ", "".join(out))
    return " ".join(joined.split())


def contains_registered_name(payload: str, owners: Sequence[OwnerNames] = OWNERS) -> str | None:
    """A name from the registry still in the payload: word-bounded or glued (compact form)."""
    text = re.sub(r"dono:\d+", " ", payload)
    spaced, compact = f" {fold(text)} ", fold(text).replace(" ", "")
    for owner in owners:
        for name in owner.nomes:
            key = fold(name)
            if f" {key} " in spaced or (
                len(key.replace(" ", "")) >= 6 and key.replace(" ", "") in compact
            ):
                return name
    return None


def digit_skeleton(text: str) -> str:
    """Digits only: every char with a numeric value 0..9 is a digit, a letter is a break."""
    out = []
    for c in unicodedata.normalize("NFKC", text):
        value = unicodedata.digit(c, None)
        if value is None:
            number = unicodedata.numeric(c, None)
            value = int(number) if number is not None and number in range(10) else None
        if value is not None:
            out.append(str(value))
        elif c.isalpha():
            out.append(" ")
    return "".join(out)


def valid_cnpjs_in(payload: str) -> list[str]:
    """Any 14-digit window that is a valid CNPJ in the digit skeleton (fail-closed checker)."""
    found = []
    for digits in digit_skeleton(payload).split():  # nothing removed: "dono:" hides nothing
        found += [
            digits[i : i + 14] for i in range(len(digits) - 13) if is_valid_cnpj(digits[i : i + 14])
        ]
    return found


# --- disabled, classify refused ----------------------------------------------


def test_disabled_interpret_raises_before_any_io() -> None:
    provider, transport, events = build(enabled=False)

    with pytest.raises(ExternalDisabled):
        provider.interpret("contratos da Alfa", DEPARTMENTS)

    assert transport.calls == [] and events.order == [] and events.owner_reads == 0


def test_transport_without_zero_retention_is_refused_before_any_io() -> None:
    provider, transport, events = build(retencao_zero=False)

    with pytest.raises(ExternalDisabled, match="retenção"):
        provider.interpret("contratos da Alfa", DEPARTMENTS)

    assert transport.calls == [] and events.order == [] and events.owner_reads == 0


def test_no_transport_means_disabled() -> None:
    provider = ExternalQueryProvider(
        enabled=True, transport=None, owners_source=lambda: OWNERS, audit=lambda r: None
    )

    with pytest.raises(ExternalDisabled):
        provider.interpret("x", DEPARTMENTS)


@pytest.mark.parametrize("enabled", [True, False])
def test_classify_is_refused_whatever_the_switch(enabled: bool) -> None:
    provider, transport, events = build(enabled=enabled)
    content = ExtractedContent("texto do documento", (), 1, False, "pdf_text")

    with pytest.raises(ExternalClassifyRefused):
        provider.classify(content, DEPARTMENTS)

    assert transport.calls == [] and events.owner_reads == 0
    assert all(r.resultado == "denied" for r in events.records)


def test_classify_refusal_survives_a_broken_audit_sink() -> None:
    provider, _, _ = build(fail_sink_on="denied")

    with pytest.raises(ExternalClassifyRefused):
        provider.classify(ExtractedContent("x", (), 1, False, "pdf_text"), DEPARTMENTS)


def test_classify_never_touches_the_document() -> None:
    class Hostile:
        @property
        def text(self) -> str:
            raise AssertionError("document text was read")

        @property
        def images(self) -> tuple[()]:
            raise AssertionError("images were read")

    provider, _, _ = build()

    with pytest.raises(ExternalClassifyRefused):
        provider.classify(Hostile(), DEPARTMENTS)  # type: ignore[arg-type]


def test_external_module_does_not_import_extract() -> None:
    code = (
        "import sys, osa.llm.external;"
        "print('osa.extract' in sys.modules or 'osa.extract.model' in sys.modules)"
    )
    out = subprocess.run(
        [sys.executable, "-I", "-c", code], capture_output=True, text=True, check=True
    )

    assert out.stdout.strip() == "False"


# --- the factory --------------------------------------------------------------


def effective(*, external: bool) -> Any:
    return SimpleNamespace(
        nome="gemma3:12b", digest=None, provedor_externo_ativo=external, chave_presente=external
    )


def test_disabled_factory_returns_local_and_does_not_import_external() -> None:
    code = (
        "import sys;"
        "from types import SimpleNamespace as N;"
        "from osa.core.settings import Settings;"
        "from osa.llm.provider import build_query_provider;"
        "p = build_query_provider(Settings(), N(nome='gemma3:12b', digest=None, "
        "provedor_externo_ativo=False, chave_presente=False));"
        "print(type(p).__name__, 'osa.llm.external' in sys.modules)"
    )
    out = subprocess.run(
        [sys.executable, "-I", "-c", code], capture_output=True, text=True, check=True
    )

    assert out.stdout.split() == ["OllamaProvider", "False"]


def test_factory_local_when_off() -> None:
    assert isinstance(build_query_provider(Settings(), effective(external=False)), OllamaProvider)


def test_enabled_without_transport_is_an_explicit_error_not_the_local_model() -> None:
    events = Events()
    for kwargs in (
        {},
        {"transport": FakeTransport(events)},
        {"transport": FakeTransport(events), "owners_source": lambda: OWNERS},
        {"transport": FakeTransport(events), "audit": lambda r: None},
        {"owners_source": lambda: OWNERS, "audit": lambda r: None},
    ):
        with pytest.raises(ExternalNotConfigured):
            build_query_provider(Settings(), effective(external=True), **kwargs)


def test_enabled_with_everything_builds_the_external_provider() -> None:
    provider = build_query_provider(
        Settings(),
        effective(external=True),
        transport=FakeTransport(Events()),
        owners_source=lambda: OWNERS,
        audit=lambda record: None,
    )

    assert isinstance(provider, ExternalQueryProvider)


# --- masking ------------------------------------------------------------------


@pytest.mark.parametrize(
    "request_text",
    [
        "me manda o contrato da Alfa Comércio de 2025",
        "contrato da ALFA COMÉRCIO",
        "contrato da alfa comercio e servicos ltda",
        "contrato da Alfa-Comércio Ltda",
        "contrato da Alfá",  # NFD accent
        "contrato da " + unicodedata.normalize("NFD", "Alfa Comércio"),
        "notas da ACS e da Beta & Filhos",
        "notas de Beta   Serviços, e de Alfa.",
        "Alfa, Beta Serviços; Beta & Filhos",
        "guia da empresa (Alfa) de março",
        f"CNPJ {masked(CNPJ_ALFA)} contrato",
        f"cnpj {CNPJ_BETA} nota",
        "ﬁscal da Beta Serviços",
    ],
)
def test_interpret_sends_no_registered_alias(request_text: str) -> None:
    provider, transport, _ = build()

    provider.interpret(request_text, DEPARTMENTS)

    payload = transport.payload()
    assert contains_registered_name(payload) is None
    assert valid_cnpjs_in(payload) == []
    assert request_text not in payload


def test_replacement_uses_the_owner_id_and_the_longest_alias() -> None:
    text, count = mask_owner_names("Alfa Comércio e Serviços Ltda e a Alfa, Beta & Filhos", OWNERS)

    assert text == "dono:1 e a dono:1, dono:2"
    assert count == 3


def test_word_boundary_leaves_other_words_alone() -> None:
    text, count = mask_owner_names("o alfabeto da Alfabética e a Alfa", OWNERS)

    assert text == "o alfabeto da Alfabética e a dono:1" and count == 1


def test_known_cnpj_becomes_the_owner_and_any_other_valid_cnpj_is_hidden() -> None:
    raw = f"{masked(CNPJ_ALFA)} / {CNPJ_BETA} / {masked(CNPJ_UNREGISTERED)} / {CNPJ_UNREGISTERED}"

    text, count = mask_owner_names(raw, OWNERS)

    assert text == "dono:1 / dono:2 / [cnpj] / [cnpj]"
    assert count == 4


def test_invalid_numbers_are_not_touched() -> None:
    wrong = CNPJ_ALFA[:-1] + str((int(CNPJ_ALFA[-1]) + 1) % 10)
    raw = f"protocolo 12345678901234 e {wrong} e 2025-01-01 e 11999998888"

    text, _ = mask_owner_names(raw, OWNERS)

    assert text == raw


def test_cnpj_masking_is_independent_of_the_registry() -> None:
    provider, transport, _ = build(owners=())

    provider.interpret(f"notas do cnpj {masked(CNPJ_UNREGISTERED)} e {CNPJ_ALFA}", DEPARTMENTS)

    assert valid_cnpjs_in(transport.payload()) == []
    assert "[cnpj]" in transport.calls[0]["user"]


def test_masking_fuzz_seeded() -> None:
    rng = random.Random(106)
    names = [name for owner in OWNERS for name in owner.nomes]
    seps = [" ", "  ", "-", ", ", " / ", "\n", "."]
    for _ in range(300):
        name = rng.choice(names)
        words = name.split()
        pieces = []
        for word in words:
            word = (
                word.upper() if rng.random() < 0.3 else word.lower() if rng.random() < 0.3 else word
            )
            if rng.random() < 0.3:
                word = unicodedata.normalize("NFD", word)
            pieces.append(word)
        spelled = rng.choice(seps[:3]).join(pieces)
        suffix = rng.choice(["", " de 2025", " ontem", ",", "."])
        raw = f"quero {spelled}{suffix}"
        text, _ = mask_owner_names(raw, OWNERS)
        assert contains_registered_name(text) is None, (raw, text)


# --- leak check over the whole payload ------------------------------------------


def test_glued_name_is_blocked_and_nothing_is_sent() -> None:
    provider, transport, events = build()

    with pytest.raises(ExternalLeakBlocked):
        provider.interpret("contrato da AlfaComércioLtda de 2025", DEPARTMENTS)

    assert transport.calls == []
    assert "audit:tentativa" not in events.order
    assert [r.resultado for r in events.records] == ["denied"]


def test_a_registered_name_in_the_schema_or_prompt_is_caught_too() -> None:
    owners = (OwnerNames(id=7, rotulo="Fiscal Exemplo", nomes=("Fiscal Exemplo", "Jurídico")),)
    provider, transport, _ = build(owners=owners)

    with pytest.raises(ExternalLeakBlocked):
        provider.interpret("contratos de 2025", DEPARTMENTS)  # "Jurídico" sits in the enum

    assert transport.calls == []


def test_a_valid_cnpj_the_masker_misses_is_still_blocked() -> None:
    provider, transport, _ = build(owners=())
    c = CNPJ_UNREGISTERED
    # letters between the groups: not a separator the masker knows, still readable as a CNPJ
    odd = f"{c[:2]}a{c[2:5]}b{c[5:8]}c{c[8:12]}d{c[12:]}"

    with pytest.raises(ExternalLeakBlocked):
        provider.interpret(f"nota do cnpj {odd}", DEPARTMENTS)

    assert transport.calls == []


# --- audit ---------------------------------------------------------------------


def test_audit_records_the_attempt_before_the_network_and_the_result_after() -> None:
    provider, _, events = build()

    provider.interpret("contratos da Alfa", DEPARTMENTS)

    assert events.order == ["audit:tentativa", "transport", "audit:ok"]
    attempt, result = events.records
    assert attempt.acao == result.acao == "llm.external.interpret"
    assert attempt.detalhes["transporte"] == "transporte-de-teste"
    assert attempt.detalhes["donos_substituidos"] == 1
    assert "prompt_hash" in attempt.detalhes and "schema_hash" in attempt.detalhes
    assert "duracao_ms" in result.detalhes


def test_audit_details_never_carry_text() -> None:
    provider, transport, events = build()
    provider.interpret("contratos da Alfa Comércio com SENTINELA-DO-PEDIDO", DEPARTMENTS)

    details = json.dumps([dict(r.detalhes) for r in events.records], ensure_ascii=False)

    assert "SENTINELA" not in details and "Alfa" not in details and "contratos" not in details
    assert transport.calls[0]["user"] not in details


def test_transport_failure_is_audited_as_failed_with_the_type_only() -> None:
    provider, _, events = build(reply=ConnectionError("chave-ficticia-sk-123"))

    with pytest.raises(ExternalCallFailed) as error:
        provider.interpret("contratos da Alfa", DEPARTMENTS)

    assert events.order == ["audit:tentativa", "transport", "audit:failed"]
    assert events.records[-1].detalhes["erro"] == "ConnectionError"
    assert "chave-ficticia" not in str(error.value)
    assert "chave-ficticia" not in json.dumps([dict(r.detalhes) for r in events.records])


def test_invalid_reply_is_audited_as_failed() -> None:
    from osa.llm.provider import EnvelopeInvalid

    provider, _, events = build(reply="not json")

    with pytest.raises(EnvelopeInvalid):
        provider.interpret("contratos da Alfa", DEPARTMENTS)

    assert events.order == ["audit:tentativa", "transport", "audit:failed"]


def test_failing_audit_before_the_network_blocks_the_call() -> None:
    provider, transport, events = build(fail_sink_on="tentativa")

    with pytest.raises(ExternalAuditFailed):
        provider.interpret("contratos da Alfa", DEPARTMENTS)

    assert transport.calls == [] and "transport" not in events.order


def test_failing_audit_after_the_network_does_not_hand_back_an_unaudited_answer() -> None:
    provider, transport, _ = build(fail_sink_on="ok")

    with pytest.raises(ExternalAuditFailed):
        provider.interpret("contratos da Alfa", DEPARTMENTS)

    assert len(transport.calls) == 1


def test_long_request_is_refused_before_audit_and_network() -> None:
    provider, transport, events = build()

    with pytest.raises(QueryTooLong, match="longo"):
        provider.interpret("x" * 5000, DEPARTMENTS)

    assert transport.calls == [] and events.order == []
    assert issubclass(QueryTooLong, LLMError)


def test_limit_is_checked_before_masking_lengthens_the_request() -> None:
    # 4 chars of "ACS" + space become 7 of "dono:1 ": the masked text passes the Telegram limit.
    provider, transport, _ = build()
    request = ("ACS " * 1024)[:4096]

    provider.interpret(request, DEPARTMENTS)

    assert len(transport.calls) == 1
    assert "ACS" not in transport.payload()


# --- alias glued to digits (review S5) -----------------------------------------------


@pytest.mark.parametrize(
    "request_text",
    ["notas da alfa2025 e da Beta Serviços2024", "2025Alfa e ACS3", "alfa_2025", "ref.ACS-7"],
)
def test_alias_glued_to_digits_is_masked(request_text: str) -> None:
    provider, transport, _ = build()

    provider.interpret(request_text, DEPARTMENTS)

    assert contains_registered_name(transport.payload()) is None


def test_mask_and_scan_use_letter_only_boundaries() -> None:
    text, count = mask_owner_names("alfa2025 e ACS3 mas não alfabeto nem ACSA", OWNERS)

    # a space keeps the token from swallowing the digits ("dono:12025" would be owner 12025)
    assert text == "dono:1 2025 e dono:1 3 mas não alfabeto nem ACSA" and count == 2
    assert find_leak("notas da alfa2025", OWNERS) == "nome"
    assert find_leak("o alfabeto", OWNERS) is None


# --- resolution on the way back ----------------------------------------------


def test_owner_tokens_are_resolved_locally() -> None:
    reply = {**QUERY_REPLY, "dono": "dono:1", "palavras_chave": ["dono:2", "aluguel", "dono:99"]}
    provider, _, _ = build(reply=reply)

    envelope = provider.interpret("contrato da Alfa e da Beta", DEPARTMENTS)

    assert envelope.dono == "Alfa Comércio"
    assert envelope.palavras_chave == ["Beta Serviços", "aluguel"]


def test_unknown_owner_id_is_dropped_and_free_text_is_kept() -> None:
    unknown, _, _ = build(reply={**QUERY_REPLY, "dono": "dono:99"})
    free, _, _ = build(reply={**QUERY_REPLY, "dono": "Empresa do Usuário"})

    assert unknown.interpret("contratos", DEPARTMENTS).dono is None
    assert free.interpret("contratos", DEPARTMENTS).dono == "Empresa do Usuário"


def test_department_enum_applies_to_the_external_reply_too() -> None:
    from osa.llm.provider import EnvelopeInvalid

    provider, _, _ = build(reply={**QUERY_REPLY, "departamento": "Comercial"})

    with pytest.raises(EnvelopeInvalid):
        provider.interpret("contratos", DEPARTMENTS)


# --- the database sink -----------------------------------------------------------


class FakeSession:
    def __init__(self) -> None:
        self.added: list[Any] = []
        self.commits = 0

    def __enter__(self) -> FakeSession:
        return self

    def __exit__(self, *exc: object) -> None:
        return None

    def add(self, item: Any) -> None:
        self.added.append(item)

    def flush(self) -> None:
        return None

    def commit(self) -> None:
        self.commits += 1


def test_db_sink_writes_one_committed_event_per_record() -> None:
    session = FakeSession()
    sink = db_audit_sink(lambda: session, "cli:teste")  # type: ignore[arg-type,return-value]

    sink(AuditRecord("llm.external.interpret", "tentativa", {"transporte": "x"}))
    sink(AuditRecord("llm.external.interpret", "ok", {"duracao_ms": 3}))
    sink(AuditRecord("llm.external.classify", "denied", {}))

    assert session.commits == 3
    first, second, third = session.added
    assert (first.ator, first.alvo_tipo) == ("cli:teste", "llm_externo")
    # the audit table has no "pending": an unconfirmed attempt is stored as failed + phase
    assert first.resultado == "failed" and first.detalhes["fase"] == "tentativa"
    assert second.resultado == "ok" and third.resultado == "denied"


def test_db_sink_failure_surfaces_as_audit_failed_on_the_provider() -> None:
    class Broken(FakeSession):
        def commit(self) -> None:
            raise RuntimeError("db down")

    sink = db_audit_sink(lambda: Broken(), "cli:teste")  # type: ignore[arg-type,return-value]
    events = Events()
    provider = ExternalQueryProvider(
        enabled=True, transport=FakeTransport(events), owners_source=lambda: OWNERS, audit=sink
    )

    with pytest.raises(ExternalAuditFailed):
        provider.interpret("contratos", DEPARTMENTS)
    assert events.order == []


# --- CNPJ glued to digits or in other digit forms (re-review P1) ------------------------------


A = CNPJ_ALFA


def fullwidth(text: str) -> str:
    """Fullwidth forms of digits and punctuation (U+FF01..U+FF5E)."""
    return "".join(chr(ord(c) + 0xFEE0) if "!" <= c <= "~" else c for c in text)


def arabic_indic(text: str) -> str:
    return "".join(chr(0x0660 + int(c)) if c.isdigit() else c for c in text)


@pytest.mark.parametrize(
    "request_text",
    [
        f"CNPJ{CNPJ_ALFA}2025",  # glued to digits on the right
        f"ref 99{CNPJ_ALFA}",  # glued on the left
        f"nota do {fullwidth(CNPJ_ALFA)}",
        f"nota do {fullwidth(masked(CNPJ_ALFA))}",
        f"nota do {arabic_indic(CNPJ_ALFA)}",
        "cnpj " + "\u202f".join((A[:2], A[2:5])) + "\u2009" + A[5:8] + "/" + A[8:12] + "-" + A[12:],
        "cnpj " + A[:2] + "." + A[2:5] + " " + A[5:8] + "-" + A[8:12] + "/" + A[12:],  # mixed
        f"de {CNPJ_UNREGISTERED}7",  # any valid CNPJ, registered or not
        f"de {fullwidth(CNPJ_UNREGISTERED)}",
    ],
)
def test_cnpj_in_any_digit_form_never_reaches_the_transport(request_text: str) -> None:
    provider, transport, _ = build()

    provider.interpret(request_text, DEPARTMENTS)

    assert valid_cnpjs_in(transport.payload()) == []


def test_glued_registered_cnpj_becomes_the_owner_token() -> None:
    text, count = mask_owner_names(f"CNPJ{CNPJ_ALFA}2025 e {fullwidth(CNPJ_BETA)}", OWNERS)

    assert text == "CNPJdono:1 2025 e dono:2" and count == 2


def test_leak_check_sees_glued_and_unicode_cnpjs() -> None:
    assert find_leak(f"CNPJ{CNPJ_ALFA}812025", OWNERS) == "cnpj"
    assert find_leak(f"x {fullwidth(CNPJ_ALFA)}", OWNERS) == "cnpj"
    assert find_leak(f"x {arabic_indic(CNPJ_UNREGISTERED)}", OWNERS) == "cnpj"
    assert find_leak("Hoje é 2026-03-04; pedido 4", OWNERS) is None


# --- second re-review P1: structural CNPJ checks that fail closed ---------------------------


def circled(text: str) -> str:
    """Double-circled digits (U+24F5..U+24FE are 1..10) and circled zero (U+24EA): not Nd."""
    return "".join(("⓪" if c == "0" else chr(0x24F4 + int(c))) if c.isdigit() else c for c in text)


def superscript(text: str) -> str:
    table = str.maketrans("0123456789", "⁰¹²³⁴⁵⁶⁷⁸⁹")
    return text.translate(table)


ASTRA_PROBES = [
    "11    222    333    0001    81",  # more than 3 separators between groups
    circled(CNPJ_ALFA),  # "⓵⓵⓶⓶⓶⓷⓷⓷⓪⓪⓪⓵⓼⓵"
    superscript(CNPJ_ALFA),
    "1​1​2​2​2​3​3​3​0​0​0​1​8​1",
    " -- ".join((A[:2], A[2:5], A[5:8], A[8:12], A[12:])),
    "........".join(A),
    f"cnpj {CNPJ_UNREGISTERED[:7]}     {CNPJ_UNREGISTERED[7:]}",
]


@pytest.mark.parametrize("probe", ASTRA_PROBES)
def test_astra_probes_never_reach_the_transport(probe: str) -> None:
    provider, transport, _ = build()

    try:
        provider.interpret(f"notas do {probe} de 2025", DEPARTMENTS)
    except ExternalLeakBlocked:
        assert transport.calls == []
        return
    assert valid_cnpjs_in(transport.payload()) == []


def test_masker_maps_every_numeric_form_and_long_separators() -> None:
    for probe in ASTRA_PROBES[:6]:
        text, count = mask_owner_names(f"notas do {probe}", OWNERS)
        assert count == 1 and "dono:1" in text, probe


def test_final_check_blocks_any_valid_cnpj_skeleton_in_the_payload() -> None:
    # Letters break a digit run; everything else is ignored.
    assert find_leak("x " + "~~".join(A) + " y", OWNERS) == "cnpj"
    assert find_leak("x " + " ⁄ ".join(circled(A)) + " y", OWNERS) == "cnpj"
    assert find_leak("x " + " abcd ".join(A) + " y", OWNERS) is None  # words split it


@pytest.mark.parametrize(
    "request_text",
    [
        "notas de 01/02/2025 a 31/12/2025",
        "telefone (41) 3333-4444 ou 41 99999-0000",
        "boletos de R$ 1.234,56 e R$ 98.765,43 vencidos em 10/03/2026",
        "pedido 4512, nota 000458, competência 03/2026",
    ],
)
def test_ordinary_numbers_do_not_block(request_text: str) -> None:
    provider, transport, _ = build()

    provider.interpret(request_text, DEPARTMENTS)

    assert len(transport.calls) == 1


# --- third re-review P1: a "dono:" prefix never hides a CNPJ ---------------------------------


@pytest.mark.parametrize("cnpj", [CNPJ_ALFA, CNPJ_UNREGISTERED])
def test_owner_token_shape_does_not_hide_a_cnpj_in_the_request(cnpj: str) -> None:
    provider, transport, _ = build()

    try:
        provider.interpret(f"Documentos de dono:{cnpj}", DEPARTMENTS)
    except ExternalLeakBlocked:
        assert transport.calls == []
        return
    assert valid_cnpjs_in(transport.payload()) == []


def test_owner_token_shape_does_not_hide_a_cnpj_in_a_department_description() -> None:
    from osa.llm.envelopes import Department

    provider, transport, _ = build()
    described = [Department("Fiscal", f"Documentos de dono:{CNPJ_ALFA}"), "Financeiro"]

    with pytest.raises(ExternalLeakBlocked):
        provider.interpret("contratos de 2025", described)

    assert transport.calls == []
    assert find_leak(f"x dono:{CNPJ_UNREGISTERED} y", OWNERS) == "cnpj"


@pytest.mark.parametrize(
    "request_text",
    ["contratos da Alfa e da Beta Serviços", "alfa2025 e ACS 2024", "dono:1 e dono:2 de 2025"],
)
def test_legitimate_owner_tokens_do_not_block(request_text: str) -> None:
    provider, transport, _ = build()

    provider.interpret(request_text, DEPARTMENTS)

    assert len(transport.calls) == 1
