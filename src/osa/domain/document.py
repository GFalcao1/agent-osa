"""Collection-specific vocabulary values; labels do not authorize file paths."""

from __future__ import annotations

from dataclasses import dataclass
from uuid import UUID


@dataclass(frozen=True, slots=True)
class _CatalogEntry:
    id: UUID
    collection_id: UUID
    name: str

    def __post_init__(self) -> None:
        if not isinstance(self.id, UUID):
            raise ValueError("id must be a UUID")
        if not isinstance(self.collection_id, UUID):
            raise ValueError("collection_id must be a UUID")
        if not isinstance(self.name, str) or not self.name.strip():
            raise ValueError("name must be nonempty text")


@dataclass(frozen=True, slots=True)
class Category(_CatalogEntry):
    """A category belonging to a collection's configurable catalog."""


@dataclass(frozen=True, slots=True)
class DocumentType(_CatalogEntry):
    """A document classification, distinct from its physical file format."""
