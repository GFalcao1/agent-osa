"""Nomes de arquivo e de pasta: NFC, limite em unidades UTF-16, regras do Windows e sufixos."""

from __future__ import annotations

import re
import unicodedata
from typing import Final

from osa.domain.errors import UnsafePath
from osa.fs.errors import NameTooLong

#: Unidades UTF-16 do nome em NFC (limite do NTFS e do APFS). A suite native confirma no share;
#: se o share medir menos, o valor entra como parâmetro ``max_name_units`` das operações.
MAX_NAME_UNITS: Final = 255
#: Pasta da quarentena dentro de cada raiz; nenhum nome de pasta válido começa com ponto.
QUARANTINE_DIR: Final = ".osa-quarentena"
#: Maior ``n`` tentado em `` (n)`` antes de ``CollisionExhausted``.
MAX_SUFFIX: Final = 9999
#: Extensões compostas (casefold); só o que está aqui vale como composta.
COMPOSITE_EXTENSIONS: Final = (
    ".tar.gz",
    ".tar.bz2",
    ".tar.xz",
    ".tar.zst",
    ".tar.lz",
    ".tar.z",
    ".pdf.p7s",
    ".xml.p7s",
)

_FORBIDDEN_CHARS: Final = frozenset('<>:"|?*/\\')
#: Dispositivos do Windows: ``COM0``-``COM9`` e ``LPT0``-``LPT9`` e os sobrescritos ``¹²³``.
_DEVICE_DIGITS: Final = "0123456789\u00b9\u00b2\u00b3"
_RESERVED_WINDOWS: Final = frozenset(
    {"con", "prn", "aux", "nul"}
    | {f"com{d}" for d in _DEVICE_DIGITS}
    | {f"lpt{d}" for d in _DEVICE_DIGITS}
)
_SIMPLE_EXTENSION: Final = re.compile(r"\.[^\s./\\]{1,16}")


def nfc(value: str) -> str:
    """O texto em NFC: toda forma que vai ao disco é NFC."""
    return unicodedata.normalize("NFC", value)


def component_key(name: str) -> str:
    """Chave de colisão de um nome: SMB e APFS ignoram caixa e forma Unicode."""
    return nfc(nfc(name).casefold())


def name_units(name: str) -> int:
    """Unidades UTF-16 do nome em NFC (emoji conta 2)."""
    return len(nfc(name).encode("utf-16-le", "surrogatepass")) // 2


def _is_forbidden(char: str) -> bool:
    return char in _FORBIDDEN_CHARS or unicodedata.category(char).startswith("C")


def _validate_component(name: str, *, max_units: int, what: str) -> str:
    value = nfc(name)
    if not value or not value.strip():
        raise UnsafePath(f"{what} vazio")
    if value in {".", ".."}:
        raise UnsafePath(f"{what} inválido")
    if any(_is_forbidden(char) for char in value):
        raise UnsafePath(f"{what} contém caractere proibido")
    if value.endswith((".", " ")):
        raise UnsafePath(f"{what} não pode terminar com ponto ou espaço")
    if value.split(".", 1)[0].strip().casefold() in _RESERVED_WINDOWS:
        raise UnsafePath(f"{what} é reservado")
    if name_units(value) > max_units:
        raise NameTooLong(f"{what} excede {max_units} unidades UTF-16")
    return value


def validate_filename(name: str, *, max_units: int = MAX_NAME_UNITS) -> str:
    """O nome em NFC se for um componente seguro; ``UnsafePath`` ou ``NameTooLong`` senão."""
    return _validate_component(name, max_units=max_units, what="nome de arquivo")


def validate_folder_component(name: str, *, max_units: int = MAX_NAME_UNITS) -> str:
    """Como ``validate_filename`` e também recusa início com ponto.

    Assim nenhum dono ou departamento colide com ``.osa-quarentena``.
    """
    value = _validate_component(name, max_units=max_units, what="nome de pasta")
    if value.startswith("."):
        raise UnsafePath("nome de pasta não pode começar com ponto")
    return value


def split_name(name: str) -> tuple[str, str]:
    """``(stem, extensão)``; a extensão composta da lista vale inteira.

    ``contrato.pdf.p7s`` -> (``contrato``, ``.pdf.p7s``); ``.bashrc`` e ``Makefile`` não têm
    extensão; ``a.b.c.pdf`` -> (``a.b.c``, ``.pdf``).
    """
    for extension in COMPOSITE_EXTENSIONS:
        size = len(extension)
        if len(name) > size and name[-size:].casefold() == extension:
            return name[:-size], name[-size:]
    index = name.rfind(".")
    if index <= 0 or index == len(name) - 1:
        return name, ""
    extension = name[index:]
    if not _SIMPLE_EXTENSION.fullmatch(extension):
        return name, ""
    return name[:index], extension


def suffixed(name: str, n: int, *, max_units: int = MAX_NAME_UNITS) -> str:
    """O nome da tentativa ``n``: ``n == 1`` é o próprio nome; ``n >= 2`` vira `` (n)``.

    Nunca trunca: se o resultado passa do limite, ``NameTooLong`` (com ``suffix=n``).
    """
    if n < 1:
        raise ValueError("n começa em 1")
    value = validate_filename(name, max_units=max_units)
    if n == 1:
        return value
    stem, extension = split_name(value)
    candidate = f"{stem} ({n}){extension}"
    if name_units(candidate) > max_units:
        raise NameTooLong(f"nome com sufixo ({n}) excede {max_units} unidades UTF-16", suffix=n)
    return candidate
