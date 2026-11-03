"""Pasta monitorada e worker (TASK-111) contra o PostgreSQL descartável e uma raiz em ``tmp_path``.

Extração e modelo são falsos (o texto vem de um mapa por nome); listagem, estabilidade,
índice, evidência de dono, revisão, arquivamento por rename, auditoria e reinício são reais.
O relógio é injetado: cada ciclo avança o tempo, e os arquivos são "envelhecidos" com
``os.utime`` (código de teste) para passar da idade mínima sem esperar.
"""

from __future__ import annotations

import os
from collections.abc import Iterator, Sequence
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import create_engine, func, select
from sqlalchemy.engine import URL
from sqlalchemy.orm import Session, sessionmaker

from osa.admin import services
from osa.core.settings import Settings
from osa.courier.ports import LocalContent, LocalFs
from osa.courier.store import SqlStore
from osa.db.models import ArchiveOperation, AuditEvent, Document, ReviewCase, Root, WatchEntry
from osa.domain.document import DocumentState, Origin, WatchState
from osa.extract.model import DEFAULT_SENSITIVE_EXTENSIONS, ExtractedContent, ExtractLimits
from osa.fs import noreplace
from osa.fs.errors import NoReplaceUnavailable
from osa.llm.envelopes import ClassificationEnvelope
from osa.llm.provider import ModelUnavailable
from osa.worker.app import Worker
from osa.worker.scan import scan_root
from osa.worker.watch import Notice, WatchOptions, watch_cycle

T0 = datetime(2026, 11, 3, 10, 0, tzinfo=UTC)
ACTOR = "worker"


def make_cnpj(base12: str) -> str:
    def digit(digits: str, weights: list[int]) -> str:
        rest = sum(int(d) * w for d, w in zip(digits, weights, strict=True)) % 11
        return "0" if rest < 2 else str(11 - rest)

    first = digit(base12, [5, 4, 3, 2, 9, 8, 7, 6, 5, 4, 3, 2])
    return base12 + first + digit(base12 + first, [6, 5, 4, 3, 2, 9, 8, 7, 6, 5, 4, 3, 2])


CNPJ_ALFA = make_cnpj("112223330001")
TEXTS = {
    "nota-alfa.pdf": f"Nota fiscal de serviço. Tomador Alfa Comércio, CNPJ {CNPJ_ALFA}.",
    "nota-ambigua.pdf": "Nota fiscal emitida para Alfa e para Beta, ambas tomadoras.",
    "nota-sem-dono.pdf": "Nota fiscal de serviço sem identificação do tomador.",
}


def mounted(_path: Path) -> bool:
    return True


def pdf(label: str) -> bytes:
    return f"%PDF-1.4\n% {label}\n%%EOF\n".encode()


class FakeContent(LocalContent):
    """Real class by name, sniff and hash (each read recorded); extraction from ``TEXTS``."""

    def __init__(self) -> None:
        super().__init__(ExtractLimits(sensitive_extensions=DEFAULT_SENSITIVE_EXTENSIONS))
        self.hashed: list[str] = []
        self.extracted: list[str] = []

    def sha256(self, path: Path) -> str:
        self.hashed.append(path.name)
        return super().sha256(path)

    def extract(self, path: Path) -> ExtractedContent:
        self.extracted.append(path.name)
        return ExtractedContent(
            text=TEXTS.get(path.name, f"Documento fictício {path.name}"), images=(),
            pages_total=1, truncated=False, method="pdf_text",
        )  # fmt: skip


@dataclass
class FakeLlm:
    fail: BaseException | None = None
    calls: int = 0

    def classify(self, content: ExtractedContent, departments: Sequence[Any]) -> Any:
        self.calls += 1
        if self.fail is not None:
            raise self.fail
        return ClassificationEnvelope(
            departamento="Fiscal", departamento_alternativo=None, tipo_documental="nota fiscal",
            tags=["nota"], resumo="Nota fiscal fictícia.", datas=[date(2026, 1, 31)],
            ano_referencia=2026, precisa_revisao=False, motivo_revisao=None,
        )  # fmt: skip


