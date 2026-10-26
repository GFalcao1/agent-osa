"""Nomes: NFC, limite em unidades UTF-16, regras do Windows, extensões e sufixos."""

from __future__ import annotations

import unicodedata

import pytest

from osa.domain.errors import UnsafePath
from osa.fs.errors import NameTooLong
from osa.fs.names import (
    MAX_NAME_UNITS,
    component_key,
    name_units,
    nfc,
    split_name,
    suffixed,
    validate_filename,
    validate_folder_component,
)


def test_default_limit_is_255_utf16_units() -> None:
    assert MAX_NAME_UNITS == 255


def test_units_count_utf16_not_bytes_or_characters() -> None:
    assert name_units("a" * 255) == 255
    assert name_units("á" * 200) == 200  # 400 bytes em UTF-8, 200 unidades
    assert name_units(unicodedata.normalize("NFD", "á" * 128)) == 128  # NFD vira NFC
    assert name_units("\U0001f600") == 2  # emoji: par substituto


def test_component_key_ignores_case_and_unicode_form() -> None:
    nfd = unicodedata.normalize("NFD", "Ação.PDF")
    assert component_key(nfd) == component_key("ação.pdf")
    assert nfc(nfd) == "Ação.PDF"


@pytest.mark.parametrize(
    "name",
    ["contrato.pdf", "Relatório 2025 (final).xlsx", ".bashrc", "Makefile", "a b.c.d", "x" * 255],
)
def test_validate_filename_accepts_safe_names(name: str) -> None:
    assert validate_filename(name) == name


def test_validate_filename_normalizes_to_nfc() -> None:
    nfd = unicodedata.normalize("NFD", "ação.pdf")
    assert nfd != "ação.pdf"
    assert validate_filename(nfd) == "ação.pdf"


@pytest.mark.parametrize(
    "name",
    [
        "",
        " ",
        ".",
        "..",
        "a/b",
        "a\\b",
        "a\0b",
        "a\x1fb",
        "a\x7fb",
        "a:b",
        "a<b",
        "a>b",
        'a"b',
        "a|b",
        "a?b",
        "a*b",
        "x.",
        "x ",
        "con",
        "CON.txt",
        "nul.tar.gz",
        "aux",
        "prn.pdf",
        "com1",
        "LPT9.doc",
        "COM0",
        "lpt0.txt",
        "COM¹.txt",
        "com²",
        "COM³.pdf",
        "LPT¹",
        "lpt².doc",
        "LPT³.tar.gz",
        "x‮.pdf",  # inversão de texto (categoria Cf)
    ],
)
def test_validate_filename_refuses_unsafe_or_windows_invalid_names(name: str) -> None:
    with pytest.raises(UnsafePath):
        validate_filename(name)


def test_validate_filename_refuses_names_over_the_limit() -> None:
    assert validate_filename("a" * 255)
    with pytest.raises(NameTooLong):
        validate_filename("a" * 256)
    with pytest.raises(NameTooLong):
        validate_filename("\U0001f600" * 128)  # 256 unidades
    assert validate_filename("\U0001f600" * 127)
    with pytest.raises(NameTooLong):
        validate_filename("a" * 21, max_units=20)  # limite medido menor, parametrizado


def test_name_too_long_is_an_unsafe_path() -> None:
    assert issubclass(NameTooLong, UnsafePath)


def test_validate_folder_component_refuses_leading_dot() -> None:
    assert validate_folder_component("Fiscal 2026") == "Fiscal 2026"
    for name in (".osa-quarentena", ".oculta", "a/b"):
        with pytest.raises(UnsafePath):
            validate_folder_component(name)


@pytest.mark.parametrize(
    ("name", "expected"),
    [
        ("contrato.pdf", ("contrato", ".pdf")),
        ("contrato.pdf.p7s", ("contrato", ".pdf.p7s")),
        ("Contrato.PDF.P7S", ("Contrato", ".PDF.P7S")),
        ("nota.xml.p7s", ("nota", ".xml.p7s")),
        ("backup.tar.gz", ("backup", ".tar.gz")),
        ("backup.tar.bz2", ("backup", ".tar.bz2")),
        ("backup.tar.xz", ("backup", ".tar.xz")),
        ("backup.tar.zst", ("backup", ".tar.zst")),
        ("a.b.c.pdf", ("a.b.c", ".pdf")),
        ("Makefile", ("Makefile", "")),
        (".bashrc", (".bashrc", "")),
        ("termina.", ("termina.", "")),
        ("a.docx.p7s", ("a.docx", ".p7s")),  # fora da lista: regra geral
        (".tar.gz", (".tar", ".gz")),  # sem stem para a composta
        ("nota. final", ("nota. final", "")),  # "extensão" com espaço não é extensão
    ],
)
def test_split_name(name: str, expected: tuple[str, str]) -> None:
    assert split_name(name) == expected


@pytest.mark.parametrize(
    ("name", "n", "expected"),
    [
        ("contrato.pdf", 1, "contrato.pdf"),
        ("contrato.pdf", 2, "contrato (2).pdf"),
        ("contrato.pdf", 10, "contrato (10).pdf"),
        ("contrato.pdf.p7s", 2, "contrato (2).pdf.p7s"),
        ("x.xml.p7s", 3, "x (3).xml.p7s"),
        ("backup.tar.gz", 2, "backup (2).tar.gz"),
        ("Makefile", 2, "Makefile (2)"),
        (".bashrc", 2, ".bashrc (2)"),
        ("a.b.pdf", 2, "a.b (2).pdf"),
    ],
)
def test_suffixed(name: str, n: int, expected: str) -> None:
    assert suffixed(name, n) == expected


def test_suffixed_never_truncates() -> None:
    name = "a" * 251 + ".pdf"  # 255 unidades
    assert suffixed(name, 1) == name
    with pytest.raises(NameTooLong) as error:
        suffixed(name, 2)
    assert error.value.suffix == 2
    assert suffixed("a" * 247 + ".pdf", 2) == "a" * 247 + " (2).pdf"  # cabe exatamente


def test_suffixed_rejects_n_below_one_and_invalid_names() -> None:
    with pytest.raises(ValueError):
        suffixed("a.pdf", 0)
    with pytest.raises(UnsafePath):
        suffixed("a:b.pdf", 2)
