"""O scan do acervo contra o PostgreSQL descartável e uma raiz de teste em ``tmp_path``.

Extração e modelo são falsos (o texto vem do nome do arquivo); caminhada, hash, índice,
cursor e auditoria são reais. Nenhum teste espera que o produto mova, crie ou apague algo:
a árvore é fotografada antes e comparada depois. O que os testes apagam ou movem (para
simular um arquivo sumido ou movido à mão) é código de teste, nunca do produto.
"""

from __future__ import annotations

import hashlib
import os
import shutil
import time
from collections.abc import Iterator, Sequence
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from osa.admin import services
from osa.courier.ports import LocalContent, LocalFs
from osa.courier.store import SqlStore
from osa.db.models import AuditEvent, Department, Document, Owner, Root
from osa.domain.document import DocumentState, Origin
from osa.domain.errors import Conflict
from osa.extract.model import DEFAULT_SENSITIVE_EXTENSIONS, ExtractedContent, ExtractLimits
from osa.fs.names import QUARANTINE_DIR
from osa.llm.envelopes import ClassificationEnvelope
from osa.llm.provider import EnvelopeInvalid, ModelUnavailable
from osa.worker.scan import (
    MOTIVO_FORA_DO_PADRAO,
    MOTIVO_MOVIDO,
    ScanInterrupted,
    ScanOptions,
    ScanSummary,
    scan_root,
)

ACTOR = "test:scan"
OWNERS = ("Alfa Comércio", "Beta Serviços", "Gama Indústria")
DEPARTMENTS = (
    ("Fiscal", "01 Fiscal", True),
    ("Pessoal", "02 Pessoal", False),
    ("Contratos", "03 Contratos", False),
    ("Jurídico", "04 Jurídico", False),
)
SENSITIVE_BYTES = b"0\x82\x03\x00segredo-ficticio-nunca-lido"


def mounted(_path: Path) -> bool:
    return True


def pdf(label: str) -> bytes:
    return f"%PDF-1.4\n% {label}\n%%EOF\n".encode()


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def put(path: Path, data: bytes) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    return path


TEXTS = {
    "boleto.pdf": "Boleto de energia elétrica, vencimento em março de 2026",
    "nota.pdf": "Nota fiscal de serviço de consultoria",
    "folha.pdf": "Folha de pagamento do mês",
    "contrato.pdf": "Contrato de prestação de serviços",
    "ata.pdf": "Ata de reunião da diretoria",
}


class FakeContent(LocalContent):
    """Real class by name, sniff and hash; the extraction is a text from the file name."""

    def __init__(self) -> None:
        super().__init__(ExtractLimits(sensitive_extensions=DEFAULT_SENSITIVE_EXTENSIONS))
        self.extracted: list[Path] = []

    def extract(self, path: Path) -> ExtractedContent:
        self.extracted.append(path)
        text = TEXTS.get(path.name, f"Documento fictício {path.name}")
        return ExtractedContent(
            text=text, images=(), pages_total=1, truncated=False, method="pdf_text"
        )


@dataclass
class FakeLlm:
    fail_at: int | None = None  # the Nth call raises ModelUnavailable
    invalid_at: int | None = None  # the Nth call raises EnvelopeInvalid
    crash_at: int | None = None  # the Nth call raises an unexpected error (a crash)
    calls: list[str] = field(default_factory=list)

    def classify(self, content: ExtractedContent, departments: Sequence[Any]) -> Any:
        self.calls.append(content.text)
        if self.fail_at is not None and len(self.calls) == self.fail_at:
            raise ModelUnavailable("servidor fora do ar (fictício)")
        if self.crash_at is not None and len(self.calls) == self.crash_at:
            raise RuntimeError("queda fictícia do processo")
        if self.invalid_at is not None and len(self.calls) == self.invalid_at:
            raise EnvelopeInvalid("envelope fora do schema (fictício)")
        lower = content.text.lower()
        tipo = "boleto" if "boleto" in lower else "nota fiscal" if "nota" in lower else "documento"
        dates = [date(2026, 3, 10)] if "março" in lower else []
        return ClassificationEnvelope(
            departamento="Fiscal",
            departamento_alternativo=None,
            tipo_documental=tipo,
            tags=[tipo.split()[0]],
            resumo=content.text[:100],
            datas=dates,
            ano_referencia=2026 if dates else None,
            precisa_revisao=False,
            motivo_revisao=None,
        )


