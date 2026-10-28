"""O correio contra o PostgreSQL descartável e uma raiz de teste em ``tmp_path``.

Extração e modelo são falsos; índice, operações, revisão, auditoria, quarentena e o rename
são reais. Pula quando o volume de teste não comprova rename exclusivo (como a suite de
filesystem faz).
"""

from __future__ import annotations

import hashlib
import json
import os
from collections.abc import Iterator, Sequence
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from osa.admin import services
from osa.courier import owners
from osa.courier.intake import Intake, stage_upload
from osa.courier.pipeline import Archived, Reviewed, process
from osa.courier.ports import LocalContent, LocalFs
from osa.courier.store import SqlStore
from osa.courier.undo import undo_operation
from osa.db.models import ArchiveOperation, AuditEvent, Department, Document, Owner, ReviewCase
from osa.domain.document import Origin
from osa.domain.errors import AccessDenied, Conflict
from osa.extract.model import DEFAULT_SENSITIVE_EXTENSIONS, ExtractedContent, ExtractLimits
from osa.fs import noreplace
from osa.fs.errors import NoReplaceUnavailable
from osa.fs.names import QUARANTINE_DIR
from osa.llm.envelopes import ClassificationEnvelope

ACTOR = "test:courier"
PDF = b"%PDF-1.4\n% documento ficticio para o correio\n%%EOF\n"


def make_cnpj(base12: str) -> str:
    def digit(digits: str, weights: list[int]) -> str:
        rest = sum(int(d) * w for d, w in zip(digits, weights, strict=True)) % 11
        return "0" if rest < 2 else str(11 - rest)

    first = digit(base12, [5, 4, 3, 2, 9, 8, 7, 6, 5, 4, 3, 2])
    second = digit(base12 + first, [6, 5, 4, 3, 2, 9, 8, 7, 6, 5, 4, 3, 2])
    return base12 + first + second


CNPJ_ALFA = make_cnpj("112223330001")
CNPJ_BETA = make_cnpj("445556660001")
TEXT = f"NOTA FISCAL DE SERVIÇO\nTomador: Alfa Comércio Ltda, CNPJ {CNPJ_ALFA}\nValor: R$ 100,00"


def mounted(_path: Path) -> bool:
    return True


class FakeContent(LocalContent):
    """Real class sniff and hash; the extraction is replaced by a fixed text."""

    def __init__(self, text: str = TEXT) -> None:
        super().__init__(ExtractLimits(sensitive_extensions=DEFAULT_SENSITIVE_EXTENSIONS))
        self.text = text

    def extract(self, path: Path) -> ExtractedContent:
        return ExtractedContent(
            text=self.text, images=(), pages_total=1, truncated=False, method="pdf_text"
        )


class FakeLlm:
    def __init__(self, envelope: ClassificationEnvelope) -> None:
        self.envelope = envelope
        self.calls: list[tuple[Any, ...]] = []

    def classify(self, content: ExtractedContent, departments: Sequence[Any]) -> Any:
        self.calls.append(tuple(departments))
        return self.envelope


def envelope(*, revisao: bool = False, motivo: str | None = None) -> ClassificationEnvelope:
    return ClassificationEnvelope(
        departamento="Fiscal",
        departamento_alternativo=None,
        tipo_documental="nota fiscal de serviço",
        tags=["nota"],
        resumo="Nota fiscal fictícia.",
        datas=[date(2026, 1, 31)],
        ano_referencia=2026,
        precisa_revisao=revisao,
        motivo_revisao=motivo,
    )


@dataclass
class Installation:
    session: Session
    root: Path
    root_id: int
    alfa: Owner
    beta: Owner
    fiscal: Department
    admin_id: int
    operator_id: int

    def events(self, acao: str | None = None) -> list[AuditEvent]:
        query = select(AuditEvent).where(AuditEvent.ator == ACTOR).order_by(AuditEvent.id)
        if acao is not None:
            query = query.where(AuditEvent.acao == acao)
        return list(self.session.scalars(query))

    def document(self) -> Document:
        return self.session.scalars(select(Document)).one()


def _require_noreplace(root: Path) -> None:
    fd = os.open(root, os.O_RDONLY | os.O_DIRECTORY)
    try:
        noreplace.proven_primitive(fd)
    except NoReplaceUnavailable as error:
        pytest.skip(f"volume de teste sem rename exclusivo comprovado: {error}")
    finally:
        os.close(fd)