@dataclass
class Watch:
    sessions: sessionmaker[Session]
    root: Path
    folder_id: int
    root_id: int
    content: FakeContent
    llm: FakeLlm
    effects: bool = True
    now: datetime = T0
    notices: list[Notice] = field(default_factory=list)

    @property
    def inbox(self) -> Path:
        return self.root / "entrada"

    def drop(self, name: str, data: bytes | None = None, *, age_s: int = 600) -> Path:
        """A file appears in the watched folder, written ``age_s`` seconds ago."""
        path = self.inbox / name
        path.write_bytes(data if data is not None else pdf(name))
        self.age(path, age_s)
        return path

    def age(self, path: Path, age_s: int = 600) -> None:
        stamp = (self.now - timedelta(seconds=age_s)).timestamp()
        os.utime(path, (stamp, stamp))

    def cycle(self, *, llm: FakeLlm | None = None, min_age_s: float = 60) -> Any:
        self.now += timedelta(seconds=45)
        with self.sessions() as session:
            summary = watch_cycle(
                self.folder_id, store=SqlStore(session), fs=LocalFs(), content=self.content,
                llm=llm or self.llm, actor=ACTOR,
                options=WatchOptions(2, min_age_s, self.effects), now=self.now,
            )  # fmt: skip
        self.notices.extend(summary.notices)
        return summary

    def cycles(self, n: int, **kwargs: Any) -> list[Any]:
        return [self.cycle(**kwargs) for _ in range(n)]

    def rows[T](self, model: type[T]) -> list[T]:
        with self.sessions() as session:
            return list(session.scalars(select(model).order_by(model.id)))  # type: ignore[attr-defined]

    def entry(self, name: str) -> WatchEntry | None:
        with self.sessions() as session:
            return session.scalars(
                select(WatchEntry).where(WatchEntry.caminho == f"entrada/{name}")
            ).one_or_none()

    def document(self, name: str) -> Document:
        with self.sessions() as session:
            return session.scalars(select(Document).where(Document.nome == name)).one()

    def events(self, acao: str) -> list[AuditEvent]:
        with self.sessions() as session:
            return list(
                session.scalars(
                    select(AuditEvent).where(AuditEvent.acao == acao).order_by(AuditEvent.id)
                )
            )


def _require_noreplace(root: Path) -> None:
    fd = os.open(root, os.O_RDONLY | os.O_DIRECTORY)
    try:
        noreplace.proven_primitive(fd)
    except NoReplaceUnavailable as error:
        pytest.skip(f"volume de teste sem rename exclusivo comprovado: {error}")
    finally:
        os.close(fd)


def _build(url: URL, tmp_path: Path, *, effects: bool) -> Iterator[Watch]:
    root = tmp_path.resolve() / "raiz"
    (root / "entrada").mkdir(parents=True)
    _require_noreplace(root)
    engine = create_engine(url)
    sessions = sessionmaker(engine, expire_on_commit=False)
    with sessions() as session:
        services.bootstrap_admin(session, telegram_id=1001, nome="Admin", actor="test:boot")
        registered = services.create_root(
            session, caminho=str(root), is_mount_point=mounted, actor="test:setup"
        )
        folder = services.create_watched_folder(
            session, root_id=registered.id, caminho="entrada", actor="test:setup"
        )
        services.create_department(
            session, nome="Fiscal", pasta="01 Fiscal", usa_ano=True, actor="test:setup"
        )
        services.create_owner(
            session, nome_fantasia="Alfa Comércio", cnpj=CNPJ_ALFA, aliases=["Alfa"],
            actor="test:setup",
        )  # fmt: skip
        services.create_owner(
            session, nome_fantasia="Beta Serviços", aliases=["Beta"], actor="test:setup"
        )
        session.commit()
        ids = (folder.id, registered.id)
    try:
        yield Watch(sessions, root, ids[0], ids[1], FakeContent(), FakeLlm(), effects=effects)
    finally:
        engine.dispose()


@pytest.fixture
def watch(migrated_database: URL, tmp_path: Path) -> Iterator[Watch]:
    yield from _build(migrated_database, tmp_path, effects=True)


