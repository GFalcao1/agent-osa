"""Template: cria só o que falta; a segunda aplicação é no-op; efeitos desligados não escrevem."""

from __future__ import annotations

import os
import unicodedata
from collections.abc import Callable
from pathlib import Path

import pytest

from osa.domain.errors import Conflict, UnsafePath
from osa.fs import roots
from osa.fs.errors import NameTooLong
from osa.fs.template import DepartmentFolder, TreeResult, ensure_owner_tree

Snapshot = Callable[[Path], dict[str, tuple[str, int, int, int]]]

DEPARTMENTS = (
    DepartmentFolder("Fiscal", usa_ano=True),
    DepartmentFolder("Contábil", usa_ano=False),
    DepartmentFolder("RH", usa_ano=True),
)


def _names(root: Path, paths: tuple[Path, ...]) -> list[str]:
    return [path.relative_to(root).as_posix() for path in paths]


def test_creates_the_full_tree_with_year(root: Path) -> None:
    result = ensure_owner_tree(root, "Alfa", DEPARTMENTS, 2026, effects_enabled=True)
    assert result.simulated is False
    assert result.owner_dir == root / "Alfa"
    assert _names(root, result.created) == [
        "Alfa",
        "Alfa/Fiscal",
        "Alfa/Fiscal/2026",
        "Alfa/Contábil",
        "Alfa/RH",
        "Alfa/RH/2026",
    ]
    assert result.existing == ()
    assert (root / "Alfa" / "Fiscal" / "2026").is_dir()
    assert not (root / "Alfa" / "Contábil" / "2026").exists()


def test_second_application_is_a_noop(root: Path, snapshot: Snapshot) -> None:
    ensure_owner_tree(root, "Alfa", DEPARTMENTS, 2026, effects_enabled=True)
    before = snapshot(root)
    again = ensure_owner_tree(root, "Alfa", DEPARTMENTS, 2026, effects_enabled=True)
    assert again.created == ()
    assert len(again.existing) == 6
    assert snapshot(root) == before


def test_creates_only_the_missing_department(root: Path) -> None:
    ensure_owner_tree(root, "Alfa", DEPARTMENTS[:1], 2026, effects_enabled=True)
    marker = root / "Alfa" / "Fiscal" / "nota.pdf"
    marker.write_bytes(b"nao mexer")
    result = ensure_owner_tree(root, "Alfa", DEPARTMENTS, 2026, effects_enabled=True)
    assert _names(root, result.created) == ["Alfa/Contábil", "Alfa/RH", "Alfa/RH/2026"]
    assert marker.read_bytes() == b"nao mexer"


def test_department_with_year_but_unknown_year_creates_only_the_department(root: Path) -> None:
    result = ensure_owner_tree(root, "Alfa", DEPARTMENTS, None, effects_enabled=True)
    assert _names(root, result.created) == ["Alfa", "Alfa/Fiscal", "Alfa/Contábil", "Alfa/RH"]
    assert list((root / "Alfa" / "Fiscal").iterdir()) == []


def test_adds_the_year_folder_later(root: Path) -> None:
    ensure_owner_tree(root, "Alfa", DEPARTMENTS, None, effects_enabled=True)
    result = ensure_owner_tree(root, "Alfa", DEPARTMENTS, 2026, effects_enabled=True)
    assert _names(root, result.created) == ["Alfa/Fiscal/2026", "Alfa/RH/2026"]


@pytest.mark.parametrize("year", [1899, 2101, True])
def test_invalid_year_is_a_value_error(root: Path, year: int) -> None:
    with pytest.raises(ValueError):
        ensure_owner_tree(root, "Alfa", DEPARTMENTS, year, effects_enabled=True)
    assert list(root.iterdir()) == []


def test_reuses_existing_folder_in_other_case_or_unicode_form(root: Path) -> None:
    nfd = unicodedata.normalize("NFD", "Contábil")
    (root / "ALFA" / nfd).mkdir(parents=True)
    result = ensure_owner_tree(root, "Alfa", DEPARTMENTS[1:2], effects_enabled=True)
    assert result.created == ()
    assert [p.name for p in root.iterdir()] == ["ALFA"]
    assert unicodedata.normalize("NFC", result.existing[1].name) == "Contábil"
    assert result.owner_dir == root / "ALFA"


def test_duplicate_departments_by_key_are_a_conflict(root: Path) -> None:
    departments = (DepartmentFolder("Fiscal", False), DepartmentFolder("FISCAL", False))
    with pytest.raises(Conflict):
        ensure_owner_tree(root, "Alfa", departments, effects_enabled=True)
    assert list(root.iterdir()) == []


def test_existing_file_or_link_in_place_of_folder_is_unsafe(root: Path, tmp_path: Path) -> None:
    (root / "Alfa").write_bytes(b"x")
    with pytest.raises(UnsafePath):
        ensure_owner_tree(root, "Alfa", DEPARTMENTS, effects_enabled=True)
    (root / "Alfa").unlink()
    (root / "Alfa").mkdir()
    (root / "Alfa" / "Fiscal").symlink_to(tmp_path, target_is_directory=True)
    with pytest.raises(UnsafePath):
        ensure_owner_tree(root, "Alfa", DEPARTMENTS, effects_enabled=True)
    assert list(tmp_path.glob("Fiscal")) == []


@pytest.mark.parametrize("owner", [".oculta", ".osa-quarentena", "a/b", "a:b", "con", "x."])
def test_unsafe_owner_names_are_refused(root: Path, owner: str) -> None:
    with pytest.raises(UnsafePath):
        ensure_owner_tree(root, owner, DEPARTMENTS, effects_enabled=True)
    assert list(root.iterdir()) == []


