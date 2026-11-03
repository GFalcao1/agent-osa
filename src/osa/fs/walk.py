"""Leitura da árvore de uma raiz para o scan: lista, nunca segue link, nunca escreve.

``iter_files`` caminha a raiz a partir do descritor de ``open_root``, componente por componente
com ``O_NOFOLLOW|O_DIRECTORY`` (como ``open_beneath``), em ordem determinística (nomes em ordem
de código Unicode em cada pasta, profundidade primeiro). A ordem é o que torna o scan retomável:
``after`` recebe o último caminho confirmado e a caminhada pula tudo que vem antes dele, sem
reler as pastas já concluídas.

Fica de fora tudo que começa com ponto (a quarentena ``.osa-quarentena``, a sonda
``.osa-probe-*``, ``.DS_Store`` e os ``._*`` que o macOS grava em share SMB), links simbólicos,
dispositivos e qualquer entrada que não seja pasta ou arquivo comum. Um erro de leitura numa
pasta (permissão, pasta sumida) é registrado no resultado e a caminhada continua.
"""

from __future__ import annotations

import errno
import os
import stat
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path

from osa.fs.roots import open_root

_DIR_FLAGS = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW


@dataclass(frozen=True, slots=True)
class FileEntry:
    """Um arquivo comum encontrado: componentes relativos à raiz e o ``stat`` sem seguir link."""

    parts: tuple[str, ...]
    size: int
    mtime: float

    @property
    def relative(self) -> str:
        return "/".join(self.parts)

    @property
    def name(self) -> str:
        return self.parts[-1]


@dataclass(frozen=True, slots=True)
class SkippedDir:
    """Uma pasta que não pôde ser lida; o scan registra e segue."""

    parts: tuple[str, ...]
    motivo: str


Visit = FileEntry | SkippedDir


def _code(error: OSError) -> str:
    return errno.errorcode.get(error.errno, "?") if error.errno is not None else "?"


def split_cursor(cursor: str | None) -> tuple[str, ...]:
    return tuple(part for part in cursor.split("/") if part) if cursor else ()


def comes_after(parts: tuple[str, ...], cursor: tuple[str, ...]) -> bool:
    """``parts`` vem depois de ``cursor`` na ordem da caminhada (tuplas comparadas em ordem)."""
    return parts > cursor


def _skip_dir(parts: tuple[str, ...], cursor: tuple[str, ...]) -> bool:
    """A pasta inteira já foi percorrida: todos os seus caminhos vêm antes do cursor.

    Uma pasta ``p`` está concluída quando ``p`` não é prefixo do cursor e ``p`` ordena antes
    dele (os descendentes de ``p`` começam por ``p`` e ordenam entre ``p`` e o próximo irmão).
    """
    if not cursor:
        return False
    depth = len(parts)
    return cursor[:depth] != parts and parts < cursor[:depth]


def _visit(dir_fd: int, parts: tuple[str, ...], cursor: tuple[str, ...]) -> Iterator[Visit]:
    try:
        names = sorted(os.listdir(dir_fd))
    except OSError as error:
        yield SkippedDir(parts, f"pasta ilegível ({_code(error)})")
        return
    for name in names:
        if name.startswith("."):
            continue
        child = (*parts, name)
        try:
            info = os.stat(name, dir_fd=dir_fd, follow_symlinks=False)
        except OSError:
            continue  # sumiu entre a listagem e o stat
        if stat.S_ISDIR(info.st_mode):
            if _skip_dir(child, cursor):
                continue
            try:
                fd = os.open(name, _DIR_FLAGS, dir_fd=dir_fd)
            except OSError as error:
                if error.errno in {errno.ELOOP, errno.ENOTDIR, errno.ENOENT}:
                    continue  # virou link ou sumiu
                yield SkippedDir(child, f"pasta ilegível ({_code(error)})")
                continue
            try:
                yield from _visit(fd, child, cursor)
            finally:
                os.close(fd)
        elif stat.S_ISREG(info.st_mode):
            if cursor and not comes_after(child, cursor):
                continue
            yield FileEntry(child, info.st_size, info.st_mtime)
        # links, sockets, dispositivos: ignorados


def iter_files(root: Path | str, *, after: str | None = None) -> Iterator[Visit]:
    """Arquivos comuns da raiz em ordem determinística, a partir do caminho ``after``.

    A raiz é aberta uma vez com ``open_root`` (recusa link em qualquer componente); cada pasta
    é aberta pelo descritor da anterior sem seguir link. Nada é criado, movido ou apagado.
    """
    cursor = split_cursor(after)
    with open_root(root) as root_fd:
        yield from _visit(root_fd, (), cursor)