@pytest.fixture
def simulated(migrated_database: URL, tmp_path: Path) -> Iterator[Watch]:
    yield from _build(migrated_database, tmp_path, effects=False)


DEST = "Alfa Comércio/01 Fiscal/2026/nota-alfa.pdf"


# --- estabilidade -------------------------------------------------------------------------------


def test_a_growing_file_is_never_read_until_it_stops_changing(watch: Watch) -> None:
    path = watch.drop("nota-alfa.pdf", pdf("inicio"))
    for chunk in range(4):  # a copy in progress: grows every cycle
        watch.cycle()
        with path.open("ab") as handle:
            handle.write(b"% mais um pedaco " + str(chunk).encode() + b"\n")
        watch.age(path)
        assert watch.content.hashed == [] and watch.content.extracted == []
        assert not (watch.root / DEST).exists()

    path.write_bytes(pdf("nota-alfa.pdf"))  # the copy ends with the final content
    watch.age(path)
    watch.cycles(3)

    assert watch.content.hashed == ["nota-alfa.pdf"]
    assert (watch.root / DEST).exists() and not path.exists()


def test_a_fresh_file_waits_for_the_minimum_age(watch: Watch) -> None:
    watch.drop("nota-alfa.pdf", age_s=0)

    watch.cycles(3, min_age_s=3600)

    assert watch.content.hashed == [] and watch.entry("nota-alfa.pdf") is not None
    assert watch.entry("nota-alfa.pdf").estado == WatchState.DETECTADO.value  # type: ignore[union-attr]


def test_detection_is_recorded_before_anything_else(watch: Watch) -> None:
    watch.drop("nota-alfa.pdf")

    first = watch.cycle()

    assert first.novos == 1 and first.estaveis == 0
    [detected] = watch.events("watch.detect")
    assert detected.detalhes["caminho"] == "entrada/nota-alfa.pdf" and detected.sha256 is None
    assert watch.rows(Document) == []  # no index row before the file is stable


# --- dono por evidência --------------------------------------------------------------------------


def test_stable_file_with_a_registered_cnpj_is_archived_in_place(watch: Watch) -> None:
    source = watch.drop("nota-alfa.pdf")

    watch.cycles(3)

    assert not source.exists() and (watch.root / DEST).exists()
    assert not (watch.root / ".osa-quarentena").exists()  # read in place, never staged
    doc = watch.document("nota-alfa.pdf")
    assert (doc.estado, doc.origem, doc.caminho) == ("ARQUIVADO", Origin.WATCHED.value, DEST)
    [operation] = watch.rows(ArchiveOperation)
    assert (operation.origem, operation.destino, operation.modo) == (
        "entrada/nota-alfa.pdf", DEST, "auto"
    )  # fmt: skip
    [evidence] = [e for e in watch.events("archive.intent")]
    assert evidence.detalhes["evidencia"] == "texto"  # the owner came from the CNPJ, not the model
    assert watch.entry("nota-alfa.pdf") is None  # gone from the folder: nothing left to track
    assert watch.notices == []


@pytest.mark.parametrize(
    ("name", "motivo"),
    [("nota-ambigua.pdf", "dono_ambiguo"), ("nota-sem-dono.pdf", "sem_evidencia_de_dono")],
)
def test_ambiguous_or_missing_owner_goes_to_review_and_stays_in_the_folder(
    watch: Watch, name: str, motivo: str
) -> None:
    source = watch.drop(name)

    watch.cycles(5)

    assert source.exists()  # only the archiving takes it out of the watched folder
    [case] = watch.rows(ReviewCase)
    assert case.motivo == motivo
    doc = watch.document(name)
    assert doc.estado == DocumentState.EM_REVISAO.value and doc.caminho == f"entrada/{name}"
    [notice] = watch.notices  # one notice, however many cycles
    assert (notice.kind, notice.reviewed is not None) == ("revisao", True)
    assert watch.entry(name).estado == WatchState.PROCESSADO.value  # type: ignore[union-attr]


def test_unsupported_stays_at_origin_and_warns_once(watch: Watch) -> None:
    source = watch.drop("planilha.ods", b"PK\x03\x04 nao suportado")

    watch.cycles(6)

    assert source.exists()
    doc = watch.document("planilha.ods")
    assert doc.estado == DocumentState.NAO_SUPORTADO.value
    assert [n.kind for n in watch.notices] == ["nao_suportado"]
    assert watch.content.extracted == []


