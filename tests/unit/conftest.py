"""Fixtures compartilhadas pelos testes unitários (só dados fictícios).

Reúne as fixtures de filesystem (TASK-104) e as de extração (TASK-105).
"""

from __future__ import annotations

import importlib.util
import os
import stat
import sys
from collections.abc import Callable, Iterator
from pathlib import Path
from types import ModuleType

import pytest

from osa.fs import noreplace
from osa.fs.errors import NoReplaceUnavailable

# --- filesystem -------------------------------------------------------------

Snapshot = dict[str, tuple[str, int, int, int]]


def snapshot_tree(path: Path) -> Snapshot:
    """Tipo, tamanho, inode e mtime de tudo sob ``path`` (sem seguir links)."""
    result: Snapshot = {}
    for current, directories, files in os.walk(path):
        for entry in (*directories, *files):
            full = Path(current, entry)
            info = full.lstat()
            kind = "d" if stat.S_ISDIR(info.st_mode) else "l" if stat.S_ISLNK(info.st_mode) else "f"
            result[str(full.relative_to(path))] = (
                kind,
                info.st_size,
                info.st_ino,
                info.st_mtime_ns,
            )
    return result


@pytest.fixture
def snapshot() -> Callable[[Path], Snapshot]:
    return snapshot_tree


@pytest.fixture(autouse=True)
def _fresh_noreplace_cache() -> Iterator[None]:
    noreplace.clear_unsupported_cache()
    yield
    noreplace.clear_unsupported_cache()


@pytest.fixture
def root(tmp_path: Path) -> Path:
    """Uma raiz de teste (``tmp_path/raiz``), já na forma canônica."""
    path = tmp_path.resolve() / "raiz"
    path.mkdir()
    return path


@pytest.fixture
def write() -> Callable[..., Path]:
    def _write(path: Path, content: bytes = b"conteudo ficticio") -> Path:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)
        return path

    return _write


@pytest.fixture
def require_noreplace(root: Path) -> None:
    """Pula o teste de efeito quando o volume de teste não comprova rename exclusivo."""
    fd = os.open(root, os.O_RDONLY | os.O_DIRECTORY)
    try:
        noreplace.proven_primitive(fd)
    except NoReplaceUnavailable as error:
        pytest.skip(f"volume de teste sem rename exclusivo comprovado: {error}")
    finally:
        os.close(fd)


# --- extração ---------------------------------------------------------------

FIXTURES_SCRIPT = Path(__file__).resolve().parents[1] / "fixtures" / "make_fixtures.py"


@pytest.fixture(scope="session")
def fixture_maker() -> ModuleType:
    """O módulo ``make_fixtures`` (tests/fixtures não é um pacote)."""
    spec = importlib.util.spec_from_file_location("osa_make_fixtures", FIXTURES_SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules["osa_make_fixtures"] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="session")
def docs(tmp_path_factory: pytest.TempPathFactory, fixture_maker: ModuleType) -> dict[str, Path]:
    """Todos os documentos fictícios, gerados uma vez por sessão em diretório temporário."""
    built: dict[str, Path] = fixture_maker.build(tmp_path_factory.mktemp("docs"))
    return built
