"""Higiene de ``src/osa/fs``: sem apagar, mover por API que substitui, truncar ou gravar.

O teste estático garante **higiene** (ausência dessas chamadas, sem lista de exceção). Ele não
prova ausência de overwrite: essa prova são os testes de colisão, de corrida e a suite native.
O rename exclusivo (ctypes) só é aceito em ``noreplace.py``, chamado direto com a flag
``_RENAME_*``; o valor da flag é provado pelos testes de ``noreplace``, não por este.
"""

from __future__ import annotations

import ast
import hashlib
import os
import shutil
from collections.abc import Callable
from pathlib import Path

import pytest

from osa.fs import archive
from osa.fs.archive import archive as archive_file
from osa.fs.archive import undo
from osa.fs.quarantine import QUARANTINE_DIR, ensure_quarantine, new_quarantine_file
from osa.fs.template import DepartmentFolder, ensure_owner_tree

FS_DIR = Path(archive.__file__).resolve().parent
QUARANTINE_FILE = "quarantine.py"
#: o único ``mkdir`` do pacote, por descritor, em ``open_beneath(create=True)``
MKDIR_FILE = "roots.py"

FORBIDDEN_CALLS = frozenset(
    {
        # apagar
        "unlink",
        "unlinkat",
        "remove",
        "rmdir",
        "removedirs",
        "rmtree",
        "send2trash",
        # mover ou ligar por API que substitui
        "move",
        "replace",
        "rename",
        "renames",
        "renameat",
        "link",
        "linkat",
        "symlink",
        "symlinkat",
        # truncar, gravar, copiar
        "truncate",
        "ftruncate",
        "write",
        "pwrite",
        "writev",
        "pwritev",
        "sendfile",
        "copy_file_range",
        "write_text",
        "write_bytes",
        "touch",
        "copy",
        "copy2",
        "copyfile",
        "copyfileobj",
        "copytree",
        # abrir fora de os.open (que tem as flags checadas)
        "open",
        "openat",
        "fdopen",
        # criar pasta fora de open_beneath
        "makedirs",
        # permissão e tempo
        "chmod",
        "fchmod",
        "lchmod",
        "chown",
        "fchown",
        "lchown",
        "utime",
        # processos
        "system",
        "popen",
        "Popen",
        "fork",
        "forkpty",
        # indireção dinâmica
        "eval",
        "exec",
        "__import__",
        "vars",
        "globals",
        "locals",
    }
)
#: ``execv``, ``execve``, ``spawnl``, ``posix_spawnp``...
FORBIDDEN_PREFIXES = ("exec", "spawn", "posix_spawn")
FORBIDDEN_MODULES = frozenset(
    {"shutil", "subprocess", "send2trash", "importlib", "tempfile", "multiprocessing"}
)
#: módulos de onde ``from X import <chamada proibida>`` é recusado
OS_MODULES = frozenset({"os", "posix", "nt"})
FORBIDDEN_FLAGS = frozenset({"O_TRUNC", "O_APPEND", "O_RDWR"})
#: só em ``quarantine.py`` e só como operandos diretos de ``O_CREAT | O_EXCL | ...``
WRITE_FLAGS = frozenset({"O_CREAT", "O_WRONLY"})
#: renames exclusivos: só em ``noreplace.py``, chamados direto com a flag ``_RENAME_*``
RENAME_BACKENDS = frozenset({"renameat2", "renameatx_np"})
NOREPLACE_FILE = "noreplace.py"
#: nomes que guardam a libc; ``libc[nome]`` contornaria a lista de chamadas
LIBC_NAMES = frozenset({"libc", "_libc", "lib"})
LIBC_FACTORIES = frozenset({"_library", "_load_libc", "CDLL"})


def _call_name(node: ast.Call) -> str | None:
    if isinstance(node.func, ast.Attribute):
        return node.func.attr
    if isinstance(node.func, ast.Name):
        return node.func.id
    return None


def _is_forbidden_name(name: str | None) -> bool:
    return name is not None and (name in FORBIDDEN_CALLS or name.startswith(FORBIDDEN_PREFIXES))


def _is_os_open(node: ast.Call) -> bool:
    func = node.func
    return (
        isinstance(func, ast.Attribute)
        and func.attr == "open"
        and isinstance(func.value, ast.Name)
        and func.value.id == "os"
    )