def test_sensitive_file_in_the_watched_folder_is_never_read(watch: Watch) -> None:
    source = watch.drop("certificado.pfx", b"0\x82segredo-ficticio")

    watch.cycles(4)

    assert source.exists() and watch.content.hashed == [] and watch.rows(Document) == []
    assert [n.kind for n in watch.notices] == ["sensivel"]
    [refused] = watch.events("document.intake")
    assert refused.resultado == "denied"


def test_a_duplicate_of_an_archived_file_stays_and_is_reported_once(watch: Watch) -> None:
    watch.drop("nota-alfa.pdf")
    watch.cycles(3)
    copy = watch.drop("copia.pdf", (watch.root / DEST).read_bytes())

    watch.cycles(4)

    assert copy.exists() and len(watch.rows(Document)) == 1
    assert [n.kind for n in watch.notices] == ["duplicata"]


# --- reinício e falhas ---------------------------------------------------------------------------


def test_restart_after_detection_neither_duplicates_the_entry_nor_the_archiving(
    watch: Watch,
) -> None:
    watch.drop("nota-alfa.pdf")
    watch.cycle()  # DETECTADO, then the worker stops
    restarted = Watch(watch.sessions, watch.root, watch.folder_id, watch.root_id, watch.content,
                      FakeLlm(), now=watch.now)  # fmt: skip

    restarted.cycles(3)

    assert len(restarted.rows(Document)) == 1 and len(restarted.rows(ArchiveOperation)) == 1
    assert len(restarted.events("watch.detect")) == 1


def test_a_crash_in_the_middle_of_the_courier_reprocesses_the_same_row_once(watch: Watch) -> None:
    watch.drop("nota-alfa.pdf")
    watch.cycles(2)
    with pytest.raises(KeyboardInterrupt):  # the process dies while the model answers
        watch.cycle(llm=FakeLlm(fail=KeyboardInterrupt()))
    assert watch.entry("nota-alfa.pdf").estado == WatchState.ESTAVEL.value  # type: ignore[union-attr]
    [half] = watch.rows(Document)
    assert half.estado == DocumentState.EXTRAIDO.value

    watch.cycle()

    [doc] = watch.rows(Document)
    assert (doc.id, doc.estado) == (half.id, "ARQUIVADO")
    assert len(watch.rows(ArchiveOperation)) == 1


def test_model_away_leaves_stable_files_for_the_next_cycle(watch: Watch) -> None:
    watch.drop("nota-alfa.pdf")
    watch.drop("nota-sem-dono.pdf")
    watch.cycles(2)

    away = watch.cycle(llm=FakeLlm(fail=ModelUnavailable("fora do ar (fictício)")))

    assert away.adiados == 2 and away.falhas == 0  # the second never reached the courier
    assert watch.entry("nota-alfa.pdf").estado == WatchState.ESTAVEL.value  # type: ignore[union-attr]
    assert watch.notices == []

    watch.cycle()

    assert (watch.root / DEST).exists()
    assert watch.document("nota-sem-dono.pdf").estado == DocumentState.EM_REVISAO.value


def test_a_failure_isolates_the_file_and_the_cycle_goes_on(watch: Watch) -> None:
    watch.drop("a-quebrado.pdf")
    watch.drop("nota-alfa.pdf")
    watch.cycles(2)

    class Breaks(FakeContent):
        def extract(self, path: Path) -> ExtractedContent:
            if path.name == "a-quebrado.pdf":
                from osa.domain.errors import ExtractionFailed

                raise ExtractionFailed("arquivo corrompido (fictício)")
            return super().extract(path)

    watch.content = Breaks()
    summary = watch.cycle()

    assert (summary.falhas, summary.arquivados) == (1, 1)
    assert watch.document("a-quebrado.pdf").estado == DocumentState.FALHOU.value
    assert [n.kind for n in watch.notices] == ["falhou"]
    watch.cycles(3)
    assert [n.kind for n in watch.notices] == ["falhou"]  # never retried while unchanged


