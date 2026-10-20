"""Raiz aberta uma vez, caminhada por descritor e recusa de escapes."""

from __future__ import annotations

import os
import unicodedata
from collections.abc import Callable
from pathlib import Path

import pytest

from osa.domain.errors import Conflict, NotFound, UnsafePath
from osa.fs import roots
from osa.fs.errors import CrossRoot, NameTooLong
from osa.fs.roots import open_beneath, open_root, relative_parts, resolve_within

Write = Callable[..., Path]


# --- resolve_within: escapes ---------------------------------------------------------------


@pytest.mark.parametrize(
    "relative",
    ["..", "../x", "a/../../x", "a/..", "a/../b", "", ".", "./", "a/./b/..", "//", "..//x"],
)
def test_resolve_within_refuses_dotdot_empty_and_dot(
    root: Path, write: Write, relative: str
) -> None:
    write(root / "a" / "b")
    write(root.parent / "x")
    with pytest.raises(UnsafePath):
        resolve_within(root, relative)


def test_resolve_within_refuses_nul_and_backslash_without_value_error(root: Path) -> None:
    for relative in ("a\0b", "a\\b", "\0", "a/\0"):
        with pytest.raises(UnsafePath):
            resolve_within(root, relative)


def test_resolve_within_accepts_existing_files_and_folders(root: Path, write: Write) -> None:
    write(root / "Alfa" / "Fiscal" / "nota.pdf")
    assert resolve_within(root, "Alfa/Fiscal/nota.pdf") == root / "Alfa" / "Fiscal" / "nota.pdf"
    assert resolve_within(root, "Alfa/Fiscal") == root / "Alfa" / "Fiscal"
    assert resolve_within(root, root / "Alfa" / "Fiscal") == root / "Alfa" / "Fiscal"


def test_resolve_within_requires_existence(root: Path) -> None:
    with pytest.raises(NotFound):
        resolve_within(root, "nao/existe.pdf")


def test_resolve_within_absolute_outside_root_is_refused(root: Path, write: Write) -> None:
    outside = write(root.parent / "fora.txt")
    with pytest.raises(CrossRoot):
        resolve_within(root, outside)


def test_resolve_within_refuses_sibling_with_same_prefix(root: Path, write: Write) -> None:
    sibling = write(Path(f"{root}-evil") / "x.txt")
    with pytest.raises(CrossRoot):
        resolve_within(root, sibling)


def test_resolve_within_symlink_as_last_component(root: Path, write: Write) -> None:
    target = write(root.parent / "segredo.txt")
    (root / "Alfa").mkdir()
    (root / "Alfa" / "atalho").symlink_to(target)
    with pytest.raises(UnsafePath):
        resolve_within(root, "Alfa/atalho")


def test_resolve_within_symlink_as_intermediate_component(root: Path, write: Write) -> None:
    write(root.parent / "fora" / "segredo.txt")
    (root / "Alfa").symlink_to(root.parent / "fora", target_is_directory=True)
    with pytest.raises(UnsafePath):
        resolve_within(root, "Alfa/segredo.txt")


def test_resolve_within_dangling_symlink(root: Path) -> None:
    (root / "pendente").symlink_to(root.parent / "nao-existe")
    with pytest.raises(UnsafePath):
        resolve_within(root, "pendente")


def test_resolve_within_symlink_inside_root_is_still_refused(root: Path, write: Write) -> None:
    write(root / "real" / "a.txt")
    (root / "apelido").symlink_to(root / "real", target_is_directory=True)
    with pytest.raises(UnsafePath):
        resolve_within(root, "apelido/a.txt")


def test_resolve_within_returns_recorded_spelling(root: Path, write: Write) -> None:
    nfd = unicodedata.normalize("NFD", "Ação")
    write(root / nfd / "a.pdf")
    found = resolve_within(root, "Ação/a.pdf")
    assert found.resolve(strict=True) == found
    assert unicodedata.normalize("NFC", found.parent.name) == "Ação"


# --- raiz --------------------------------------------------------------------------------------