def _flag_name(node: ast.AST) -> str | None:
    if isinstance(node, ast.Attribute) and node.attr.startswith("O_"):
        return node.attr
    if isinstance(node, ast.Name) and node.id.startswith("O_"):
        return node.id
    return None


def _has_flag(node: ast.AST) -> bool:
    return any(_flag_name(item) for item in ast.walk(node))


def _open_flags(node: ast.Call) -> ast.expr | None:
    """O argumento de flags de ``os.open`` (posicional ou ``flags=``)."""
    if len(node.args) >= 2:
        return node.args[1]
    for keyword in node.keywords:
        if keyword.arg == "flags":
            return keyword.value
    return None


def _or_operands(chain: ast.expr) -> list[ast.expr]:
    """Os operandos diretos de uma cadeia ``a | b | c`` (sem descer em chamadas)."""
    if isinstance(chain, ast.BinOp) and isinstance(chain.op, ast.BitOr):
        return [*_or_operands(chain.left), *_or_operands(chain.right)]
    return [chain]


def _or_chains(tree: ast.AST) -> list[ast.expr]:
    """Expressões ``a | b | c`` maximais (a raiz de cada cadeia de ``|``)."""
    inner: set[int] = set()
    chains: list[ast.expr] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.BinOp) and isinstance(node.op, ast.BitOr):
            for child in (node.left, node.right):
                if isinstance(child, ast.BinOp) and isinstance(child.op, ast.BitOr):
                    inner.add(id(child))
    for node in ast.walk(tree):
        if isinstance(node, ast.BinOp) and isinstance(node.op, ast.BitOr) and id(node) not in inner:
            chains.append(node)
    return chains


def _exclusive_write_flags(tree: ast.AST) -> set[int]:
    """Nós de ``O_CREAT``/``O_WRONLY`` que são operandos diretos de ``O_CREAT | O_EXCL | ...``."""
    allowed: set[int] = set()
    for chain in _or_chains(tree):
        operands = _or_operands(chain)
        direct = {_flag_name(operand) for operand in operands}
        if {"O_CREAT", "O_EXCL"} <= direct:
            allowed.update(id(op) for op in operands if _flag_name(op) in WRITE_FLAGS)
    return allowed


def _allowed_rename_backends(tree: ast.AST) -> set[int]:
    """Referências a ``renameat2``/``renameatx_np`` permitidas em ``noreplace.py``.

    Só a chamada direta ``libc.renameat2(a, b, c, d, _RENAME_*)`` e o ajuste de
    ``.argtypes``/``.restype``; atribuir a função a um nome (e chamá-la com outra flag) não.
    """
    allowed: set[int] = set()
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr in RENAME_BACKENDS
            and len(node.args) == 5
            and not node.keywords
            and isinstance(node.args[4], ast.Name)
            and node.args[4].id.startswith("_RENAME_")
        ):
            allowed.add(id(node.func))
        if (
            isinstance(node, ast.Attribute)
            and node.attr in {"argtypes", "restype"}
            and isinstance(node.value, ast.Attribute)
            and node.value.attr in RENAME_BACKENDS
        ):
            allowed.add(id(node.value))
    return allowed


def _check_call(node: ast.Call, filename: str) -> list[str]:
    line = node.lineno
    name = _call_name(node)
    if _is_os_open(node):
        # os.open é a abertura por descritor; flag literal esconderia O_CREAT/O_TRUNC
        flags_arg = _open_flags(node)
        literals = [
            item
            for item in (ast.walk(flags_arg) if flags_arg is not None else ())
            if isinstance(item, ast.Constant) and isinstance(item.value, int)
        ]
        return [f"flag numérica em os.open (linha {line})"] if literals else []
    problems: list[str] = []
    if _is_forbidden_name(name):
        problems.append(f"chamada proibida: {name} (linha {line})")
    if name == "mkdir" and filename != MKDIR_FILE:
        problems.append(f"mkdir fora de {MKDIR_FILE} (linha {line})")
    if name == "getattr" and not (
        len(node.args) >= 2
        and isinstance(node.args[1], ast.Constant)
        and isinstance(node.args[1].value, str)
    ):
        problems.append(f"getattr com nome não literal (linha {line})")
    return problems


