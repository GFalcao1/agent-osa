"""File class decided by name first, then by a content sniff for supported formats.

The sensitive class is decided from the normalized file name alone, so a sensitive
file is never opened, statted for content or read. Content is sniffed only for
classes that will be extracted, and a mismatch between extension and content
makes the file unsupported (a file is never promoted to a more trusted class).
"""

from __future__ import annotations

import os
import stat
import unicodedata
import zipfile
from collections.abc import Iterable
from pathlib import Path

from osa.domain.document import FileClass, classify_extension
from osa.domain.errors import ExtractionFailed
from osa.extract.model import DEFAULT_SENSITIVE_EXTENSIONS, ExtractLimits
from osa.extract.zipguard import (
    OLE_MAGIC,
    InvalidZip,
    check_entry_count,
    check_real_entries,
)

_PNG = bytes.fromhex("89504e470d0a1a0a")
_JPEG = bytes.fromhex("ffd8ff")
_HEIF_BRANDS = frozenset(
    {b"heic", b"heix", b"heim", b"heis", b"hevc", b"hevx", b"hevm", b"hevs", b"mif1", b"msf1"}
)
_PDF_WINDOW = 1024
_HEAD = 4096


def normalized_extension(name: str) -> str:
    """Extension of a file name as the file systems we serve resolve it.

    Unicode compatibility forms are folded (full-width letters), case is folded,
    and surrounding spaces and trailing dots are dropped (``a.pfx.`` and
    ``A.PFX `` are both ``.pfx``). A name such as ``.key`` has the extension ``.key``.
    """
    text = unicodedata.normalize("NFKC", name).strip().rstrip(". \t\r\n").casefold()
    head, dot, tail = text.rpartition(".")
    if not dot or not tail or any(c in tail for c in "/\\"):
        return ""
    return f".{tail}"


def class_by_name(name: str, sensitive_extensions: Iterable[str]) -> FileClass:
    """Class from the file name only; performs no I/O."""
    return classify_extension(normalized_extension(name), sensitive_extensions)


def _is_heif(head: bytes) -> bool:
    return len(head) >= 12 and head[4:8] == b"ftyp" and head[8:12].lower() in _HEIF_BRANDS


def _is_image(head: bytes) -> bool:
    return head.startswith(_PNG) or head.startswith(_JPEG) or _is_heif(head)


def _sniff(path: Path, cls: FileClass, ext: str, limits: ExtractLimits) -> bool:
    """True when the content matches the class; nothing is parsed before the size check."""
    try:
        # O_NONBLOCK: opening a FIFO under a supported name must not hang.
        fd = os.open(path, os.O_RDONLY | os.O_NONBLOCK)
    except OSError as exc:
        raise ExtractionFailed("arquivo ilegível") from exc
    try:
        with os.fdopen(fd, "rb") as handle:
            info = os.fstat(handle.fileno())
            if not stat.S_ISREG(info.st_mode):
                raise ExtractionFailed("não é um arquivo comum")
            if info.st_size > limits.max_file_bytes:
                raise ExtractionFailed("arquivo grande demais")
            head = handle.read(_HEAD)
            if cls is FileClass.PDF:
                return b"%PDF-" in head[:_PDF_WINDOW]
            if cls is FileClass.IMAGE:
                return _is_image(head)
            if head.startswith(OLE_MAGIC):
                return True  # password-protected or legacy file: extraction reports it
            try:
                if not check_entry_count(handle, info.st_size, limits):
                    return False
            except InvalidZip:
                return False  # corrupt end records: like any other corrupt zip
            try:
                with zipfile.ZipFile(handle) as archive:
                    infos = archive.infolist()
            except MemoryError:
                raise
            except Exception:
                return False
    except ExtractionFailed:
        raise
    except OSError as exc:
        raise ExtractionFailed("arquivo ilegível") from exc
    check_real_entries(len(infos), limits)
    marker = "word/document.xml" if ext == ".docx" else "xl/workbook.xml"
    return any(item.filename == marker for item in infos)


def file_class(path: Path | str, limits: ExtractLimits | None = None) -> FileClass:
    """Class of ``path``: sensitive and unsupported are decided without opening it.

    Raises ExtractionFailed when a supported-looking file cannot be read.
    """
    limits = limits or ExtractLimits(sensitive_extensions=DEFAULT_SENSITIVE_EXTENSIONS)
    path = Path(path)
    cls = class_by_name(path.name, limits.sensitive_extensions)
    if cls in (FileClass.SENSITIVE, FileClass.UNSUPPORTED):
        return cls
    ext = normalized_extension(path.name)
    return cls if _sniff(path, cls, ext, limits) else FileClass.UNSUPPORTED
