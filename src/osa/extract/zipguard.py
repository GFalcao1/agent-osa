"""Guards for OOXML (zip) files: entry count, bytes actually read, and safe XML.

Declared sizes in a zip are attacker controlled, so the limits here are enforced
while reading: every entry is decompressed in chunks against a per-part cap and a
shared total cap, and every XML part is parsed by defusedxml, which refuses DTDs,
entities and external references (billion laughs, external entity expansion).
"""

from __future__ import annotations

import os
import struct
import zipfile
from pathlib import Path
from typing import IO, BinaryIO

from defusedxml import ElementTree as SafeET

from osa.domain.errors import ExtractionFailed
from osa.extract.model import ExtractLimits

_EOCD = b"PK\x05\x06"
_EOCD_MIN = 22
_MAX_COMMENT = 65535
_CHUNK = 64 * 1024
_ZIP64_ENTRIES = 0xFFFF
_ZIP64_LOCATOR = b"PK\x06\x07"
_ZIP64_LOCATOR_SIZE = 20
_ZIP64_RECORD = b"PK\x06\x06"
_ZIP64_RECORD_SIZE = 56
# A central directory record is 46 bytes plus name, extra and comment; OOXML names are short.
_MAX_DIR_BYTES_PER_ENTRY = 1024
OLE_MAGIC = bytes.fromhex("d0cf11e0a1b11ae1")
_XML_SUFFIXES = (".xml", ".rels")


class InvalidZip(ExtractionFailed):
    """Corrupt or contradictory end records (as opposed to a zip over the limits)."""


def _end_record(handle: BinaryIO, size: int) -> tuple[int, int, int] | None:
    """(entries, central directory bytes, record offset) of the classic end record."""
    if size < _EOCD_MIN:
        return None
    window = min(size, _EOCD_MIN + _MAX_COMMENT)
    handle.seek(size - window)
    tail = handle.read(window)
    index = tail.rfind(_EOCD)
    if index < 0 or len(tail) - index < _EOCD_MIN:
        return None
    entries = int.from_bytes(tail[index + 10 : index + 12], "little")
    directory = int.from_bytes(tail[index + 12 : index + 16], "little")
    return entries, directory, size - window + index


def declared_entries(handle: BinaryIO, size: int) -> int | None:
    """Entry count from the classic end-of-central-directory record; None when not a zip."""
    record = _end_record(handle, size)
    return None if record is None else record[0]


def _zip64_records(handle: BinaryIO, eocd_offset: int) -> list[tuple[int, int]]:
    """(entries, directory bytes) of every ZIP64 end record a reader could use.

    Empty when there is no ZIP64 locator right before the classic record. A locator
    that leads to no ZIP64 record is refused: readers disagree on such a file.
    Both places a reader looks are checked (where the locator points, and right
    before the locator), so no choice between them escapes the limits.
    """
    locator_offset = eocd_offset - _ZIP64_LOCATOR_SIZE
    if locator_offset < 0:
        return []
    handle.seek(locator_offset)
    locator = handle.read(_ZIP64_LOCATOR_SIZE)
    if len(locator) != _ZIP64_LOCATOR_SIZE or not locator.startswith(_ZIP64_LOCATOR):
        return []
    _sig, _disk, pointed, _disks = struct.unpack("<4sLQL", locator)
    found: list[tuple[int, int]] = []
    for offset in {pointed, locator_offset - _ZIP64_RECORD_SIZE}:
        if not 0 <= offset <= locator_offset - _ZIP64_RECORD_SIZE:
            continue
        handle.seek(offset)
        record = handle.read(_ZIP64_RECORD_SIZE)
        if len(record) == _ZIP64_RECORD_SIZE and record.startswith(_ZIP64_RECORD):
            fields = struct.unpack("<4sQ2H2L4Q", record)
            found.append((max(fields[6], fields[7]), fields[8]))
    if not found:
        raise InvalidZip("arquivo compactado inválido (registro ZIP64 ausente)")
    return found


def _parser_view(handle: BinaryIO) -> tuple[int, int] | None:
    """(entries, directory bytes) exactly as ``zipfile`` will read them, when it exposes them.

    ``zipfile._EndRecData`` is what ``ZipFile`` calls before it parses the central
    directory (ZIP64 values applied); it reads only the end of the file.
    """
    reader = getattr(zipfile, "_EndRecData", None)
    if reader is None:  # pragma: no cover - private helper of CPython's zipfile
        return None
    try:
        record = reader(handle)
    except MemoryError:
        raise
    except Exception as exc:
        raise InvalidZip("arquivo compactado inválido") from exc
    if record is None:
        return None
    return int(record[4]), int(record[5])


