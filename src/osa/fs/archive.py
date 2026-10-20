"""Arquivar e desfazer: rename sem substituição, dentro de uma mesma raiz e de um mesmo volume.

A raiz é aberta e validada **uma vez** por operação; origem e destino são derivados desse
mesmo descritor, então trocar o caminho da raiz entre as etapas não cruza árvores. Todo efeito
é o rename exclusivo de ``noreplace``; sem backend comprovado a operação é recusada antes de
qualquer efeito, também no modo simulado. Nada aqui apaga, trunca ou substitui.

Limite honesto da identidade da origem: ela é conferida por descritor aberto e **depois** do
rename. A conferência detecta, não impede, a movimentação de um arquivo trocado por outro
cliente na janela entre a abertura e o rename (``IdentityChanged``, com o destino informado).
"""

from __future__ import annotations

import errno
import hashlib
import os
import re
import stat
from contextlib import ExitStack
from dataclasses import dataclass
from pathlib import Path, PurePath
from typing import Final

from osa.domain.errors import Conflict, NotFound, UnsafePath
from osa.fs import names
from osa.fs.errors import (
    CollisionExhausted,
    CrossDevice,
    CrossRoot,
    DestinationMissing,
    IdentityChanged,
)
from osa.fs.names import (
    MAX_NAME_UNITS,
    QUARANTINE_DIR,
    component_key,
    suffixed,
    validate_filename,
)
from osa.fs.noreplace import Primitive, proven_primitive, rename_noreplace
from osa.fs.roots import (
    find_entry,
    open_beneath,
    open_root,
    relative_parts,
)

#: ``st_ino`` entra na identidade da origem. A suite native decide se o inode é estável em SMB;
#: se não for, vira ``False`` e a identidade usa só tamanho, mtime e ``st_nlink``.
IDENTITY_USES_INODE: Final = True

_SOURCE_FLAGS = os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK
_SHA256 = re.compile(r"[0-9a-f]{64}")
_CHUNK = 1024 * 1024


@dataclass(frozen=True, slots=True)
class ArchiveRecord:
    """Conteúdo de uma linha de ``archive_operations`` (contrato; a migração é da TASK-107).

    Caminhos relativos à raiz, POSIX, na grafia gravada. O ``sha256`` é obrigatório: o
    desfazer só move o arquivo cujo conteúdo tem esse hash.
    """

    root_id: int
    root: Path
    origem: str
    destino: str
    sha256: str

    def __post_init__(self) -> None:
        if not _SHA256.fullmatch(self.sha256):
            raise ValueError("sha256 deve ter 64 dígitos hexadecimais minúsculos")
        for value in (self.origem, self.destino):
            if PurePath(value).is_absolute():
                raise ValueError("caminho do registro deve ser relativo à raiz")
            if not relative_parts(Path(self.root), value):
                raise ValueError("caminho do registro vazio")


@dataclass(frozen=True, slots=True)
class ArchiveResult:
    root: Path
    source_rel: str  # POSIX, relativo à raiz
    dest_rel: str  # POSIX, relativo à raiz, nome final
    final_name: str
    suffix: int  # 1 = sem sufixo
    simulated: bool
    size: int | None
    primitive: Primitive | None  # None se simulado

    @property
    def path(self) -> Path:
        return Path(self.root, *self.dest_rel.split("/"))

    def record(self, *, root_id: int, sha256: str) -> ArchiveRecord:
        """O registro desta operação, para ``archive_operations`` e para ``undo``."""
        return ArchiveRecord(
            root_id=root_id,
            root=self.root,
            origem=self.source_rel,
            destino=self.dest_rel,
            sha256=sha256,
        )


def _identity(info: os.stat_result) -> tuple[int, ...]:
    # st_nlink entra sempre: um hardlink criado na janela até o rename muda a identidade
    values = (info.st_size, info.st_mtime_ns, info.st_nlink)
    if IDENTITY_USES_INODE:
        return (info.st_dev, info.st_ino, *values)
    return values


