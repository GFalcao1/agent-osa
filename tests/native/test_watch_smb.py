"""Prova native da pasta monitorada contra um share SMB de teste (TASK-111).

Roda só com ``OSA_TEST_SHARE_ROOT`` num mount ``smbfs`` (ver ``conftest.py``); sem isso, todo
teste é pulado com o motivo e a suite fica PENDENTE. Só arquivos fictícios; nenhum teste apaga.

O que o share pode fazer diferente de um volume local: atualizar tamanho e mtime com atraso
(cache de atributos do cliente SMB), mostrar o arquivo antes de a cópia terminar e gravar em
pedaços grandes. Aqui um processo à parte copia um arquivo grande devagar para a pasta
monitorada enquanto o ciclo de observação roda; o arquivo não pode ser dado como estável
antes de a cópia terminar.

Limite deste teste: o escritor roda na mesma máquina e no mesmo mount que o poller, com
appends simples. Um cliente Windows (Explorer/CopyFileEx) pode pré-alocar o tamanho final e só
atualizar o mtime ao fechar; isso só se prova copiando de outra máquina para o share, com o
worker real (a abertura com ``O_SHLOCK`` em ``LocalFs.fingerprint`` é a defesa a medir ali).
"""

from __future__ import annotations

import multiprocessing
import time
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path

import pytest

from osa.db.models import WatchEntry
from osa.domain.document import WatchState
from osa.fs.walk import list_folder
from osa.worker.watch import WatchOptions, is_stable, next_cycles

pytestmark = pytest.mark.native

Note = Callable[[str], None]
CHUNK = b"%" + b"0" * (256 * 1024 - 1)  # 256 KiB fictícios
CHUNKS = 40  # ~10 MB, um pedaço a cada 0,25 s: ~10 s de cópia


def _slow_copy(target: str, done: str) -> None:
    with open(target, "wb") as handle:
        for _ in range(CHUNKS):
            handle.write(CHUNK)
            handle.flush()
            time.sleep(0.25)
    Path(done).write_text("ok")


def test_a_big_file_copied_while_polling_is_not_stable_until_the_copy_ends(
    work: Path, note: Note
) -> None:
    inbox = work / "entrada"
    inbox.mkdir()
    marker = work / "copia-terminou"
    copier = multiprocessing.get_context("spawn").Process(
        target=_slow_copy, args=(str(inbox / "grande.pdf"), str(marker))
    )
    options = WatchOptions(stable_cycles=2, min_age_s=0)
    entry: WatchEntry | None = None
    stable_at: float | None = None
    started = time.monotonic()
    copier.start()
    try:
        while time.monotonic() - started < 60:
            listed = [e for e in list_folder(work, "entrada") if e.name == "grande.pdf"]
            if listed:
                item = listed[0]
                now = datetime.now(UTC)
                if entry is None:
                    entry = WatchEntry(
                        watched_folder_id=1, caminho=item.relative, tamanho=item.size,
                        mtime_ns=item.mtime_ns, ciclos_estaveis=0,
                        estado=WatchState.DETECTADO.value,
                    )  # fmt: skip
                else:
                    cycles = next_cycles(entry, item.size, item.mtime_ns)
                    entry.tamanho, entry.mtime_ns, entry.ciclos_estaveis = (
                        item.size, item.mtime_ns, cycles,
                    )  # fmt: skip
                    if is_stable(cycles, item.mtime_ns, now, options):
                        stable_at = time.monotonic()
                        break
            time.sleep(0.5)  # a fast poll: the real one is 30-60 s
    finally:
        copier.join(timeout=60)

    assert stable_at is not None, "o arquivo nunca ficou estável"
    assert marker.exists(), "estável antes de a cópia terminar"
    assert entry is not None and entry.tamanho == len(CHUNK) * CHUNKS
    note(f"cópia de {entry.tamanho} bytes estável {stable_at - started:.1f} s após o início")
