"""A raiz é aberta e validada uma vez por operação; todo o resto nasce desse descritor.

``open_root`` abre a raiz sem seguir link e entrega o descritor. ``open_beneath`` caminha a
partir de um descritor, componente por componente, com ``O_NOFOLLOW|O_DIRECTORY``, então um
caminho trocado depois (renomear a raiz, trocar uma pasta por link) não muda a árvore em uso.
Origem e destino de uma operação são derivados do mesmo descritor de raiz.

``resolve_within`` é uma **verificação** (existe, está dentro, sem link), não uma capability:
quem vai produzir efeito usa ``open_root`` e ``open_beneath``.
"""

from __future__ import annotations

import errno
import os
import stat
from collections.abc import Iterator, Sequence
from contextlib import ExitStack, contextmanager
from dataclasses import dataclass
from pathlib import Path, PurePath

from osa.domain.errors import Conflict, NotFound, UnsafePath
from osa.fs.errors import CrossRoot, NameTooLong
from osa.fs.names import MAX_NAME_UNITS, QUARANTINE_DIR, component_key, validate_folder_component

_DIR_FLAGS = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
#: Na caminhada da raiz, cada componente é aberto por ``dir_fd`` sem seguir link. No macOS usa
#: ``O_NOFOLLOW_ANY`` (link em qualquer componente é ``ELOOP``), que o kernel recusa com
#: ``EINVAL`` se vier junto de ``O_NOFOLLOW``; nos outros sistemas, ``O_NOFOLLOW``.
_NOFOLLOW_ANY: int = getattr(os, "O_NOFOLLOW_ANY", 0)
_ROOT_FLAGS = os.O_RDONLY | os.O_DIRECTORY | _NOFOLLOW_ANY if _NOFOLLOW_ANY else _DIR_FLAGS
#: ``/`` e os ancestrais da raiz só precisam de busca (``x``), não de leitura: ``O_SEARCH`` no
#: macOS, ``O_PATH`` no Linux; sem nenhum dos dois, as flags da raiz (exigem leitura).
_O_SEARCH: int = getattr(os, "O_SEARCH", 0)
_O_PATH: int = getattr(os, "O_PATH", 0)
if _O_SEARCH and _NOFOLLOW_ANY:
    _SEARCH_FLAGS = _O_SEARCH | os.O_DIRECTORY | _NOFOLLOW_ANY
elif _O_PATH:
    _SEARCH_FLAGS = _O_PATH | os.O_DIRECTORY | os.O_NOFOLLOW
else:
    _SEARCH_FLAGS = _ROOT_FLAGS


@dataclass(frozen=True, slots=True)
class OpenDir:
    """Diretório aberto por ``open_beneath``.

    ``parts`` está na grafia gravada no disco (não a pedida); ``created[i]`` diz se o
    componente ``i`` foi criado agora.
    """

    fd: int
    parts: tuple[str, ...]
    created: tuple[bool, ...]


def check_component(part: str) -> str:
    """O componente se for um nome simples; ``UnsafePath`` senão (nunca ``ValueError``)."""
    if not part or part in {".", ".."}:
        raise UnsafePath("componente de caminho inválido")
    if "\0" in part or "/" in part or "\\" in part:
        raise UnsafePath("componente de caminho contém caractere proibido")
    return part


def relative_parts(root: Path, path: str | PurePath) -> tuple[str, ...]:
    """Componentes de ``path`` relativos à raiz, só por análise do texto.

    Absoluto só vale se estiver lexicalmente dentro da raiz (``CrossRoot`` senão); relativo é
    relativo à raiz. ``..``, ``.``, vazio, NUL e barra invertida são ``UnsafePath``.
    """
    text = os.fspath(path)
    if "\0" in text or "\\" in text:
        raise UnsafePath("caminho contém caractere proibido")
    candidate = PurePath(text)
    if candidate.is_absolute():
        try:
            candidate = candidate.relative_to(PurePath(root))
        except ValueError:
            raise CrossRoot("caminho fora da raiz") from None
    parts = candidate.parts
    for part in parts:
        check_component(part)
    return parts


def _walk_root(text: str) -> int:
    """Abre ``/`` e caminha os componentes da raiz por ``dir_fd``, sem seguir link em nenhum.

    Ancestrais abertos só para busca; o último componente (a raiz) para leitura.
    """
    parts = PurePath(text).parts[1:]
    current = os.open("/", _SEARCH_FLAGS)
    try:
        for index, part in enumerate(parts):
            flags = _ROOT_FLAGS if index == len(parts) - 1 else _SEARCH_FLAGS
            try:
                child = os.open(part, flags, dir_fd=current)
            except OSError:
                raise UnsafePath("raiz inacessível, inexistente ou com link simbólico") from None
            os.close(current)
            current = child
    except BaseException:
        os.close(current)
        raise
    return current