@dataclass
class Installation:
    session: Session
    root: Path
    root_id: int
    owners: dict[str, Owner]
    departments: dict[str, Department]
    content: FakeContent
    llm: FakeLlm

    def scan(
        self, *, llm: FakeLlm | None = None, options: ScanOptions | None = None
    ) -> ScanSummary:
        return scan_root(
            self.root_id,
            store=SqlStore(self.session),
            fs=LocalFs(),
            content=self.content,
            llm=llm or self.llm,
            actor=ACTOR,
            options=options or ScanOptions(),
        )

    def documents(self) -> list[Document]:
        return list(self.session.scalars(select(Document).order_by(Document.id)))

    def by_path(self, rel: str) -> Document:
        return self.session.scalars(select(Document).where(Document.caminho == rel)).one()

    def events(self, acao: str | None = None) -> list[AuditEvent]:
        query = select(AuditEvent).where(AuditEvent.ator == ACTOR).order_by(AuditEvent.id)
        if acao is not None:
            query = query.where(AuditEvent.acao == acao)
        return list(self.session.scalars(query))

    def db_root(self) -> Root:
        return self.session.get_one(Root, self.root_id)

    def snapshot(self) -> dict[str, bytes]:
        return {
            str(p.relative_to(self.root)): p.read_bytes()
            for p in sorted(self.root.rglob("*"))
            if p.is_file() or p.is_symlink()
        } | {str(p.relative_to(self.root)) + "/": b"" for p in self.root.rglob("*") if p.is_dir()}


@pytest.fixture
def installation(session: Session, tmp_path: Path) -> Iterator[Installation]:
    root = tmp_path.resolve() / "raiz"
    root.mkdir()
    services.bootstrap_admin(session, telegram_id=1001, nome="Admin Ficticio", actor="test:boot")
    registered = services.create_root(
        session, caminho=str(root), is_mount_point=mounted, actor="test:setup"
    )
    departments = {
        nome: services.create_department(
            session, nome=nome, pasta=pasta, usa_ano=usa_ano, actor="test:setup"
        )
        for nome, pasta, usa_ano in DEPARTMENTS
    }
    owners = {
        nome: services.create_owner(session, nome_fantasia=nome, actor="test:setup")
        for nome in OWNERS
    }
    session.commit()
    # 3 donos × 4 departamentos, um PDF em cada (Fiscal por ano), todos distintos
    for owner in owners.values():
        for dep in departments.values():
            folder = root / owner.pasta / dep.pasta / ("2025" if dep.usa_ano else "")
            name = {
                "Fiscal": "boleto.pdf",
                "Pessoal": "folha.pdf",
                "Contratos": "contrato.pdf",
            }.get(dep.nome, "ata.pdf")
            put(folder / name, pdf(f"{owner.pasta} {dep.pasta} {name}"))
    put(root / "solto.pdf", pdf("solto"))
    put(root / QUARANTINE_DIR / "0123abcd.pdf", pdf("quarentena"))
    put(root / "Alfa Comércio" / ".DS_Store", b"\0\0")
    yield Installation(session, root, registered.id, owners, departments, FakeContent(), FakeLlm())


# --- árvore indexada, nada movido -------------------------------------------------------------