@pytest.fixture
def installation(session: Session, tmp_path: Path) -> Iterator[Installation]:
    root = tmp_path.resolve() / "raiz"
    root.mkdir()
    _require_noreplace(root)
    admin = services.bootstrap_admin(
        session, telegram_id=1001, nome="Admin Ficticio", actor="test:bootstrap"
    )
    registered = services.create_root(
        session, caminho=str(root), is_mount_point=mounted, actor="test:setup"
    )
    fiscal = services.create_department(
        session, nome="Fiscal", pasta="01 Fiscal", usa_ano=True,
        descricao="Notas fiscais, guias e certidões", actor="test:setup",
    )  # fmt: skip
    services.create_department(session, nome="Pessoal", pasta="02 Pessoal", actor="test:setup")
    alfa = services.create_owner(
        session, nome_fantasia="Alfa Comércio", razao_social="Alfa Comércio Ltda",
        cnpj=CNPJ_ALFA, aliases=["Alfa"], actor="test:setup",
    )  # fmt: skip
    beta = services.create_owner(
        session, nome_fantasia="Beta Serviços", cnpj=CNPJ_BETA, actor="test:setup"
    )
    operator = services.create_user(
        session, telegram_id=2002, nome="Maria Ficticia", papel="operator",
        departamento_id=fiscal.id, actor="test:setup",
    )  # fmt: skip
    session.commit()
    yield Installation(session, root, registered.id, alfa, beta, fiscal, admin.id, operator.id)


def _stage(inst: Installation, name: str = "nota.pdf", data: bytes = PDF) -> str:
    staged = stage_upload(
        inst.root, data, name, sensitive_extensions=DEFAULT_SENSITIVE_EXTENSIONS,
        effects_enabled=True,
    )  # fmt: skip
    assert not staged.simulated and staged.path.read_bytes() == data
    return staged.relative_path


def _process(
    inst: Installation,
    relative: str,
    *,
    env: ClassificationEnvelope | None = None,
    declared: int | None = None,
    name: str = "nota.pdf",
    effects: bool = True,
) -> Any:
    intake = Intake(
        inst.root_id, inst.root / relative, relative, name, Origin.TELEGRAM,
        declared_owner=declared, sender=inst.operator_id,
    )  # fmt: skip
    return process(
        intake,
        store=SqlStore(inst.session),
        fs=LocalFs(),
        content=FakeContent(),
        llm=FakeLlm(env or envelope()),
        actor=ACTOR,
        effects_enabled=effects,
    )


DEST = "Alfa Comércio/01 Fiscal/2026/nota.pdf"


# --- quarentena -------------------------------------------------------------------------------


def test_stage_upload_simulates_on_a_root_without_quarantine(installation: Installation) -> None:
    inst = installation
    staged = stage_upload(
        inst.root,
        PDF,
        "nota.pdf",
        sensitive_extensions=DEFAULT_SENSITIVE_EXTENSIONS,
        effects_enabled=False,
    )
    assert staged.simulated and staged.relative_path.startswith(f"{QUARANTINE_DIR}/")
    assert staged.relative_path.endswith(".pdf") and not (inst.root / QUARANTINE_DIR).exists()
    real = _stage(inst)
    assert (inst.root / QUARANTINE_DIR).is_dir()
    predicted = stage_upload(
        inst.root,
        PDF,
        "nota.pdf",
        sensitive_extensions=DEFAULT_SENSITIVE_EXTENSIONS,
        effects_enabled=False,
    )
    assert predicted.simulated and not (inst.root / predicted.relative_path).exists()
    assert [p.name for p in (inst.root / QUARANTINE_DIR).iterdir()] == [real.rsplit("/", 1)[1]]


# --- fluxo A ------------------------------------------------------------------------------