def check_entry_count(handle: BinaryIO, size: int, limits: ExtractLimits) -> bool:
    """Pre-filter from the end records, before the central directory is parsed.

    True for a zip within the limits, False when it is not a zip at all. Counts
    and directory sizes come from the classic record, any ZIP64 record and the
    view ``zipfile`` itself will use; every one must be within the limits. The
    declared count can be forged (``zipfile`` walks the directory by its size),
    so the directory size is bounded too, which bounds the entries ``zipfile``
    would parse; the real count is checked again after opening
    (:func:`check_real_entries`).
    """
    record = _end_record(handle, size)
    if record is None:
        return False
    count, directory, offset = record
    if count == _ZIP64_ENTRIES:
        raise ExtractionFailed("arquivo compactado com entradas demais")
    views = [(count, directory), *_zip64_records(handle, offset)]
    parser = _parser_view(handle)
    if parser is not None:
        views.append(parser)
    max_directory = limits.max_zip_entries * _MAX_DIR_BYTES_PER_ENTRY
    for entries, directory_bytes in views:
        if entries > limits.max_zip_entries or directory_bytes > max_directory:
            raise ExtractionFailed("arquivo compactado com entradas demais")
    return True


def check_real_entries(count: int, limits: ExtractLimits) -> None:
    """Entries actually listed by the central directory, whatever the end record says."""
    if count > limits.max_zip_entries:
        raise ExtractionFailed("arquivo compactado com entradas demais")


def open_checked(path: Path, limits: ExtractLimits) -> zipfile.ZipFile:
    """Open an OOXML zip after the entry-count and declared-size checks."""
    try:
        with path.open("rb") as handle:
            size = os.fstat(handle.fileno()).st_size
            if not check_entry_count(handle, size, limits):
                raise ExtractionFailed("arquivo compactado inválido")
        archive = zipfile.ZipFile(path)
    except (ExtractionFailed, MemoryError):
        raise
    except Exception as exc:
        raise ExtractionFailed("arquivo compactado inválido") from exc
    try:
        infos = archive.infolist()
        check_real_entries(len(infos), limits)
        declared = sum(info.file_size for info in infos)
    except BaseException:
        archive.close()
        raise
    if declared > limits.max_unzipped_bytes:
        archive.close()
        raise ExtractionFailed("conteúdo descompactado grande demais")
    return archive


class _Budget:
    def __init__(self, total: int) -> None:
        self.left = total

    def spend(self, amount: int) -> None:
        self.left -= amount
        if self.left < 0:
            raise ExtractionFailed("conteúdo descompactado grande demais")


class _CappedReader:
    """File-like that fails once a part or the whole archive exceeds its cap."""

    def __init__(self, handle: IO[bytes], budget: _Budget, part_cap: int) -> None:
        self._handle = handle
        self._budget = budget
        self._part_left = part_cap

    def read(self, size: int = -1) -> bytes:
        want = _CHUNK if size is None or size < 0 else min(size, _CHUNK)
        data = self._handle.read(want)
        self._part_left -= len(data)
        if self._part_left < 0:
            raise ExtractionFailed("parte do arquivo grande demais")
        self._budget.spend(len(data))
        return data


def scan_archive(archive: zipfile.ZipFile, limits: ExtractLimits) -> None:
    """Read every entry within the caps; parse every XML part with defusedxml.

    Raises ExtractionFailed for any breach, malformed zip or XML, DTD or entity.
    """
    budget = _Budget(limits.max_unzipped_bytes)
    try:
        for info in archive.infolist():
            if info.is_dir():
                continue
            with archive.open(info) as handle:
                reader = _CappedReader(handle, budget, limits.max_part_bytes)
                if info.filename.lower().endswith(_XML_SUFFIXES):
                    for _event, element in SafeET.iterparse(
                        reader,
                        events=("end",),
                        forbid_dtd=True,
                        forbid_entities=True,
                        forbid_external=True,
                    ):
                        element.clear()
                else:
                    while reader.read(_CHUNK):
                        pass
    except (ExtractionFailed, MemoryError):
        raise
    except Exception as exc:
        raise ExtractionFailed("arquivo compactado ou XML inválido ou inseguro") from exc
