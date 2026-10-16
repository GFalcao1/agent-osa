from __future__ import annotations

import re

import pytest

from osa.domain.document import (
    ArchiveMode,
    DocumentState,
    FileClass,
    Origin,
    ReviewState,
    classify_extension,
)
from osa.domain.errors import DomainError, NotStable, SensitiveFile, Unsupported
from osa.domain.owner import (
    ALIAS_PATTERN,
    CNPJ_PATTERN,
    is_valid_cnpj,
    normalize_alias,
    normalize_cnpj,
    normalize_text,
)
from osa.domain.user import Role

SENSITIVE = (".pfx", ".p12", ".pem", ".key", ".cer", ".crt")


def make_cnpj(base12: str) -> str:
    """Fictitious CNPJ with correct check digits, computed from 12 base digits."""

    def digit(digits: str, weights: list[int]) -> str:
        total = sum(int(d) * w for d, w in zip(digits, weights, strict=True))
        rest = total % 11
        return "0" if rest < 2 else str(11 - rest)

    first = digit(base12, [5, 4, 3, 2, 9, 8, 7, 6, 5, 4, 3, 2])
    second = digit(base12 + first, [6, 5, 4, 3, 2, 9, 8, 7, 6, 5, 4, 3, 2])
    return base12 + first + second


VALID = make_cnpj("112223330001")


def masked(cnpj: str) -> str:
    return f"{cnpj[:2]}.{cnpj[2:5]}.{cnpj[5:8]}/{cnpj[8:12]}-{cnpj[12:]}"


def test_alias_variants_collide() -> None:
    assert (
        normalize_alias("Alfa Ltda.")
        == normalize_alias("ALFA LTDA")
        == normalize_alias("alfa ltda")
    )
    assert normalize_alias("Alfa Ltda.") == "alfa ltda"


def test_alias_ignores_accents_punctuation_and_spacing() -> None:
    assert normalize_alias("  Açaí   &  Cia. ") == "acai cia"
    assert normalize_text("São-João, S/A") == "sao joao s a"


def test_alias_does_not_strip_corporate_suffix() -> None:
    assert normalize_alias("Alfa Ltda") != normalize_alias("Alfa")


@pytest.mark.parametrize("value", ["", "   ", "...", "&&"])
def test_alias_empty_after_normalization_is_refused(value: str) -> None:
    with pytest.raises(ValueError, match="alias"):
        normalize_alias(value)


UNICODE_EDGES = [
    "Alfa Ltda.",
    "  Açaí   &  Cia. ",
    "Straße",
    "STRAẞE",
    "İSTANBUL",
    "ﬁscal",
    "１２３ Ltda",
    "x²",
    "ǅemal",
    "Søren Æble",
    "Łódź",
    "Kelvin \u212a",
    "ſ",
    "tab\tand\nnewline",
    "東京 Alfa",
    "٣ Alfa",
]


@pytest.mark.parametrize("value", UNICODE_EDGES)
def test_alias_output_matches_database_check(value: str) -> None:
    """The CHECK on owner_aliases.alias accepts exactly what normalize_alias produces."""
    key = normalize_alias(value)
    assert re.fullmatch(ALIAS_PATTERN, key)
    assert normalize_alias(key) == key  # idempotent: stored keys normalize to themselves


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("Straße", "strasse"),
        ("STRAẞE", "strasse"),
        ("İSTANBUL", "istanbul"),
        ("ﬁscal", "fiscal"),
        ("１２３ Ltda", "123 ltda"),
        ("ǅemal", "dzemal"),
        ("Kelvin \u212a", "kelvin k"),
    ],
)
def test_alias_caseless_compatibility_folding(value: str, expected: str) -> None:
    assert normalize_alias(value) == expected


@pytest.mark.parametrize(
    "value", ["Alfa Ltda.", "ALFA", " alfa", "alfa ", "alfa  ltda", "açaí", "alfa\n"]
)
def test_alias_pattern_rejects_non_normalized_text(value: str) -> None:
    assert re.fullmatch(ALIAS_PATTERN, value) is None


def test_cnpj_output_matches_database_check() -> None:
    assert re.fullmatch(CNPJ_PATTERN, normalize_cnpj(masked(VALID)))
    assert re.fullmatch(CNPJ_PATTERN, masked(VALID)) is None


def test_cnpj_with_and_without_mask_collide() -> None:
    assert normalize_cnpj(masked(VALID)) == normalize_cnpj(VALID) == VALID


def test_cnpj_accepts_valid_fixture() -> None:
    assert is_valid_cnpj(VALID)
    assert is_valid_cnpj(masked(VALID))


def test_cnpj_wrong_check_digit_is_refused() -> None:
    wrong = VALID[:-1] + str((int(VALID[-1]) + 1) % 10)
    assert not is_valid_cnpj(wrong)
    with pytest.raises(ValueError, match="CNPJ"):
        normalize_cnpj(wrong)


@pytest.mark.parametrize("value", ["", "123", VALID + "0", "1" * 14, "0" * 14])
def test_cnpj_bad_length_or_repeated_digits_refused(value: str) -> None:
    assert not is_valid_cnpj(value)
    with pytest.raises(ValueError, match="CNPJ"):
        normalize_cnpj(value)


def test_alphanumeric_cnpj_is_out_of_scope_with_clear_error() -> None:
    with pytest.raises(ValueError, match="alfanum"):
        normalize_cnpj("12.ABC.345/01DE-35")


def test_document_states_match_spec_section_9() -> None:
    assert {s.value for s in DocumentState} == {
        "DETECTADO",
        "ESTAVEL",
        "EXTRAIDO",
        "CLASSIFICADO",
        "ARQUIVADO",
        "EM_REVISAO",
        "NA_ORIGEM",
        "NAO_SUPORTADO",
        "FALHOU",
    }


def test_enum_values() -> None:
    assert {o.value for o in Origin} == {"telegram", "watched", "scan"}
    assert {m.value for m in ArchiveMode} == {"auto", "review", "undo"}
    assert {s.value for s in ReviewState} == {"aberto", "decidido", "origem"}
    assert {r.value for r in Role} == {"admin", "operator", "reviewer"}


@pytest.mark.parametrize(
    ("ext", "expected"),
    [
        (".pdf", FileClass.PDF),
        (".PDF", FileClass.PDF),
        ("pdf", FileClass.PDF),
        (".docx", FileClass.OFFICE),
        (".xlsx", FileClass.OFFICE),
        (".jpg", FileClass.IMAGE),
        (".jpeg", FileClass.IMAGE),
        (".png", FileClass.IMAGE),
        (".heic", FileClass.IMAGE),
        (".pfx", FileClass.SENSITIVE),
        (".P12", FileClass.SENSITIVE),
        (".pem", FileClass.SENSITIVE),
        (".key", FileClass.SENSITIVE),
        (".cer", FileClass.SENSITIVE),
        (".crt", FileClass.SENSITIVE),
        (".zip", FileClass.UNSUPPORTED),
        ("", FileClass.UNSUPPORTED),
    ],
)
def test_classify_extension(ext: str, expected: FileClass) -> None:
    assert classify_extension(ext, SENSITIVE) is expected


def test_sensitive_wins_over_every_other_class() -> None:
    assert classify_extension(".pdf", (".pdf",)) is FileClass.SENSITIVE


def test_new_errors_are_domain_errors() -> None:
    for error in (NotStable, SensitiveFile, Unsupported):
        assert issubclass(error, DomainError)
