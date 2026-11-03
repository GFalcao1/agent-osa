"""Resolução dos filtros da busca contra o cadastro, sem banco (a consulta real é integração)."""

from __future__ import annotations

from datetime import date
from types import SimpleNamespace
from typing import Any

import pytest

from osa.llm.envelopes import QueryEnvelope
from osa.search.service import (
    SearchLimits,
    match_department,
    match_owner,
    resolve_filters,
)


def owner(
    owner_id: int, nome: str, pasta: str, razao: str | None = None, ativo: bool = True
) -> Any:
    return SimpleNamespace(
        id=owner_id, nome_fantasia=nome, pasta=pasta, razao_social=razao, ativo=ativo
    )


def department(dep_id: int, nome: str, ativo: bool = True) -> Any:
    return SimpleNamespace(id=dep_id, nome=nome, ativo=ativo)


ALFA = owner(1, "Alfa Comércio", "Alfa Comércio", "Alfa Comércio Ltda")
BETA = owner(2, "Beta Serviços", "Beta Servicos")
INATIVA = owner(3, "Gama", "Gama", ativo=False)
OWNERS = [ALFA, BETA, INATIVA]
FISCAL = department(1, "Fiscal")
PESSOAL = department(2, "Pessoal")
ANTIGO = department(3, "Antigo", ativo=False)
DEPARTMENTS = [FISCAL, PESSOAL, ANTIGO]


def envelope(**values: Any) -> QueryEnvelope:
    base: dict[str, Any] = {
        "dono": None,
        "departamento": None,
        "tipo_documental": None,
        "periodo_inicio": None,
        "periodo_fim": None,
        "palavras_chave": [],
    }
    return QueryEnvelope(**{**base, **values})


@pytest.mark.parametrize(
    "text", ["Alfa Comércio", "alfa comércio", "ALFA COMÉRCIO LTDA".title(), " Alfa Comércio "]
)
def test_match_owner_by_trade_name_legal_name_or_folder(text: str) -> None:
    assert match_owner(text, OWNERS) is ALFA


def test_match_owner_by_folder_spelling() -> None:
    assert match_owner("beta servicos", OWNERS) is BETA


def test_match_owner_ignores_inactive_and_unknown_and_empty() -> None:
    assert match_owner("Gama", OWNERS) is None
    assert match_owner("Delta", OWNERS) is None
    assert match_owner("   ", OWNERS) is None


def test_match_owner_is_exact_not_prefix() -> None:
    assert match_owner("Alfa", OWNERS) is None


def test_match_department_by_template_name_only_active() -> None:
    assert match_department("fiscal", DEPARTMENTS) is FISCAL
    assert match_department("Antigo", DEPARTMENTS) is None
    assert match_department("Jurídico", DEPARTMENTS) is None


def test_resolve_filters_marks_what_the_registry_does_not_know() -> None:
    filters = resolve_filters(
        envelope(dono="Delta", departamento="Jurídico", palavras_chave=["boleto"]),
        owners=OWNERS,
        departments=DEPARTMENTS,
    )
    assert filters.unresolved
    assert filters.owner_unresolved == "Delta" and filters.department_unresolved == "Jurídico"
    assert filters.owner_id is None and filters.department_id is None


def test_resolve_filters_prefers_the_alias_lookup_over_the_name_match() -> None:
    filters = resolve_filters(
        envelope(dono="qualquer coisa"), owners=OWNERS, departments=DEPARTMENTS, owner_by_alias=BETA
    )
    assert filters.owner_id == BETA.id and not filters.unresolved


def test_resolve_filters_normalises_terms_and_type_and_keeps_the_period() -> None:
    filters = resolve_filters(
        envelope(
            dono="Alfa Comércio",
            departamento="Fiscal",
            tipo_documental="  boleto ",
            periodo_inicio=date(2026, 3, 1),
            periodo_fim=date(2026, 3, 31),
            palavras_chave=["março", " energia ", " "],
        ),
        owners=OWNERS,
        departments=DEPARTMENTS,
    )
    assert filters.owner_id == ALFA.id and filters.department_id == FISCAL.id
    assert filters.terms == ("março", "energia")
    assert filters.tipo_documental == "boleto"
    assert (filters.periodo_inicio, filters.periodo_fim) == (date(2026, 3, 1), date(2026, 3, 31))
    assert not filters.unresolved


def test_search_limits_are_bounded() -> None:
    assert SearchLimits(max_hits=50).max_hits == 50
    with pytest.raises(ValueError):
        SearchLimits(max_hits=0)
    with pytest.raises(ValueError):
        SearchLimits(max_hits=51)
