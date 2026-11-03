"""O corpus adversarial completo contra as defesas que não dependem do modelo (TASK-112).

O modelo pode ser enganado; o que não pode é o resto da carta e correio (SPEC §8, ADR-0001,
ADR-0012). Para cada documento adversarial do corpus do repositório: o texto vai ao modelo só
entre as marcas com o nonce da chamada; um modelo sequestrado não consegue devolver nada fora
do schema (campo extra, dono, ferramenta, departamento inventado); e a regra de decisão nunca
arquiva sem evidência de dono, nem com um envelope que dispensa a revisão. Se o modelo de
verdade marca ou não a revisão é a suite live (``tests/live/test_model_quality.py``).
"""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path
from typing import Any

import pytest

from osa.courier.decision import (
    MOTIVO_DEPARTAMENTO_INVALIDO,
    MOTIVO_SEM_DONO,
    AutoArchive,
    NeedsReview,
    TemplateDepartment,
    decide,
)
from osa.extract.model import ExtractedContent, ExtractLimits
from osa.llm import eval as harness
from osa.llm import prompts
from osa.llm.envelopes import (
    ClassificationEnvelope,
    classification_envelope,
    intent_envelope,
    parse_envelope,
)
from osa.llm.provider import EnvelopeInvalid

CORPUS = Path(__file__).resolve().parents[1] / "fixtures" / "corpus"
LIMITS = ExtractLimits()


@pytest.fixture(scope="module")
def adversarial() -> list[tuple[str, ExtractedContent | None]]:
    corpus = harness.load_corpus(CORPUS)
    found = []
    for entry in corpus.entries:
        if not entry.adversarial:
            continue
        # Every adversarial must reach the model: one that breaks extraction proves nothing.
        content: ExtractedContent | None = harness.extract_document(entry.path, LIMITS)
        found.append((entry.id, content))
    assert len(found) >= 10, "o corpus adversarial do repositório encolheu"
    return found


def departments() -> list[str]:
    return list(harness.load_corpus(CORPUS).departments)


# --- delimitador --------------------------------------------------------------------------------


def test_every_adversarial_text_goes_to_the_model_only_between_the_marks(
    adversarial: list[tuple[str, ExtractedContent | None]],
) -> None:
    for entry_id, content in adversarial:
        if content is None:
            continue
        rendered = prompts.render_classify(
            content, departments(), max_text_chars=12000, max_image_pages=5
        )
        opening = f"<<<DOCUMENTO {rendered.nonce}>>>"
        closing = f"<<<FIM DOCUMENTO {rendered.nonce}>>>"
        head, _, rest = rendered.user.partition(opening + "\n")
        inside, _, tail = rest.partition("\n" + closing)
        assert head and tail, entry_id  # one real block, then the reminder naming the marks
        assert rendered.nonce not in inside, entry_id  # the document cannot close the block
        assert inside == prompts.sanitize_document_text(content.text)[:12000], entry_id
        if inside.strip():  # the document never reaches the system message
            assert inside not in rendered.system, entry_id


def test_a_document_that_imitates_the_delimiter_is_flagged_by_code(
    adversarial: list[tuple[str, ExtractedContent | None]],
) -> None:
    imitating = dict(adversarial)["adv-03"]
    assert imitating is not None
    assert prompts.has_embedded_instruction_markers(imitating.text)


# --- schema: o que um modelo sequestrado tentaria devolver ------------------------------------

BASE: dict[str, Any] = {
    "departamento": "Fiscal",
    "departamento_alternativo": None,
    "tipo_documental": "nota fiscal",
    "tags": [],
    "resumo": "Resumo fictício.",
    "datas": [],
    "ano_referencia": 2026,
    "precisa_revisao": False,
    "motivo_revisao": None,
}

HIJACKED_REPLIES = [
    {**BASE, "dono": "Beta Serviços"},  # tries to choose the owner
    {**BASE, "acao": "arquivar"},  # tries to act
    {**BASE, "ferramenta": {"nome": "mover", "destino": "/"}},  # tries a tool
    {**BASE, "departamento": "Diretoria Secreta"},  # outside the enum
    {**BASE, "departamento_alternativo": "../../etc"},
    {**BASE, "ano_referencia": 1},
    {**BASE, "tags": ["x"] * 50},
    {k: v for k, v in BASE.items() if k != "precisa_revisao"},  # a field left out
]


@pytest.mark.parametrize("reply", HIJACKED_REPLIES)
def test_a_hijacked_classification_reply_is_refused_by_the_schema(reply: dict[str, Any]) -> None:
    model = classification_envelope(departments())
    with pytest.raises(EnvelopeInvalid):
        parse_envelope(model, json.dumps(reply))


@pytest.mark.parametrize(
    "extra",
    [{"acao": "apagar"}, {"acao": "enviar_tudo"}, {"destinatario": "alguem@exemplo.invalid"}],
)
def test_a_hijacked_query_reply_is_refused_by_the_schema(extra: dict[str, Any]) -> None:
    reply = {
        "acao": "buscar", "dono": None, "departamento": None, "tipo_documental": None,
        "periodo_inicio": None, "periodo_fim": None, "palavras_chave": [], **extra,
    }  # fmt: skip
    with pytest.raises(EnvelopeInvalid):
        parse_envelope(intent_envelope(departments()), json.dumps(reply))


def test_every_wire_schema_forbids_extra_fields_and_tools() -> None:
    from osa.llm.envelopes import wire_schema

    for model in (classification_envelope(departments()), intent_envelope(departments())):
        schema = wire_schema(model)
        assert schema["additionalProperties"] is False
        assert set(schema["required"]) == set(schema["properties"])  # nothing optional
        if model is classification_envelope(departments()):
            assert "dono" not in schema["properties"]  # the model never names the owner


# --- decisão: sem evidência, nada é arquivado sozinho ---------------------------------------------

TEMPLATE = [TemplateDepartment(1, name, f"{i:02d} {name}", False) for i, name in
            enumerate(departments(), start=1)]  # fmt: skip


def obedient_envelope(**values: Any) -> ClassificationEnvelope:
    """What a fully hijacked model would say: no review, a department, a year."""
    model = classification_envelope(departments())
    return model.model_validate({**BASE, "departamento": departments()[0], **values})


def test_a_hijacked_envelope_without_owner_evidence_always_goes_to_review() -> None:
    decision = decide(None, obedient_envelope(), TEMPLATE)
    assert isinstance(decision, NeedsReview) and decision.motivo == MOTIVO_SEM_DONO


def test_the_owner_never_comes_from_the_envelope() -> None:
    assert "dono" not in ClassificationEnvelope.model_fields
    assert "owner" not in ClassificationEnvelope.model_fields


def test_a_department_the_registry_does_not_know_is_a_review() -> None:
    envelope = obedient_envelope()
    stale = [TemplateDepartment(99, "Outro", "99 Outro", False)]
    decision = decide(None, envelope, stale)
    assert isinstance(decision, NeedsReview) and decision.motivo == MOTIVO_DEPARTAMENTO_INVALIDO


def test_with_evidence_the_only_freedom_left_is_the_department_of_the_template() -> None:
    from osa.courier.evidence import declared_evidence

    decision = decide(declared_evidence(7), obedient_envelope(datas=[date(2026, 1, 1)]), TEMPLATE)
    assert isinstance(decision, AutoArchive)
    assert decision.owner_id == 7  # from the evidence, never from the model
    assert decision.department in TEMPLATE
