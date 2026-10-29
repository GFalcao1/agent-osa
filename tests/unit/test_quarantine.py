"""Quarentena dentro da raiz: criação única, nomes exclusivos e efeitos desligados por padrão."""

from __future__ import annotations

import os
import stat
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

from osa.domain.errors import UnsafePath
from osa.fs import quarantine, roots
from osa.fs.errors import CrossRoot, DestinationMissing
from osa.fs.quarantine import (
    QUARANTINE_DIR,
    ensure_quarantine,
    new_quarantine_file,
)

Snapshot = Callable[[Path], dict[str, tuple[str, int, int, int]]]


def test_quarantine_dir_name() -> None:
    assert QUARANTINE_DIR == ".osa-quarentena"


def test_ensure_creates_once_and_second_call_is_a_noop(root: Path, snapshot: Snapshot) -> None:
    first = ensure_quarantine(root, effects_enabled=True)
    assert (first.created, first.simulated, first.path) == (True, False, root / QUARANTINE_DIR)
    assert (root / QUARANTINE_DIR).is_dir()
    before = snapshot(root)
    second = ensure_quarantine(root, effects_enabled=True)
    assert (second.created, second.simulated) == (False, False)
    assert snapshot(root) == before


def test_ensure_refuses_file_or_link_in_its_place(root: Path, tmp_path: Path) -> None:
    (root / QUARANTINE_DIR).write_bytes(b"x")
    with pytest.raises(UnsafePath):
        ensure_quarantine(root, effects_enabled=True)
    (root / QUARANTINE_DIR).unlink()
    (root / QUARANTINE_DIR).symlink_to(tmp_path, target_is_directory=True)
    with pytest.raises(UnsafePath):
        ensure_quarantine(root, effects_enabled=True)
    with pytest.raises(UnsafePath):
        ensure_quarantine(root)  # também no simulado


def test_ensure_is_off_by_default_and_creates_nothing(
    root: Path, snapshot: Snapshot, monkeypatch: pytest.MonkeyPatch
) -> None:
    before = snapshot(root)

    def forbidden(*args: object, **kwargs: object) -> None:
        raise AssertionError("escrita com efeitos desligados")

    monkeypatch.setattr(roots.os, "mkdir", forbidden)
    default = ensure_quarantine(root)
    explicit = ensure_quarantine(root, effects_enabled=False)
    assert default == explicit
    assert (default.created, default.simulated) == (True, True)
    assert snapshot(root) == before == {}


def test_ensure_simulated_with_existing_quarantine_predicts_noop(root: Path) -> None:
    (root / QUARANTINE_DIR).mkdir()
    result = ensure_quarantine(root)
    assert (result.created, result.simulated) == (False, True)


def test_new_file_requires_the_folder_to_exist(root: Path) -> None:
    for effects in (True, False):
        with pytest.raises(DestinationMissing), new_quarantine_file(root, effects_enabled=effects):
            pass
    assert not (root / QUARANTINE_DIR).exists()


