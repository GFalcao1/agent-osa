"""Arquivar e desfazer: colisão, limites de nome, mesma raiz, identidade, efeitos desligados."""

from __future__ import annotations

import hashlib
import os
import types
import unicodedata
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

from osa.domain.errors import Conflict, NotFound, UnsafePath
from osa.fs import archive as archive_module
from osa.fs import names, noreplace
from osa.fs.archive import ArchiveRecord, ArchiveResult, archive, undo
from osa.fs.errors import (
    CollisionExhausted,
    CrossDevice,
    CrossRoot,
    DestinationMissing,
    IdentityChanged,
    NameTooLong,
    NoReplaceUnavailable,
)
from osa.fs.names import QUARANTINE_DIR
from osa.fs.noreplace import Primitive

Write = Callable[..., Path]
Snapshot = Callable[[Path], dict[str, tuple[str, int, int, int]]]

pytestmark = pytest.mark.usefixtures("require_noreplace")


def sha(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


@pytest.fixture
def tree(root: Path) -> Path:
    """``raiz/Alfa/Fiscal`` (destino do template) e ``raiz/.osa-quarentena`` (origem)."""
    (root / "Alfa" / "Fiscal").mkdir(parents=True)
    (root / QUARANTINE_DIR).mkdir()
    return root


def put(root: Path, rel: str, content: bytes = b"novo") -> Path:
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content)
    return path


# --- caminho feliz ---------------------------------------------------------------------------


def test_archive_moves_by_rename_and_keeps_the_name(tree: Path) -> None:
    put(tree, "Alfa/Fiscal/outro.pdf", b"ja existia")
    put(tree, f"{QUARANTINE_DIR}/u1", b"conteudo")
    result = archive(tree, f"{QUARANTINE_DIR}/u1", "Alfa/Fiscal", "nota.pdf", effects_enabled=True)
    assert isinstance(result, ArchiveResult)
    assert (result.simulated, result.suffix, result.final_name) == (False, 1, "nota.pdf")
    assert result.source_rel == f"{QUARANTINE_DIR}/u1"
    assert result.dest_rel == "Alfa/Fiscal/nota.pdf"
    assert result.path == tree / "Alfa" / "Fiscal" / "nota.pdf"
    assert result.size == len(b"conteudo")
    assert result.primitive in set(Primitive)
    assert (tree / "Alfa" / "Fiscal" / "nota.pdf").read_bytes() == b"conteudo"
    assert not (tree / QUARANTINE_DIR / "u1").exists()


def test_archive_accepts_absolute_paths_inside_the_root(tree: Path) -> None:
    src = put(tree, "Alfa/entrada.pdf", b"a")
    result = archive(tree, src, tree / "Alfa" / "Fiscal", effects_enabled=True)
    assert result.dest_rel == "Alfa/Fiscal/entrada.pdf"


def test_name_defaults_to_the_source_name_and_is_nfc(tree: Path) -> None:
    nfd = unicodedata.normalize("NFD", "ação.pdf")
    put(tree, f"Alfa/{nfd}", b"a")
    result = archive(tree, f"Alfa/{nfd}", "Alfa/Fiscal", effects_enabled=True)
    assert result.final_name == "ação.pdf"
    assert unicodedata.normalize("NFC", result.path.name) == "ação.pdf"


def test_destination_folder_in_other_case_is_reused_in_recorded_spelling(tree: Path) -> None:
    put(tree, f"{QUARANTINE_DIR}/u1", b"a")
    result = archive(tree, f"{QUARANTINE_DIR}/u1", "ALFA/fiscal", "a.pdf", effects_enabled=True)
    assert result.dest_rel == "Alfa/Fiscal/a.pdf"
    assert [p.name for p in tree.iterdir() if p.name.lower() == "alfa"] == ["Alfa"]


# --- colisão ---------------------------------------------------------------------------------


def test_collision_gets_suffix_two_then_three_and_never_replaces(tree: Path) -> None:
    original = put(tree, "Alfa/Fiscal/nota.pdf", b"original")
    results = []
    for index in range(3):
        put(tree, f"{QUARANTINE_DIR}/u{index}", f"novo{index}".encode())
        results.append(
            archive(
                tree, f"{QUARANTINE_DIR}/u{index}", "Alfa/Fiscal", "nota.pdf", effects_enabled=True
            )
        )
    assert [r.final_name for r in results] == ["nota (2).pdf", "nota (3).pdf", "nota (4).pdf"]
    assert [r.suffix for r in results] == [2, 3, 4]
    assert original.read_bytes() == b"original"
    for index, result in enumerate(results):
        assert result.path.read_bytes() == f"novo{index}".encode()
    assert list((tree / QUARANTINE_DIR).iterdir()) == []