def test_tree_is_indexed_with_owner_and_department_from_the_path_and_nothing_moves(
    installation: Installation,
) -> None:
    inst = installation
    before = inst.snapshot()

    summary = inst.scan()

    assert inst.snapshot() == before
    assert summary.concluido and not summary.retomado
    assert (summary.arquivos, summary.novos, summary.fora_do_padrao) == (13, 13, 1)
    docs = inst.documents()
    assert len(docs) == 13 and len({d.sha256 for d in docs}) == 13
    for owner in inst.owners.values():
        for dep in inst.departments.values():
            year = "2025/" if dep.usa_ano else ""
            name = {
                "Fiscal": "boleto.pdf",
                "Pessoal": "folha.pdf",
                "Contratos": "contrato.pdf",
            }.get(dep.nome, "ata.pdf")
            doc = inst.by_path(f"{owner.pasta}/{dep.pasta}/{year}{name}")
            assert (doc.owner_id, doc.departamento_id) == (owner.id, dep.id)
            assert doc.ano == (2025 if dep.usa_ano else (2026 if name == "boleto.pdf" else None))
            assert doc.estado == DocumentState.ARQUIVADO.value and doc.motivo is None
            assert doc.origem == Origin.SCAN.value and doc.nome == name
            assert doc.texto == TEXTS[name] and doc.tipo_documental
            assert doc.visto_em is not None and doc.ausente_em is None
    assert not any(d.caminho.startswith(QUARANTINE_DIR) for d in docs if d.caminho)
    assert inst.events("document.archive") == [] and inst.events("archive.intent") == []
    root = inst.db_root()
    assert root.ultimo_scan is not None and root.scan_cursor is None
    assert root.scan_iniciado_em is None


def test_file_outside_the_pattern_stays_at_origin_with_no_owner(installation: Installation) -> None:
    inst = installation
    put(inst.root / "Alfa Comércio" / "avulso.pdf", pdf("avulso"))
    put(inst.root / "Alfa Comércio" / "09 Outro" / "x.pdf", pdf("pasta desconhecida"))
    put(inst.root / "Delta" / "01 Fiscal" / "y.pdf", pdf("dono desconhecido"))

    summary = inst.scan()

    assert summary.fora_do_padrao == 4
    for rel in ("solto.pdf", "Alfa Comércio/avulso.pdf", "Alfa Comércio/09 Outro/x.pdf",
                "Delta/01 Fiscal/y.pdf"):  # fmt: skip
        doc = inst.by_path(rel)
        assert doc.estado == DocumentState.NA_ORIGEM.value
        assert doc.motivo == MOTIVO_FORA_DO_PADRAO
        assert doc.owner_id is None and doc.departamento_id is None
        assert doc.texto  # indexado e encontrável mesmo assim
    assert inst.events("document.review") == []


def test_audit_trail_of_one_scanned_document_never_carries_text(
    installation: Installation,
) -> None:
    inst = installation
    inst.scan()
    doc = inst.by_path("Alfa Comércio/01 Fiscal/2025/boleto.pdf")
    events = [
        e for e in inst.events() if e.alvo_tipo == "document" and e.alvo_id == str(doc.id)
    ]

    assert [e.acao for e in events] == [
        "document.detect", "document.extract", "document.classify", "document.index"
    ]  # fmt: skip
    assert all(e.sha256 == doc.sha256 for e in events)
    assert events[-1].detalhes["estado"] == "ARQUIVADO"
    assert events[-1].detalhes["departamento_divergente"] is False
    for event in inst.events():
        assert "energia" not in str(event.detalhes).lower()
    assert events[0].detalhes["caminho"] == "Alfa Comércio/01 Fiscal/2025/boleto.pdf"


def test_model_department_that_disagrees_with_the_path_is_only_recorded(
    installation: Installation,
) -> None:
    inst = installation
    inst.scan()
    doc = inst.by_path("Beta Serviços/02 Pessoal/folha.pdf")  # modelo sempre diz Fiscal

    assert doc.departamento_id == inst.departments["Pessoal"].id
    event = next(e for e in inst.events("document.index") if e.alvo_id == str(doc.id))
    assert event.detalhes["departamento_modelo"] == "Fiscal"
    assert event.detalhes["departamento_divergente"] is True


def test_start_and_finish_are_audited_on_the_root(installation: Installation) -> None:
    inst = installation
    inst.scan()
    start, finish = inst.events("scan.start"), inst.events("scan.finish")
    assert len(start) == 1 and start[0].alvo_tipo == "root" and start[0].sha256 is None
    assert len(finish) == 1 and finish[0].detalhes["novos"] == 13


