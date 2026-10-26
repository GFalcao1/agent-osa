"""Rename que o sistema recusa se o destino existe; sem isso, nada acontece.

Só há dois backends, ambos do sistema operacional e atômicos:

- macOS: ``renameatx_np(..., RENAME_EXCL)``, e só depois de ler a capability
  ``VOL_CAP_INT_RENAME_EXCL`` do volume de destino (sem ela o kernel pode ignorar a flag e
  substituir o destino);
- Linux: ``renameat2(..., RENAME_NOREPLACE)``.

Não existe fallback: sem backend comprovado a operação é recusada com
``NoReplaceUnavailable``, antes de qualquer efeito. Nada de reserva de nome com placeholder,
``link`` ou remoção. No Linux a prova da capability é a própria chamada (``EINVAL`` ou
``ENOTSUP`` do volume viram ``NoReplaceUnavailable`` sem nada ter sido movido), então o
modo simulado só enxerga a falta de backend, não a de suporte do volume.
"""

from __future__ import annotations

import ctypes
import ctypes.util
import errno
import os
import struct
import sys
import threading
from dataclasses import dataclass
from enum import StrEnum
from typing import Final

from osa.domain.errors import NotFound
from osa.fs.errors import CrossDevice, NameTooLong, NoReplaceUnavailable
from osa.fs.roots import check_component

_RENAME_EXCL: Final = 0x4  # macOS
_RENAME_NOREPLACE: Final = 0x1  # Linux
_RENAME_ARGTYPES: Final = (
    ctypes.c_int,
    ctypes.c_char_p,
    ctypes.c_int,
    ctypes.c_char_p,
    ctypes.c_uint,
)
VOL_CAP_INT_RENAME_EXCL: Final = 0x00080000
_ATTR_BIT_MAP_COUNT: Final = 5
_ATTR_VOL_INFO: Final = 0x80000000
_ATTR_VOL_CAPABILITIES: Final = 0x00020000
_CAP_INTERFACES: Final = 1


class Primitive(StrEnum):
    RENAMEATX_NP = "renameatx_np"
    RENAMEAT2 = "renameat2"


@dataclass(frozen=True, slots=True)
class RenameOutcome:
    renamed: bool  # False: o destino já existe (EEXIST), nada foi movido
    primitive: Primitive


@dataclass(frozen=True, slots=True)
class VolumeCapabilities:
    """Bits de ``VOL_CAPABILITIES_INTERFACES`` do volume (macOS)."""

    rename_excl: bool
    rename_excl_valid: bool
    raw_capabilities: int
    raw_valid: int
    raw_format: int = 0  # VOL_CAPABILITIES_FORMAT (caixa, links, hardlinks), só para registro
    raw_format_valid: int = 0


class _AttrList(ctypes.Structure):
    _fields_ = (
        ("bitmapcount", ctypes.c_ushort),
        ("reserved", ctypes.c_uint16),
        ("commonattr", ctypes.c_uint32),
        ("volattr", ctypes.c_uint32),
        ("dirattr", ctypes.c_uint32),
        ("fileattr", ctypes.c_uint32),
        ("forkattr", ctypes.c_uint32),
    )


_lock = threading.Lock()
_unsupported_devices: set[int] = set()


def _platform() -> str:
    return sys.platform


def _load_libc() -> ctypes.CDLL:
    return ctypes.CDLL(ctypes.util.find_library("c"), use_errno=True)


_libc: ctypes.CDLL | None = None


def _library() -> ctypes.CDLL:
    global _libc
    if _libc is None:
        _libc = _load_libc()
    return _libc


def clear_unsupported_cache() -> None:
    """Esquece os volumes marcados como sem suporte (uso em testes)."""
    with _lock:
        _unsupported_devices.clear()


def volume_capabilities(dir_fd: int) -> VolumeCapabilities | None:
    """Capabilities de interface do volume do diretório, ou ``None`` se não deu para ler."""
    if _platform() != "darwin":
        return None
    libc = _library()
    function = getattr(libc, "fgetattrlist", None)
    if function is None:
        return None
    function.argtypes = [
        ctypes.c_int,
        ctypes.POINTER(_AttrList),
        ctypes.c_void_p,
        ctypes.c_size_t,
        ctypes.c_ulong,
    ]
    function.restype = ctypes.c_int
    request = _AttrList(_ATTR_BIT_MAP_COUNT, 0, 0, _ATTR_VOL_INFO | _ATTR_VOL_CAPABILITIES, 0, 0, 0)
    buffer = ctypes.create_string_buffer(256)
    if function(dir_fd, ctypes.byref(request), buffer, len(buffer), 0) != 0:
        return None
    length = struct.unpack_from("I", buffer, 0)[0]
    if length < 4 + 32:
        return None
    values = struct.unpack_from("8I", buffer, 4)
    capabilities = values[_CAP_INTERFACES]
    valid = values[4 + _CAP_INTERFACES]
    return VolumeCapabilities(
        rename_excl=bool(capabilities & VOL_CAP_INT_RENAME_EXCL),
        rename_excl_valid=bool(valid & VOL_CAP_INT_RENAME_EXCL),
        raw_capabilities=capabilities,
        raw_valid=valid,
        raw_format=values[0],
        raw_format_valid=values[4],
    )


