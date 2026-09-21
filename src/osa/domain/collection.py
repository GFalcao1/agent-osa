"""Stable identities for document collections, independent of their subject."""

from __future__ import annotations

from dataclasses import dataclass
from uuid import UUID


@dataclass(frozen=True, slots=True)
class CollectionRef:
    """A collection reference; a name alone never establishes membership or access."""

    collection_id: UUID
    name: str

    def __post_init__(self) -> None:
        if not isinstance(self.collection_id, UUID):
            raise ValueError("collection_id must be a UUID")
        if not isinstance(self.name, str) or not self.name.strip():
            raise ValueError("name must be nonempty text")
