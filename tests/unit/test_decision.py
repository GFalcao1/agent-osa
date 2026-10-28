"""Tabela verdade da decisão (SPEC §6): automático só com tudo a favor; o resto é revisão."""

from __future__ import annotations

import itertools
from datetime import date

import pytest

from osa.courier.decision import (
    MOTIVO_ANO_AUSENTE,
    MOTIVO_DEPARTAMENTO_INVALIDO,
    MOTIVO_SEM_DONO,
    AutoArchive,
    NeedsReview,
    TemplateDepartment,
    decide,
)
from osa.courier.evidence import OwnerEvidence, declared_evidence
from osa.llm.envelopes import (
    FALLBACK_REASON,
    MOTIVO_DEPARTAMENTOS_AMBIGUOS,
    ClassificationEnvelope,
)

FISCAL = TemplateDepartment(1, "Fiscal", "01 Fiscal", usa_ano=True)
PESSOAL = TemplateDepartment(2, "Pessoal", "02 Pessoal", usa_ano=False)
TEMPLATE = (FISCAL, PESSOAL)
EVIDENCE = OwnerEvidence(5, "cnpj", "0" * 14)


def envelope(
    departamento: str = "Fiscal",
    *,
    alternativo: str | None = None,
    revisao: bool = False,
    motivo: str | None = None,
    ano: int | None = 2026,
) -> ClassificationEnvelope:
    return ClassificationEnvelope(
        departamento=departamento,
        departamento_alternativo=alternativo,
        tipo_documental="nota fiscal",
        tags=["nota"],
        resumo="Nota fiscal fictícia de serviço.",
        datas=[date(2026, 1, 31)],
        ano_referencia=ano,
        precisa_revisao=revisao,
        motivo_revisao=motivo,
    )


# --- the whole table: evidence x precisa_revisao x department valid x year ----------------

CASES = list(
    itertools.product(
        (True, False),  # evidence
        (False, True),  # precisa_revisao
        ("Fiscal", "Pessoal", "Inexistente"),  # department (Fiscal files by year)
        (2026, None),  # ano_referencia
    )
)


@pytest.mark.parametrize(("has_evidence", "revisao", "departamento", "ano"), CASES)
def test_truth_table(has_evidence: bool, revisao: bool, departamento: str, ano: int | None) -> None:
    evidence = EVIDENCE if has_evidence else None
    env = envelope(departamento, revisao=revisao, motivo="duvida" if revisao else None, ano=ano)

    decision = decide(evidence, env, TEMPLATE)

    automatic = (
        has_evidence
        and not revisao
        and departamento in ("Fiscal", "Pessoal")
        and (departamento == "Pessoal" or ano is not None)
    )
    if automatic:
        assert isinstance(decision, AutoArchive)
        assert decision.owner_id == 5
        assert decision.department.nome == departamento
        assert decision.year == (ano if departamento == "Fiscal" else None)
    else:
        assert isinstance(decision, NeedsReview)


def test_only_one_combination_per_department_is_automatic() -> None:
    automatic = [
        case
        for case in CASES
        if isinstance(
            decide(
                EVIDENCE if case[0] else None,
                envelope(case[2], revisao=case[1], motivo="x" if case[1] else None, ano=case[3]),
                TEMPLATE,
            ),
            AutoArchive,
        )
    ]
    assert automatic == [(True, False, "Fiscal", 2026), (True, False, "Pessoal", 2026),
                         (True, False, "Pessoal", None)]  # fmt: skip


# --- reasons -------------------------------------------------------------------------------


def test_department_outside_the_template_is_reviewed_without_suggestion() -> None:
    decision = decide(EVIDENCE, envelope("Inexistente"), TEMPLATE)
    assert decision == NeedsReview(MOTIVO_DEPARTAMENTO_INVALIDO, None)


def test_model_reservation_keeps_its_own_reason_and_suggests_the_department() -> None:
    decision = decide(EVIDENCE, envelope(revisao=True, motivo="texto_insuficiente"), TEMPLATE)
    assert decision == NeedsReview("texto_insuficiente", FISCAL)


def test_reservation_without_reason_gets_the_fallback_reason() -> None:
    decision = decide(EVIDENCE, envelope(revisao=True, motivo=None), TEMPLATE)
    assert decision == NeedsReview(FALLBACK_REASON, FISCAL)


def test_alternative_department_is_a_review_even_if_the_flag_were_false() -> None:
    env = envelope("Fiscal", alternativo="Pessoal", revisao=False)
    assert env.precisa_revisao is True  # the envelope itself forces it (ADR-0012, 9)
    decision = decide(EVIDENCE, env, TEMPLATE)
    assert decision == NeedsReview(MOTIVO_DEPARTAMENTOS_AMBIGUOS, FISCAL)


def test_without_evidence_the_reason_is_the_missing_owner() -> None:
    assert decide(None, envelope(), TEMPLATE) == NeedsReview(MOTIVO_SEM_DONO, FISCAL)


def test_department_by_year_without_a_reference_year_is_reviewed() -> None:
    assert decide(EVIDENCE, envelope(ano=None), TEMPLATE) == NeedsReview(MOTIVO_ANO_AUSENTE, FISCAL)


def test_department_without_year_ignores_the_reference_year() -> None:
    decision = decide(declared_evidence(9), envelope("Pessoal", ano=2020), TEMPLATE)
    assert decision == AutoArchive(9, PESSOAL, None)


def test_department_names_are_matched_exactly_as_the_enum() -> None:
    decision = decide(EVIDENCE, envelope("fiscal"), TEMPLATE)
    assert decision == NeedsReview(MOTIVO_DEPARTAMENTO_INVALIDO, None)


def test_the_model_reason_wins_over_the_missing_owner() -> None:
    decision = decide(None, envelope(revisao=True, motivo="instrucao_embutida"), TEMPLATE)
    assert decision.motivo == "instrucao_embutida"  # type: ignore[union-attr]