def test_new_file_is_off_by_default_and_creates_nothing(
    root: Path, snapshot: Snapshot, monkeypatch: pytest.MonkeyPatch
) -> None:
    ensure_quarantine(root, effects_enabled=True)
    before = snapshot(root)
    real_open = os.open

    def guarded(path: object, flags: int, *args: object, **kwargs: object) -> int:
        assert not flags & (os.O_CREAT | os.O_WRONLY | os.O_RDWR), "criação com efeitos desligados"
        return real_open(path, flags, *args, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(os, "open", guarded)
    with (
        new_quarantine_file(root) as default,
        new_quarantine_file(root, effects_enabled=False) as explicit,
    ):
        assert default.simulated and explicit.simulated
        assert default.fd is None and explicit.fd is None
        assert default.rel.startswith(f"{QUARANTINE_DIR}/")
    assert snapshot(root) == before
    assert list((root / QUARANTINE_DIR).iterdir()) == []


def test_new_file_creates_exclusive_private_file(root: Path) -> None:
    ensure_quarantine(root, effects_enabled=True)
    with new_quarantine_file(root, effects_enabled=True) as file:
        assert file.simulated is False
        assert file.fd is not None
        os.write(file.fd, b"conteudo ficticio")
    created = root / file.rel
    assert created.read_bytes() == b"conteudo ficticio"
    assert stat.S_IMODE(created.stat().st_mode) == 0o600
    assert file.rel == f"{QUARANTINE_DIR}/{created.name}"
    assert len(created.name) == 32 and int(created.name, 16) >= 0
    with pytest.raises(OSError):  # fd fechado na saída
        os.fstat(file.fd)


def test_new_files_have_distinct_names_even_in_parallel(root: Path) -> None:
    ensure_quarantine(root, effects_enabled=True)

    def create(_: int) -> str:
        with new_quarantine_file(root, effects_enabled=True) as file:
            assert file.fd is not None
            os.write(file.fd, b"x")
            return file.rel

    with ThreadPoolExecutor(max_workers=8) as pool:
        names = list(pool.map(create, range(50)))
    assert len(set(names)) == 50
    assert len(list((root / QUARANTINE_DIR).iterdir())) == 50


def test_name_does_not_depend_on_the_original_name(root: Path) -> None:
    ensure_quarantine(root, effects_enabled=True)
    with new_quarantine_file(root, effects_enabled=True) as file:
        pass
    assert "contrato" not in file.rel and ".pdf" not in file.rel


def test_never_overwrites_an_existing_name(root: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    ensure_quarantine(root, effects_enabled=True)
    taken = root / QUARANTINE_DIR / ("a" * 32)
    taken.write_bytes(b"antigo")
    ids = iter(["a" * 32, "a" * 32, "b" * 32])
    monkeypatch.setattr(quarantine, "_new_id", lambda: next(ids))
    with new_quarantine_file(root, effects_enabled=True) as file:
        assert file.fd is not None
        os.write(file.fd, b"novo")
    assert taken.read_bytes() == b"antigo"
    assert (root / file.rel).name == "b" * 32


def test_gives_up_after_too_many_collisions(root: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from osa.domain.errors import Conflict

    ensure_quarantine(root, effects_enabled=True)
    (root / QUARANTINE_DIR / ("a" * 32)).write_bytes(b"antigo")
    monkeypatch.setattr(quarantine, "_new_id", lambda: "a" * 32)
    with pytest.raises(Conflict), new_quarantine_file(root, effects_enabled=True):
        pass
    assert (root / QUARANTINE_DIR / ("a" * 32)).read_bytes() == b"antigo"


def test_archive_never_accepts_the_quarantine_as_destination(root: Path) -> None:
    from osa.fs.archive import archive

    ensure_quarantine(root, effects_enabled=True)
    (root / "Alfa").mkdir()
    (root / "Alfa" / "a.pdf").write_bytes(b"x")
    for destination in (QUARANTINE_DIR, f"{QUARANTINE_DIR}/sub", ".OSA-QUARENTENA"):
        with pytest.raises(CrossRoot):
            archive(root, "Alfa/a.pdf", destination, effects_enabled=True)
    assert list((root / QUARANTINE_DIR).iterdir()) == []


# --- sufixo (extensão normalizada do original) -------------------------------------------


def test_suffix_keeps_the_extension_after_the_exclusive_id(root: Path) -> None:
    ensure_quarantine(root, effects_enabled=True)
    with new_quarantine_file(root, suffix=".pdf", effects_enabled=True) as file:
        pass
    name = file.rel.rsplit("/", 1)[1]
    assert name.endswith(".pdf") and len(name) == 36
    assert int(name[:-4], 16) >= 0
    assert (root / file.rel).is_file()


def test_suffix_is_predicted_when_simulated(root: Path) -> None:
    ensure_quarantine(root, effects_enabled=True)
    with new_quarantine_file(root, suffix=".docx", effects_enabled=False) as file:
        assert file.simulated and file.fd is None
    assert file.rel.endswith(".docx")
    assert not (root / file.rel).exists()


@pytest.mark.parametrize("suffix", ["pdf", ".PDF", ".", ".a/b", ".tar.gz", "." + "a" * 17, ".ção"])
def test_other_suffixes_are_refused_before_anything_is_created(root: Path, suffix: str) -> None:
    ensure_quarantine(root, effects_enabled=True)
    with pytest.raises(UnsafePath, match="sufixo"):
        with new_quarantine_file(root, suffix=suffix, effects_enabled=True):
            pass
    assert list((root / QUARANTINE_DIR).iterdir()) == []
