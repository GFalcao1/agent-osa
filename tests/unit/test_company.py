from __future__ import annotations

import pytest

from osa.domain.company import CNPJ, normalize_cnpj
from osa.domain.document import Department, DocumentType

# Vectors: Receita Federal, "Manual de Cálculo do DV do CNPJ", updated 2025-07-11:
# https://www.gov.br/receitafederal/pt-br/centrais-de-conteudo/publicacoes/documentos-tecnicos/cnpj

@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("04.252.011/0001-10", "04252011000110"),
        ("12.ABC.345/01DE-35", "12ABC34501DE35"),
        ("12.abc.345/01de-35", "12ABC34501DE35"),
    ],
)
def test_accepts_official_cnpj_vectors(raw: str, expected: str) -> None:
    assert CNPJ.parse(raw).value == expected


def test_preserves_leading_zeros() -> None:
    assert normalize_cnpj("04.252.011/0001-10").startswith("0")


@pytest.mark.parametrize(
    "raw",
    ["04.252.011/0001-11", "12.ABC.345/01DE-36", "123", "12.AБC.345/01DE-35"],
)
def test_rejects_invalid_or_non_ascii_cnpj(raw: str) -> None:
    with pytest.raises(ValueError):
        CNPJ.parse(raw)


def test_document_vocabularies_reject_unknown_values() -> None:
    with pytest.raises(ValueError):
        Department("Unknown")
    with pytest.raises(ValueError):
        DocumentType("Unknown")