def test_effects_off_simulates_and_leaves_the_file_without_reprocessing(simulated: Watch) -> None:
    source = simulated.drop("nota-alfa.pdf")

    simulated.cycles(6)

    assert source.exists() and not (simulated.root / DEST).exists()
    doc = simulated.document("nota-alfa.pdf")
    assert doc.estado == DocumentState.CLASSIFICADO.value and (doc.motivo or "").startswith(
        "simulado"
    )
    assert simulated.llm.calls == 1  # processed once, then skipped while unchanged


def test_a_file_replaced_under_the_same_name_is_processed_again(simulated: Watch) -> None:
    path = simulated.drop("nota-sem-dono.pdf")
    simulated.cycles(3)
    old = simulated.document("nota-sem-dono.pdf")
    path.write_bytes(pdf("outra versao"))
    simulated.age(path, 300)

    simulated.cycles(3)

    assert simulated.llm.calls == 2 and len(simulated.rows(Document)) == 2
    with simulated.sessions() as session:
        previous = session.get_one(Document, old.id)
    # the old content is no longer at that path: its case cannot be decided onto the new one
    assert (
        previous.caminho is None and previous.motivo == "conteúdo substituído na pasta monitorada"
    )
    assert len(simulated.events("watch.replaced")) == 1


# --- convivência com o scan e o worker -----------------------------------------------------------


def test_the_scan_never_reads_the_watched_folder_nor_marks_its_reviews_absent(
    watch: Watch,
) -> None:
    watch.drop("nota-sem-dono.pdf")
    watch.cycles(3)  # EM_REVISAO, still in entrada/
    watch.content.hashed.clear()
    watch.drop("copiando.pdf", pdf("em copia"), age_s=0)

    with watch.sessions() as session:
        summary = scan_root(
            watch.root_id, store=SqlStore(session), fs=LocalFs(), content=watch.content,
            llm=watch.llm, actor="test:scan",
        )  # fmt: skip

    assert summary.concluido and summary.ausentes == 0
    assert watch.content.hashed == []  # nothing under entrada/ was read
    assert watch.document("nota-sem-dono.pdf").ausente_em is None


@dataclass
class Collect:
    notices: list[Notice] = field(default_factory=list)
    reminders: int = 0
    fail_next: int = 0  # the next N sends fail (Telegram away)

    def send(self, notice: Notice) -> bool:
        if self.fail_next:
            self.fail_next -= 1
            return False
        self.notices.append(notice)
        return True

    def remind(self) -> bool:
        self.reminders += 1
        return True


def _worker(watch: Watch, clock: list[datetime], **settings: Any) -> tuple[Worker, Collect]:
    collect = Collect()
    worker = Worker(
        Settings(stable_cycles=2, min_file_age_seconds=60, enable_file_effects=True, **settings),
        watch.sessions, fs=LocalFs(), content=watch.content, llm=watch.llm, notifier=collect,
        clock=lambda: clock[0],
    )  # fmt: skip
    return worker, collect


def test_worker_tick_watches_reminds_once_a_day_and_rescans_in_parts(watch: Watch) -> None:
    for n in range(5):
        tree = watch.root / "Alfa Comércio" / "01 Fiscal" / "2025"
        tree.mkdir(parents=True, exist_ok=True)
        (tree / f"antigo-{n}.pdf").write_bytes(pdf(f"antigo {n}"))
    watch.drop("nota-sem-dono.pdf")
    local_ten = datetime.now().astimezone().replace(hour=10, minute=0, second=0, microsecond=0)
    clock = [local_ten.astimezone(UTC)]
    worker, collect = _worker(watch, clock, scan_documents_per_cycle=2)

    ticks = []
    for _ in range(6):
        ticks.append(worker.tick())
        clock[0] += timedelta(seconds=45)

    assert [n.kind for n in collect.notices] == ["revisao"]
    assert collect.reminders == 1 and len(watch.events("reminder.daily")) == 1
    pauses = watch.events("scan.pause")
    assert len(pauses) >= 2  # two documents per tick, resumable
    with watch.sessions() as session:
        root = session.get_one(Root, watch.root_id)
        assert root.ultimo_scan is not None and root.scan_cursor is None
        scanned = session.scalar(
            select(func.count()).select_from(Document).where(Document.origem == Origin.SCAN.value)
        )
    assert scanned == 5