def _check_import(node: ast.Import | ast.ImportFrom) -> list[str]:
    line = node.lineno
    if isinstance(node, ast.Import):
        return [
            f"import proibido: {alias.name} (linha {line})"
            for alias in node.names
            if alias.name.split(".")[0] in FORBIDDEN_MODULES
        ]
    module = (node.module or "").split(".")[0]
    problems: list[str] = []
    if module in FORBIDDEN_MODULES:
        problems.append(f"import proibido: {node.module} (linha {line})")
    for alias in node.names:
        if module in OS_MODULES and _is_forbidden_name(alias.name):
            problems.append(f"from {module} import {alias.name} (linha {line})")
        if _is_forbidden_name(alias.name) and alias.asname not in {None, alias.name}:
            problems.append(f"alias de nome proibido: {alias.name} (linha {line})")
    return problems


def _is_libc_lookup(node: ast.Subscript) -> bool:
    """``libc[...]``, ``_library()[...]`` ou ``x.__dict__[...]``."""
    value = node.value
    if isinstance(value, ast.Name):
        return value.id in LIBC_NAMES
    if isinstance(value, ast.Attribute):
        return value.attr in LIBC_NAMES or value.attr == "__dict__"
    if isinstance(value, ast.Call):
        return _call_name(value) in LIBC_FACTORIES
    return False


def check_source(source: str, *, filename: str = "x.py") -> list[str]:
    """Violações de higiene em um módulo de ``src/osa/fs``; lista vazia se estiver limpo."""
    tree = ast.parse(source)
    problems: list[str] = []
    exclusive = _exclusive_write_flags(tree)
    renames = _allowed_rename_backends(tree) if filename == NOREPLACE_FILE else set()
    for node in ast.walk(tree):
        line = getattr(node, "lineno", 0)
        if isinstance(node, ast.Call):
            problems += _check_call(node, filename)
        elif isinstance(node, ast.Import | ast.ImportFrom):
            problems += _check_import(node)
        elif isinstance(node, ast.Subscript) and _is_libc_lookup(node):
            problems.append(f"busca dinâmica na libc ou em __dict__ (linha {line})")
        elif (
            isinstance(node, ast.Constant)
            and isinstance(node.value, str)
            and _is_forbidden_name(node.value)
        ):
            problems.append(f"nome proibido como texto: {node.value!r} (linha {line})")
        elif isinstance(node, ast.UnaryOp | ast.BinOp) and _has_flag(node):
            # flags só se combinam com ``|``: ``& ~``, ``-`` e ``^`` tirariam O_EXCL
            if not (isinstance(node, ast.BinOp) and isinstance(node.op, ast.BitOr)):
                problems.append(f"flag combinada fora de | (linha {line})")
        if isinstance(node, ast.Attribute):
            if node.attr == "__dict__":
                problems.append(f"acesso a __dict__ (linha {line})")
            if node.attr in RENAME_BACKENDS and id(node) not in renames:
                problems.append(
                    f"{node.attr} fora da chamada direta com _RENAME_* em {NOREPLACE_FILE} "
                    f"(linha {line})"
                )
        flag = _flag_name(node)
        if flag in FORBIDDEN_FLAGS:
            problems.append(f"flag proibida: {flag} (linha {line})")
        if flag in WRITE_FLAGS:
            if filename != QUARANTINE_FILE:
                problems.append(f"flag de escrita fora da quarentena: {flag} (linha {line})")
            elif id(node) not in exclusive:
                problems.append(f"{flag} fora de O_CREAT | O_EXCL | ... (linha {line})")
    return problems


# --- o verificador não é decorativo ----------------------------------------------------------


@pytest.mark.parametrize(
    "source",
    [
        "import os\nos.unlink(p)",
        "import os\nos.remove(p)",
        "import os\nos.rmdir(p)",
        "import os\nos.removedirs(p)",
        "import os\nos.rename(a, b)",
        "import os\nos.replace(a, b)",
        "from pathlib import Path\nPath(a).replace(b)",
        "from pathlib import Path\nPath(a).unlink()",
        "from pathlib import Path\nPath(a).write_text('x')",
        "from pathlib import Path\nPath(a).touch()",
        "import shutil",
        "from shutil import move",
        "import shutil as s\ns.rmtree(p)",
        "import subprocess",
        "from os import unlink",
        "from os import remove as r",
        "from os import rename",
        "getattr(os, 'unlink')(p)",
        "x = 'rmtree'",
        "open(p, 'wb')",
        "import os\nos.truncate(p, 0)",
        "import os\nos.link(a, b)",
        "import os\nos.symlink(a, b)",
        "import os\nos.write(fd, b'x')",
        "import os\nos.open(p, os.O_WRONLY | os.O_TRUNC)",
        "import os\nos.open(p, os.O_RDWR)",
        "import os\nos.open(p, os.O_WRONLY | os.O_APPEND)",
        "import os\nos.open(p, os.O_CREAT)",
        "import os\nos.open(p, os.O_CREAT | os.O_WRONLY)",
        "from os import O_CREAT\nx = O_CREAT | 1",
        "import os\nos.chmod(p, 0o777)",
        "eval('1')",
    ],
)
def test_checker_flags_bad_code(source: str) -> None:
    assert check_source(source), source


