"""Company identifiers independent of databases and transports."""

from __future__ import annotations

from dataclasses import dataclass


def normalize_cnpj(raw: str) -> str:
    """Normalize permitted formatting and validate a CNPJ check-digit pair."""

    if not isinstance(raw, str):
        raise ValueError("CNPJ must be text")

    value = raw.upper().replace(".", "").replace("/", "").replace("-", "")
    if len(value) != 14 or not value.isascii() or not value.isalnum() or not value[-2:].isdigit():
        raise ValueError("CNPJ must contain 12 ASCII alphanumeric characters and two digits")

    first_digit = _check_digit(value[:12])
    second_digit = _check_digit(value[:12] + str(first_digit))
    if value[-2:] != f"{first_digit}{second_digit}":
        raise ValueError("CNPJ has invalid check digits")
    return value


def _check_digit(characters: str) -> int:
    total = sum(
        (ord(character) - ord("0")) * (2 + index % 8)
        for index, character in enumerate(reversed(characters))
    )
    remainder = total % 11
    return 0 if remainder < 2 else 11 - remainder


@dataclass(frozen=True, slots=True)
class CNPJ:
    """A validated textual CNPJ; leading zeroes always remain significant."""

    value: str

    @classmethod
    def parse(cls, raw: str) -> CNPJ:
        return cls(value=normalize_cnpj(raw))

    def __str__(self) -> str:
        return self.value
