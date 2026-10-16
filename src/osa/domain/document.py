"""Document vocabulary: index states, origins and file classes (spec §7 and §9)."""

from __future__ import annotations

from collections.abc import Iterable
from enum import StrEnum


class DocumentState(StrEnum):
    DETECTADO = "DETECTADO"
    ESTAVEL = "ESTAVEL"
    EXTRAIDO = "EXTRAIDO"
    CLASSIFICADO = "CLASSIFICADO"
    ARQUIVADO = "ARQUIVADO"
    EM_REVISAO = "EM_REVISAO"
    NA_ORIGEM = "NA_ORIGEM"
    NAO_SUPORTADO = "NAO_SUPORTADO"
    FALHOU = "FALHOU"


class Origin(StrEnum):
    TELEGRAM = "telegram"
    WATCHED = "watched"
    SCAN = "scan"


class ArchiveMode(StrEnum):
    AUTO = "auto"
    REVIEW = "review"
    UNDO = "undo"


class ReviewState(StrEnum):
    ABERTO = "aberto"
    DECIDIDO = "decidido"
    ORIGEM = "origem"


class FileClass(StrEnum):
    """Format class by extension; textual vs scanned PDF is known only after extraction."""

    PDF = "pdf"
    OFFICE = "office"
    IMAGE = "image"
    SENSITIVE = "sensitive"
    UNSUPPORTED = "unsupported"


_OFFICE = frozenset({".docx", ".xlsx"})
_IMAGE = frozenset({".jpg", ".jpeg", ".png", ".heic"})


def _norm_ext(ext: str) -> str:
    ext = ext.strip().lower()
    return ext if not ext or ext.startswith(".") else f".{ext}"


def classify_extension(ext: str, sensitive_extensions: Iterable[str]) -> FileClass:
    """Class of a file by extension; the sensitive class always wins."""
    ext = _norm_ext(ext)
    if ext in {_norm_ext(s) for s in sensitive_extensions}:
        return FileClass.SENSITIVE
    if ext == ".pdf":
        return FileClass.PDF
    if ext in _OFFICE:
        return FileClass.OFFICE
    if ext in _IMAGE:
        return FileClass.IMAGE
    return FileClass.UNSUPPORTED