# --- sensível e não suportado -----------------------------------------------------------------


def test_sensitive_file_is_indexed_without_text_and_never_extracted(
    installation: Installation,
) -> None:
    inst = installation
    put(inst.root / "Alfa Comércio" / "04 Jurídico" / "certificado.pfx", SENSITIVE_BYTES)

    summary = inst.scan()

    assert summary.sensiveis == 1
    doc = inst.by_path("Alfa Comércio/04 Jurídico/certificado.pfx")
    assert doc.sha256 == sha(SENSITIVE_BYTES)
    assert doc.texto is None and doc.tipo_documental is None and doc.resumo is None
    assert doc.estado == DocumentState.NA_ORIGEM.value and "sensível" in (doc.motivo or "")
    assert doc.owner_id == inst.owners["Alfa Comércio"].id  # caminho continua valendo
    assert all(p.name != "certificado.pfx" for p in inst.content.extracted)
    assert all("segredo" not in text for text in inst.llm.calls)
    assert all("segredo" not in str(e.detalhes) for e in inst.events())


def test_unsupported_by_name_and_by_content_are_indexed_as_such(
    installation: Installation,
) -> None:
    inst = installation
    put(inst.root / "Beta Serviços" / "02 Pessoal" / "notas.txt", b"texto solto")
    put(inst.root / "Beta Serviços" / "02 Pessoal" / "falso.pdf", b"isto nao e um pdf")

    summary = inst.scan()

    assert summary.nao_suportados == 2
    for rel in ("Beta Serviços/02 Pessoal/notas.txt", "Beta Serviços/02 Pessoal/falso.pdf"):
        doc = inst.by_path(rel)
        assert doc.estado == DocumentState.NAO_SUPORTADO.value and doc.texto is None
    assert all(p.name not in {"notas.txt", "falso.pdf"} for p in inst.content.extracted)


# --- movido, duplicado, ausente ---------------------------------------------------------------


def test_file_moved_by_hand_updates_the_path_without_a_new_entry(
    installation: Installation,
) -> None:
    inst = installation
    inst.scan()
    before = inst.by_path("Alfa Comércio/03 Contratos/contrato.pdf")
    document_id, sha256 = before.id, before.sha256
    source = inst.root / "Alfa Comércio" / "03 Contratos" / "contrato.pdf"
    target = inst.root / "Beta Serviços" / "04 Jurídico" / "contrato-alfa.pdf"
    os.rename(source, target)  # código de teste: simula a mão humana

    summary = inst.scan()

    assert (summary.movidos, summary.novos, summary.ausentes) == (1, 0, 0)
    assert len(inst.documents()) == 13
    doc = inst.session.get_one(Document, document_id)
    assert doc.sha256 == sha256
    assert doc.caminho == "Beta Serviços/04 Jurídico/contrato-alfa.pdf"
    assert doc.nome == "contrato-alfa.pdf"
    assert (doc.owner_id, doc.departamento_id) == (
        inst.owners["Beta Serviços"].id, inst.departments["Jurídico"].id
    )  # fmt: skip
    assert doc.estado == DocumentState.ARQUIVADO.value and doc.ausente_em is None
    [moved] = inst.events("document.move")
    assert moved.detalhes["motivo"] == MOTIVO_MOVIDO
    assert moved.detalhes["origem"] == "Alfa Comércio/03 Contratos/contrato.pdf"
    assert moved.detalhes["destino"] == doc.caminho
    assert len(inst.llm.calls) == 13  # não foi ao modelo de novo


def test_file_moved_out_of_the_pattern_goes_back_to_origin_state(
    installation: Installation,
) -> None:
    inst = installation
    inst.scan()
    os.rename(
        inst.root / "Gama Indústria" / "02 Pessoal" / "folha.pdf", inst.root / "folha-gama.pdf"
    )

    inst.scan()

    doc = inst.by_path("folha-gama.pdf")
    assert doc.estado == DocumentState.NA_ORIGEM.value and doc.motivo == MOTIVO_FORA_DO_PADRAO
    assert doc.owner_id is None and doc.departamento_id is None


