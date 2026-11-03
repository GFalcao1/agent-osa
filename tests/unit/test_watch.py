"""Pasta monitorada sem banco: a regra de estabilidade, a listagem e a poda no scan (TASK-111).

O ciclo completo com índice, correio e reinício fica em ``tests/integration/test_watch_flow.py``.
"""

from __future__ import annotations

import os
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from osa.db.models import WatchEntry
from osa.domain.document import WatchState
from osa.domain.errors import NotFound, UnsafePath
from osa.fs.names import QUARANTINE_DIR
from osa.fs.walk import FileEntry, iter_files, list_folder
from osa.worker.watch import WatchOptions, is_stable, next_cycles

NOW = datetime(2026, 11, 3, 12, 0, tzinfo=UTC)
OPTIONS = WatchOptions(stable_cycles=2, min_age_s=60)


def ns(moment: datetime) -> int:
    return int(moment.timestamp() * 1e9)


def entry(size: int = 10, mtime: datetime = NOW, cycles: int = 0) -> WatchEntry:
    return WatchEntry(
        watched_folder_id=1, caminho="entrada/a.pdf", tamanho=size, mtime_ns=ns(mtime),
        ciclos_estaveis=cycles, estado=WatchState.DETECTADO.value,
    )  # fmt: skip


# --- regra de estabilidade --------------------------------------------------------------------


def test_cycles_count_up_while_size_and_mtime_repeat() -> None:
    old = NOW - timedelta(minutes=5)
    assert next_cycles(entry(10, old, 0), 10, ns(old)) == 1
    assert next_cycles(entry(10, old, 1), 10, ns(old)) == 2


@pytest.mark.parametrize(
    ("size", "mtime"), [(11, NOW - timedelta(minutes=5)), (10, NOW - timedelta(minutes=4))]
)
def test_any_change_of_size_or_mtime_starts_over(size: int, mtime: datetime) -> None:
    assert next_cycles(entry(10, NOW - timedelta(minutes=5), 7), size, ns(mtime)) == 0


def test_stable_needs_the_cycles_and_the_age() -> None:
    old, fresh = ns(NOW - timedelta(seconds=61)), ns(NOW - timedelta(seconds=30))
    assert is_stable(2, old, NOW, OPTIONS)
    assert not is_stable(1, old, NOW, OPTIONS)  # two unchanged cycles are required
    assert not is_stable(5, fresh, NOW, OPTIONS)  # younger than the minimum age
    assert is_stable(1, fresh, NOW, WatchOptions(stable_cycles=1, min_age_s=0))


def test_first_sighting_is_never_stable() -> None:
    # the first cycle only records the file: zero cycles unchanged
    assert not is_stable(0, ns(NOW - timedelta(days=1)), NOW, WatchOptions(1, 0))


# --- listagem da pasta ------------------------------------------------------------------------


def put(path: Path, data: bytes = b"%PDF-1.4 ficticio") -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    return path


@pytest.fixture
def root(tmp_path: Path) -> Path:
    base = tmp_path.resolve() / "raiz"
    put(base / "entrada" / "b.pdf")
    put(base / "entrada" / "a.pdf", b"%PDF maior que o outro")
    put(base / "entrada" / "._a.pdf")
    put(base / "entrada" / ".~lock.planilha.xlsx#")
    put(base / "entrada" / "sub" / "dentro.pdf")
    put(base / "Alfa" / "01 Fiscal" / "nota.pdf")
    put(base / QUARANTINE_DIR / "q.pdf")
    return base


def test_list_folder_sees_only_regular_files_directly_inside_by_stat(root: Path) -> None:
    listed = list_folder(root, "entrada")

    assert [e.relative for e in listed] == ["entrada/a.pdf", "entrada/b.pdf"]
    assert all(isinstance(e, FileEntry) and e.mtime_ns > 0 for e in listed)
    assert listed[0].size == len(b"%PDF maior que o outro")


def test_list_folder_ignores_links_and_refuses_a_linked_folder(root: Path, tmp_path: Path) -> None:
    outside = put(tmp_path / "fora" / "segredo.pdf")
    os.symlink(outside, root / "entrada" / "link.pdf")
    os.symlink(outside.parent, root / "atalho")

    assert "entrada/link.pdf" not in [e.relative for e in list_folder(root, "entrada")]
    with pytest.raises(UnsafePath):
        list_folder(root, "atalho")


