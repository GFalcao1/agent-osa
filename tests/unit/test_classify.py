from __future__ import annotations

import builtins
import io
import os
import subprocess
import zipfile
from pathlib import Path

import pytest

from osa.domain.document import FileClass
from osa.domain.errors import ExtractionFailed, SensitiveFile, Unsupported
from osa.extract import ExtractLimits, extract, file_class
from osa.extract.classify import class_by_name, normalized_extension

SENSITIVE = (".pfx", ".p12", ".pem", ".key", ".cer", ".crt")

# Names that must be sensitive however they are spelled.
SENSITIVE_NAMES = [
    *(f"certificado{ext}" for ext in SENSITIVE),
    "CERTIFICADO.PFX",
    "chave.Key",
    "a.pfx.",
    "A.PFX ",
    "a.pfx. .",
    "  a.pfx",
    "a.pfx\t",
    ".key",
    ".pfx",
    "a.ＰＦＸ",  # full-width letters fold to .pfx
    "pasta com espaço/ cert.p12",
]


def forbid_io(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    """Make every way of opening, reading, statting or spawning fail the test."""
    calls: list[str] = []

    def forbidden(name: str):  # type: ignore[no-untyped-def]
        def refuse(*args: object, **kwargs: object) -> None:
            calls.append(name)
            raise AssertionError(f"{name} must not be called for this file")

        return refuse

    monkeypatch.setattr(builtins, "open", forbidden("builtins.open"))
    monkeypatch.setattr(io, "open", forbidden("io.open"))
    monkeypatch.setattr(os, "open", forbidden("os.open"))
    monkeypatch.setattr(os, "stat", forbidden("os.stat"))
    monkeypatch.setattr(os, "lstat", forbidden("os.lstat"))
    monkeypatch.setattr(os, "scandir", forbidden("os.scandir"))
    monkeypatch.setattr(Path, "open", forbidden("Path.open"))
    monkeypatch.setattr(Path, "read_bytes", forbidden("Path.read_bytes"))
    monkeypatch.setattr(Path, "read_text", forbidden("Path.read_text"))
    monkeypatch.setattr(subprocess, "Popen", forbidden("subprocess.Popen"))
    return calls


@pytest.mark.parametrize("name", SENSITIVE_NAMES)
def test_sensitive_is_decided_without_reading(name: str, monkeypatch: pytest.MonkeyPatch) -> None:
    calls = forbid_io(monkeypatch)
    path = Path("/fictional/inbox") / name  # does not exist: nothing may look at it

    assert file_class(path) is FileClass.SENSITIVE
    assert class_by_name(path.name, SENSITIVE) is FileClass.SENSITIVE
    with pytest.raises(SensitiveFile):
        extract(path)

    assert calls == []


def test_sensitive_file_with_content_is_never_opened(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    key = tmp_path / "A.PFX "
    key.write_bytes(b"fictional key material")
    calls = forbid_io(monkeypatch)

    with pytest.raises(SensitiveFile):
        extract(key)
    assert file_class(key) is FileClass.SENSITIVE

    assert calls == []


@pytest.mark.parametrize("name", ["arquivo.xyz", "sem_extensao", "dados.txt", "a.pfx.bak", "x."])
def test_unsupported_is_decided_without_reading(name: str, monkeypatch: pytest.MonkeyPatch) -> None:
    calls = forbid_io(monkeypatch)
    path = Path("/fictional/inbox") / name

    assert file_class(path) is FileClass.UNSUPPORTED
    with pytest.raises(Unsupported):
        extract(path)

    assert calls == []


def test_sensitive_extensions_come_from_the_limits(monkeypatch: pytest.MonkeyPatch) -> None:
    limits = ExtractLimits(sensitive_extensions=(".p7b",))
    calls = forbid_io(monkeypatch)

    assert file_class("/fictional/a.p7b", limits) is FileClass.SENSITIVE
    with pytest.raises(SensitiveFile):
        extract("/fictional/a.P7B.", limits)

    assert calls == []


@pytest.mark.parametrize(
    ("name", "expected"),
    [
        ("a.PDF", ".pdf"),
        ("a.tar.gz", ".gz"),
        ("a.pfx.", ".pfx"),
        ("a.pfx ", ".pfx"),
        (".key", ".key"),
        ("semponto", ""),
        ("termina.", ""),
        ("", ""),
        ("a.ＰＦＸ", ".pfx"),
    ],
)
def test_normalized_extension(name: str, expected: str) -> None:
    assert normalized_extension(name) == expected


def test_each_class_of_spec_section_7_is_covered_by_a_fixture(docs: dict[str, Path]) -> None:
    expected = {
        "texto.pdf": FileClass.PDF,
        "escaneado.pdf": FileClass.PDF,
        "misto.pdf": FileClass.PDF,
        "contrato.docx": FileClass.OFFICE,
        "planilha.xlsx": FileClass.OFFICE,
        "imagem.png": FileClass.IMAGE,
        "imagem.jpg": FileClass.IMAGE,
        "imagem.heic": FileClass.IMAGE,
        "certificado.pfx": FileClass.SENSITIVE,
        "arquivo.xyz": FileClass.UNSUPPORTED,
    }
    for name, cls in expected.items():
        assert file_class(docs[name]) is cls, name
        assert file_class(str(docs[name])) is cls, name
    assert set(expected.values()) == set(FileClass)


@pytest.mark.parametrize(
    ("name", "content"),
    [
        ("falso.pdf", bytes.fromhex("89504e470d0a1a0a") + b"0" * 32),
        ("falso.png", b"%PDF-1.7\n" + b"0" * 32),
        ("falso.jpg", b"texto simples"),
        ("falso.heic", b"\x00\x00\x00\x18ftypmp42" + b"0" * 32),
        ("falso.docx", b"PK\x03\x04 lixo"),
        ("falso.xlsx", b""),
    ],
)
def test_content_that_contradicts_the_extension_is_unsupported(
    tmp_path: Path, name: str, content: bytes
) -> None:
    path = tmp_path / name
    path.write_bytes(content)

    assert file_class(path) is FileClass.UNSUPPORTED
    with pytest.raises(Unsupported):
        extract(path)


def test_any_image_format_is_accepted_under_any_image_extension(
    tmp_path: Path, docs: dict[str, Path]
) -> None:
    renamed = tmp_path / "foto.png"
    renamed.write_bytes(docs["imagem.jpg"].read_bytes())

    assert file_class(renamed) is FileClass.IMAGE


def test_office_zip_must_have_the_right_main_part(tmp_path: Path) -> None:
    other = tmp_path / "outro.docx"
    with zipfile.ZipFile(other, "w") as archive:
        archive.writestr("xl/workbook.xml", "<workbook/>")
    swapped = tmp_path / "trocado.xlsx"
    with zipfile.ZipFile(swapped, "w") as archive:
        archive.writestr("word/document.xml", "<document/>")

    assert file_class(other) is FileClass.UNSUPPORTED
    assert file_class(swapped) is FileClass.UNSUPPORTED


def test_ole_container_under_an_office_extension_fails_with_a_clear_error(tmp_path: Path) -> None:
    protected = tmp_path / "protegido.docx"
    protected.write_bytes(bytes.fromhex("d0cf11e0a1b11ae1") + b"0" * 512)

    assert file_class(protected) is FileClass.OFFICE
    with pytest.raises(ExtractionFailed, match="senha"):
        extract(protected)


def test_zip_with_too_many_entries_is_refused_before_it_is_parsed(tmp_path: Path) -> None:
    many = tmp_path / "muitas.docx"
    with zipfile.ZipFile(many, "w") as archive:
        archive.writestr("word/document.xml", "<document/>")
        for index in range(120):
            archive.writestr(f"extra/{index}.bin", b"x")

    with pytest.raises(ExtractionFailed, match="entradas"):
        file_class(many, ExtractLimits(max_zip_entries=100))


def test_unreadable_supported_file_is_a_failure_not_a_class(tmp_path: Path) -> None:
    with pytest.raises(ExtractionFailed):
        file_class(tmp_path / "sumiu.pdf")
    folder = tmp_path / "pasta.png"
    folder.mkdir()
    with pytest.raises(ExtractionFailed):
        file_class(folder)