def test_flow_a_archives_by_rename_and_persists_index_operation_and_audit(
    installation: Installation,
) -> None:
    inst = installation
    relative = _stage(inst)
    assert relative.startswith(f"{QUARANTINE_DIR}/") and relative.endswith(".pdf")

    outcome = _process(inst, relative, declared=inst.alfa.id)

    assert isinstance(outcome, Archived) and not outcome.simulated
    assert outcome.destino == DEST
    assert (inst.root / DEST).read_bytes() == PDF
    assert not (inst.root / relative).exists()

    document = inst.document()
    assert (document.estado, document.caminho, document.motivo) == ("ARQUIVADO", DEST, None)
    assert (document.owner_id, document.departamento_id, document.ano) == (
        inst.alfa.id, inst.fiscal.id, 2026,
    )  # fmt: skip
    assert (document.origem, document.enviado_por, document.root_id) == (
        "telegram", inst.operator_id, inst.root_id,
    )  # fmt: skip
    assert document.sha256 == hashlib.sha256(PDF).hexdigest()
    assert document.texto == TEXT and document.tags == ["nota"]
    assert document.datas == [date(2026, 1, 31)]

    [operation] = inst.session.scalars(select(ArchiveOperation)).all()
    assert (operation.document_id, operation.root_id) == (document.id, inst.root_id)
    assert (operation.origem, operation.destino, operation.modo) == (relative, DEST, "auto")
    assert (operation.sha256, operation.ator) == (document.sha256, ACTOR)
    assert outcome.operation_id == operation.id

    assert [e.acao for e in inst.events()] == [
        "document.detect", "document.extract", "document.classify", "archive.intent",
        "document.archive",
    ]  # fmt: skip
    for event in inst.events():
        assert (event.alvo_tipo, event.alvo_id, event.sha256) == (
            "document",
            str(document.id),
            document.sha256,
        )
        assert "NOTA FISCAL" not in json.dumps(event.detalhes, ensure_ascii=False)
    assert inst.events("document.archive")[0].detalhes["destino"] == DEST


def test_owner_is_inferred_from_the_text_when_not_declared(installation: Installation) -> None:
    inst = installation
    outcome = _process(inst, _stage(inst))
    assert isinstance(outcome, Archived) and outcome.owner_id == inst.alfa.id
    assert inst.events("archive.intent")[0].detalhes["evidencia"] == "texto"


def test_review_case_is_persisted_and_the_file_stays_in_quarantine(
    installation: Installation,
) -> None:
    inst = installation
    relative = _stage(inst)

    outcome = _process(inst, relative, env=envelope(revisao=True, motivo="texto_insuficiente"))

    assert isinstance(outcome, Reviewed) and outcome.motivo == "texto_insuficiente"
    [case] = inst.session.scalars(select(ReviewCase)).all()
    assert (case.estado, case.motivo, case.departamento_sugerido_id) == (
        "aberto",
        "texto_insuficiente",
        inst.fiscal.id,
    )
    document = inst.document()
    assert (document.estado, document.caminho) == ("EM_REVISAO", relative)
    assert (inst.root / relative).exists()
    assert inst.session.scalars(select(ArchiveOperation)).all() == []
    assert [e.acao for e in inst.events()][-1] == "document.review"


def test_effects_off_simulates_against_the_real_tree(installation: Installation) -> None:
    inst = installation
    relative = _stage(inst)
    outcome = _process(inst, relative, declared=inst.alfa.id, effects=False)
    assert isinstance(outcome, Archived) and outcome.simulated
    assert outcome.destino == DEST and outcome.operation_id is None
    assert (inst.root / relative).exists() and not (inst.root / "Alfa Comércio").exists()
    assert inst.document().estado == "CLASSIFICADO"
    assert inst.events("document.archive")[0].resultado == "simulated"


def test_duplicate_upload_is_informed_without_a_second_row(installation: Installation) -> None:
    inst = installation
    first = _process(inst, _stage(inst), declared=inst.alfa.id)
    second = _process(inst, _stage(inst, name="copia.pdf"), declared=inst.alfa.id, name="copia.pdf")
    assert second.document_id == first.document_id and second.estado == "ARQUIVADO"
    assert len(inst.session.scalars(select(Document)).all()) == 1


# --- desfazer -----------------------------------------------------------------------------


def test_undo_moves_the_file_back_and_records_the_reverse_operation(
    installation: Installation,
) -> None:
    inst = installation
    relative = _stage(inst)
    archived = _process(inst, relative, declared=inst.alfa.id)
    assert archived.operation_id is not None

    result = undo_operation(
        archived.operation_id, store=SqlStore(inst.session), fs=LocalFs(), actor=ACTOR,
        effects_enabled=True,
    )  # fmt: skip

    assert result.destino == relative and not result.simulated
    assert (inst.root / relative).read_bytes() == PDF and not (inst.root / DEST).exists()
    operations = inst.session.scalars(select(ArchiveOperation).order_by(ArchiveOperation.id)).all()
    assert [o.modo for o in operations] == ["auto", "undo"]
    assert (operations[1].origem, operations[1].destino) == (DEST, relative)
    assert (operations[1].root_id, operations[1].sha256) == (inst.root_id, operations[0].sha256)
    document = inst.document()
    assert (document.estado, document.caminho) == ("NA_ORIGEM", relative)
    assert inst.events("archive.undo")[-1].resultado == "ok"
    with pytest.raises(Conflict, match="já desfeito"):
        undo_operation(
            archived.operation_id, store=SqlStore(inst.session), fs=LocalFs(), actor=ACTOR,
            effects_enabled=True,
        )  # fmt: skip