def test_collision_skips_a_hole_in_the_sequence_only_forward(tree: Path) -> None:
    put(tree, "Alfa/Fiscal/nota.pdf", b"1")
    put(tree, "Alfa/Fiscal/nota (3).pdf", b"3")
    put(tree, f"{QUARANTINE_DIR}/u", b"novo")
    result = archive(tree, f"{QUARANTINE_DIR}/u", "Alfa/Fiscal", "nota.pdf", effects_enabled=True)
    assert result.final_name == "nota (2).pdf"
    put(tree, f"{QUARANTINE_DIR}/v", b"novo2")
    result = archive(tree, f"{QUARANTINE_DIR}/v", "Alfa/Fiscal", "nota.pdf", effects_enabled=True)
    assert result.final_name == "nota (4).pdf"


@pytest.mark.parametrize(
    ("name", "expected"),
    [
        ("backup.tar.gz", "backup (2).tar.gz"),
        ("contrato.pdf.p7s", "contrato (2).pdf.p7s"),
        ("nota.xml.p7s", "nota (2).xml.p7s"),
        ("Makefile", "Makefile (2)"),
        (".bashrc", ".bashrc (2)"),
        ("a.b.pdf", "a.b (2).pdf"),
    ],
)
def test_suffix_goes_before_the_extension(tree: Path, name: str, expected: str) -> None:
    put(tree, f"Alfa/Fiscal/{name}", b"existente")
    put(tree, f"{QUARANTINE_DIR}/u", b"novo")
    result = archive(tree, f"{QUARANTINE_DIR}/u", "Alfa/Fiscal", name, effects_enabled=True)
    assert result.final_name == expected
    assert (tree / "Alfa" / "Fiscal" / name).read_bytes() == b"existente"


def test_case_collision_never_loses_a_byte(tree: Path) -> None:
    existing = put(tree, "Alfa/Fiscal/Contrato.PDF", b"existente")
    put(tree, f"{QUARANTINE_DIR}/u", b"novo")
    result = archive(
        tree, f"{QUARANTINE_DIR}/u", "Alfa/Fiscal", "contrato.pdf", effects_enabled=True
    )
    assert existing.read_bytes() == b"existente"
    assert result.path.read_bytes() == b"novo"
    # a chave de colisão ignora a caixa (share SMB e APFS), mesmo em volume sensível a ela
    assert result.final_name == "contrato (2).pdf"


def test_unicode_form_collision_never_loses_a_byte(tree: Path) -> None:
    nfd = unicodedata.normalize("NFD", "ação.pdf")
    existing = put(tree, f"Alfa/Fiscal/{nfd}", b"existente")
    put(tree, f"{QUARANTINE_DIR}/u", b"novo")
    result = archive(tree, f"{QUARANTINE_DIR}/u", "Alfa/Fiscal", "ação.pdf", effects_enabled=True)
    assert existing.read_bytes() == b"existente"
    assert result.path.read_bytes() == b"novo"
    assert result.final_name == "ação (2).pdf"  # a chave ignora a forma Unicode