def test_a_copy_of_a_known_file_is_a_duplicate_and_the_index_keeps_the_original(
    installation: Installation,
) -> None:
    inst = installation
    inst.scan()
    original = inst.root / "Alfa Comércio" / "01 Fiscal" / "2025" / "boleto.pdf"
    shutil.copyfile(original, inst.root / "Gama Indústria" / "04 Jurídico" / "copia.pdf")

    summary = inst.scan()

    assert (summary.duplicatas, summary.novos, summary.movidos) == (1, 0, 0)
    assert len(inst.documents()) == 13
    doc = inst.by_path("Alfa Comércio/01 Fiscal/2025/boleto.pdf")
    assert doc.caminho == "Alfa Comércio/01 Fiscal/2025/boleto.pdf"
    [dup] = inst.events("document.duplicate")
    assert dup.resultado == "denied" and dup.detalhes["caminho"].endswith("copia.pdf")


def test_missing_file_is_marked_absent_and_kept_then_cleared_when_it_returns(
    installation: Installation,
) -> None:
    inst = installation
    inst.scan()
    path = inst.root / "Gama Indústria" / "03 Contratos" / "contrato.pdf"
    data = path.read_bytes()
    path.unlink()  # código de teste

    summary = inst.scan()

    assert summary.ausentes == 1 and len(inst.documents()) == 13
    doc = inst.by_path("Gama Indústria/03 Contratos/contrato.pdf")
    assert doc.ausente_em is not None and doc.estado == DocumentState.ARQUIVADO.value
    [absent] = inst.events("document.absent")
    assert absent.alvo_id == str(doc.id)

    put(path, data)
    second = inst.scan()

    assert second.ausentes == 0
    assert inst.by_path("Gama Indústria/03 Contratos/contrato.pdf").ausente_em is None
    assert inst.scan().ausentes == 0  # já marcado não é marcado de novo


def test_absent_is_never_marked_by_an_incremental_scan(installation: Installation) -> None:
    inst = installation
    inst.scan()
    (inst.root / "Gama Indústria" / "03 Contratos" / "contrato.pdf").unlink()

    summary = inst.scan(options=ScanOptions(since=datetime.now(UTC) - timedelta(days=1)))

    assert summary.ausentes == 0
    assert inst.by_path("Gama Indústria/03 Contratos/contrato.pdf").ausente_em is None


# --- retomada ---------------------------------------------------------------------------------


def test_interrupted_scan_resumes_from_the_cursor_without_duplicates(
    installation: Installation,
) -> None:
    inst = installation
    flaky = FakeLlm(fail_at=5)

    with pytest.raises(ScanInterrupted):
        inst.scan(llm=flaky)

    root = inst.db_root()
    assert root.scan_iniciado_em is not None and root.scan_cursor is not None
    assert root.ultimo_scan is None
    partial = inst.documents()
    assert len(partial) == 5
    failed = [d for d in partial if d.estado == DocumentState.FALHOU.value]
    assert len(failed) == 1 and "modelo indisponível" in (failed[0].motivo or "")
    [stop] = inst.events("scan.stop")
    assert stop.detalhes["cursor"] == root.scan_cursor

    summary = inst.scan()

    assert summary.retomado and summary.concluido
    assert summary.reprocessados == 1 and summary.novos == 8
    docs = inst.documents()
    assert len(docs) == 13 and len({d.sha256 for d in docs}) == 13
    assert {d.estado for d in docs} == {"ARQUIVADO", "NA_ORIGEM"}
    assert len(inst.llm.calls) == 9  # 8 novos + 1 reprocessado
    root = inst.db_root()
    assert root.scan_cursor is None and root.scan_iniciado_em is None
    assert root.ultimo_scan is not None