# --- cadastro visto pelo correio ------------------------------------------------------------


def test_store_reads_only_active_owners_and_departments_in_template_order(
    installation: Installation,
) -> None:
    inst = installation
    services.set_owner_active(inst.session, inst.beta.id, False, actor="test:setup")
    arquivo = services.create_department(inst.session, nome="Arquivo", actor="test:setup")
    services.set_department_active(inst.session, arquivo.id, False, actor="test:setup")
    store = SqlStore(inst.session)
    assert [c.id for c in store.owner_candidates()] == [inst.alfa.id]
    assert store.owner_candidates()[0].aliases == ("alfa comercio", "alfa comercio ltda", "alfa")
    assert [d.nome for d in store.template()] == ["Fiscal", "Pessoal"]
    assert store.template()[0].descricao == "Notas fiscais, guias e certidões"


def test_declared_owner_resolves_by_alias_or_cnpj_only_when_active(
    installation: Installation,
) -> None:
    inst = installation
    assert owners.resolve_declared_owner(inst.session, " ALFA ") == inst.alfa
    assert owners.resolve_declared_owner(inst.session, "Alfa Comércio Ltda.") == inst.alfa
    assert owners.resolve_declared_owner(inst.session, CNPJ_BETA) == inst.beta
    assert owners.resolve_declared_owner(inst.session, "Gama") is None
    assert owners.resolve_declared_owner(inst.session, "") is None
    services.set_owner_active(inst.session, inst.beta.id, False, actor="test:setup")
    assert owners.resolve_declared_owner(inst.session, CNPJ_BETA) is None


def test_upload_root_prefers_the_root_that_already_holds_the_owner(
    installation: Installation, tmp_path: Path
) -> None:
    inst = installation
    second = tmp_path.resolve() / "segunda"
    second.mkdir()
    other = services.create_root(
        inst.session, caminho=str(second), is_mount_point=mounted, actor="test:setup"
    )
    assert owners.upload_root(inst.session, inst.alfa, fs=LocalFs()).id == inst.root_id
    (second / inst.alfa.pasta).mkdir()
    assert owners.upload_root(inst.session, inst.alfa, fs=LocalFs()).id == other.id
    services.set_root_active(inst.session, other.id, False, actor="test:setup")
    assert owners.upload_root(inst.session, inst.alfa, fs=LocalFs()).id == inst.root_id


def test_new_owner_needs_an_administrator_and_gets_the_template_tree(
    installation: Installation,
) -> None:
    inst = installation
    request = owners.OwnerRequest(nome="Gama Indústria", requested_by=inst.operator_id)
    with pytest.raises(AccessDenied):
        owners.confirm_owner_request(
            inst.session, request, admin_id=inst.operator_id, fs=LocalFs(),
            effects_enabled=True, actor=ACTOR,
        )  # fmt: skip
    assert owners.resolve_declared_owner(inst.session, "Gama Indústria") is None

    owner = owners.confirm_owner_request(
        inst.session, request, admin_id=inst.admin_id, fs=LocalFs(), effects_enabled=True,
        actor=ACTOR,
    )  # fmt: skip

    assert owners.resolve_declared_owner(inst.session, "gama industria") == owner
    assert (inst.root / "Gama Indústria" / "01 Fiscal").is_dir()
    assert (inst.root / "Gama Indústria" / "02 Pessoal").is_dir()
    [confirmed] = inst.events("owner.request.confirm")
    assert confirmed.detalhes == {"solicitante": inst.operator_id, "administrador": inst.admin_id}
    [tree] = inst.events("owner.tree")
    assert tree.detalhes == {"root_id": inst.root_id, "pastas_criadas": 3}
    assert confirmed.id < tree.id
    with pytest.raises(Conflict, match="já cadastrado"):
        owners.confirm_owner_request(
            inst.session, request, admin_id=inst.admin_id, fs=LocalFs(), effects_enabled=True,
            actor=ACTOR,
        )  # fmt: skip

    owners.refuse_owner_request(
        inst.session, owners.OwnerRequest("Delta", inst.operator_id), admin_id=inst.admin_id,
        actor=ACTOR,
    )  # fmt: skip
    assert inst.events("owner.request.refuse")[0].resultado == "denied"
    assert owners.resolve_declared_owner(inst.session, "Delta") is None