def test_reminder_waits_for_nine_and_survives_a_restart(watch: Watch) -> None:
    local = datetime.now().astimezone()
    clock = [local.replace(hour=8, minute=59, second=0, microsecond=0).astimezone(UTC)]
    worker, collect = _worker(watch, clock)
    worker.tick()
    assert collect.reminders == 0

    clock[0] += timedelta(minutes=2)
    worker.tick()
    again, collect_again = _worker(watch, clock)  # the worker restarts the same day
    again.tick()

    assert collect.reminders == 1 and collect_again.reminders == 0


# --- achados da revisão ---------------------------------------------------------------------


class ChangesOnRead(LocalFs):
    """A writer touches the file right after the listing (between the listing and the read)."""

    def __init__(self, path: Path) -> None:
        self.path = path

    def fingerprint(self, root: Path, relative: str) -> tuple[int, int] | None:
        with self.path.open("ab") as handle:
            handle.write(b"% outro cliente ainda escrevendo\n")
        return super().fingerprint(root, relative)


def test_a_file_changed_between_the_listing_and_the_read_is_not_read(watch: Watch) -> None:
    path = watch.drop("nota-alfa.pdf")
    watch.cycles(2)

    with watch.sessions() as session:
        summary = watch_cycle(
            watch.folder_id, store=SqlStore(session), fs=ChangesOnRead(path),
            content=watch.content, llm=watch.llm, actor=ACTOR,
            options=WatchOptions(2, 60, True), now=watch.now + timedelta(seconds=45),
        )  # fmt: skip

    assert summary.mudaram == 1 and watch.content.hashed == []
    assert watch.entry("nota-alfa.pdf").estado == WatchState.DETECTADO.value  # type: ignore[union-attr]


def test_the_archive_checks_the_content_it_decided_on(watch: Watch) -> None:
    path = watch.drop("nota-alfa.pdf")
    watch.cycles(2)

    class SwapsBeforeRename(LocalFs):
        def archive(self, root: Path, src: str, *args: Any, **kwargs: Any) -> Any:
            path.write_bytes(pdf("conteudo trocado depois de lido"))  # another client re-saves
            return super().archive(root, src, *args, **kwargs)

    with watch.sessions() as session:
        summary = watch_cycle(
            watch.folder_id, store=SqlStore(session), fs=SwapsBeforeRename(),
            content=watch.content, llm=watch.llm, actor=ACTOR,
            options=WatchOptions(2, 60, True), now=watch.now + timedelta(seconds=45),
        )  # fmt: skip

    assert summary.falhas == 1 and summary.arquivados == 0
    assert path.exists() and not (watch.root / DEST).exists()  # nothing moved
    assert watch.rows(ArchiveOperation) == []
    [failed] = watch.events("document.fail")
    assert "sha256" in failed.detalhes["motivo"] or "identidade" in failed.detalhes["motivo"]


def test_a_refused_template_stops_the_cycle_and_keeps_the_file_stable(watch: Watch) -> None:
    from osa.courier.pipeline import ConfigurationError
    from osa.llm.provider import InvalidDepartments

    watch.drop("nota-alfa.pdf")
    watch.cycles(2)

    with pytest.raises(ConfigurationError):
        watch.cycle(llm=FakeLlm(fail=InvalidDepartments("descrição recusada (fictícia)")))

    assert watch.entry("nota-alfa.pdf").estado == WatchState.ESTAVEL.value  # type: ignore[union-attr]
    assert watch.events("watch.skip") == []


def test_a_crash_after_the_review_case_still_notifies_the_case(watch: Watch) -> None:
    watch.drop("nota-sem-dono.pdf")
    watch.cycles(3)
    [case] = watch.rows(ReviewCase)
    with watch.sessions() as session:  # as if the process died before the row was PROCESSADO
        entry = session.scalars(select(WatchEntry)).one()
        entry.estado, entry.aviso = WatchState.ESTAVEL.value, None
        session.commit()
    watch.notices.clear()

    watch.cycle()

    assert [n.kind for n in watch.notices] == ["revisao"]  # its own case, not "duplicata"
    assert len(watch.rows(ReviewCase)) == 1 and watch.rows(ReviewCase)[0].id == case.id