@contextmanager
def open_root(root: Path | str) -> Iterator[int]:
    """Abre a raiz uma vez, sem seguir link em nenhum componente, e entrega o descritor.

    A raiz configurada tem de ser o caminho real: absoluta, sem NUL, sem ``..`` e sem link em
    nenhum componente, inclusive o último (um apelido como um link em ``/Volumes`` é recusado;
    configure o destino do link). A abertura parte de ``/`` e caminha componente a componente
    com ``dir_fd`` e ``O_NOFOLLOW|O_DIRECTORY`` (mais ``O_NOFOLLOW_ANY`` no macOS), então um
    componente trocado por link entre a checagem e a abertura é ``UnsafePath``. ``realpath`` e a
    identidade do diretório aberto contra o caminho ficam como conferência cruzada.

    O correio passa às operações caminhos **relativos à raiz** (os absolutos só são aceitos se
    estiverem lexicalmente dentro dela, e nunca são resolvidos).
    """
    base = Path(root)
    text = str(base)
    if "\0" in text or not base.is_absolute():
        raise UnsafePath("raiz deve ser um caminho absoluto sem NUL")
    if not base.parts[1:]:
        raise UnsafePath("a raiz do sistema (/) não é uma raiz de arquivamento")
    if os.path.realpath(text) != text:
        raise UnsafePath("raiz com link simbólico ou fora da forma canônica")
    fd = _walk_root(text)
    try:
        opened = os.fstat(fd)
        try:
            current = os.stat(text, follow_symlinks=False)
        except OSError:
            raise UnsafePath("raiz mudou durante a abertura") from None
        if (opened.st_dev, opened.st_ino) != (current.st_dev, current.st_ino):
            raise UnsafePath("raiz mudou durante a abertura")
        if os.path.realpath(text) != text:
            raise UnsafePath("raiz mudou durante a abertura")
        yield fd
    finally:
        os.close(fd)


def find_entry(dir_fd: int, name: str) -> str | None:
    """A grafia gravada do componente ``name`` no diretório, ou ``None`` se não existe.

    Igual exato vence; senão compara por ``component_key`` (caixa e forma Unicode); dois
    candidatos distintos são ``Conflict``.
    """
    entries = os.listdir(dir_fd)
    if name in entries:
        return name
    key = component_key(name)
    matches = [entry for entry in entries if component_key(entry) == key]
    if len(matches) > 1:
        raise Conflict("mais de uma pasta com o mesmo nome (caixa ou forma Unicode)")
    return matches[0] if matches else None


@contextmanager
def open_beneath(
    root_fd: int,
    parts: Sequence[str],
    *,
    create: bool = False,
    max_name_units: int = MAX_NAME_UNITS,
) -> Iterator[OpenDir]:
    """Abre ``parts`` sob o descritor da raiz, sem seguir link, e entrega o último diretório.

    Componente ausente é ``NotFound`` (ou é criado com ``mkdir`` por descritor se ``create``;
    único ``mkdir`` do pacote); link ou arquivo no lugar de pasta é ``UnsafePath``. Sem
    componentes, entrega o próprio descritor da raiz, que continua sendo de quem o abriu.
    """
    with ExitStack() as stack:
        current = root_fd
        names: list[str] = []
        created: list[bool] = []
        for part in parts:
            check_component(part)
            entry = find_entry(current, part)
            made = False
            if entry is None:
                if not create:
                    raise NotFound("pasta não existe")
                # único nome com ponto que a aplicação cria: a quarentena
                entry = (
                    part
                    if part == QUARANTINE_DIR
                    else validate_folder_component(part, max_units=max_name_units)
                )
                try:
                    os.mkdir(entry, 0o755, dir_fd=current)
                    made = True
                except FileExistsError:
                    found = find_entry(current, part)
                    if found is None:
                        raise Conflict("pasta criada e sumida durante a operação") from None
                    entry = found
                except OSError as error:
                    if error.errno == errno.ENAMETOOLONG:
                        raise NameTooLong("nome de pasta excede o limite do share") from None
                    raise
            try:
                child = os.open(entry, _DIR_FLAGS, dir_fd=current)
            except FileNotFoundError:
                raise NotFound("pasta sumiu durante a operação") from None
            except OSError as error:
                if error.errno in {errno.ELOOP, errno.ENOTDIR}:
                    raise UnsafePath("link simbólico ou arquivo no lugar de pasta") from None
                if error.errno == errno.ENAMETOOLONG:
                    raise NameTooLong("nome de pasta excede o limite do share") from None
                raise
            stack.callback(os.close, child)
            current = child
            names.append(entry)
            created.append(made)
        yield OpenDir(current, tuple(names), tuple(created))


def resolve_within(root: Path, relative: str | PurePath) -> Path:
    """O caminho existente de ``relative`` dentro de ``root``, na grafia gravada.

    Recusa ``..``, vazio, NUL, ``\\``, absoluto fora da raiz (``CrossRoot``), link em qualquer
    componente e o que não existe. É uma verificação: o efeito deve usar ``open_root``.
    """
    parts = relative_parts(root, relative)
    if not parts:
        raise UnsafePath("caminho vazio")
    with open_root(root) as root_fd, open_beneath(root_fd, parts[:-1]) as parent:
        entry = find_entry(parent.fd, parts[-1])
        if entry is None:
            raise NotFound("caminho não existe")
        if stat.S_ISLNK(os.stat(entry, dir_fd=parent.fd, follow_symlinks=False).st_mode):
            raise UnsafePath("link simbólico no caminho")
        result = Path(root, *parent.parts, entry)
    try:
        resolved = result.resolve(strict=True)
    except OSError:
        raise NotFound("caminho não existe") from None
    if resolved != result:
        raise UnsafePath("caminho resolve para fora da forma esperada")
    return result