def test_collision_exhausted_leaves_everything_in_place(
    tree: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(names, "MAX_SUFFIX", 3)
    for name in ("nota.pdf", "nota (2).pdf", "nota (3).pdf"):
        put(tree, f"Alfa/Fiscal/{name}", name.encode())
    source = put(tree, f"{QUARANTINE_DIR}/u", b"novo")
    with pytest.raises(CollisionExhausted):
        archive(tree, f"{QUARANTINE_DIR}/u", "Alfa/Fiscal", "nota.pdf", effects_enabled=True)
    assert source.read_bytes() == b"novo"
    assert len(list((tree / "Alfa" / "Fiscal").iterdir())) == 3


def test_source_already_in_destination_is_a_conflict_not_a_suffix(tree: Path) -> None:
    put(tree, "Alfa/Fiscal/nota.pdf", b"x")
    with pytest.raises(Conflict):
        archive(tree, "Alfa/Fiscal/nota.pdf", "Alfa/Fiscal", effects_enabled=True)
    assert [p.name for p in (tree / "Alfa" / "Fiscal").iterdir()] == ["nota.pdf"]


def test_parallel_archives_to_the_same_name_lose_nothing(tree: Path) -> None:
    count = 12
    for index in range(count):
        put(tree, f"{QUARANTINE_DIR}/u{index}", f"conteudo-{index}".encode())

    def run(index: int) -> ArchiveResult:
        return archive(
            tree, f"{QUARANTINE_DIR}/u{index}", "Alfa/Fiscal", "doc.pdf", effects_enabled=True
        )

    with ThreadPoolExecutor(max_workers=count) as pool:
        results = list(pool.map(run, range(count)))
    finals = {r.final_name for r in results}
    assert len(finals) == count
    assert finals == {"doc.pdf"} | {f"doc ({n}).pdf" for n in range(2, count + 1)}
    for index, result in enumerate(results):
        assert result.path.read_bytes() == f"conteudo-{index}".encode()
    assert list((tree / QUARANTINE_DIR).iterdir()) == []


# --- limites de nome -------------------------------------------------------------------------


def test_name_at_the_limit_is_archived_without_collision(tree: Path) -> None:
    name = "a" * 251 + ".pdf"  # 255 unidades
    put(tree, f"{QUARANTINE_DIR}/u", b"x")
    result = archive(tree, f"{QUARANTINE_DIR}/u", "Alfa/Fiscal", name, effects_enabled=True)
    assert result.final_name == name
    assert result.path.exists()


def test_name_at_the_limit_with_collision_is_refused_without_effect(
    tree: Path, snapshot: Snapshot
) -> None:
    name = "a" * 251 + ".pdf"
    put(tree, f"Alfa/Fiscal/{name}", b"existente")
    source = put(tree, f"{QUARANTINE_DIR}/u", b"novo")
    before = snapshot(tree)
    with pytest.raises(NameTooLong) as error:
        archive(tree, f"{QUARANTINE_DIR}/u", "Alfa/Fiscal", name, effects_enabled=True)
    assert error.value.suffix == 2
    assert snapshot(tree) == before
    assert source.read_bytes() == b"novo"


def test_name_over_the_limit_is_refused_never_truncated(tree: Path, snapshot: Snapshot) -> None:
    put(tree, f"{QUARANTINE_DIR}/u", b"x")
    before = snapshot(tree)
    with pytest.raises(NameTooLong):
        archive(tree, f"{QUARANTINE_DIR}/u", "Alfa/Fiscal", "a" * 256, effects_enabled=True)
    with pytest.raises(NameTooLong):  # simulado também
        archive(tree, f"{QUARANTINE_DIR}/u", "Alfa/Fiscal", "a" * 256)
    assert snapshot(tree) == before


def test_accented_names_by_utf16_units_not_bytes(tree: Path) -> None:
    try:
        (tree / ("á" * 200)).write_bytes(b"x")
    except OSError:
        pytest.skip("volume limita o nome em bytes (ext4): só APFS, NTFS e SMB medem em UTF-16")
    for index, name in enumerate(("á" * 196 + ".pdf", unicodedata.normalize("NFD", "á" * 128))):
        put(tree, f"{QUARANTINE_DIR}/u{index}", b"x")
        result = archive(
            tree, f"{QUARANTINE_DIR}/u{index}", "Alfa/Fiscal", name, effects_enabled=True
        )
        assert names.name_units(result.final_name) <= 255
        assert result.path.exists()
    put(tree, f"{QUARANTINE_DIR}/longo", b"x")
    with pytest.raises(NameTooLong):
        archive(tree, f"{QUARANTINE_DIR}/longo", "Alfa/Fiscal", "á" * 256, effects_enabled=True)
    with pytest.raises(NameTooLong):  # emoji conta 2 unidades
        archive(
            tree, f"{QUARANTINE_DIR}/longo", "Alfa/Fiscal", "\U0001f600" * 128, effects_enabled=True
        )


def test_measured_limit_is_propagated_by_archive(tree: Path, snapshot: Snapshot) -> None:
    put(tree, f"{QUARANTINE_DIR}/u", b"x")
    before = snapshot(tree)
    with pytest.raises(NameTooLong):
        archive(
            tree,
            f"{QUARANTINE_DIR}/u",
            "Alfa/Fiscal",
            "a" * 21,
            max_name_units=20,
            effects_enabled=True,
        )
    with pytest.raises(NameTooLong):
        archive(tree, f"{QUARANTINE_DIR}/u", "Alfa/Fiscal", "a" * 21, max_name_units=20)
    assert snapshot(tree) == before
    put(tree, "Alfa/Fiscal/" + "a" * 18 + ".p", b"existente")  # 20 unidades
    with pytest.raises(NameTooLong) as error:  # o sufixo ( 2) não cabe no limite medido
        archive(
            tree,
            f"{QUARANTINE_DIR}/u",
            "Alfa/Fiscal",
            "a" * 18 + ".p",
            max_name_units=20,
            effects_enabled=True,
        )
    assert error.value.suffix == 2
    result = archive(
        tree,
        f"{QUARANTINE_DIR}/u",
        "Alfa/Fiscal",
        "a" * 20,
        max_name_units=20,
        effects_enabled=True,
    )
    assert result.final_name == "a" * 20


@pytest.mark.parametrize(
    "name", ["a:b.pdf", "con.txt", "x.", "x ", "a\x00b", "a\x1fb", "..", "a/b"]
)
def test_names_invalid_on_windows_are_refused(tree: Path, snapshot: Snapshot, name: str) -> None:
    put(tree, f"{QUARANTINE_DIR}/u", b"x")
    before = snapshot(tree)
    with pytest.raises(UnsafePath):
        archive(tree, f"{QUARANTINE_DIR}/u", "Alfa/Fiscal", name, effects_enabled=True)
    assert snapshot(tree) == before


# --- mesma raiz, escapes, origens estranhas --------------------------------------------------


def test_source_in_another_root_is_refused(tree: Path, tmp_path: Path) -> None:
    other = tmp_path.resolve() / "outra-raiz"
    foreign = put(other, "x.pdf", b"fora")
    with pytest.raises(CrossRoot):
        archive(tree, foreign, "Alfa/Fiscal", effects_enabled=True)
    assert foreign.read_bytes() == b"fora"


def test_destination_outside_the_root_is_refused(tree: Path, tmp_path: Path) -> None:
    source = put(tree, f"{QUARANTINE_DIR}/u", b"x")
    outside = tmp_path.resolve() / "fora"
    outside.mkdir()
    for destination in (outside, "/", f"{tree}-evil"):
        with pytest.raises(CrossRoot):
            archive(tree, source, destination, "a.pdf", effects_enabled=True)
    assert list(outside.iterdir()) == []


@pytest.mark.parametrize("bad", ["../x", "Alfa/../../x", "Alfa/..", "Alfa\\Fiscal", "Alfa/\0"])
def test_dotdot_backslash_and_nul_are_unsafe(tree: Path, bad: str) -> None:
    source = put(tree, f"{QUARANTINE_DIR}/u", b"x")
    with pytest.raises(UnsafePath):
        archive(tree, source, bad, "a.pdf", effects_enabled=True)
    with pytest.raises(UnsafePath):
        archive(tree, bad, "Alfa/Fiscal", "a.pdf", effects_enabled=True)
    assert source.exists()


def test_destination_cannot_be_the_root_itself(tree: Path) -> None:
    source = put(tree, f"{QUARANTINE_DIR}/u", b"x")
    for destination in (".", "", tree):
        with pytest.raises(UnsafePath):
            archive(tree, source, destination, "a.pdf", effects_enabled=True)


def test_quarantine_is_never_a_destination(tree: Path) -> None:
    source = put(tree, "Alfa/a.pdf", b"x")
    with pytest.raises(CrossRoot):
        archive(tree, source, QUARANTINE_DIR, effects_enabled=True)
    with pytest.raises(CrossRoot):
        archive(tree, source, tree / QUARANTINE_DIR / "sub", effects_enabled=True)


def test_symlink_directory_and_fifo_sources_are_refused(tree: Path, tmp_path: Path) -> None:
    target = put(tmp_path.resolve(), "fora/segredo.txt", b"segredo")
    (tree / "Alfa" / "atalho").symlink_to(target)
    (tree / "Alfa" / "pasta").mkdir()
    os.mkfifo(tree / "Alfa" / "fila")
    for name in ("atalho", "pasta", "fila"):
        with pytest.raises(UnsafePath):
            archive(tree, f"Alfa/{name}", "Alfa/Fiscal", effects_enabled=True)
    assert target.read_bytes() == b"segredo"
    assert (tree / "Alfa" / "atalho").is_symlink()


@pytest.mark.parametrize("effects_enabled", [False, True], ids=["simulado", "real"])
def test_hardlinked_source_is_refused_without_effect(
    tree: Path, tmp_path: Path, snapshot: Snapshot, effects_enabled: bool
) -> None:
    """Origem com mais de um nome (hardlink, talvez fora da raiz) não é arquivada."""
    outside = put(tmp_path.resolve(), "fora/original.txt", b"fora")
    os.link(outside, tree / QUARANTINE_DIR / "u")  # o teste cria o hardlink, não o produto
    before = snapshot(tree)
    with pytest.raises(UnsafePath, match="hardlink"):
        archive(
            tree, f"{QUARANTINE_DIR}/u", "Alfa/Fiscal", "a.pdf", effects_enabled=effects_enabled
        )
    assert snapshot(tree) == before
    assert outside.stat().st_nlink == 2


def test_symlinked_folder_in_source_or_destination_is_refused(tree: Path, tmp_path: Path) -> None:
    outside = tmp_path.resolve() / "fora"
    outside.mkdir()
    put(outside, "a.pdf", b"fora")
    (tree / "Alfa" / "link").symlink_to(outside, target_is_directory=True)
    source = put(tree, f"{QUARANTINE_DIR}/u", b"x")
    with pytest.raises(UnsafePath):
        archive(tree, source, "Alfa/link", "a.pdf", effects_enabled=True)
    with pytest.raises(UnsafePath):
        archive(tree, "Alfa/link/a.pdf", "Alfa/Fiscal", effects_enabled=True)
    assert source.exists() and (outside / "a.pdf").read_bytes() == b"fora"


def test_missing_destination_creates_nothing(tree: Path, snapshot: Snapshot) -> None:
    put(tree, f"{QUARANTINE_DIR}/u", b"x")
    before = snapshot(tree)
    for destination in ("Alfa/Contabil", "Beta/Fiscal", "Alfa/Fiscal/2026"):
        with pytest.raises(DestinationMissing):
            archive(tree, f"{QUARANTINE_DIR}/u", destination, "a.pdf", effects_enabled=True)
    assert snapshot(tree) == before


def test_missing_source_is_not_found(tree: Path) -> None:
    with pytest.raises(NotFound):
        archive(tree, f"{QUARANTINE_DIR}/nao-existe", "Alfa/Fiscal", "a.pdf", effects_enabled=True)
    with pytest.raises(NotFound):
        archive(tree, "Sumiu/a.pdf", "Alfa/Fiscal", effects_enabled=True)


def test_different_devices_are_refused(tree: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    source = put(tree, f"{QUARANTINE_DIR}/u", b"x")
    real_fstat = os.fstat
    calls: list[int] = []

    def fake_fstat(fd: int) -> object:
        info = real_fstat(fd)
        calls.append(fd)
        if len(calls) == 3:  # raiz, diretório de origem e, por fim, o de destino
            values = {n: getattr(info, n) for n in dir(info) if n.startswith("st_")}
            values["st_dev"] = info.st_dev + 1
            return types.SimpleNamespace(**values)
        return info

    monkeypatch.setattr(archive_module.os, "fstat", fake_fstat)
    with pytest.raises(CrossDevice):
        archive(tree, source, "Alfa/Fiscal", "a.pdf", effects_enabled=True)
    assert source.read_bytes() == b"x"


# --- raiz aberta uma vez ---------------------------------------------------------------------


def _count_root_opens(monkeypatch: pytest.MonkeyPatch, root: Path) -> list[object]:
    opened: list[object] = []
    real_open = os.open

    def spy(path: object, flags: int, *args: object, **kwargs: object) -> int:
        if kwargs.get("dir_fd") is None and path == "/":  # open_root parte de / uma vez
            opened.append(path)
        return real_open(path, flags, *args, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(os, "open", spy)
    return opened


def test_root_is_opened_once_per_archive_and_per_undo(
    tree: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    put(tree, f"{QUARANTINE_DIR}/u", b"conteudo")
    opened = _count_root_opens(monkeypatch, tree)
    result = archive(tree, f"{QUARANTINE_DIR}/u", "Alfa/Fiscal", "a.pdf", effects_enabled=True)
    assert len(opened) == 1
    undo(result.record(root_id=1, sha256=sha(b"conteudo")), effects_enabled=True)
    assert len(opened) == 2


def test_swapping_the_root_path_between_steps_never_crosses_trees(
    tree: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    put(tree, f"{QUARANTINE_DIR}/u", b"original")
    decoy = tmp_path.resolve() / "isca"
    (decoy / "Alfa" / "Fiscal").mkdir(parents=True)
    (decoy / QUARANTINE_DIR).mkdir()
    put(decoy, f"{QUARANTINE_DIR}/u", b"isca")
    moved = tmp_path.resolve() / "raiz-movida"
    real_open = os.open
    swapped: list[bool] = []

    def swapping_open(path: object, flags: int, *args: object, **kwargs: object) -> int:
        # na abertura do destino (depois da raiz e da origem), o caminho da raiz é trocado
        if path == "Fiscal" and not swapped:
            swapped.append(True)
            os.rename(tree, moved)
            os.symlink(decoy, tree)
        return real_open(path, flags, *args, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(os, "open", swapping_open)
    result = archive(tree, f"{QUARANTINE_DIR}/u", "Alfa/Fiscal", "a.pdf", effects_enabled=True)
    assert swapped == [True]
    assert result.dest_rel == "Alfa/Fiscal/a.pdf"
    assert (moved / "Alfa" / "Fiscal" / "a.pdf").read_bytes() == b"original"
    assert not (moved / QUARANTINE_DIR / "u").exists()
    # a árvore isca (para onde o caminho da raiz passou a apontar) não foi tocada
    assert (decoy / QUARANTINE_DIR / "u").read_bytes() == b"isca"
    assert list((decoy / "Alfa" / "Fiscal").iterdir()) == []


# --- identidade da origem (detecta depois, não impede) ---------------------------------------


def test_capability_is_checked_once_per_operation_even_with_collisions(
    tree: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    for name in ("a.pdf", "a (2).pdf", "a (3).pdf"):
        put(tree, f"Alfa/Fiscal/{name}", b"ocupado")
    put(tree, f"{QUARANTINE_DIR}/u", b"novo")
    checks: list[int] = []
    real_proven = noreplace.proven_primitive
    real_rename = archive_module.rename_noreplace
    renames: list[object] = []

    def counting(dir_fd: int) -> Primitive:
        checks.append(dir_fd)
        return real_proven(dir_fd)

    def colliding_once(*args: object, **kwargs: object) -> object:
        renames.append(kwargs.get("primitive"))
        if len(renames) == 1:  # o nome some da listagem e reaparece: colisão na chamada
            return noreplace.RenameOutcome(renamed=False, primitive=kwargs["primitive"])  # type: ignore[arg-type]
        return real_rename(*args, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(archive_module, "proven_primitive", counting)
    monkeypatch.setattr(noreplace, "proven_primitive", counting)
    monkeypatch.setattr(archive_module, "rename_noreplace", colliding_once)
    result = archive(tree, f"{QUARANTINE_DIR}/u", "Alfa/Fiscal", "a.pdf", effects_enabled=True)
    assert result.final_name == "a (5).pdf"
    assert len(renames) == 2 and all(isinstance(p, Primitive) for p in renames)
    assert len(checks) == 1


def test_source_swapped_before_the_rename_is_detected_and_reported(
    tree: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    put(tree, f"{QUARANTINE_DIR}/u", b"original")
    real = archive_module.rename_noreplace

    def swap_then_rename(src_fd: int, src: str, dst_fd: int, dst: str, **kwargs: object) -> object:
        (tree / QUARANTINE_DIR / src).rename(tree / QUARANTINE_DIR / "antigo")
        (tree / QUARANTINE_DIR / src).write_bytes(b"trocado")
        return real(src_fd, src, dst_fd, dst, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(archive_module, "rename_noreplace", swap_then_rename)
    with pytest.raises(IdentityChanged) as error:
        archive(tree, f"{QUARANTINE_DIR}/u", "Alfa/Fiscal", "a.pdf", effects_enabled=True)
    assert error.value.destino == "Alfa/Fiscal/a.pdf"
    assert "posterior" in str(error.value) and "não impede" in str(error.value)
    # a detecção é posterior: o arquivo trocado já foi movido, e o original segue na quarentena
    assert (tree / "Alfa" / "Fiscal" / "a.pdf").read_bytes() == b"trocado"
    assert (tree / QUARANTINE_DIR / "antigo").read_bytes() == b"original"


@pytest.mark.parametrize("uses_inode", [True, False], ids=["com-inode", "sem-inode"])
def test_hardlink_created_before_the_rename_is_detected(
    tree: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, uses_inode: bool
) -> None:
    """Hardlink criado entre a conferência da origem e o rename: ``IdentityChanged``."""
    monkeypatch.setattr(archive_module, "IDENTITY_USES_INODE", uses_inode)
    put(tree, f"{QUARANTINE_DIR}/u", b"original")
    outside = tmp_path.resolve() / "fora"
    outside.mkdir()
    real = archive_module.rename_noreplace

    def link_then_rename(src_fd: int, src: str, dst_fd: int, dst: str, **kwargs: object) -> object:
        os.link(tree / QUARANTINE_DIR / src, outside / "segundo-nome")  # o teste, não o produto
        return real(src_fd, src, dst_fd, dst, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(archive_module, "rename_noreplace", link_then_rename)
    with pytest.raises(IdentityChanged) as error:
        archive(tree, f"{QUARANTINE_DIR}/u", "Alfa/Fiscal", "a.pdf", effects_enabled=True)
    assert error.value.destino == "Alfa/Fiscal/a.pdf"


def test_source_modified_before_the_rename_is_detected(
    tree: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    put(tree, f"{QUARANTINE_DIR}/u", b"original")
    real = archive_module.rename_noreplace

    def append_then_rename(
        src_fd: int, src: str, dst_fd: int, dst: str, **kwargs: object
    ) -> object:
        with open(tree / QUARANTINE_DIR / src, "ab") as handle:
            handle.write(b" mais bytes")
        return real(src_fd, src, dst_fd, dst, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(archive_module, "rename_noreplace", append_then_rename)
    with pytest.raises(IdentityChanged) as error:
        archive(tree, f"{QUARANTINE_DIR}/u", "Alfa/Fiscal", "a.pdf", effects_enabled=True)
    assert error.value.destino == "Alfa/Fiscal/a.pdf"


def test_identity_error_is_a_domain_conflict_and_not_swallowed() -> None:
    assert issubclass(IdentityChanged, Conflict)


# --- sem rename exclusivo comprovado ---------------------------------------------------------


def test_without_a_proven_primitive_nothing_happens_even_simulated(
    tree: Path, snapshot: Snapshot, monkeypatch: pytest.MonkeyPatch
) -> None:
    put(tree, f"{QUARANTINE_DIR}/u", b"x")
    before = snapshot(tree)
    monkeypatch.setattr(noreplace, "_platform", lambda: "win32")
    for effects in (True, False):
        with pytest.raises(NoReplaceUnavailable):
            archive(tree, f"{QUARANTINE_DIR}/u", "Alfa/Fiscal", "a.pdf", effects_enabled=effects)
    assert snapshot(tree) == before


# --- desfazer --------------------------------------------------------------------------------


def _archived(tree: Path, origin: str, content: bytes, name: str = "nota.pdf") -> ArchiveRecord:
    put(tree, origin, content)
    result = archive(tree, origin, "Alfa/Fiscal", name, effects_enabled=True)
    return result.record(root_id=7, sha256=sha(content))


def test_undo_returns_to_the_origin_with_identical_bytes(tree: Path) -> None:
    record = _archived(tree, "Alfa/Entrada/nota-original.pdf", b"conteudo")
    assert not (tree / "Alfa" / "Entrada" / "nota-original.pdf").exists()
    result = undo(record, effects_enabled=True)
    assert (result.simulated, result.suffix) == (False, 1)
    assert result.path == tree / "Alfa" / "Entrada" / "nota-original.pdf"
    assert result.path.read_bytes() == b"conteudo"
    assert not (tree / "Alfa" / "Fiscal" / "nota.pdf").exists()
    assert (result.source_rel, result.dest_rel) == (record.destino, record.origem)


def test_undo_with_occupied_spot_gets_a_suffix(tree: Path) -> None:
    record = _archived(tree, "Alfa/Entrada/a.pdf", b"conteudo")
    occupied = put(tree, "Alfa/Entrada/a.pdf", b"outro arquivo")
    result = undo(record, effects_enabled=True)
    assert result.final_name == "a (2).pdf"
    assert occupied.read_bytes() == b"outro arquivo"
    assert result.path.read_bytes() == b"conteudo"


def test_undo_to_the_quarantine_goes_back_to_the_quarantine(tree: Path) -> None:
    record = _archived(tree, f"{QUARANTINE_DIR}/u1", b"upload")
    result = undo(record, effects_enabled=True)
    assert result.dest_rel == f"{QUARANTINE_DIR}/u1"
    assert (tree / QUARANTINE_DIR / "u1").read_bytes() == b"upload"


def test_undo_with_changed_content_is_refused(tree: Path) -> None:
    record = _archived(tree, f"{QUARANTINE_DIR}/u1", b"conteudo")
    (tree / "Alfa" / "Fiscal" / "nota.pdf").write_bytes(b"outro!!!")  # mesmo tamanho
    with pytest.raises(IdentityChanged):
        undo(record, effects_enabled=True)
    assert (tree / "Alfa" / "Fiscal" / "nota.pdf").read_bytes() == b"outro!!!"
    assert not (tree / QUARANTINE_DIR / "u1").exists()


def test_undo_when_destination_was_moved_by_hand_is_not_found(tree: Path) -> None:
    record = _archived(tree, f"{QUARANTINE_DIR}/u1", b"conteudo")
    (tree / "Alfa" / "Fiscal" / "nota.pdf").rename(tree / "Alfa" / "movido.pdf")
    with pytest.raises(NotFound):
        undo(record, effects_enabled=True)
    assert (tree / "Alfa" / "movido.pdf").exists()


def test_second_undo_is_not_found(tree: Path) -> None:
    record = _archived(tree, f"{QUARANTINE_DIR}/u1", b"conteudo")
    undo(record, effects_enabled=True)
    with pytest.raises(NotFound):
        undo(record, effects_enabled=True)


def test_undo_never_creates_the_origin_folder(tree: Path, snapshot: Snapshot) -> None:
    record = _archived(tree, "Alfa/Entrada/a.pdf", b"conteudo")
    (tree / "Alfa" / "Entrada").rmdir()  # o teste esvazia a origem; o produto nunca apaga
    before = snapshot(tree)
    with pytest.raises(DestinationMissing):
        undo(record, effects_enabled=True)
    assert snapshot(tree) == before


def test_undo_never_recreates_a_missing_quarantine(tree: Path, snapshot: Snapshot) -> None:
    record = _archived(tree, f"{QUARANTINE_DIR}/u1", b"conteudo")
    (tree / QUARANTINE_DIR).rmdir()
    before = snapshot(tree)
    with pytest.raises(DestinationMissing):
        undo(record, effects_enabled=True)
    assert snapshot(tree) == before


def test_undo_propagates_the_measured_name_limit(tree: Path) -> None:
    name = "a" * 18 + ".p"  # 20 unidades
    record = _archived(tree, f"Alfa/Entrada/{name}", b"conteudo", name=name)
    put(tree, f"Alfa/Entrada/{name}", b"ocupado")
    with pytest.raises(NameTooLong):
        undo(record, effects_enabled=True, max_name_units=20)
    assert (tree / "Alfa" / "Fiscal" / name).read_bytes() == b"conteudo"


def test_record_requires_a_valid_sha256_and_relative_paths_inside_the_root(tree: Path) -> None:
    good = {"root_id": 1, "root": tree, "origem": "a/b", "destino": "c/d"}
    ArchiveRecord(**good, sha256=sha(b"x"))
    with pytest.raises(TypeError):
        ArchiveRecord(**good)  # type: ignore[call-arg]
    for bad_hash in ("", "abc", sha(b"x").upper(), "g" * 64):
        with pytest.raises(ValueError):
            ArchiveRecord(**good, sha256=bad_hash)
    for field, value in (("origem", "/etc/x"), ("destino", f"{tree}/c/d"), ("origem", "")):
        with pytest.raises(ValueError):
            ArchiveRecord(**{**good, field: value}, sha256=sha(b"x"))
    for field in ("origem", "destino"):
        with pytest.raises(UnsafePath):
            ArchiveRecord(**{**good, field: "../fora"}, sha256=sha(b"x"))


# --- efeitos desligados ----------------------------------------------------------------------


def test_effects_off_by_default_touch_nothing(
    tree: Path, snapshot: Snapshot, monkeypatch: pytest.MonkeyPatch
) -> None:
    put(tree, "Alfa/Fiscal/nota.pdf", b"existente")
    put(tree, f"{QUARANTINE_DIR}/u", b"novo")
    before = snapshot(tree)
    monkeypatch.setattr(noreplace, "_call_rename", lambda *a, **k: pytest.fail("rename"))
    default = archive(tree, f"{QUARANTINE_DIR}/u", "Alfa/Fiscal", "nota.pdf")
    explicit = archive(
        tree, f"{QUARANTINE_DIR}/u", "Alfa/Fiscal", "nota.pdf", effects_enabled=False
    )
    assert default == explicit
    assert default.simulated is True and default.primitive is None
    assert (default.final_name, default.suffix, default.size) == ("nota (2).pdf", 2, 4)
    assert snapshot(tree) == before


def test_undo_effects_off_by_default_touches_nothing(
    tree: Path, snapshot: Snapshot, monkeypatch: pytest.MonkeyPatch
) -> None:
    record = _archived(tree, "Alfa/Entrada/a.pdf", b"conteudo")
    before = snapshot(tree)
    monkeypatch.setattr(noreplace, "_call_rename", lambda *a, **k: pytest.fail("rename"))
    default = undo(record)
    explicit = undo(record, effects_enabled=False)
    assert default == explicit
    assert default.simulated is True
    assert default.dest_rel == "Alfa/Entrada/a.pdf"
    assert snapshot(tree) == before


def test_simulated_undo_still_checks_the_hash(tree: Path) -> None:
    record = _archived(tree, "Alfa/Entrada/a.pdf", b"conteudo")
    (tree / "Alfa" / "Fiscal" / "nota.pdf").write_bytes(b"adulterado")
    with pytest.raises(IdentityChanged):
        undo(record)


def test_simulated_refusals_match_the_real_ones(tree: Path) -> None:
    put(tree, f"{QUARANTINE_DIR}/u", b"x")
    for effects in (False, True):
        with pytest.raises(DestinationMissing):
            archive(tree, f"{QUARANTINE_DIR}/u", "Beta", "a.pdf", effects_enabled=effects)
        with pytest.raises(CrossRoot):
            archive(tree, f"{QUARANTINE_DIR}/u", "/", "a.pdf", effects_enabled=effects)
        with pytest.raises(NotFound):
            archive(tree, f"{QUARANTINE_DIR}/x", "Alfa/Fiscal", "a.pdf", effects_enabled=effects)


def test_simulated_equals_real_in_a_quiet_directory(tree: Path) -> None:
    """Só num diretório quieto: o simulado prevê com o estado observado, sem prometer
    o mesmo destino sob concorrência, permissões ou capacidades diferentes."""
    put(tree, "Alfa/Fiscal/nota.pdf", b"existente")
    put(tree, "Alfa/Fiscal/nota (2).pdf", b"existente 2")
    put(tree, f"{QUARANTINE_DIR}/u", b"novo")
    predicted = archive(tree, f"{QUARANTINE_DIR}/u", "Alfa/Fiscal", "nota.pdf")
    real = archive(tree, f"{QUARANTINE_DIR}/u", "Alfa/Fiscal", "nota.pdf", effects_enabled=True)
    assert (predicted.dest_rel, predicted.suffix, predicted.size) == (
        real.dest_rel,
        real.suffix,
        real.size,
    )
    assert (predicted.simulated, real.simulated) == (True, False)