def test_crash_in_the_middle_of_a_file_resumes_on_that_file(installation: Installation) -> None:
    inst = installation
    with pytest.raises(RuntimeError):
        inst.scan(llm=FakeLlm(crash_at=4))

    root = inst.db_root()
    half = [d for d in inst.documents() if d.estado == DocumentState.EXTRAIDO.value]
    assert len(half) == 1 and root.scan_cursor is not None
    assert tuple(root.scan_cursor.split("/")) < tuple((half[0].caminho or "").split("/"))

    summary = inst.scan()

    assert summary.retomado and summary.reprocessados == 1
    docs = inst.documents()
    assert len(docs) == 13 and {d.estado for d in docs} == {"ARQUIVADO", "NA_ORIGEM"}


def test_a_full_scan_never_resumes_an_interrupted_incremental_one(
    installation: Installation,
) -> None:
    inst = installation
    inst.scan()
    old = time.time() - 10 * 86400
    for path in inst.root.rglob("*.pdf"):
        os.utime(path, (old, old))
    put(inst.root / "Alfa Comércio" / "02 Pessoal" / "novo.pdf", pdf("novo"))
    put(inst.root / "Gama Indústria" / "02 Pessoal" / "novo2.pdf", pdf("novo2"))
    since = datetime.now(UTC) - timedelta(days=1)
    with pytest.raises(ScanInterrupted):
        inst.scan(llm=FakeLlm(fail_at=1), options=ScanOptions(since=since))
    assert inst.db_root().scan_desde is not None

    summary = inst.scan()  # completo: começa do zero, não herda o passe parcial

    assert not summary.retomado and summary.concluido
    assert summary.ausentes == 0 and summary.vistos == 13
    assert all(d.ausente_em is None for d in inst.documents())
    resumed = inst.scan(llm=FakeLlm(), options=ScanOptions(since=since))
    assert not resumed.retomado  # o scan completo já tinha fechado o cursor


def test_unreadable_folder_is_reported_and_blocks_the_absent_marking(
    installation: Installation,
) -> None:
    if os.geteuid() == 0:
        pytest.skip("root lê qualquer pasta")
    inst = installation
    inst.scan()
    locked = inst.root / "Beta Serviços" / "02 Pessoal"
    os.chmod(locked, 0o000)
    try:
        summary = inst.scan()
    finally:
        os.chmod(locked, 0o755)

    assert summary.pastas_ilegiveis == 1 and summary.ausentes == 0 and summary.concluido
    assert all(d.ausente_em is None for d in inst.documents())
    [skip] = inst.events("scan.skip")
    assert skip.detalhes["caminho"] == "Beta Serviços/02 Pessoal"


def test_unreadable_file_counts_as_seen_and_blocks_the_absent_marking(
    installation: Installation,
) -> None:
    if os.geteuid() == 0:
        pytest.skip("root lê qualquer arquivo")
    inst = installation
    inst.scan()
    locked = inst.root / "Beta Serviços" / "02 Pessoal" / "folha.pdf"
    os.chmod(locked, 0o000)
    try:
        summary = inst.scan()
    finally:
        os.chmod(locked, 0o644)

    assert summary.ilegiveis == 1 and summary.ausentes == 0
    doc = inst.by_path("Beta Serviços/02 Pessoal/folha.pdf")
    assert doc.ausente_em is None and doc.estado == DocumentState.ARQUIVADO.value
    [skip] = inst.events("scan.skip")
    assert "ilegível" in skip.detalhes["motivo"]


def test_same_file_spelled_differently_is_seen_not_duplicated(installation: Installation) -> None:
    inst = installation
    inst.scan()
    folder = inst.root / "Alfa Comércio" / "02 Pessoal"
    os.rename(folder / "folha.pdf", folder / "FOLHA.pdf")  # só a caixa mudou

    summary = inst.scan()

    assert (summary.vistos, summary.duplicatas, summary.movidos, summary.ausentes) == (13, 0, 0, 0)
    doc = inst.by_path("Alfa Comércio/02 Pessoal/FOLHA.pdf")
    assert doc.ausente_em is None and inst.events("document.duplicate") == []