def proven_primitive(dir_fd: int) -> Primitive:
    """O backend de rename exclusivo comprovado para o volume do diretório.

    ``NoReplaceUnavailable`` se não houver. Só lê: nenhum efeito.
    """
    platform = _platform()
    device = os.fstat(dir_fd).st_dev
    with _lock:
        if device in _unsupported_devices:
            raise NoReplaceUnavailable("volume marcado sem rename exclusivo nesta execução")
    libc = _library()
    if platform == "darwin":
        if getattr(libc, "renameatx_np", None) is None:
            raise NoReplaceUnavailable("renameatx_np indisponível neste sistema")
        caps = volume_capabilities(dir_fd)
        if caps is None or not (caps.rename_excl and caps.rename_excl_valid):
            raise NoReplaceUnavailable("o volume não anuncia rename exclusivo (RENAME_EXCL)")
        return Primitive.RENAMEATX_NP
    if platform.startswith("linux"):
        if getattr(libc, "renameat2", None) is None:
            raise NoReplaceUnavailable("renameat2 indisponível neste sistema")
        return Primitive.RENAMEAT2
    raise NoReplaceUnavailable("sistema sem backend de rename exclusivo")


def _call_rename(
    primitive: Primitive, src_dir_fd: int, src_name: bytes, dst_dir_fd: int, dst_name: bytes
) -> int:
    """A chamada nativa; devolve 0 ou -1 com o errno em ``ctypes.get_errno()``.

    Cada backend é chamado direto com a sua flag constante (o teste estático exige isso).
    """
    libc = _library()
    if primitive is Primitive.RENAMEATX_NP:
        libc.renameatx_np.argtypes = _RENAME_ARGTYPES
        libc.renameatx_np.restype = ctypes.c_int
        ctypes.set_errno(0)
        return int(libc.renameatx_np(src_dir_fd, src_name, dst_dir_fd, dst_name, _RENAME_EXCL))
    libc.renameat2.argtypes = _RENAME_ARGTYPES
    libc.renameat2.restype = ctypes.c_int
    ctypes.set_errno(0)
    return int(libc.renameat2(src_dir_fd, src_name, dst_dir_fd, dst_name, _RENAME_NOREPLACE))


def rename_noreplace(
    src_dir_fd: int,
    src_name: str,
    dst_dir_fd: int,
    dst_name: str,
    *,
    primitive: Primitive | None = None,
) -> RenameOutcome:
    """Renomeia ``src_name`` para ``dst_name`` sem nunca substituir um destino existente.

    ``renamed=False`` quando o destino já existe. ``CrossDevice`` (``EXDEV``), ``NotFound``
    (``ENOENT``), ``NameTooLong`` (``ENAMETOOLONG``) e ``NoReplaceUnavailable`` (o volume não
    suporta; nada foi movido) são erros de domínio; qualquer outro ``OSError`` sobe sem
    nova tentativa.

    ``primitive`` é o backend já comprovado por ``proven_primitive`` para o volume de
    ``dst_dir_fd`` na mesma operação (o laço de sufixos consulta a capability uma vez); sem
    ele, a capability é consultada aqui.
    """
    check_component(src_name)
    check_component(dst_name)
    if primitive is None:
        primitive = proven_primitive(dst_dir_fd)
    result = _call_rename(
        primitive, src_dir_fd, os.fsencode(src_name), dst_dir_fd, os.fsencode(dst_name)
    )
    if result == 0:
        return RenameOutcome(renamed=True, primitive=primitive)
    code = ctypes.get_errno()
    if code == errno.EEXIST:
        return RenameOutcome(renamed=False, primitive=primitive)
    if code == errno.EXDEV:
        raise CrossDevice("origem e destino em volumes diferentes")
    if code == errno.ENOENT:
        raise NotFound("origem ou pasta de destino não existe")
    if code == errno.ENAMETOOLONG:
        raise NameTooLong("nome excede o limite do share")
    if code in {errno.EINVAL, errno.ENOTSUP, errno.ENOSYS}:
        if code != errno.EINVAL:
            with _lock:
                _unsupported_devices.add(os.fstat(dst_dir_fd).st_dev)
        raise NoReplaceUnavailable("o volume recusou o rename exclusivo; nada foi movido")
    raise OSError(code, os.strerror(code))