def test_root_must_be_absolute_existing_directory(root: Path, write: Write) -> None:
    for bad in (Path("relativa"), root / "nao-existe", write(root / "arquivo")):
        with pytest.raises(UnsafePath), open_root(bad):
            pass
    with pytest.raises(UnsafePath), open_root(str(root) + "\0x"):
        pass


def test_root_given_as_symlink_is_refused(root: Path, tmp_path: Path) -> None:
    link = tmp_path / "apelido-da-raiz"
    link.symlink_to(root, target_is_directory=True)
    with pytest.raises(UnsafePath), open_root(link):
        pass
    with pytest.raises(UnsafePath):
        resolve_within(link, "a")


def _portable_flags(monkeypatch: pytest.MonkeyPatch) -> None:
    """Tira ``O_NOFOLLOW_ANY``/``O_SEARCH``: só ``O_NOFOLLOW`` por componente (o caso do Linux)."""
    monkeypatch.setattr(roots, "_ROOT_FLAGS", roots._DIR_FLAGS)
    monkeypatch.setattr(roots, "_SEARCH_FLAGS", roots._DIR_FLAGS)


@pytest.mark.parametrize("portable", [False, True], ids=["flags-do-sistema", "so-o_nofollow"])
@pytest.mark.parametrize("swap", ["pai", "ultimo"])
def test_root_swapped_while_opening_the_last_component_is_refused(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, portable: bool, swap: str
) -> None:
    """Pai já aberto por descritor; ao abrir o último componente, o pai ou o próprio último
    vira link para a isca. O descritor do pai continua na árvore original, mas a raiz pedida
    não é mais esse diretório: recusa."""
    if portable:
        _portable_flags(monkeypatch)
    base = tmp_path.resolve()
    real_root = base / "real" / "raiz"
    (real_root / "Alfa").mkdir(parents=True)
    (base / "isca" / "raiz" / "Alfa").mkdir(parents=True)
    real_open = os.open
    swapped: list[bool] = []

    def swapping_open(path: object, flags: int, *args: object, **kwargs: object) -> int:
        if not swapped and path == "raiz" and kwargs.get("dir_fd") is not None:
            swapped.append(True)  # o teste troca, não o produto
            if swap == "pai":
                os.rename(base / "real", base / "real-movida")
                os.symlink(base / "isca", base / "real")
            else:
                os.rename(real_root, base / "real" / "raiz-movida")
                os.symlink(base / "isca" / "raiz", real_root)
        return real_open(path, flags, *args, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(roots.os, "open", swapping_open)
    with pytest.raises(UnsafePath), open_root(real_root):
        pass
    assert swapped == [True]


def test_root_under_a_search_only_ancestor_opens(tmp_path: Path) -> None:
    """Ancestral só com ``x`` (sem ``r``) basta para chegar à raiz, como no caminho comum."""
    if os.geteuid() == 0:
        pytest.skip("root ignora permissão de leitura")
    base = tmp_path.resolve()
    real_root = base / "so-x" / "raiz"
    (real_root / "Alfa").mkdir(parents=True)
    os.chmod(base / "so-x", 0o111)  # o teste ajusta permissão, não o produto
    try:
        with open_root(real_root) as root_fd:
            assert os.listdir(root_fd) == ["Alfa"]
    finally:
        os.chmod(base / "so-x", 0o755)


def test_slash_is_not_a_root() -> None:
    with pytest.raises(UnsafePath), open_root("/"):
        pass
    with pytest.raises(UnsafePath), open_root(Path("/")):
        pass


@pytest.mark.parametrize("portable", [False, True], ids=["flags-do-sistema", "so-o_nofollow"])
def test_root_parent_swapped_for_symlink_during_open_is_refused(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, portable: bool
) -> None:
    """Componente intermediário da raiz trocado por link entre a checagem e a abertura.

    ``so-o_nofollow`` tira o ``O_NOFOLLOW_ANY`` do macOS: é o caminho do Linux da CI, onde só
    a caminhada por ``dir_fd`` protege.
    """
    if portable:
        _portable_flags(monkeypatch)
    base = tmp_path.resolve()
    real_root = base / "real" / "raiz"
    (real_root / "Alfa").mkdir(parents=True)
    (base / "isca" / "raiz" / "Alfa").mkdir(parents=True)
    real_open = os.open
    swapped: list[bool] = []

    def swapping_open(path: object, flags: int, *args: object, **kwargs: object) -> int:
        # na primeira abertura (depois do realpath), o pai "real" vira link para a isca
        if not swapped:
            swapped.append(True)
            os.rename(base / "real", base / "real-movida")  # o teste troca, não o produto
            os.symlink(base / "isca", base / "real")
        return real_open(path, flags, *args, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(roots.os, "open", swapping_open)
    with pytest.raises(UnsafePath), open_root(real_root):
        pass
    assert swapped == [True]


def test_root_is_walked_from_slash_component_by_component(
    root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[tuple[object, int, object]] = []
    real_open = os.open

    def spy(path: object, flags: int, *args: object, **kwargs: object) -> int:
        calls.append((path, flags, kwargs.get("dir_fd")))
        return real_open(path, flags, *args, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(roots.os, "open", spy)
    with open_root(root):
        pass
    assert calls[0][0] == "/" and calls[0][2] is None
    assert [call[0] for call in calls[1:]] == list(root.parts[1:])
    for _, flags, dir_fd in calls[1:]:
        assert dir_fd is not None
        assert flags & os.O_DIRECTORY
        assert flags & (os.O_NOFOLLOW | getattr(os, "O_NOFOLLOW_ANY", 0))
    # ancestrais só precisam de busca; a raiz final é aberta para leitura (listagem)
    assert {flags for _, flags, _ in calls[:-1]} == {roots._SEARCH_FLAGS}
    assert calls[-1][1] == roots._ROOT_FLAGS


def test_root_with_dotdot_is_refused(root: Path) -> None:
    (root / "sub").mkdir()
    with pytest.raises(UnsafePath), open_root(root / "sub" / ".."):
        pass


def test_root_path_swapped_after_open_keeps_the_original_tree(
    root: Path, tmp_path: Path, write: Write
) -> None:
    write(root / "Alfa" / "Fiscal" / "a.txt", b"original")
    decoy = tmp_path / "isca"
    write(decoy / "Alfa" / "Fiscal" / "a.txt", b"isca")
    moved = tmp_path / "raiz-movida"
    with open_root(root) as root_fd:
        os.rename(root, moved)  # o teste (não o produto) troca a raiz de lugar
        os.symlink(decoy, root)
        with open_beneath(root_fd, ("Alfa", "Fiscal")) as opened:
            assert os.listdir(opened.fd) == ["a.txt"]
            with open(moved / "Alfa" / "Fiscal" / "a.txt", "rb") as handle:
                original = handle.read()
            assert original == b"original"
            assert (
                os.stat("a.txt", dir_fd=opened.fd).st_ino
                == (moved / "Alfa" / "Fiscal" / "a.txt").stat().st_ino
            )
            assert (
                os.stat("a.txt", dir_fd=opened.fd).st_ino
                != (decoy / "Alfa" / "Fiscal" / "a.txt").stat().st_ino
            )


def test_directory_swapped_for_symlink_between_open_and_use(root: Path, write: Write) -> None:
    write(root / "Alfa" / "a.txt")
    write(root.parent / "fora" / "segredo.txt")
    with open_root(root) as root_fd, open_beneath(root_fd, ("Alfa",)) as opened:
        os.rename(root / "Alfa", root / "Alfa-antiga")
        os.symlink(root.parent / "fora", root / "Alfa")
        # o descritor continua apontando para o diretório original
        assert os.listdir(opened.fd) == ["a.txt"]
        # abrir de novo, agora pelo nome, recusa o link
        with pytest.raises(UnsafePath), open_beneath(root_fd, ("Alfa",)):
            pass


# --- open_beneath --------------------------------------------------------------------------


def test_open_beneath_without_parts_yields_the_root_descriptor(root: Path) -> None:
    with open_root(root) as root_fd, open_beneath(root_fd, ()) as opened:
        assert opened.fd == root_fd
        assert opened.parts == ()
    # a raiz continua aberta para quem a abriu até a saída do contexto externo


def test_open_beneath_refuses_bad_components(root: Path) -> None:
    with open_root(root) as root_fd:
        for parts in (("..",), (".",), ("",), ("a/b",), ("a\\b",), ("a\0",)):
            with pytest.raises(UnsafePath), open_beneath(root_fd, parts):
                pass


def test_open_beneath_missing_is_not_found_unless_created(root: Path) -> None:
    with open_root(root) as root_fd:
        with pytest.raises(NotFound), open_beneath(root_fd, ("Novo",)):
            pass
        assert not (root / "Novo").exists()
        with open_beneath(root_fd, ("Novo", "Sub"), create=True) as opened:
            assert opened.created == (True, True)
        with open_beneath(root_fd, ("Novo", "Sub"), create=True) as again:
            assert again.created == (False, False)


def test_open_beneath_file_in_place_of_folder_is_unsafe(root: Path, write: Write) -> None:
    write(root / "arquivo")
    with open_root(root) as root_fd, pytest.raises(UnsafePath):
        with open_beneath(root_fd, ("arquivo",), create=True):
            pass


def test_open_beneath_reuses_folder_in_other_case_or_unicode_form(root: Path) -> None:
    nfd = unicodedata.normalize("NFD", "Ação")
    (root / nfd).mkdir()
    with open_root(root) as root_fd:
        for requested in ("Ação", "AÇÃO", "ação"):
            with open_beneath(root_fd, (requested,), create=True) as opened:
                assert opened.created == (False,)
                assert unicodedata.normalize("NFC", opened.parts[0]) == "Ação"
    assert len(list(root.iterdir())) == 1


def test_open_beneath_two_candidates_is_a_conflict(root: Path) -> None:
    (root / "Alfa").mkdir()
    try:
        (root / "alfa").mkdir()
    except FileExistsError:
        pytest.skip("volume insensível a caixa: não cria duas pastas")
    with open_root(root) as root_fd:
        with open_beneath(root_fd, ("Alfa",)) as exact:
            assert exact.parts == ("Alfa",)
        with pytest.raises(Conflict), open_beneath(root_fd, ("ALFA",)):
            pass


def test_open_beneath_create_refuses_unsafe_or_long_names(root: Path) -> None:
    with open_root(root) as root_fd:
        with pytest.raises(UnsafePath), open_beneath(root_fd, (".oculta",), create=True):
            pass
        with pytest.raises(NameTooLong), open_beneath(root_fd, ("a" * 256,), create=True):
            pass
        with (
            pytest.raises(NameTooLong),
            open_beneath(root_fd, ("a" * 30,), create=True, max_name_units=20),
        ):
            pass
    assert list(root.iterdir()) == []


def test_relative_parts_rules(root: Path) -> None:
    assert relative_parts(root, "a/b") == ("a", "b")
    assert relative_parts(root, root / "a" / "b") == ("a", "b")
    assert relative_parts(root, ".") == ()
    with pytest.raises(CrossRoot):
        relative_parts(root, "/outra/raiz/x")
    with pytest.raises(UnsafePath):
        relative_parts(root, "a/../b")


def test_find_entry_prefers_exact_and_refuses_ambiguity(
    root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A ambiguidade só existe em volume sensível a caixa; aqui a listagem é simulada."""
    monkeypatch.setattr(roots.os, "listdir", lambda fd: ["Alfa", "alfa", "Beta"])
    assert roots.find_entry(0, "Alfa") == "Alfa"
    assert roots.find_entry(0, "alfa") == "alfa"
    assert roots.find_entry(0, "beta") == "Beta"
    assert roots.find_entry(0, "Gama") is None
    with pytest.raises(Conflict):
        roots.find_entry(0, "ALFA")
