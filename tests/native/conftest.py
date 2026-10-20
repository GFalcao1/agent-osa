"""Suite native: exige um share SMB de teste montado no macOS (``OSA_TEST_SHARE_ROOT``).

Sem a variável, ou com um caminho que não é um mount ``smbfs``, todo teste é pulado com o
motivo (visível em ``-ra``) e a suite native continua PENDENTE: skip não é aprovação. Só
arquivos fictícios; nenhum teste apaga nada (a limpeza do que sobrar em
``osa-native-<uuid>/`` é manual, fora do código do produto).

``OSA_TEST_SHARE_ALLOW_ANY_FS=1`` existe só para ensaiar os testes num volume local; marca o
resultado como "NÃO PROVA SMB" e nunca fecha o gate.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

import pytest
from mounts import parse_mount

from osa.fs import noreplace

ENV_ROOT = "OSA_TEST_SHARE_ROOT"
ENV_ALLOW_ANY = "OSA_TEST_SHARE_ALLOW_ANY_FS"
ENV_SERVER = "OSA_TEST_SHARE_SERVER_INFO"
# O conftest raiz limpa ``OSA_*`` a cada teste: as variáveis são capturadas no import.
_RAW_ROOT = os.environ.get(ENV_ROOT)
_ALLOW_ANY = os.environ.get(ENV_ALLOW_ANY) == "1"
_SERVER_INFO = os.environ.get(ENV_SERVER, "")

_HERE = Path(__file__).resolve().parent
_VOL_FMT = {
    "SYMBOLICLINKS": 0x2,
    "HARDLINKS": 0x4,
    "CASE_SENSITIVE": 0x100,
    "CASE_PRESERVING": 0x200,
}


@dataclass
class ShareEnv:
    session_dir: Path
    fstype: str
    proves_smb: bool
    notes: list[str] = field(default_factory=list)


@dataclass
class _State:
    pending: str | None = None  # motivo pelo qual o gate native segue pendente
    env: ShareEnv | None = None


_STATE = _State()


def pytest_collection_modifyitems(items: list[pytest.Item]) -> None:
    for item in items:
        if _HERE in Path(str(item.fspath)).resolve().parents:
            item.add_marker(pytest.mark.native)


def _run(*command: str) -> str:
    try:
        done = subprocess.run(command, capture_output=True, text=True, timeout=20, check=False)
    except (OSError, subprocess.SubprocessError):
        return ""
    return done.stdout.strip()


def mount_type(path: Path) -> tuple[str, str] | None:
    """Tipo de filesystem e ponto de montagem de ``path`` segundo ``mount``."""
    return parse_mount(_run(shutil.which("mount") or "/sbin/mount"), path)


def _describe(session_dir: Path, fstype: str, proves_smb: bool) -> list[str]:
    lines = [f"sistema: {sys.platform} {_run('sw_vers', '-productVersion')}".strip()]
    lines.append(f"filesystem do share: {fstype}")
    lines.append(f"servidor (informado em {ENV_SERVER}): {_SERVER_INFO or 'não informado'}")
    if not proves_smb:
        lines.append(f"ATENÇÃO: {ENV_ALLOW_ANY}=1 com filesystem {fstype}: NÃO PROVA SMB")
    if fstype == "smbfs" and shutil.which("smbutil"):
        for line in _run("smbutil", "statshares", "-a").splitlines():
            if any(key in line for key in ("SMB_VERSION", "SIGNING", "ATTRIBUTES")):
                lines.append(f"smbutil: {line.strip()}")
    fd = os.open(session_dir, os.O_RDONLY | os.O_DIRECTORY)
    try:
        caps = noreplace.volume_capabilities(fd)
        if caps is None:
            lines.append("capabilities do volume: indisponíveis neste sistema")
        else:
            lines.append(
                f"VOL_CAP_INT_RENAME_EXCL: ligado={caps.rename_excl} "
                f"válido={caps.rename_excl_valid}"
            )
            for label, bit in _VOL_FMT.items():
                valid = bool(caps.raw_format_valid & bit)
                lines.append(f"VOL_CAP_FMT_{label}: {bool(caps.raw_format & bit)} (válido={valid})")
        lines.append(f"NAME_MAX (pathconf): {os.pathconf(session_dir, 'PC_NAME_MAX')}")
        lines.append(f"PATH_MAX (pathconf): {os.pathconf(session_dir, 'PC_PATH_MAX')}")
    finally:
        os.close(fd)
    return lines


@pytest.fixture(scope="session")
def share() -> ShareEnv:
    """O share de teste validado, com a pasta de sessão ``osa-native-<uuid>/``."""
    if not _RAW_ROOT:
        reason = f"{ENV_ROOT} não definido: suite native PENDENTE (precisa de share SMB de teste)"
        _STATE.pending = reason
        pytest.skip(reason)
    base = Path(_RAW_ROOT)

    def refuse(reason: str) -> None:
        _STATE.pending = f"{reason}: suite native PENDENTE"
        pytest.skip(_STATE.pending)

    if not base.is_absolute() or not base.is_dir() or os.path.realpath(base) != str(base):
        refuse(f"{ENV_ROOT} deve ser um diretório existente, absoluto e sem link simbólico")
    mount = mount_type(base)
    if mount is None:
        refuse(f"não foi possível identificar o filesystem de {ENV_ROOT}")
    assert mount is not None
    fstype = mount[0]
    proves_smb = fstype == "smbfs"
    if not proves_smb and not _ALLOW_ANY:
        refuse(f"{ENV_ROOT} está em '{fstype}', não em smbfs")
    session_dir = base / f"osa-native-{uuid.uuid4().hex}"
    os.mkdir(session_dir)
    env = ShareEnv(session_dir, fstype, proves_smb)
    env.notes.extend(_describe(session_dir, fstype, proves_smb))
    _STATE.env = env
    _STATE.pending = None if proves_smb else "executada em filesystem que não é smbfs"
    return env


@pytest.fixture
def work(share: ShareEnv) -> Path:
    """Uma raiz nova (o ``root`` do produto) dentro do share, para cada teste."""
    path = share.session_dir / uuid.uuid4().hex[:8]
    os.mkdir(path)
    return path


@pytest.fixture
def note(share: ShareEnv, request: pytest.FixtureRequest) -> Callable[[str], None]:
    """Registra uma medição no resumo final (vai para o registro de execução da task)."""

    def add(text: str) -> None:
        share.notes.append(f"{request.node.name}: {text}")

    return add


def pytest_terminal_summary(terminalreporter: pytest.TerminalReporter) -> None:
    write = terminalreporter.write_line
    if _STATE.env is not None:
        write("")
        write("== suite native: ambiente e medições ==")
        for line in _STATE.env.notes:
            write(f"  {line}")
        if _STATE.pending:
            write(f"  RESULTADO: {_STATE.pending}; o gate native continua PENDENTE")
    elif _STATE.pending:
        write("")
        write(f"== suite native: {_STATE.pending} ==")
