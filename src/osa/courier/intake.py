"""What enters the courier: one file, its root, how it got there and who declared the owner.

``stage_upload`` is step 3 of flow A (SPEC §5): the upload is written to the quarantine of
the owner's root under an exclusive name, never over anything, before any reading. A file
of the sensitive class is refused by name, before a byte is written.
"""

from __future__ import annotations

import os
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path

from osa.domain.document import FileClass, Origin
from osa.domain.errors import SensitiveFile
from osa.extract.classify import class_by_name, normalized_extension

_QUARANTINE_SUFFIX_OK = frozenset("abcdefghijklmnopqrstuvwxyz0123456789")


@dataclass(frozen=True, slots=True)
class Intake:
    """One file to process.

    ``source`` is the file to read. ``relative_path`` is its POSIX path relative to the root
    (quarantine or watched folder) when it lies inside it, which every effect requires; it
    is ``None`` only for a simulated run on a file outside the root (``osa process`` with
    effects off). ``original_name`` is the name the file is archived under; the quarantine
    name is an id. ``declared_owner`` is an ``owners.id`` resolved by code from what the
    operator wrote (``osa.courier.owners``), never by the model.
    """

    root_id: int
    source: Path
    relative_path: str | None
    original_name: str
    origin: Origin
    declared_owner: int | None = None
    sender: int | None = None


@dataclass(frozen=True, slots=True)
class StagedUpload:
    relative_path: str  # POSIX, relative to the root: the source of the archiving
    path: Path
    size: int
    simulated: bool  # effects off: nothing was written, the path is only predicted


def quarantine_suffix(original_name: str) -> str:
    """The normalized extension of the original name when it is a plain one; else nothing."""
    extension = normalized_extension(original_name)
    body = extension[1:]
    if body and len(body) <= 16 and set(body) <= _QUARANTINE_SUFFIX_OK:
        return extension
    return ""


def stage_upload(
    root: Path,
    data: bytes,
    original_name: str,
    *,
    sensitive_extensions: Iterable[str],
    effects_enabled: bool,
) -> StagedUpload:
    """Write ``data`` to ``<root>/.osa-quarentena/<id><ext>`` (exclusive, 0600).

    The quarantine is created on the first upload with effects on. With effects off nothing
    is created and the result is simulated. ``SensitiveFile`` is raised from the name alone.
    """
    if class_by_name(original_name, sensitive_extensions) is FileClass.SENSITIVE:
        raise SensitiveFile(
            "upload de classe sensível recusado: coloque o arquivo diretamente no share"
        )
    from osa.fs.quarantine import ensure_quarantine, new_quarantine_file

    ensure_quarantine(root, effects_enabled=effects_enabled)
    with new_quarantine_file(
        root, suffix=quarantine_suffix(original_name), effects_enabled=effects_enabled
    ) as file:
        if file.fd is not None:
            view = memoryview(data)
            while view:
                written = os.write(file.fd, view)
                view = view[written:]
        return StagedUpload(
            relative_path=file.rel,
            path=Path(root, *file.rel.split("/")),
            size=len(data),
            simulated=file.simulated,
        )
