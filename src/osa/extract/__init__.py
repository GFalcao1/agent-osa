"""Deterministic content extraction by format (spec §7); no model, no network.

``extract`` decides the file class from the file name before touching the file
(sensitive files are never opened), then parses in an isolated child process.
"""

from __future__ import annotations

from pathlib import Path

from osa.domain.document import FileClass
from osa.domain.errors import SensitiveFile, Unsupported
from osa.extract.classify import class_by_name, file_class
from osa.extract.isolate import run_worker
from osa.extract.model import ExtractedContent, ExtractLimits, Method, PageImage

__all__ = [
    "ExtractLimits",
    "ExtractedContent",
    "Method",
    "PageImage",
    "extract",
    "file_class",
]


def extract(path: Path | str, limits: ExtractLimits | None = None) -> ExtractedContent:
    """Extract text and page images from ``path``.

    Raises SensitiveFile (no byte is read), Unsupported, or ExtractionFailed
    (corrupt, protected, oversized, hostile, timed out or out of memory).
    """
    limits = limits or ExtractLimits()
    path = Path(path)
    cls = class_by_name(path.name, limits.sensitive_extensions)
    if cls is FileClass.SENSITIVE:
        raise SensitiveFile("arquivo de classe sensível")
    if cls is FileClass.UNSUPPORTED:
        raise Unsupported("formato não suportado")
    return run_worker(path, limits)