@pytest.mark.parametrize(
    "source",
    [
        "import os\nF = os.O_CREAT\nos.open(p, F | os.O_EXCL, dir_fd=fd)",
        "import os\nos.open(p, os.O_EXCL | g(os.O_CREAT), dir_fd=fd)",
        "import os\nA = os.O_CREAT | os.O_WRONLY\nB = os.O_EXCL | os.O_NOFOLLOW",
        "import os\nos.open(p, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_TRUNC)",
        "import os\nos.open(p, 0o1001)",
        "import os\nos.open(p, os.O_RDONLY | 0x200)",
        "import os\nos.open(p, flags=0o1001)",
        "import os\nos.makedirs(p)",
        "from os import makedirs",
        "from posix import unlink",
        "getattr(os, 'unl' + 'ink')(p)",
        "getattr(os, nome)(p)",
        "import os\nos.mkdir(n, 0o755, dir_fd=fd)",
        "from pathlib import Path\nPath(p).mkdir()",
    ],
)
def test_checker_flags_bad_code_even_in_the_creating_modules(source: str) -> None:
    """Nem a quarentena escapa: O_CREAT só dentro de ``|`` com O_EXCL, sem literal, sem mkdir."""
    assert check_source(source, filename=QUARANTINE_FILE), source


@pytest.mark.parametrize(
    "source",
    [
        "libc.unlinkat(fd, p, 0)",
        "libc.renameat(a, b, c, d)",
        "libc.linkat(a, b, c, d, 0)",
        "libc.openat(fd, p, 0o1601)",
        "libc['rename'](a, b)",
        "libc[name](a, b)",
        "_library()[name](a, b)",
        "os.__dict__[k](a, b)",
        "vars(os)['unl' + 'ink'](p)",
        "import os\nos.pwritev(fd, [b'x'], 0)",
        "import os\nos.copy_file_range(a, b, 10)",
        "import os\nos.fchmod(fd, 0o777)",
        "import os\nos.execv(p, [p])",
        "import os\nos.spawnl(0, p, p)",
        "import os\nos.fork()",
        "from os import execve",
        "import multiprocessing",
        "libc.renameat2(a, b, c, d, _RENAME_NOREPLACE)",  # fora de noreplace.py
    ],
)
def test_checker_flags_native_calls_indirection_and_processes(source: str) -> None:
    assert check_source(source), source


@pytest.mark.parametrize(
    "source",
    [
        "libc.renameatx_np(a, b, c, d, 0)",
        "libc.renameat2(a, b, c, d, flag)",
        "libc.renameat2(a, b, c, d, _RENAME_NOREPLACE, 0)",
        "function = libc.renameat2\nfunction(a, b, c, d, 0)",
        "callback(libc.renameatx_np)",
    ],
)
def test_checker_requires_direct_exclusive_rename_calls(source: str) -> None:
    assert check_source(source, filename=NOREPLACE_FILE), source


def test_checker_accepts_the_direct_exclusive_rename_call() -> None:
    source = (
        "libc.renameatx_np.argtypes = T\n"
        "libc.renameatx_np.restype = R\n"
        "libc.renameatx_np(a, b, c, d, _RENAME_EXCL)\n"
        "libc.renameat2(a, b, c, d, _RENAME_NOREPLACE)"
    )
    assert check_source(source, filename=NOREPLACE_FILE) == []


@pytest.mark.parametrize(
    "source",
    [
        "import os\nos.open(p, os.O_WRONLY | os.O_NOFOLLOW, dir_fd=fd)",
        "import os\nos.open(p, os.O_WRONLY | os.O_CREAT, dir_fd=fd)",
        "import os\nos.open(p, (os.O_CREAT | os.O_EXCL) & ~os.O_EXCL, dir_fd=fd)",
        "import os\nF = os.O_WRONLY | os.O_CREAT | os.O_EXCL\nos.open(p, F - os.O_EXCL)",
        "import os\nos.open(p, os.O_CREAT | os.O_EXCL ^ os.O_EXCL)",
    ],
)
def test_checker_requires_exclusive_create_for_every_write_flag(source: str) -> None:
    assert check_source(source, filename=QUARANTINE_FILE), source


