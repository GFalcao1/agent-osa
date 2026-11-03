"""O scan sem banco nem modelo: caminhada da raiz, cursor, colocação pelo caminho e CLI.

A caminhada roda numa raiz em ``tmp_path`` (``osa.fs`` é POSIX, como o resto da suite
portável); o scan completo com índice real fica em ``tests/integration/test_scan.py``.
"""

from __future__ import annotations

import os
import unicodedata
from pathlib import Path

import pytest
from typer.testing import CliRunner

from osa.cli import app
from osa.courier.decision import TemplateDepartment
from osa.domain.errors import UnsafePath
from osa.fs.names import QUARANTINE_DIR, component_key
from osa.fs.walk import FileEntry, SkippedDir, comes_after, iter_files, split_cursor
from osa.worker.scan import (
    MOTIVO_FORA_DO_PADRAO,
    Placement,
    ScanSummary,
    final_state,
    place,
    same_path,
    year_component,
)

runner = CliRunner()


def put(path: Path, content: bytes = b"ficticio") -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content)
    return path


@pytest.fixture
def root(tmp_path: Path) -> Path:
    base = tmp_path.resolve() / "raiz"
    base.mkdir()
    put(base / "Alfa" / "01 Fiscal" / "2025" / "nota.pdf")
    put(base / "Alfa" / "01 Fiscal" / "2025" / "boleto.pdf")
    put(base / "Alfa" / "02 Pessoal" / "folha.xlsx")
    put(base / "Beta" / "01 Fiscal" / "guia.pdf")
    put(base / "solto.pdf")
    put(base / QUARANTINE_DIR / "abc.pdf")
    put(base / "Alfa" / ".DS_Store")
    put(base / "Alfa" / "._nota.pdf")
    put(base / ".osa-probe-1234")
    return base


def files(root: Path, after: str | None = None) -> list[str]:
    return [v.relative for v in iter_files(root, after=after) if isinstance(v, FileEntry)]


# --- caminhada --------------------------------------------------------------------------------


def test_walk_lists_regular_files_in_a_deterministic_order_and_skips_dot_names(root: Path) -> None:
    assert files(root) == [
        "Alfa/01 Fiscal/2025/boleto.pdf",
        "Alfa/01 Fiscal/2025/nota.pdf",
        "Alfa/02 Pessoal/folha.xlsx",
        "Beta/01 Fiscal/guia.pdf",
        "solto.pdf",
    ]


def test_walk_order_matches_tuple_order_of_the_parts(root: Path) -> None:
    listed = [tuple(rel.split("/")) for rel in files(root)]
    assert listed == sorted(listed)


def test_walk_resumes_after_the_cursor_without_repeating(root: Path) -> None:
    cursor = "Alfa/01 Fiscal/2025/nota.pdf"
    assert files(root, after=cursor) == [
        "Alfa/02 Pessoal/folha.xlsx",
        "Beta/01 Fiscal/guia.pdf",
        "solto.pdf",
    ]
    assert files(root, after="solto.pdf") == []
    assert files(root, after="Alfa/01 Fiscal/2025/a.pdf")[0] == "Alfa/01 Fiscal/2025/boleto.pdf"


