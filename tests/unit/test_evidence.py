"""Evidência de dono: CNPJ válido do cadastro ou alias por busca exata; vários donos = ambíguo."""

from __future__ import annotations

import pytest

from osa.courier.evidence import (
    KIND_ALIAS,
    KIND_CNPJ,
    AmbiguousOwner,
    OwnerCandidate,
    OwnerEvidence,
    cnpjs_in,
    declared_evidence,
    find_owner_evidence,
)


def make_cnpj(base12: str) -> str:
    def digit(digits: str, weights: list[int]) -> str:
        rest = sum(int(d) * w for d, w in zip(digits, weights, strict=True)) % 11
        return "0" if rest < 2 else str(11 - rest)

    first = digit(base12, [5, 4, 3, 2, 9, 8, 7, 6, 5, 4, 3, 2])
    second = digit(base12 + first, [6, 5, 4, 3, 2, 9, 8, 7, 6, 5, 4, 3, 2])
    return base12 + first + second


CNPJ_ALFA = make_cnpj("112223330001")
CNPJ_BETA = make_cnpj("445556660001")


def masked(c: str) -> str:
    return f"{c[:2]}.{c[2:5]}.{c[5:8]}/{c[8:12]}-{c[12:]}"


ALFA = OwnerCandidate(1, CNPJ_ALFA, ("alfa", "alfa comercio", "alfa comercio ltda"))
BETA = OwnerCandidate(2, CNPJ_BETA, ("beta", "beta servicos"))
GAMA = OwnerCandidate(3, None, ("gama",))
OWNERS = (ALFA, BETA, GAMA)


# --- CNPJ --------------------------------------------------------------------------------


def test_masked_and_unmasked_cnpj_are_found() -> None:
    assert cnpjs_in(f"Emitente: {masked(CNPJ_ALFA)}") == {CNPJ_ALFA}
    assert cnpjs_in(f"CNPJ {CNPJ_ALFA} - nota fiscal") == {CNPJ_ALFA}
    assert cnpjs_in(f"CNPJ: {CNPJ_ALFA[:2]} {CNPJ_ALFA[2:5]} {CNPJ_ALFA[5:8]} "
                    f"{CNPJ_ALFA[8:12]} {CNPJ_ALFA[12:]}") == {CNPJ_ALFA}  # fmt: skip


def test_cnpj_with_wrong_check_digit_is_ignored() -> None:
    wrong = CNPJ_ALFA[:-1] + str((int(CNPJ_ALFA[-1]) + 1) % 10)
    assert cnpjs_in(f"CNPJ {masked(wrong)}") == set()
    assert find_owner_evidence(f"Emitente {masked(wrong)}", OWNERS) is None


def test_cnpj_inside_a_longer_digit_run_is_not_a_cnpj() -> None:
    assert cnpjs_in(f"chave 9{CNPJ_ALFA}7") == set()
    assert cnpjs_in(f"linha {CNPJ_ALFA}00") == set()


def test_full_width_digits_are_folded_before_the_search() -> None:
    wide = "".join(chr(ord("０") + int(d)) for d in CNPJ_ALFA)
    assert cnpjs_in(f"CNPJ {wide}") == {CNPJ_ALFA}


def test_cnpj_evidence_points_at_the_registered_owner() -> None:
    evidence = find_owner_evidence(f"Contrato com {masked(CNPJ_BETA)} assinado", OWNERS)
    assert evidence == OwnerEvidence(2, KIND_CNPJ, CNPJ_BETA)


def test_valid_cnpj_of_an_unregistered_company_is_no_evidence() -> None:
    other = make_cnpj("778889990001")
    assert find_owner_evidence(f"Fornecedor {masked(other)}", OWNERS) is None


# --- alias -------------------------------------------------------------------------------


def test_alias_matches_whole_words_ignoring_case_accents_and_punctuation() -> None:
    assert find_owner_evidence("Nota emitida para ALFA COMÉRCIO LTDA.", OWNERS) == (
        OwnerEvidence(1, KIND_ALIAS, "alfa")
    )
    assert find_owner_evidence("cliente: gama", OWNERS) == OwnerEvidence(3, KIND_ALIAS, "gama")


def test_alias_alfa_does_not_match_alfabeto() -> None:
    assert find_owner_evidence("Curso de Alfabetização e alfabeto", OWNERS) is None
    assert find_owner_evidence("ALFAnumérico, gamaglobulina", OWNERS) is None


def test_hyphen_and_newline_still_separate_words_for_the_match() -> None:
    assert find_owner_evidence("Pagamento\nGAMA\n2026", OWNERS) == OwnerEvidence(
        3, KIND_ALIAS, "gama"
    )


def test_two_owners_in_the_text_are_ambiguous() -> None:
    with pytest.raises(AmbiguousOwner) as raised:
        find_owner_evidence(f"Contrato entre Alfa e {masked(CNPJ_BETA)}", OWNERS)
    assert raised.value.owner_ids == (1, 2)


def test_same_owner_by_cnpj_and_alias_is_one_owner_reported_by_cnpj() -> None:
    evidence = find_owner_evidence(f"Alfa Comércio, CNPJ {masked(CNPJ_ALFA)}", OWNERS)
    assert evidence == OwnerEvidence(1, KIND_CNPJ, CNPJ_ALFA)


def test_no_text_no_evidence() -> None:
    assert find_owner_evidence("", OWNERS) is None
    assert find_owner_evidence("texto sem dono", ()) is None


def test_declared_evidence_is_the_owner_id() -> None:
    assert declared_evidence(7) == OwnerEvidence(7, "declarado", "7")
