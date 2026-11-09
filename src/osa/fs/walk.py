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
from collections.abc import Iterable, Iterator
from dataclasses import dataclass
from pathlib import Path

from osa.fs.names import component_key
from osa.fs.roots import list_dir, open_beneath, open_root

_DIR_FLAGS = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW


@dataclass(frozen=True, slots=True)
class FileEntry:
    """Um arquivo comum encontrado: componentes relativos à raiz e o ``stat`` sem seguir link."""

    parts: tuple[str, ...]
    size: int
    mtime: float
    mtime_ns: int = 0  # the exact mtime, for the stability check of a watched folder

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


PruneKey = tuple[str, ...]


def prune_key(relative: str) -> PruneKey:
    """A folder to leave out, compared as the share compares names (case, Unicode form)."""
    return tuple(component_key(part) for part in relative.split("/") if part)


def _visit(
    dir_fd: int,
    parts: tuple[str, ...],
    cursor: tuple[str, ...],
    prune: frozenset[PruneKey] = frozenset(),
) -> Iterator[Visit]:
    try:
        names = sorted(list_dir(dir_fd))
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
            if prune and tuple(component_key(p) for p in child) in prune:
                continue  # a watched folder: the worker reads it, the scan never does
            try:
                fd = os.open(name, _DIR_FLAGS, dir_fd=dir_fd)
            except OSError as error:
                if error.errno in {errno.ELOOP, errno.ENOTDIR, errno.ENOENT}:
                    continue  # virou link ou sumiu
                yield SkippedDir(child, f"pasta ilegível ({_code(error)})")
                continue
            try:
                yield from _visit(fd, child, cursor, prune)
            finally:
                os.close(fd)
        elif stat.S_ISREG(info.st_mode):
            if cursor and not comes_after(child, cursor):
                continue
            yield FileEntry(child, info.st_size, info.st_mtime, info.st_mtime_ns)
        # links, sockets, dispositivos: ignorados


def iter_files(
    root: Path | str, *, after: str | None = None, skip: Iterable[str] = ()
) -> Iterator[Visit]:
    """Arquivos comuns da raiz em ordem determinística, a partir do caminho ``after``.

    A raiz é aberta uma vez com ``open_root`` (recusa link em qualquer componente); cada pasta
    é aberta pelo descritor da anterior sem seguir link. ``skip`` são pastas relativas à raiz
    que ficam inteiras de fora (as pastas monitoradas). Nada é criado, movido ou apagado.
    """
    cursor = split_cursor(after)
    prune = frozenset(prune_key(folder) for folder in skip)
    with open_root(root) as root_fd:
        yield from _visit(root_fd, (), cursor, prune)


#: Nomes de cópia em andamento ou de lixo de sistema que a pasta monitorada nunca processa.
_TEMPORARY_PREFIXES = ("~$",)
_TEMPORARY_SUFFIXES = (".part", ".partial", ".crdownload", ".download", ".tmp", ".temp")
_SYSTEM_NAMES = frozenset({"thumbs.db", "desktop.ini"})


def is_temporary_name(name: str) -> bool:
    """Arquivo que um programa ainda escreve (download, cópia, dono do Office) ou do sistema."""
    folded = name.casefold()
    return (
        name.startswith(".")
        or folded.startswith(_TEMPORARY_PREFIXES)
        or folded.endswith(_TEMPORARY_SUFFIXES)
        or folded in _SYSTEM_NAMES
    )


def list_folder(
    root: Path | str, relative: str, *, unreadable: list[str] | None = None
) -> list[FileEntry]:
    """Os arquivos comuns de uma pasta da raiz (sem descer em subpastas), em ordem de nome.

    Para a pasta monitorada: só ``stat`` sem seguir link, nunca o conteúdo; nomes com ponto e
    de arquivo temporário ficam de fora (``is_temporary_name``). Um nome cujo ``stat`` falhou
    vai para ``unreadable`` (o chamador não o toma por sumido). Pasta que não existe é
    ``NotFound``; link no caminho é ``UnsafePath``.
    """
    parts = tuple(part for part in relative.split("/") if part)
    entries: list[FileEntry] = []
    with open_root(root) as root_fd, open_beneath(root_fd, parts) as folder:
        for name in sorted(list_dir(folder.fd)):
            if is_temporary_name(name):
                continue
            try:
                info = os.stat(name, dir_fd=folder.fd, follow_symlinks=False)
            except FileNotFoundError:
                continue  # sumiu entre a listagem e o stat
            except OSError:
                if unreadable is not None:
                    unreadable.append("/".join((*folder.parts, name)))
                continue
            if stat.S_ISREG(info.st_mode):
                entries.append(
                    FileEntry((*folder.parts, name), info.st_size, info.st_mtime, info.st_mtime_ns)
                )
    return entries
