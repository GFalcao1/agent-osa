"""Primitiva de rename sem substituição: backends, capability, mapeamento de erros, sem fallback."""

from __future__ import annotations

import ctypes
import errno
import os
import sys
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from pathlib import Path

import pytest

from osa.domain.errors import NotFound
from osa.fs import noreplace
from osa.fs.errors import CrossDevice, NameTooLong, NoReplaceUnavailable
from osa.fs.noreplace import (
    Primitive,
    VolumeCapabilities,
    proven_primitive,
    rename_noreplace,
    volume_capabilities,
)

Write = Callable[..., Path]


@contextmanager
def directory(path: Path) -> Iterator[int]:
    fd = os.open(path, os.O_RDONLY | os.O_DIRECTORY)
    try:
        yield fd
    finally:
        os.close(fd)


def _no_rename_allowed(*args: object, **kwargs: object) -> int:
    raise AssertionError("nenhum rename deveria ser tentado")


def _fail_with(code: int) -> Callable[..., int]:
    def call(*args: object, **kwargs: object) -> int:
        ctypes.set_errno(code)
        return -1

    return call


def test_only_two_backends_exist_and_there_is_no_fallback() -> None:
    assert {primitive.value for primitive in Primitive} == {"renameatx_np", "renameat2"}
    public = {name for name in dir(noreplace) if not name.startswith("_")}
    assert not public & {"fallback", "rename_with_placeholder", "link_then_unlink"}


# --- efeito real, em cada backend disponível ---------------------------------------------------


@pytest.mark.usefixtures("require_noreplace")
def test_free_destination_is_renamed(root: Path, write: Write) -> None:
    write(root / "a", b"A")
    with directory(root) as fd:
        outcome = rename_noreplace(fd, "a", fd, "b")
    assert outcome.renamed is True
    assert outcome.primitive in set(Primitive)
    assert not (root / "a").exists()
    assert (root / "b").read_bytes() == b"A"


@pytest.mark.usefixtures("require_noreplace")
def test_collision_never_replaces_and_preserves_bytes(root: Path, write: Write) -> None:
    write(root / "a", b"A")
    write(root / "b", b"B")
    with directory(root) as fd:
        outcome = rename_noreplace(fd, "a", fd, "b")
    assert outcome.renamed is False
    assert (root / "b").read_bytes() == b"B"
    assert (root / "a").read_bytes() == b"A"


@pytest.mark.usefixtures("require_noreplace")
def test_collision_with_directory_or_dangling_link_is_not_replaced(
    root: Path, write: Write
) -> None:
    write(root / "a", b"A")
    (root / "dir").mkdir()
    (root / "pendente").symlink_to(root / "nao-existe")
    with directory(root) as fd:
        assert rename_noreplace(fd, "a", fd, "dir").renamed is False
        assert rename_noreplace(fd, "a", fd, "pendente").renamed is False
    assert (root / "a").read_bytes() == b"A"
    assert (root / "pendente").is_symlink()


@pytest.mark.usefixtures("require_noreplace")
def test_missing_source_is_not_found(root: Path) -> None:
    with directory(root) as fd, pytest.raises(NotFound):
        rename_noreplace(fd, "nao-existe", fd, "b")


@pytest.mark.usefixtures("require_noreplace")
def test_across_directories_by_descriptor(root: Path, write: Write) -> None:
    write(root / "origem" / "a", b"A")
    (root / "destino").mkdir()
    with directory(root / "origem") as src, directory(root / "destino") as dst:
        assert rename_noreplace(src, "a", dst, "a").renamed is True
    assert (root / "destino" / "a").read_bytes() == b"A"


@pytest.mark.usefixtures("require_noreplace")
def test_real_cross_device_rename_is_refused(root: Path, write: Write) -> None:
    other = Path("/dev/shm")
    if (
        not other.is_dir()
        or not os.access(other, os.W_OK)
        or other.stat().st_dev == root.stat().st_dev
    ):
        pytest.skip("sem segundo volume gravável (esperado: /dev/shm no Linux)")
    source = write(root / "a", b"A")
    with directory(root) as src, directory(other) as dst:
        try:
            noreplace.proven_primitive(dst)
        except NoReplaceUnavailable:
            pytest.skip("segundo volume sem rename exclusivo")
        with pytest.raises(CrossDevice):
            rename_noreplace(src, "a", dst, "osa-teste-exdev")
    assert source.read_bytes() == b"A"