def test_walk_skips_a_whole_folder_already_done_without_listing_it(
    root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    listed: list[int] = []
    real = os.listdir

    def spy(fd: int | str | bytes | os.PathLike[str] | None = None) -> list[str]:
        listed.append(1)
        return real(fd)  # type: ignore[arg-type]

    monkeypatch.setattr(os, "listdir", spy)
    files(root, after="Alfa/02 Pessoal/folha.xlsx")
    # raiz, Alfa, Alfa/02 Pessoal (a pasta do cursor), Beta, Beta/01 Fiscal: nunca Alfa/01 Fiscal
    assert len(listed) == 5


def test_walk_never_follows_links_and_ignores_them(root: Path, tmp_path: Path) -> None:
    outside = tmp_path / "fora"
    outside.mkdir()
    put(outside / "segredo.pdf")
    os.symlink(outside, root / "Alfa" / "link-pasta")
    os.symlink(outside / "segredo.pdf", root / "Alfa" / "link.pdf")

    assert all("link" not in rel for rel in files(root))


def test_walk_refuses_a_root_that_is_a_link(root: Path, tmp_path: Path) -> None:
    alias = tmp_path / "apelido"
    os.symlink(root, alias)
    with pytest.raises(UnsafePath):
        list(iter_files(alias))


def test_walk_reports_an_unreadable_folder_and_continues(root: Path) -> None:
    if os.geteuid() == 0:
        pytest.skip("root lê qualquer pasta")
    locked = root / "Alfa" / "02 Pessoal"
    os.chmod(locked, 0o000)
    try:
        visits = list(iter_files(root))
    finally:
        os.chmod(locked, 0o755)
    skipped = [v for v in visits if isinstance(v, SkippedDir)]
    assert [v.parts for v in skipped] == [("Alfa", "02 Pessoal")]
    assert "Beta/01 Fiscal/guia.pdf" in [v.relative for v in visits if isinstance(v, FileEntry)]


def test_walk_entry_carries_size_and_name(root: Path) -> None:
    entry = next(v for v in iter_files(root) if isinstance(v, FileEntry))
    assert entry.name == "boleto.pdf" and entry.size == len(b"ficticio") and entry.mtime > 0


def test_walk_keeps_the_spelling_on_disk_for_decomposed_names(tmp_path: Path) -> None:
    root = tmp_path.resolve() / "raiz"
    root.mkdir()
    nfd = unicodedata.normalize("NFD", "Conteúdo")
    put(root / nfd / "Fiscal" / "nota.pdf")
    [rel] = files(root)
    assert rel.split("/")[0] == nfd
    assert component_key(rel.split("/")[0]) == component_key("conteúdo")


@pytest.mark.parametrize(
    ("parts", "cursor", "after"),
    [
        (("b",), ("a",), True),
        (("a",), ("a",), False),
        (("a", "b"), ("a",), True),
        (("a",), ("a", "b"), False),
        (("Alfa", "Fiscal.pdf"), ("Alfa", "Fiscal", "x.pdf"), True),
    ],
)
def test_comes_after(parts: tuple[str, ...], cursor: tuple[str, ...], after: bool) -> None:
    assert comes_after(parts, cursor) is after


def test_split_cursor() -> None:
    assert split_cursor(None) == ()
    assert split_cursor("") == ()
    assert split_cursor("a/b/c.pdf") == ("a", "b", "c.pdf")


# --- colocação pelo caminho -------------------------------------------------------------------


class _Owner:
    def __init__(self, owner_id: int, pasta: str) -> None:
        self.id = owner_id
        self.pasta = pasta


FISCAL = TemplateDepartment(1, "Fiscal", "01 Fiscal", True)
PESSOAL = TemplateDepartment(2, "Pessoal", "02 Pessoal", False)
ALFA = _Owner(10, "Alfa Comércio")
OWNERS = {component_key(ALFA.pasta): ALFA}
DEPARTMENTS = {component_key(d.pasta): d for d in (FISCAL, PESSOAL)}


def _place(rel: str) -> Placement:
    return place(rel.split("/"), OWNERS, DEPARTMENTS)  # type: ignore[arg-type]


def test_place_reads_owner_department_and_year_from_the_path() -> None:
    placed = _place("Alfa Comércio/01 Fiscal/2025/nota.pdf")
    assert placed.in_pattern
    assert (placed.owner, placed.department, placed.year) == (ALFA, FISCAL, 2025)


def test_place_ignores_case_and_unicode_form_like_the_share() -> None:
    nfd = unicodedata.normalize("NFD", "alfa comércio")
    placed = _place(f"{nfd}/01 FISCAL/nota.pdf")
    assert placed.in_pattern and placed.owner is ALFA and placed.year is None


def test_place_allows_subfolders_under_the_department() -> None:
    placed = _place("Alfa Comércio/01 Fiscal/2025/Janeiro/nota.pdf")
    assert placed.in_pattern and placed.year == 2025
    deeper = _place("Alfa Comércio/02 Pessoal/2025/nota.pdf")
    assert deeper.in_pattern and deeper.year is None  # Pessoal não usa ano


@pytest.mark.parametrize(
    "rel",
    [
        "solto.pdf",
        "Alfa Comércio/nota.pdf",
        "Alfa Comércio/03 Outro/nota.pdf",
        "Gama/01 Fiscal/nota.pdf",
        "01 Fiscal/Alfa Comércio/nota.pdf",
    ],
)
def test_place_outside_the_pattern_has_no_owner_and_no_department(rel: str) -> None:
    placed = _place(rel)
    assert not placed.in_pattern
    assert (placed.owner, placed.department, placed.year) == (None, None, None)


@pytest.mark.parametrize(
    ("part", "year"),
    [("2025", 2025), ("1900", 1900), ("2100", 2100), ("0001", None), ("2025 antigo", None),
     ("25", None), ("20251", None)],
)  # fmt: skip
def test_year_component(part: str, year: int | None) -> None:
    assert year_component(part) == year


def test_final_state_follows_the_pattern() -> None:
    assert final_state(_place("Alfa Comércio/01 Fiscal/nota.pdf")) == ("ARQUIVADO", None)
    assert final_state(_place("solto.pdf")) == ("NA_ORIGEM", MOTIVO_FORA_DO_PADRAO)


@pytest.mark.parametrize(
    ("stored", "listed", "same"),
    [
        ("Alfa/01 Fiscal/nota.pdf", "Alfa/01 Fiscal/nota.pdf", True),
        ("Alfa/01 Fiscal/nota.pdf", "alfa/01 fiscal/NOTA.PDF", True),
        ("Conteúdo/01 Fiscal/nota.pdf", unicodedata.normalize("NFD", "Conteúdo/nota.pdf"), False),
        ("Conteúdo/nota.pdf", unicodedata.normalize("NFD", "Conteúdo/nota.pdf"), True),
        ("Alfa/01 Fiscal/nota.pdf", "Alfa/01 Fiscal/2025/nota.pdf", False),
        ("Alfa/01 Fiscal/nota.pdf", "Alfa/01 Fiscal/nota (2).pdf", False),
        (None, "Alfa/01 Fiscal/nota.pdf", False),
    ],
)  # fmt: skip
def test_same_path_compares_like_the_share(stored: str | None, listed: str, same: bool) -> None:
    assert same_path(stored, listed) is same


def test_summary_details_never_include_the_root_id() -> None:
    summary = ScanSummary(root_id=3, novos=2)
    details = summary.as_details()
    assert "root_id" not in details and details["novos"] == 2 and details["concluido"] is False


# --- CLI sem banco ----------------------------------------------------------------------------


def test_scan_without_database_fails_in_one_line() -> None:
    result = runner.invoke(app, ["scan"])

    assert result.exit_code == 1
    assert "OSA_DATABASE_URL" in result.output and "Traceback" not in result.output


def test_scan_refuses_a_bad_date_before_touching_anything() -> None:
    result = runner.invoke(app, ["scan", "--desde", "ontem"])

    assert result.exit_code == 1
    assert "AAAA-MM-DD" in result.output


def test_search_without_database_fails_in_one_line() -> None:
    result = runner.invoke(app, ["search", "contrato alfa"])

    assert result.exit_code == 1
    assert "OSA_DATABASE_URL" in result.output and "Traceback" not in result.output


def test_search_refuses_an_inverted_period() -> None:
    result = runner.invoke(app, ["search", "x", "--de", "2026-02-01", "--ate", "2026-01-01"])

    assert result.exit_code == 1
    assert "periodo" in result.output and "Traceback" not in result.output