def test_moved_sensitive_and_unsupported_files_keep_their_marker(
    installation: Installation,
) -> None:
    inst = installation
    put(inst.root / "solto.pfx", SENSITIVE_BYTES)
    put(inst.root / "solto.txt", b"texto solto")
    inst.scan()
    os.rename(inst.root / "solto.pfx", inst.root / "Alfa Comércio" / "04 Jurídico" / "cert.pfx")
    os.rename(inst.root / "solto.txt", inst.root / "Alfa Comércio" / "04 Jurídico" / "notas.txt")

    summary = inst.scan()

    assert summary.movidos == 2
    cert = inst.by_path("Alfa Comércio/04 Jurídico/cert.pfx")
    assert cert.estado == DocumentState.NA_ORIGEM.value and "sensível" in (cert.motivo or "")
    assert cert.owner_id == inst.owners["Alfa Comércio"].id and cert.texto is None
    notas = inst.by_path("Alfa Comércio/04 Jurídico/notas.txt")
    assert notas.estado == DocumentState.NAO_SUPORTADO.value
    assert all(p.name not in {"cert.pfx", "notas.txt"} for p in inst.content.extracted)


def test_document_left_half_way_is_indexed_again_on_the_next_scan(
    installation: Installation,
) -> None:
    inst = installation
    inst.scan()
    doc = inst.by_path("Beta Serviços/01 Fiscal/2025/boleto.pdf")
    doc.estado = DocumentState.EXTRAIDO.value  # como uma queda entre extrair e classificar
    doc.tipo_documental = None
    inst.session.commit()

    summary = inst.scan()

    assert summary.reprocessados == 1 and summary.vistos == 12
    doc = inst.by_path("Beta Serviços/01 Fiscal/2025/boleto.pdf")
    assert doc.estado == DocumentState.ARQUIVADO.value and doc.tipo_documental == "boleto"


def test_restart_option_starts_over_instead_of_resuming(installation: Installation) -> None:
    inst = installation
    with pytest.raises(ScanInterrupted):
        inst.scan(llm=FakeLlm(fail_at=3))

    summary = inst.scan(options=ScanOptions(resume=False))

    assert not summary.retomado and summary.concluido
    assert summary.vistos == 2 and summary.reprocessados == 1 and summary.novos == 10
    assert len(inst.documents()) == 13


def test_second_scan_is_idempotent(installation: Installation) -> None:
    inst = installation
    inst.scan()
    detects = len(inst.events("document.detect"))

    summary = inst.scan()

    assert summary.vistos == 13 and summary.novos == 0 and summary.reprocessados == 0
    assert len(inst.events("document.detect")) == detects
    assert len(inst.llm.calls) == 13


def test_invalid_envelope_keeps_the_text_and_the_final_state(installation: Installation) -> None:
    inst = installation

    summary = inst.scan(llm=FakeLlm(invalid_at=2))

    assert summary.concluido and summary.falhas == 0
    docs = inst.documents()
    untyped = [d for d in docs if d.tipo_documental is None and d.texto]
    assert len(untyped) == 1
    assert untyped[0].estado == DocumentState.ARQUIVADO.value
    assert untyped[0].motivo == "envelope_invalido"
    assert inst.events("document.review") == []


# --- opções e recusas -------------------------------------------------------------------------


def test_since_skips_older_files_and_indexes_newer_ones(installation: Installation) -> None:
    inst = installation
    old = time.time() - 10 * 86400
    for path in inst.root.rglob("*.pdf"):
        os.utime(path, (old, old))
    fresh = put(inst.root / "Alfa Comércio" / "02 Pessoal" / "novo.pdf", pdf("novo"))
    assert fresh.stat().st_mtime > old

    summary = inst.scan(options=ScanOptions(since=datetime.now(UTC) - timedelta(days=1)))

    assert summary.novos == 1 and summary.ignorados_por_data == 13
    assert [d.caminho for d in inst.documents()] == ["Alfa Comércio/02 Pessoal/novo.pdf"]


def test_inactive_root_is_refused(installation: Installation) -> None:
    inst = installation
    services.set_root_active(inst.session, inst.root_id, False, actor="test:setup")
    inst.session.commit()

    with pytest.raises(Conflict):
        inst.scan()
    assert inst.documents() == []