def test_checker_allows_mkdir_only_in_the_roots_module() -> None:
    source = "import os\nos.mkdir(n, 0o755, dir_fd=fd)"
    assert check_source(source, filename=MKDIR_FILE) == []
    for other in ("archive.py", "template.py", QUARANTINE_FILE, "x.py"):
        assert check_source(source, filename=other), other
    assert check_source("import os\nos.makedirs(p)", filename=MKDIR_FILE)


def test_checker_allows_exclusive_create_only_in_the_quarantine_module() -> None:
    source = "import os\nos.open(p, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)"
    assert check_source(source, filename=QUARANTINE_FILE) == []
    assert check_source(source, filename="archive.py")  # fora da quarentena


@pytest.mark.parametrize(
    "source",
    [
        "import os\nfd = os.open(p, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)",
        "import os\nos.listdir(fd)\nos.stat(n, dir_fd=fd, follow_symlinks=False)",
        "text = name.casefold()",
        "import os\nF = getattr(os, 'O_NOFOLLOW_ANY', 0)",
        "import os\nos.open(p, os.O_RDONLY | F, 0o600, dir_fd=fd)",
    ],
)
def test_checker_accepts_good_code(source: str) -> None:
    assert check_source(source) == []


# --- o código real ---------------------------------------------------------------------------


def _fs_sources() -> list[Path]:
    return sorted(FS_DIR.glob("*.py"))


def test_there_are_fs_modules_to_check() -> None:
    names = {path.name for path in _fs_sources()}
    assert {
        "roots.py",
        "template.py",
        "archive.py",
        "noreplace.py",
        "names.py",
        "quarantine.py",
        "errors.py",
    } <= names


@pytest.mark.parametrize("path", _fs_sources(), ids=lambda p: p.name)
def test_fs_modules_have_no_forbidden_calls_flags_or_imports(path: Path) -> None:
    assert check_source(path.read_text(), filename=path.name) == []


def test_os_rename_does_not_appear_in_fs_at_all() -> None:
    for path in _fs_sources():
        text = path.read_text()
        assert "os.rename" not in text.replace("os.renameat", "")
        assert "os.replace" not in text
        assert "shutil" not in text


def test_only_the_quarantine_module_writes_flags() -> None:
    for path in _fs_sources():
        has_creat = "O_CREAT" in path.read_text()
        assert has_creat == (path.name == QUARANTINE_FILE)


# --- dinâmico: nenhuma API de apagar ou substituir é chamada nas operações reais -------------


@pytest.mark.usefixtures("require_noreplace")
def test_real_operations_never_call_delete_or_replace_apis(
    root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:

    def forbidden(name: str) -> Callable[..., None]:
        def call(*args: object, **kwargs: object) -> None:
            raise AssertionError(f"{name} chamado em src/osa/fs")

        return call

    with monkeypatch.context() as patch:
        for name in (
            "unlink",
            "remove",
            "rmdir",
            "removedirs",
            "replace",
            "rename",
            "renames",
            "truncate",
            "ftruncate",
            "link",
            "symlink",
            "utime",
            "chmod",
        ):
            patch.setattr(os, name, forbidden(f"os.{name}"))
        for name in ("rmtree", "move", "copy", "copy2", "copyfile", "copytree"):
            patch.setattr(shutil, name, forbidden(f"shutil.{name}"))
        ensure_quarantine(root, effects_enabled=True)
        ensure_owner_tree(
            root, "Alfa", (DepartmentFolder("Fiscal", usa_ano=True),), 2026, effects_enabled=True
        )
        with new_quarantine_file(root, effects_enabled=True) as upload:
            assert upload.fd is not None
            os.write(upload.fd, b"conteudo ficticio")
        result = archive_file(
            root, upload.rel, "Alfa/Fiscal/2026", "nota.pdf", effects_enabled=True
        )
        digest = hashlib.sha256(b"conteudo ficticio").hexdigest()
        undone = undo(result.record(root_id=1, sha256=digest), effects_enabled=True)
        assert undone.dest_rel == upload.rel
    assert (root / upload.rel).read_bytes() == b"conteudo ficticio"
    assert (root / QUARANTINE_DIR).is_dir()