def test_invalid_names_are_refused_before_any_call(
    root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(noreplace, "_call_rename", _no_rename_allowed)
    from osa.domain.errors import UnsafePath

    with directory(root) as fd:
        for src, dst in (("a/b", "c"), ("a", "../c"), ("", "c"), ("a", "b\0")):
            with pytest.raises(UnsafePath):
                rename_noreplace(fd, src, fd, dst)


# --- sem backend comprovado: nenhuma chamada de rename ----------------------------------------


def test_false_capability_raises_without_any_rename_call(
    root: Path, write: Write, monkeypatch: pytest.MonkeyPatch
) -> None:
    write(root / "a", b"A")
    monkeypatch.setattr(noreplace, "_platform", lambda: "darwin")
    monkeypatch.setattr(
        noreplace, "volume_capabilities", lambda fd: VolumeCapabilities(False, True, 0, 0)
    )
    monkeypatch.setattr(noreplace, "_call_rename", _no_rename_allowed)
    with directory(root) as fd:
        with pytest.raises(NoReplaceUnavailable):
            proven_primitive(fd)
        with pytest.raises(NoReplaceUnavailable):
            rename_noreplace(fd, "a", fd, "b")
    assert (root / "a").read_bytes() == b"A"


def test_capability_not_valid_or_unreadable_raises(
    root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(noreplace, "_platform", lambda: "darwin")
    monkeypatch.setattr(noreplace, "_call_rename", _no_rename_allowed)
    with directory(root) as fd:
        for caps in (VolumeCapabilities(True, False, 0, 0), None):
            monkeypatch.setattr(noreplace, "volume_capabilities", lambda fd, caps=caps: caps)
            with pytest.raises(NoReplaceUnavailable):
                proven_primitive(fd)


def test_unknown_platform_has_no_backend(root: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(noreplace, "_platform", lambda: "win32")
    monkeypatch.setattr(noreplace, "_call_rename", _no_rename_allowed)
    with directory(root) as fd:
        with pytest.raises(NoReplaceUnavailable):
            proven_primitive(fd)
        with pytest.raises(NoReplaceUnavailable):
            rename_noreplace(fd, "a", fd, "b")


def test_missing_symbol_in_libc_means_unavailable(
    root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    class Empty:
        pass

    monkeypatch.setattr(noreplace, "_library", lambda: Empty())
    monkeypatch.setattr(noreplace, "_platform", lambda: "linux")
    with directory(root) as fd, pytest.raises(NoReplaceUnavailable):
        proven_primitive(fd)
    monkeypatch.setattr(noreplace, "_platform", lambda: "darwin")
    with directory(root) as fd, pytest.raises(NoReplaceUnavailable):
        proven_primitive(fd)


# --- mapeamento de errno (a chamada nativa é simulada; nada é movido) ------------------------


@pytest.fixture
def pretend_backend(monkeypatch: pytest.MonkeyPatch) -> None:
    """Faz ``proven_primitive`` aceitar o volume para exercitar o mapeamento de erros."""
    monkeypatch.setattr(noreplace, "proven_primitive", lambda fd: Primitive.RENAMEAT2)


@pytest.mark.usefixtures("pretend_backend")
@pytest.mark.parametrize(
    ("code", "expected"),
    [
        (errno.EXDEV, CrossDevice),
        (errno.ENOENT, NotFound),
        (errno.ENAMETOOLONG, NameTooLong),
        (errno.EINVAL, NoReplaceUnavailable),
        (errno.ENOTSUP, NoReplaceUnavailable),
        (errno.ENOSYS, NoReplaceUnavailable),
        (errno.EACCES, OSError),
    ],
)
def test_errno_mapping(
    root: Path, monkeypatch: pytest.MonkeyPatch, code: int, expected: type[Exception]
) -> None:
    monkeypatch.setattr(noreplace, "_call_rename", _fail_with(code))
    with directory(root) as fd, pytest.raises(expected) as error:
        rename_noreplace(fd, "a", fd, "b")
    if expected is OSError:
        assert error.value.errno == code  # type: ignore[attr-defined]


@pytest.mark.usefixtures("pretend_backend")
def test_eexist_is_reported_not_raised(root: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(noreplace, "_call_rename", _fail_with(errno.EEXIST))
    with directory(root) as fd:
        outcome = rename_noreplace(fd, "a", fd, "b")
    assert outcome.renamed is False


@pytest.mark.parametrize("code", [errno.ENOTSUP, errno.ENOSYS])
def test_not_supported_marks_the_device_and_stops_trying(
    root: Path, monkeypatch: pytest.MonkeyPatch, code: int
) -> None:
    calls: list[int] = []

    def refuse(*args: object, **kwargs: object) -> int:
        calls.append(1)
        ctypes.set_errno(code)
        return -1

    monkeypatch.setattr(noreplace, "_platform", lambda: "linux")
    monkeypatch.setattr(noreplace, "_library", lambda: type("Lib", (), {"renameat2": 1})())
    monkeypatch.setattr(noreplace, "_call_rename", refuse)
    with directory(root) as fd:
        with pytest.raises(NoReplaceUnavailable):
            rename_noreplace(fd, "a", fd, "b")
        assert len(calls) == 1
        with pytest.raises(NoReplaceUnavailable):  # agora recusa antes de tentar
            proven_primitive(fd)
        with pytest.raises(NoReplaceUnavailable):
            rename_noreplace(fd, "a", fd, "b")
        assert len(calls) == 1
    noreplace.clear_unsupported_cache()
    with directory(root) as fd:
        assert proven_primitive(fd) is Primitive.RENAMEAT2


def test_einval_is_refused_without_poisoning_the_volume(
    root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(noreplace, "_platform", lambda: "linux")
    monkeypatch.setattr(noreplace, "_library", lambda: type("Lib", (), {"renameat2": 1})())
    monkeypatch.setattr(noreplace, "_call_rename", _fail_with(errno.EINVAL))
    with directory(root) as fd:
        with pytest.raises(NoReplaceUnavailable):
            rename_noreplace(fd, "a", fd, "b")
        assert proven_primitive(fd) is Primitive.RENAMEAT2


# --- capability do volume (macOS) ------------------------------------------------------------


@pytest.mark.skipif(sys.platform != "darwin", reason="capability de volume é do macOS")
def test_volume_capabilities_are_readable_on_macos(root: Path) -> None:
    with directory(root) as fd:
        caps = volume_capabilities(fd)
    assert caps is not None
    assert caps.rename_excl_valid
    # o volume de teste local (APFS) anuncia RENAME_EXCL; um volume que não anunciasse faria
    # os testes de efeito pularem pela fixture require_noreplace
    assert caps.rename_excl


@pytest.mark.skipif(sys.platform == "darwin", reason="só fora do macOS")
def test_volume_capabilities_are_none_outside_macos(root: Path) -> None:
    with directory(root) as fd:
        assert volume_capabilities(fd) is None
