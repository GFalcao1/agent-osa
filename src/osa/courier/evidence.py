"""Owner evidence: a registered CNPJ or alias found by exact search in a document's text.

The only basis for automatic archiving besides the owner declared by the operator (SPEC
§6). The search is deterministic: a CNPJ with valid check digits that equals a registered
one, or a registered alias present as a whole word sequence. The model never takes part.
"""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from typing import Literal

from osa.domain.errors import Conflict
from osa.domain.owner import is_valid_cnpj, normalize_text

EvidenceKind = Literal["cnpj", "alias", "declarado"]

KIND_CNPJ: EvidenceKind = "cnpj"
KIND_ALIAS: EvidenceKind = "alias"
KIND_DECLARED: EvidenceKind = "declarado"

# A CNPJ as printed: masked (12.345.678/0001-95), with spaces in place of the mask, or the
# bare 14 digits. The lookarounds keep a longer digit run (a bank line, a key) from
# yielding a CNPJ by chance. Full-width digits are folded by NFKC before the search.
_CNPJ = re.compile(
    r"(?<![0-9])"
    r"([0-9]{2})[.\s]?([0-9]{3})[.\s]?([0-9]{3})\s?/?\s?([0-9]{4})[-\s]?([0-9]{2})"
    r"(?![0-9])"
)


@dataclass(frozen=True, slots=True)
class OwnerCandidate:
    """What the search knows about a registered owner: id, CNPJ digits and alias keys.

    ``aliases`` are the keys the registry stores (``normalize_alias``), never display names.
    """

    id: int
    cnpj: str | None
    aliases: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class OwnerEvidence:
    owner_id: int
    kind: EvidenceKind
    value: str  # the 14 digits, the alias key, or the declared owner id as text


class AmbiguousOwner(Conflict):
    """Two or more registered owners are present in the text: never a guess, a review."""

    def __init__(self, owner_ids: Iterable[int]) -> None:
        super().__init__("mais de um dono encontrado no texto")
        self.owner_ids = tuple(sorted(set(owner_ids)))


def cnpjs_in(text: str) -> frozenset[str]:
    """Every valid CNPJ printed in ``text``, as 14 digits; invalid check digits are ignored."""
    found: set[str] = set()
    for match in _CNPJ.finditer(unicodedata.normalize("NFKC", text)):
        digits = "".join(match.groups())
        if is_valid_cnpj(digits):
            found.add(digits)
    return frozenset(found)


def alias_in(text_key: str, alias_key: str) -> bool:
    """True when the alias key occurs as a whole word sequence of the normalized text."""
    return f" {alias_key} " in f" {text_key} "


def find_owner_evidence(text: str, owners: Sequence[OwnerCandidate]) -> OwnerEvidence | None:
    """The one owner whose CNPJ or alias is in ``text``; ``None`` if none, ``AmbiguousOwner``
    if more than one. A CNPJ match is reported over an alias match of the same owner.
    """
    cnpjs = cnpjs_in(text)
    key = normalize_text(text)
    found: dict[int, OwnerEvidence] = {}
    for owner in owners:
        if owner.cnpj is not None and owner.cnpj in cnpjs:
            found[owner.id] = OwnerEvidence(owner.id, KIND_CNPJ, owner.cnpj)
            continue
        for alias in owner.aliases:
            if alias and alias_in(key, alias):
                found[owner.id] = OwnerEvidence(owner.id, KIND_ALIAS, alias)
                break
    if len(found) > 1:
        raise AmbiguousOwner(found)
    return next(iter(found.values()), None)


def declared_evidence(owner_id: int) -> OwnerEvidence:
    """The evidence of an owner declared by the operator (SPEC §6: it wins over inference)."""
    return OwnerEvidence(owner_id, KIND_DECLARED, str(owner_id))
