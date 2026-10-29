"""The courier's ports to the outside: document content, filesystem and model.

The pipeline depends only on these protocols; the local implementations delegate to
``osa.extract`` and ``osa.fs`` (imported lazily: the filesystem package is POSIX-only and
the courier must stay importable, and testable with fakes, without it). The model port is
``osa.llm.provider.LLMProvider``: the courier only ever calls ``classify``.
"""

from __future__ import annotations

import hashlib
import os
import stat
from collections.abc import Sequence
from pathlib import Path
from typing import TYPE_CHECKING, Protocol

from osa.domain.document import FileClass
from osa.domain.errors import UnsafePath
from osa.extract.classify import class_by_name
from osa.extract.model import ExtractLimits

if TYPE_CHECKING:
    from osa.courier.decision import TemplateDepartment
    from osa.extract.model import ExtractedContent
    from osa.fs.archive import ArchiveResult
    from osa.fs.template import TreeResult
    from osa.llm.envelopes import ClassificationEnvelope, DepartmentInput

_CHUNK = 1024 * 1024
_READ_FLAGS = (
    os.O_RDONLY
    | getattr(os, "O_NOFOLLOW", 0)
    | getattr(os, "O_NONBLOCK", 0)
    | getattr(os, "O_BINARY", 0)
)


class ContentPort(Protocol):
    """What the courier does with the bytes of a document."""

    def class_by_name(self, name: str) -> FileClass:
        """Class from the name only; no I/O (a sensitive file is never opened)."""
        ...

    def file_class(self, path: Path) -> FileClass:
        """Class confirmed by a content sniff (``ExtractionFailed`` if unreadable)."""
        ...

    def sha256(self, path: Path) -> str: ...

    def extract(self, path: Path) -> ExtractedContent: ...


class FsPort(Protocol):
    """The only filesystem operations the courier performs (SPEC §10)."""

    def locate(self, root: Path, relative: str) -> Path:
        """The existing path of ``relative`` inside ``root``, link-free, or an error."""
        ...

    def has_folder(self, root: Path, relative: str) -> bool: ...

    def ensure_owner_tree(
        self,
        root: Path,
        owner_folder: str,
        departments: Sequence[TemplateDepartment],
        year: int | None,
        *,
        effects_enabled: bool,
    ) -> TreeResult: ...

    def archive(
        self, root: Path, src: str, dest_dir: str, name: str, *, effects_enabled: bool
    ) -> ArchiveResult: ...

    def undo(
        self,
        root: Path,
        root_id: int,
        origem: str,
        destino: str,
        sha256: str,
        *,
        effects_enabled: bool,
    ) -> ArchiveResult: ...


class ClassifyPort(Protocol):
    def classify(
        self, content: ExtractedContent, departments: Sequence[DepartmentInput]
    ) -> ClassificationEnvelope: ...


def sha256_of_file(path: Path) -> str:
    """sha256 of a regular file, read through a descriptor opened without following links."""
    try:
        fd = os.open(path, _READ_FLAGS)
    except OSError:
        raise UnsafePath("arquivo ilegível") from None
    try:
        if not stat.S_ISREG(os.fstat(fd).st_mode):
            raise UnsafePath("não é um arquivo comum")
        digest = hashlib.sha256()
        while chunk := os.read(fd, _CHUNK):
            digest.update(chunk)
    finally:
        os.close(fd)
    return digest.hexdigest()


class LocalContent:
    """``ContentPort`` over ``osa.extract`` with the configured limits."""

    def __init__(self, limits: ExtractLimits | None = None) -> None:
        self.limits = limits or ExtractLimits()

    def class_by_name(self, name: str) -> FileClass:
        return class_by_name(name, self.limits.sensitive_extensions)

    def file_class(self, path: Path) -> FileClass:
        from osa.extract.classify import file_class

        return file_class(path, self.limits)

    def sha256(self, path: Path) -> str:
        return sha256_of_file(path)

    def extract(self, path: Path) -> ExtractedContent:
        from osa.extract import extract

        return extract(path, self.limits)


class LocalFs:
    """``FsPort`` over ``osa.fs``: the rename-only, same-root operations of TASK-104."""

    def locate(self, root: Path, relative: str) -> Path:
        from osa.fs.roots import resolve_within

        return resolve_within(root, relative)

    def has_folder(self, root: Path, relative: str) -> bool:
        from osa.domain.errors import NotFound
        from osa.fs.roots import resolve_within

        try:
            return resolve_within(root, relative).is_dir()
        except NotFound:
            return False

    def ensure_owner_tree(
        self,
        root: Path,
        owner_folder: str,
        departments: Sequence[TemplateDepartment],
        year: int | None,
        *,
        effects_enabled: bool,
    ) -> TreeResult:
        from osa.fs.template import DepartmentFolder, ensure_owner_tree

        return ensure_owner_tree(
            root,
            owner_folder,
            [DepartmentFolder(d.pasta, d.usa_ano) for d in departments],
            year,
            effects_enabled=effects_enabled,
        )

    def archive(
        self, root: Path, src: str, dest_dir: str, name: str, *, effects_enabled: bool
    ) -> ArchiveResult:
        from osa.fs.archive import archive

        return archive(root, src, dest_dir, name, effects_enabled=effects_enabled)

    def undo(
        self,
        root: Path,
        root_id: int,
        origem: str,
        destino: str,
        sha256: str,
        *,
        effects_enabled: bool,
    ) -> ArchiveResult:
        from osa.fs.archive import ArchiveRecord, undo

        record = ArchiveRecord(
            root_id=root_id, root=root, origem=origem, destino=destino, sha256=sha256
        )
        return undo(record, effects_enabled=effects_enabled)
