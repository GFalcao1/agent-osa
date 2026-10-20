"""Quarentena ``.osa-quarentena`` dentro da raiz: onde o upload é gravado antes de arquivar.

Fica no mesmo volume da árvore, então arquivar é rename. Nada aqui apaga: um upload
interrompido deixa um arquivo parcial (órfão) na quarentena, tratado pela task de integração.
Os efeitos (criar a pasta, criar o arquivo) exigem ``effects_enabled=True``; desligado, nada é
criado e o resultado é simulado, previsto com o estado observado.
"""

from __future__ import annotations

import os
import uuid
from collections.abc import Iterator
from contextlib import ExitStack, contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Final

from osa.domain.errors import Conflict, NotFound
from osa.fs.errors import DestinationMissing
from osa.fs.names import QUARANTINE_DIR
from osa.fs.roots import find_entry, open_beneath, open_root

_MAX_ID_ATTEMPTS: Final = 16
_FILE_FLAGS = os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW


@dataclass(frozen=True, slots=True)
class QuarantineResult:
    path: Path
    created: bool  # criada agora (ou, se simulado, seria criada)
    simulated: bool


@dataclass(frozen=True, slots=True)
class QuarantineFile:
    """Arquivo exclusivo da quarentena.

    ``fd`` é o descritor gravável (``None`` quando simulado: nada foi criado); ``rel`` é o
    caminho relativo à raiz, POSIX, que vira a origem do arquivamento.
    """

    fd: int | None
    rel: str
    simulated: bool


def _new_id() -> str:
    return uuid.uuid4().hex


def ensure_quarantine(root: Path, *, effects_enabled: bool = False) -> QuarantineResult:
    """Garante a pasta ``.osa-quarentena`` na raiz; já existente e real é no-op.

    Existente como arquivo ou link é ``UnsafePath``. Desligado, só olha e devolve o previsto.
    """
    base = Path(root)
    with open_root(base) as root_fd:
        if not effects_enabled and find_entry(root_fd, QUARANTINE_DIR) is None:
            return QuarantineResult(base / QUARANTINE_DIR, created=True, simulated=True)
        with open_beneath(root_fd, (QUARANTINE_DIR,), create=effects_enabled) as opened:
            return QuarantineResult(
                base.joinpath(*opened.parts),
                created=opened.created[0],
                simulated=not effects_enabled,
            )


@contextmanager
def new_quarantine_file(root: Path, *, effects_enabled: bool = False) -> Iterator[QuarantineFile]:
    """Cria na quarentena um arquivo de nome exclusivo (``uuid4`` hex, ``O_EXCL``, ``0o600``).

    A pasta tem de existir (``DestinationMissing``; esta função não a cria). O nome não depende
    do nome original do documento, que fica no banco. Desligado, não cria nada e devolve um
    resultado simulado com ``fd=None``. O descritor é fechado na saída; nunca apaga.
    """
    with ExitStack() as stack:
        root_fd = stack.enter_context(open_root(Path(root)))
        try:
            directory = stack.enter_context(open_beneath(root_fd, (QUARANTINE_DIR,)))
        except NotFound:
            raise DestinationMissing("quarentena não existe; ensure_quarantine a cria") from None
        folder = directory.parts[0]
        if not effects_enabled:
            yield QuarantineFile(None, f"{folder}/{_new_id()}", simulated=True)
            return
        for _ in range(_MAX_ID_ATTEMPTS):
            name = _new_id()
            try:
                fd = os.open(name, _FILE_FLAGS, 0o600, dir_fd=directory.fd)
            except FileExistsError:
                continue
            break
        else:
            raise Conflict("sem nome exclusivo na quarentena")
        stack.callback(os.close, fd)
        yield QuarantineFile(fd, f"{folder}/{name}", simulated=False)