def test_unsafe_department_folder_is_refused_before_any_effect(root: Path) -> None:
    departments = (DepartmentFolder("Fiscal", False), DepartmentFolder("../fora", False))
    with pytest.raises(UnsafePath):
        ensure_owner_tree(root, "Alfa", departments, effects_enabled=True)
    assert list(root.iterdir()) == []


def test_name_over_the_limit_is_refused_and_never_truncated(root: Path) -> None:
    with pytest.raises(NameTooLong):
        ensure_owner_tree(root, "a" * 256, DEPARTMENTS, effects_enabled=True)
    assert list(root.iterdir()) == []


def test_measured_name_limit_is_propagated(root: Path) -> None:
    with pytest.raises(NameTooLong):
        ensure_owner_tree(root, "a" * 30, DEPARTMENTS, effects_enabled=True, max_name_units=20)
    with pytest.raises(NameTooLong):
        ensure_owner_tree(
            root,
            "Alfa",
            (DepartmentFolder("d" * 30, False),),
            effects_enabled=True,
            max_name_units=20,
        )
    with pytest.raises(NameTooLong):
        ensure_owner_tree(root, "a" * 30, DEPARTMENTS, effects_enabled=False, max_name_units=20)
    assert list(root.iterdir()) == []
    assert ensure_owner_tree(
        root, "a" * 20, DEPARTMENTS[:1], effects_enabled=True, max_name_units=20
    ).created


def test_missing_root_is_unsafe(tmp_path: Path) -> None:
    for effects in (True, False):
        with pytest.raises(UnsafePath):
            ensure_owner_tree(tmp_path / "nao-existe", "Alfa", DEPARTMENTS, effects_enabled=effects)


def test_never_creates_the_quarantine(root: Path) -> None:
    ensure_owner_tree(root, "Alfa", DEPARTMENTS, 2026, effects_enabled=True)
    assert sorted(p.name for p in root.iterdir()) == ["Alfa"]


# --- efeitos desligados ----------------------------------------------------------------------


def test_effects_disabled_by_default_touch_nothing(
    root: Path, snapshot: Snapshot, monkeypatch: pytest.MonkeyPatch
) -> None:
    (root / "Alfa" / "Fiscal").mkdir(parents=True)
    before = snapshot(root)

    def forbidden(*args: object, **kwargs: object) -> None:
        raise AssertionError("escrita com efeitos desligados")

    monkeypatch.setattr(roots.os, "mkdir", forbidden)
    default = ensure_owner_tree(root, "Alfa", DEPARTMENTS, 2026)
    explicit = ensure_owner_tree(root, "Alfa", DEPARTMENTS, 2026, effects_enabled=False)
    assert default == explicit
    assert default.simulated is True
    assert _names(root, default.existing) == ["Alfa", "Alfa/Fiscal"]
    assert _names(root, default.created) == [
        "Alfa/Fiscal/2026",
        "Alfa/Contábil",
        "Alfa/RH",
        "Alfa/RH/2026",
    ]
    assert snapshot(root) == before


def test_simulated_on_empty_root_predicts_everything_without_creating(
    root: Path, snapshot: Snapshot
) -> None:
    before = snapshot(root)
    result = ensure_owner_tree(root, "Alfa", DEPARTMENTS, 2026)
    assert result.simulated is True
    assert len(result.created) == 6
    assert result.existing == ()
    assert snapshot(root) == before == {}


@pytest.mark.parametrize(
    "existing",
    [
        (),
        ("Alfa",),
        ("Alfa/Contábil",),  # departamento do meio falta (Fiscal) e o seguinte existe
        ("Alfa/RH/2026",),  # o primeiro falta, o último existe com o ano
        ("Alfa/Fiscal", "Alfa/RH"),  # anos faltam, departamentos existem
        ("ALFA/contábil",),  # dono e departamento em outra caixa
    ],
    ids=["vazia", "dono", "meio-falta", "primeiro-falta", "anos-faltam", "outra-caixa"],
)
def test_simulated_equals_real_in_a_quiet_directory(root: Path, existing: tuple[str, ...]) -> None:
    """Só num diretório quieto: o simulado não promete igualdade sob concorrência."""
    for folder in existing:
        (root / folder).mkdir(parents=True)
    predicted = ensure_owner_tree(root, "Alfa", DEPARTMENTS, 2026)
    real = ensure_owner_tree(root, "Alfa", DEPARTMENTS, 2026, effects_enabled=True)
    assert (_names(root, predicted.created), _names(root, predicted.existing)) == (
        _names(root, real.created),
        _names(root, real.existing),
    )
    assert predicted.owner_dir == real.owner_dir


def test_simulated_equals_real_with_existing_owner(root: Path) -> None:
    (root / "Alfa").mkdir()
    predicted = ensure_owner_tree(root, "Alfa", DEPARTMENTS, 2026)
    real = ensure_owner_tree(root, "Alfa", DEPARTMENTS, 2026, effects_enabled=True)
    assert isinstance(real, TreeResult)
    assert (predicted.created, predicted.existing) == (real.created, real.existing)
    assert (predicted.simulated, real.simulated) == (True, False)


def test_root_is_opened_once_per_application(root: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    opened: list[object] = []
    real_open = os.open

    def spy(path: object, flags: int, *args: object, **kwargs: object) -> int:
        if kwargs.get("dir_fd") is None and path == "/":  # open_root parte de / uma vez
            opened.append(path)
        return real_open(path, flags, *args, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(os, "open", spy)
    ensure_owner_tree(root, "Alfa", DEPARTMENTS, 2026, effects_enabled=True)
    assert len(opened) == 1