def test_a_file_the_model_never_answers_is_given_up_with_a_notice(watch: Watch) -> None:
    from osa.worker.watch import MAX_ATTEMPTS

    watch.drop("nota-alfa.pdf")
    watch.cycles(2)
    away = FakeLlm(fail=ModelUnavailable("tempo total esgotado (fictício)"))

    watch.cycles(MAX_ATTEMPTS + 2, llm=away)

    assert away.calls == MAX_ATTEMPTS
    assert watch.entry("nota-alfa.pdf").estado == WatchState.PROCESSADO.value  # type: ignore[union-attr]
    assert [n.kind for n in watch.notices] == ["falhou"]


def test_an_archive_behind_a_paused_rescan_is_not_marked_absent(watch: Watch) -> None:
    from osa.worker.scan import ScanOptions

    tree = watch.root / "Beta Serviços" / "01 Fiscal" / "2025"
    tree.mkdir(parents=True)
    for n in range(3):
        (tree / f"antigo-{n}.pdf").write_bytes(pdf(f"beta antigo {n}"))
    with watch.sessions() as session:
        first = scan_root(
            watch.root_id, store=SqlStore(session), fs=LocalFs(), content=watch.content,
            llm=watch.llm, actor="test:scan", options=ScanOptions(max_indexed=1),
        )  # fmt: skip
    assert not first.concluido  # paused with the cursor under Beta
    watch.drop("nota-alfa.pdf")
    watch.cycles(3)  # archived into Alfa Comércio/..., behind the cursor
    assert (watch.root / DEST).exists()

    with watch.sessions() as session:
        done = scan_root(
            watch.root_id, store=SqlStore(session), fs=LocalFs(), content=watch.content,
            llm=watch.llm, actor="test:scan",
        )  # fmt: skip

    assert done.concluido and done.ausentes == 0
    assert watch.document("nota-alfa.pdf").ausente_em is None


def test_the_scan_never_enters_an_inactive_watched_folder(watch: Watch) -> None:
    with watch.sessions() as session:
        services.set_watched_folder_active(session, watch.folder_id, False, actor="test:setup")
        session.commit()
    watch.drop("copiando.pdf", pdf("em copia"), age_s=0)

    with watch.sessions() as session:
        scan_root(
            watch.root_id, store=SqlStore(session), fs=LocalFs(), content=watch.content,
            llm=watch.llm, actor="test:scan",
        )  # fmt: skip

    assert watch.content.hashed == [] and watch.rows(Document) == []


def test_a_notice_that_could_not_be_sent_is_sent_on_a_later_tick(watch: Watch) -> None:
    watch.drop("nota-sem-dono.pdf")
    clock = [T0]
    worker, collect = _worker(watch, clock)
    collect.fail_next = 1

    for _ in range(4):
        clock[0] += timedelta(seconds=45)
        watch.age(watch.inbox / "nota-sem-dono.pdf", 600)
        worker.tick()

    assert [n.kind for n in collect.notices] == ["revisao"]  # delivered once, after the failure
    assert watch.entry("nota-sem-dono.pdf").aviso_enviado_em is not None  # type: ignore[union-attr]


def test_only_one_worker_and_one_scan_per_root_run_at_a_time(watch: Watch) -> None:
    from osa.domain.errors import Conflict

    with watch.sessions() as session, SqlStore(session).worker_lock() as first:
        with watch.sessions() as other, SqlStore(other).worker_lock() as second:
            assert (first, second) == (True, False)
        with watch.sessions() as other, SqlStore(other).scan_lock(watch.root_id) as held:
            assert held
            with watch.sessions() as third, pytest.raises(Conflict):
                scan_root(
                    watch.root_id, store=SqlStore(third), fs=LocalFs(), content=watch.content,
                    llm=watch.llm, actor="test:scan",
                )  # fmt: skip