def _sha256_of(fd: int) -> str:
    digest = hashlib.sha256()
    os.lseek(fd, 0, os.SEEK_SET)
    while chunk := os.read(fd, _CHUNK):
        digest.update(chunk)
    return digest.hexdigest()


def _open_source(dir_fd: int, name: str) -> tuple[int, str]:
    """Abre a origem por descritor, sem seguir link; devolve o descritor e a grafia gravada."""
    entry = find_entry(dir_fd, name)
    if entry is None:
        raise NotFound("origem não existe")
    try:
        fd = os.open(entry, _SOURCE_FLAGS, dir_fd=dir_fd)
    except FileNotFoundError:
        raise NotFound("origem não existe") from None
    except OSError as error:
        if error.errno in {errno.ELOOP, errno.ENXIO}:
            raise UnsafePath("origem não é arquivo regular (link simbólico ou especial)") from None
        raise
    return fd, entry


def _relocate(
    root: Path,
    src_parts: tuple[str, ...],
    src_name: str,
    dest_parts: tuple[str, ...],
    name: str,
    *,
    effects_enabled: bool,
    max_name_units: int,
    expected_sha256: str | None,
) -> ArchiveResult:
    final = validate_filename(name, max_units=max_name_units)
    base = Path(root)
    with ExitStack() as stack:
        root_fd = stack.enter_context(open_root(base))
        source_dir = stack.enter_context(open_beneath(root_fd, src_parts))
        try:
            dest_dir = stack.enter_context(open_beneath(root_fd, dest_parts))
        except NotFound:
            raise DestinationMissing("pasta de destino não existe") from None
        source_stat = os.fstat(source_dir.fd)
        dest_stat = os.fstat(dest_dir.fd)
        if source_stat.st_dev != dest_stat.st_dev:
            raise CrossDevice("origem e destino em volumes diferentes")
        # sem backend comprovado nada acontece, nem simulado; consultado uma vez por operação
        primitive = proven_primitive(dest_dir.fd)

        source_fd, entry = _open_source(source_dir.fd, src_name)
        stack.callback(os.close, source_fd)
        before = os.fstat(source_fd)
        if not stat.S_ISREG(before.st_mode):
            raise UnsafePath("origem não é arquivo regular")
        if before.st_nlink > 1:
            # outro nome do mesmo conteúdo (talvez fora da raiz) continuaria apontando para ele
            raise UnsafePath("origem com mais de um nome (hardlink)")
        if expected_sha256 is not None:
            if _sha256_of(source_fd) != expected_sha256:
                raise IdentityChanged("o conteúdo não é mais o do registro (sha256 diferente)")
            if _identity(os.fstat(source_fd)) != _identity(before):
                raise IdentityChanged("a origem mudou enquanto era conferida")
        source_rel = "/".join((*source_dir.parts, entry))

        same_dir = (source_stat.st_dev, source_stat.st_ino) == (dest_stat.st_dev, dest_stat.st_ino)
        if same_dir and component_key(entry) == component_key(final):
            raise Conflict("a origem já está no destino com esse nome")
        taken = {component_key(existing) for existing in os.listdir(dest_dir.fd)}

        for n in range(1, names.MAX_SUFFIX + 1):
            candidate = suffixed(final, n, max_units=max_name_units)
            key = component_key(candidate)
            if key in taken:
                continue
            dest_rel = "/".join((*dest_dir.parts, candidate))
            if not effects_enabled:
                return ArchiveResult(
                    root=base,
                    source_rel=source_rel,
                    dest_rel=dest_rel,
                    final_name=candidate,
                    suffix=n,
                    simulated=True,
                    size=before.st_size,
                    primitive=None,
                )
            outcome = rename_noreplace(
                source_dir.fd, entry, dest_dir.fd, candidate, primitive=primitive
            )
            if not outcome.renamed:
                taken.add(key)
                continue
            try:
                after = os.stat(candidate, dir_fd=dest_dir.fd, follow_symlinks=False)
            except FileNotFoundError:
                raise IdentityChanged(
                    "o destino sumiu logo depois do rename", destino=dest_rel
                ) from None
            if _identity(after) != _identity(before):
                raise IdentityChanged(
                    "a origem foi trocada ou alterada durante a operação; a conferência é "
                    "posterior ao rename e detecta, não impede",
                    destino=dest_rel,
                )
            return ArchiveResult(
                root=base,
                source_rel=source_rel,
                dest_rel=dest_rel,
                final_name=candidate,
                suffix=n,
                simulated=False,
                size=before.st_size,
                primitive=outcome.primitive,
            )
    raise CollisionExhausted(f"sufixos até ({names.MAX_SUFFIX}) ocupados")


