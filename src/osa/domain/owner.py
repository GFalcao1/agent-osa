"""Owner identity rules: alias and CNPJ normalization shared with the mailroom."""

from __future__ import annotations

import re
import unicodedata

_CNPJ_MASK = re.compile(r"[.\-/\s]")
_NON_ALNUM = re.compile(r"[^0-9a-z]+")
_W1 = (5, 4, 3, 2, 9, 8, 7, 6, 5, 4, 3, 2)
_W2 = (6, *_W1)

# Exactly the output alphabet of normalize_alias / normalize_cnpj; the database
# enforces the same shapes with CHECK constraints (migration 0001 freezes a copy).
ALIAS_PATTERN = "^[0-9a-z]+( [0-9a-z]+)*$"
CNPJ_PATTERN = "^[0-9]{14}$"


def normalize_text(value: str) -> str:
    """Casefolded, accent-free text with punctuation turned into single spaces.

    Compatibility caseless folding (NFKD, casefold, NFKD) before dropping combining
    marks, so "Straße", "İSTANBUL" and "ﬁscal" fold to ASCII. Letters without an
    ASCII decomposition (ø, æ, ł, CJK) become separators: a known limitation.
    """
    folded = unicodedata.normalize("NFKD", unicodedata.normalize("NFKD", value).casefold())
    stripped = "".join(c for c in folded if not unicodedata.combining(c))
    return _NON_ALNUM.sub(" ", stripped).strip()


def normalize_alias(value: str) -> str:
    """Canonical alias key; corporate suffixes are kept ("alfa ltda" is the key)."""
    key = normalize_text(value)
    if not key:
        raise ValueError("alias must contain letters or digits")
    return key


def _check_digit(digits: str, weights: tuple[int, ...]) -> int:
    rest = sum(int(d) * w for d, w in zip(digits, weights, strict=True)) % 11
    return 0 if rest < 2 else 11 - rest


def _digits_or_none(value: str) -> str | None:
    digits = _CNPJ_MASK.sub("", value)
    return digits if len(digits) == 14 and digits.isascii() and digits.isdigit() else None


def is_valid_cnpj(value: str) -> bool:
    """True for a numeric, 14-digit CNPJ with correct check digits (mask optional)."""
    digits = _digits_or_none(value)
    if digits is None or len(set(digits)) == 1:
        return False
    first = _check_digit(digits[:12], _W1)
    second = _check_digit(digits[:12] + str(first), _W2)
    return digits[12:] == f"{first}{second}"


def normalize_cnpj(value: str) -> str:
    """The 14 unmasked digits of a valid CNPJ; raises ValueError otherwise.

    The alphanumeric CNPJ (2026) is out of scope for this version.
    """
    stripped = _CNPJ_MASK.sub("", value)
    if any(c.isalpha() for c in stripped):
        raise ValueError("CNPJ alfanumérico não suportado nesta versão")
    if not is_valid_cnpj(value):
        raise ValueError("CNPJ inválido")
    return stripped