def test_list_folder_of_a_missing_folder_is_not_found(root: Path) -> None:
    with pytest.raises(NotFound):
        list_folder(root, "nao-existe")


def test_the_scan_walk_leaves_watched_folders_out_whole(root: Path) -> None:
    everything = [v.relative for v in iter_files(root) if isinstance(v, FileEntry)]
    pruned = [v.relative for v in iter_files(root, skip=["ENTRADA"]) if isinstance(v, FileEntry)]

    assert "entrada/a.pdf" in everything and "entrada/sub/dentro.pdf" in everything
    assert pruned == ["Alfa/01 Fiscal/nota.pdf"]  # case-insensitive, like the share


def test_watch_states_match_the_migration() -> None:
    import importlib.util

    path = (
        Path(__file__).resolve().parents[2] / "src/osa/db/migrations/versions/0007_watch_entries.py"
    )
    spec = importlib.util.spec_from_file_location("migration_0007", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    assert tuple(module.WATCH_STATES) == tuple(state.value for state in WatchState)


# --- avisos do worker ---------------------------------------------------------------------------


class _Bot:
    def __init__(self) -> None:
        self.sent: list[tuple[int, str]] = []

    def send(self, chat_id: int, text: str, buttons: object = ()) -> int:
        self.sent.append((chat_id, text))
        return len(self.sent)


class _Courier:
    def __init__(self) -> None:
        from osa.telegram.ports import Actor

        self.reviewer = Actor(2, 2002, "Revisora", "reviewer", 10)
        self.admin = Actor(1, 1001, "Admin", "admin", None)

    def users_of_department(self, department_id: int) -> list[object]:
        return [self.reviewer] if department_id == 10 else []

    def admins(self) -> list[object]:
        return [self.admin]


def _notifier(group: int | None) -> tuple[object, _Bot]:
    from types import SimpleNamespace

    from osa.worker.app import TelegramNotifier

    bot = _Bot()
    ctx = SimpleNamespace(reviewers_chat_id=group, courier=_Courier(), bot=bot)
    return TelegramNotifier(ctx), bot


def test_notices_go_to_the_group_else_to_the_administrators() -> None:
    from osa.worker.watch import Notice

    with_group, bot = _notifier(-100)
    assert with_group.send(Notice("nao_suportado", "entrada/x.ods", "formato não suportado"))  # type: ignore[attr-defined]
    assert [chat for chat, _ in bot.sent] == [-100]
    assert "entrada/x.ods" in bot.sent[0][1] and "não suportado" in bot.sent[0][1]

    without, bot = _notifier(None)
    assert without.send(Notice("sensivel", "entrada/c.pfx"))  # type: ignore[attr-defined]
    assert [chat for chat, _ in bot.sent] == [1001]
    assert "não foi lido" in bot.sent[0][1]


def test_a_notice_that_could_not_be_sent_says_so_and_stays_in_the_outbox() -> None:
    from osa.worker.watch import Notice

    notifier, bot = _notifier(-100)

    def broken(chat_id: int, text: str, buttons: object = ()) -> int:
        raise RuntimeError("telegram fora do ar")

    bot.send = broken  # type: ignore[method-assign]
    assert notifier.send(Notice("nao_suportado", "entrada/x.ods", "m")) is False  # type: ignore[attr-defined]


@pytest.mark.parametrize(
    "name",
    ["~$contrato.docx", "nota.pdf.part", "baixando.crdownload", "copia.tmp", "Thumbs.db",
     "desktop.ini", ".oculto.pdf"],
)  # fmt: skip
def test_temporary_and_system_names_are_never_listed(root: Path, name: str) -> None:
    put(root / "entrada" / name)

    assert f"entrada/{name}" not in [e.relative for e in list_folder(root, "entrada")]


def test_an_unreadable_name_is_reported_not_taken_for_gone(
    root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    real = os.stat

    def flaky(path: object, *args: object, **kwargs: object) -> os.stat_result:
        if path == "b.pdf":
            raise PermissionError(13, "negado")
        return real(path, *args, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(os, "stat", flaky)
    unreadable: list[str] = []
    listed = list_folder(root, "entrada", unreadable=unreadable)

    assert [e.relative for e in listed] == ["entrada/a.pdf"] and unreadable == ["entrada/b.pdf"]