def archive(
    root: Path,
    src: str | PurePath,
    dest_dir: str | PurePath,
    name: str | None = None,
    *,
    effects_enabled: bool = False,
    max_name_units: int = MAX_NAME_UNITS,
) -> ArchiveResult:
    """Arquiva ``src`` em ``dest_dir`` por rename exclusivo, na mesma raiz e no mesmo volume.

    O correio passa ``src`` e ``dest_dir`` **relativos à raiz** (é a forma do registro);
    absolutos só são aceitos se estiverem lexicalmente dentro de ``root`` (senão
    ``CrossRoot``) e nunca são resolvidos. Origem com mais de um nome (hardlink) é
    ``UnsafePath``. O destino é uma pasta existente do template (nunca é criada;
    ``DestinationMissing``) e nunca a quarentena (``CrossRoot``). O nome é ``name`` ou o da
    origem, em NFC; em colisão ganha `` (2)``, `` (3)``... antes da extensão (composta inclusive);
    nome ou sufixo acima de ``max_name_units`` é ``NameTooLong``, sem truncar e sem efeito.
    Sem rename exclusivo comprovado, ``NoReplaceUnavailable``. Com efeitos desligados (padrão)
    só olha e devolve o previsto (``simulated=True``).
    """
    base = Path(root)
    src_parts = relative_parts(base, src)
    if not src_parts:
        raise UnsafePath("origem vazia")
    dest_parts = relative_parts(base, dest_dir)
    if not dest_parts:
        raise UnsafePath("destino não pode ser a própria raiz")
    if component_key(dest_parts[0]) == component_key(QUARANTINE_DIR):
        raise CrossRoot("a quarentena nunca é destino de arquivamento")
    return _relocate(
        base,
        src_parts[:-1],
        src_parts[-1],
        dest_parts,
        name if name is not None else src_parts[-1],
        effects_enabled=effects_enabled,
        max_name_units=max_name_units,
        expected_sha256=None,
    )


def undo(
    record: ArchiveRecord,
    *,
    effects_enabled: bool = False,
    max_name_units: int = MAX_NAME_UNITS,
) -> ArchiveResult:
    """Rename inverso de um arquivamento registrado, conferido pelo ``sha256`` do registro.

    O arquivo de ``record.destino`` volta para a pasta e o nome de ``record.origem`` (a origem
    pode ser a quarentena). A pasta tem de existir (``DestinationMissing``; nunca é criada, a
    quarentena inclusive). Conteúdo diferente do registrado é ``IdentityChanged``; destino
    ausente (movido à mão, ou desfeito antes) é ``NotFound``. Vaga ocupada vira `` (n)``.
    """
    base = Path(record.root)
    origem = relative_parts(base, record.origem)
    destino = relative_parts(base, record.destino)
    return _relocate(
        base,
        destino[:-1],
        destino[-1],
        origem[:-1],
        origem[-1],
        effects_enabled=effects_enabled,
        max_name_units=max_name_units,
        expected_sha256=record.sha256,
    )
