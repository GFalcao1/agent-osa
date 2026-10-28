"""One file from the quarantine (or a watched folder) to its destination or to review.

Flow A of SPEC §5 from step 3, with every effect behind a port: class by name (a sensitive
file is never opened), hash, duplicate, class by content, extraction, envelope, owner
evidence, decision, template tree, archiving, ``archive_operations`` and audit. The index
row moves through the states of SPEC §9 and every transition is committed before the next
step, so the state is persisted **before** the rename and a failure anywhere leaves
``FALHOU`` with a reason and the file where it was. With effects off the whole flow runs and
the archiving is recorded as simulated, without a rename.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Final

from osa.core.logging import get_logger, log_context
from osa.courier.decision import (
    MOTIVO_ARQUIVAMENTO_RECUSADO,
    MOTIVO_DONO_AMBIGUO,
    MOTIVO_DONO_DIVERGENTE,
    MOTIVO_ENVELOPE_INVALIDO,
    MOTIVO_SEM_DONO,
    AutoArchive,
    NeedsReview,
    TemplateDepartment,
    decide,
)
from osa.courier.evidence import (
    AmbiguousOwner,
    OwnerEvidence,
    declared_evidence,
    find_owner_evidence,
)
from osa.courier.intake import Intake
from osa.courier.ports import ClassifyPort, ContentPort, FsPort
from osa.courier.store import Store
from osa.db.audit import RESULT_DENIED, RESULT_FAILED, RESULT_OK, RESULT_SIMULATED
from osa.db.models import ArchiveOperation, Department, Document, Owner, ReviewCase, Root
from osa.domain.document import ArchiveMode, DocumentState, FileClass, ReviewState
from osa.domain.errors import (
    Conflict,
    DomainError,
    ExtractionFailed,
    SensitiveFile,
    UnsafePath,
    Unsupported,
)
from osa.extract.model import ExtractedContent
from osa.fs.errors import IdentityChanged
from osa.fs.names import nfc
from osa.llm.envelopes import ClassificationEnvelope
from osa.llm.envelopes import Department as DepartmentSpec
from osa.llm.provider import EnvelopeInvalid, InvalidDepartments, LLMError

log = get_logger("osa.courier")

ALVO: Final = "document"
MOTIVO_SENSIVEL: Final = "classe sensível: nunca lida"


class ConfigurationError(DomainError):
    """The registry cannot be used as it is (a department description the prompt refuses).

    Raised after the document is left ``FALHOU`` with the reason: visible to the operator and
    the administrator, never turned into an archiving decision.
    """


# --- outcomes ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class Archived:
    document_id: int
    estado: str
    destino: str  # POSIX, relative to the root (predicted when simulated)
    simulated: bool
    operation_id: int | None  # None when simulated
    suffix: int  # 1 = the original name
    owner_id: int
    departamento_id: int


@dataclass(frozen=True, slots=True)
class Reviewed:
    document_id: int
    estado: str
    case_id: int
    motivo: str
    departamento_sugerido_id: int | None


@dataclass(frozen=True, slots=True)
class Duplicate:
    document_id: int
    estado: str  # of the document already indexed: ARQUIVADO or EM_REVISAO
    caminho: str | None


@dataclass(frozen=True, slots=True)
class NotSupported:
    document_id: int
    estado: str
    motivo: str


@dataclass(frozen=True, slots=True)
class Failed:
    document_id: int
    estado: str
    motivo: str


Outcome = Archived | Reviewed | Duplicate | NotSupported | Failed


# --- the run ----------------------------------------------------------------------------


@dataclass(slots=True)
class _Run:
    intake: Intake
    store: Store
    fs: FsPort
    content: ContentPort
    llm: ClassifyPort
    actor: str
    effects_enabled: bool
    root: Root
    owner: Owner | None
    path: Path
    document: Document

    def audit(self, acao: str, resultado: str, **detalhes: Any) -> None:
        """Audit on the document; ``detalhes`` are ids, hashes and reasons, never text."""
        self.store.audit(
            ator=self.actor,
            acao=acao,
            alvo_tipo=ALVO,
            alvo_id=self.document.id,
            sha256=self.document.sha256,
            resultado=resultado,
            detalhes=detalhes,
        )

    def move_to(self, estado: DocumentState, motivo: str | None = None) -> None:
        self.document.estado = estado.value
        self.document.motivo = motivo
        self.store.save()

    def failed(self, motivo: str, **detalhes: Any) -> Failed:
        self.move_to(DocumentState.FALHOU, motivo)
        self.audit("document.fail", RESULT_FAILED, motivo=motivo, **detalhes)
        self.store.commit()
        log.warning("document.failed", motivo=motivo)
        return Failed(self.document.id, self.document.estado, motivo)

    def unsupported(self, motivo: str) -> NotSupported:
        self.move_to(DocumentState.NAO_SUPORTADO, motivo)
        self.audit("document.unsupported", RESULT_DENIED, motivo=motivo)
        self.store.commit()
        return NotSupported(self.document.id, self.document.estado, motivo)

    def review(
        self, motivo: str, department: TemplateDepartment | None, **detalhes: Any
    ) -> Reviewed:
        case = ReviewCase(
            document_id=self.document.id,
            departamento_sugerido_id=department.id if department is not None else None,
            motivo=motivo,
            estado=ReviewState.ABERTO.value,
        )
        self.store.add_review_case(case)
        self.move_to(DocumentState.EM_REVISAO, motivo)
        self.audit(
            "document.review",
            RESULT_OK,
            motivo=motivo,
            case_id=case.id,
            departamento_sugerido_id=case.departamento_sugerido_id,
            **detalhes,
        )
        self.store.commit()
        return Reviewed(
            self.document.id, self.document.estado, case.id, motivo, case.departamento_sugerido_id
        )


def _template(departments: Sequence[Department]) -> list[TemplateDepartment]:
    return [TemplateDepartment(d.id, d.nome, d.pasta, d.usa_ano) for d in departments]


def process(
    intake: Intake,
    *,
    store: Store,
    fs: FsPort,
    content: ContentPort,
    llm: ClassifyPort,
    actor: str,
    effects_enabled: bool = False,
) -> Outcome:
    """Run flow A from step 3 for ``intake`` and return what happened to the document.

    Raises before touching the index only for what the caller got wrong: an unknown or
    inactive root or owner (``NotFound``/``Conflict``), a file outside the root with effects
    on (``UnsafePath``) and a file of the sensitive class (``SensitiveFile``, audited, never
    read). After the index row exists every failure becomes ``FALHOU`` and is returned as
    ``Failed``, except ``ConfigurationError``, which is raised after being recorded.
    """
    root = store.root(intake.root_id)
    if not root.ativo:
        raise Conflict("raiz inativa")
    owner: Owner | None = None
    if intake.declared_owner is not None:
        owner = store.owner(intake.declared_owner)
        if not owner.ativo:
            raise Conflict("dono inativo")
    if intake.relative_path is None and effects_enabled:
        raise UnsafePath(
            "com efeitos ligados o arquivo precisa estar na raiz (quarentena ou pasta monitorada)"
        )
    if content.class_by_name(intake.original_name) is FileClass.SENSITIVE:
        store.audit(
            ator=actor,
            acao="document.intake",
            alvo_tipo=ALVO,
            alvo_id=None,
            sha256=None,
            resultado=RESULT_DENIED,
            detalhes={"motivo": MOTIVO_SENSIVEL, "origem": intake.origin.value, "root_id": root.id},
        )
        store.commit()
        raise SensitiveFile("arquivo de classe sensível: nunca é lido nem arquivado")

    path = (
        fs.locate(Path(root.caminho), intake.relative_path)
        if intake.relative_path is not None
        else intake.source
    )
    sha256 = content.sha256(path)
    existing = store.document_by_sha256(sha256)
    if existing is not None and existing.estado in (
        DocumentState.ARQUIVADO.value,
        DocumentState.EM_REVISAO.value,
    ):
        store.audit(
            ator=actor,
            acao="document.duplicate",
            alvo_tipo=ALVO,
            alvo_id=existing.id,
            sha256=sha256,
            resultado=RESULT_DENIED,
            detalhes={"estado": existing.estado, "origem": intake.origin.value},
        )
        store.commit()
        return Duplicate(existing.id, existing.estado, existing.caminho)

    document = existing if existing is not None else Document(sha256=sha256, tags=[], datas=[])
    document.root_id = root.id
    document.caminho = intake.relative_path
    document.origem = intake.origin.value
    document.enviado_por = intake.sender
    document.estado = DocumentState.DETECTADO.value
    document.motivo = None
    if existing is None:
        store.add_document(document)
    else:
        store.save()
    run = _Run(intake, store, fs, content, llm, actor, effects_enabled, root, owner, path, document)
    run.audit(
        "document.detect",
        RESULT_OK,
        origem=intake.origin.value,
        root_id=root.id,
        reprocessado=existing is not None,
        dono_declarado=owner.id if owner is not None else None,
    )
    store.commit()
    with log_context(document_id=str(document.id)):
        return _after_detection(run)


def _after_detection(run: _Run) -> Outcome:
    try:
        return _steps(run)
    except ConfigurationError:
        raise
    except Exception as error:  # every failure leaves FALHOU; the caller never crashes
        log.exception("document.unexpected_failure", kind=type(error).__name__)
        try:
            return run.failed(f"falha inesperada ({type(error).__name__})")
        except Exception:  # the index itself is down: report the first error
            raise error from None


def _steps(run: _Run) -> Outcome:
    intake, store, content, llm = run.intake, run.store, run.content, run.llm
    document = run.document

    # 4. class by content, then extraction (SPEC §7)
    if content.file_class(run.path) is FileClass.UNSUPPORTED:
        return run.unsupported("formato não suportado ou conteúdo diferente da extensão")
    try:
        extracted = content.extract(run.path)
    except Unsupported as error:
        return run.unsupported(str(error))
    except ExtractionFailed as error:
        return run.failed(f"extração falhou: {error}")
    document.texto = extracted.text
    run.move_to(DocumentState.EXTRAIDO)
    run.audit(
        "document.extract",
        RESULT_OK,
        metodo=extracted.method,
        paginas=extracted.pages_total,
        imagens=len(extracted.images),
        truncado=extracted.truncated,
        caracteres=len(extracted.text),
    )
    store.commit()

    # 5. envelope (SPEC §8); the model never sees or chooses the owner
    departments = list(store.template())
    specs = [DepartmentSpec(d.nome, d.descricao) for d in departments]
    try:
        envelope = llm.classify(extracted, specs)
    except InvalidDepartments as error:
        motivo = f"configuração do template recusada: {error}"
        run.failed(motivo)
        raise ConfigurationError(motivo) from error
    except EnvelopeInvalid as error:
        return run.review(MOTIVO_ENVELOPE_INVALIDO, None, erro=str(error))
    except LLMError as error:
        return run.failed(f"modelo indisponível: {error}")
    _apply_envelope(document, envelope)
    run.move_to(DocumentState.CLASSIFICADO)
    run.audit(
        "document.classify",
        RESULT_OK,
        departamento=envelope.departamento,
        departamento_alternativo=envelope.departamento_alternativo,
        precisa_revisao=envelope.precisa_revisao,
        motivo_revisao=envelope.motivo_revisao,
        ano_referencia=envelope.ano_referencia,
    )
    store.commit()

    # 6. evidence and decision (SPEC §6)
    template = _template(departments)
    evidence, reserva = _owner_evidence(run, extracted)
    decision = decide(evidence, envelope, template)
    if reserva is not None:
        # An ambiguous or contradicted owner is a reason of its own; only the model's own
        # reservation and a department outside the template say more than it.
        if isinstance(decision, AutoArchive):
            decision = NeedsReview(reserva, decision.department)
        elif decision.motivo == MOTIVO_SEM_DONO:
            decision = NeedsReview(reserva, decision.department)
    if isinstance(decision, NeedsReview):
        return run.review(
            decision.motivo,
            decision.department,
            evidencia=evidence.kind if evidence is not None else None,
        )
    return _archive(run, decision, template, intake)


def _apply_envelope(document: Document, envelope: ClassificationEnvelope) -> None:
    document.tipo_documental = envelope.tipo_documental
    document.tags = list(envelope.tags)
    document.resumo = envelope.resumo
    document.datas = list(envelope.datas)
    document.ano = envelope.ano_referencia


def _owner_evidence(
    run: _Run, extracted: ExtractedContent
) -> tuple[OwnerEvidence | None, str | None]:
    """The evidence the decision uses and, when the text contradicts it, the reservation.

    A declared owner wins over inference (SPEC §6). The text is still searched: when it
    names, without ambiguity, a different registered owner and never the declared one, the
    case is a review (``dono_divergente``); when it names several owners, the declared one
    among them, the declaration stands. Without a declared owner the text is the only
    evidence: several owners are ambiguous, a review.
    """
    declared = run.owner
    try:
        inferred = find_owner_evidence(extracted.text, run.store.owner_candidates())
    except AmbiguousOwner as ambiguous:
        if declared is None:
            run.audit("document.evidence", RESULT_OK, ambiguo=list(ambiguous.owner_ids))
            return None, MOTIVO_DONO_AMBIGUO
        if declared.id in ambiguous.owner_ids:
            return declared_evidence(declared.id), None
        run.audit(
            "document.evidence",
            RESULT_OK,
            declarado=declared.id,
            divergente=list(ambiguous.owner_ids),
        )
        return declared_evidence(declared.id), MOTIVO_DONO_DIVERGENTE
    if declared is None:
        return inferred, None
    if inferred is not None and inferred.owner_id != declared.id:
        run.audit(
            "document.evidence",
            RESULT_OK,
            declarado=declared.id,
            divergente=[inferred.owner_id],
            tipo=inferred.kind,
        )
        return declared_evidence(declared.id), MOTIVO_DONO_DIVERGENTE
    return declared_evidence(declared.id), None


def _archive(
    run: _Run,
    decision: AutoArchive,
    template: Sequence[TemplateDepartment],
    intake: Intake,
) -> Outcome:
    store, fs, document = run.store, run.fs, run.document
    owner = run.owner if run.owner is not None else store.owner(decision.owner_id)
    department = decision.department
    root = Path(run.root.caminho)
    parts = [owner.pasta, department.pasta]
    if decision.year is not None:
        parts.append(str(decision.year))
    dest_dir = "/".join(parts)

    # The decision is persisted before any effect (SPEC §9: state first, rename after).
    document.owner_id = owner.id
    document.departamento_id = department.id
    if decision.year is not None:
        document.ano = decision.year
    store.save()
    run.audit(
        "archive.intent",
        RESULT_OK,
        owner_id=owner.id,
        departamento_id=department.id,
        ano=decision.year,
        destino_dir=dest_dir,
        origem=intake.relative_path,
        evidencia="declarado" if run.owner is not None else "texto",
    )
    store.commit()

    try:
        fs.ensure_owner_tree(root, owner.pasta, template, None, effects_enabled=run.effects_enabled)
        if decision.year is not None:
            fs.ensure_owner_tree(
                root, owner.pasta, [department], decision.year, effects_enabled=run.effects_enabled
            )
        if intake.relative_path is None or (
            not run.effects_enabled and not fs.has_folder(root, dest_dir)
        ):
            # Simulated run (effects are off, checked at the start) on a file outside the root
            # or into a folder that only the real run would create: the destination is
            # predicted without the collision check.
            predicted = f"{dest_dir}/{nfc(intake.original_name)}"
            return _simulated(run, owner, department, predicted, suffix=1)
        result = fs.archive(
            root,
            intake.relative_path,
            dest_dir,
            intake.original_name,
            effects_enabled=run.effects_enabled,
        )
    except IdentityChanged as error:
        # The rename happened and the identity check came after it: no automatic rollback.
        if error.destino is not None:
            document.caminho = error.destino
        return run.failed(f"identidade da origem mudou: {error}", destino=error.destino)
    except DomainError as error:
        # Name too long, no proven exclusive rename, suffixes exhausted, folder missing or
        # unsafe: nothing was moved and the case goes to review (SPEC §5-A step 7).
        return run.review(
            MOTIVO_ARQUIVAMENTO_RECUSADO, department, erro=str(error), destino_dir=dest_dir
        )
    if result.simulated:
        return _simulated(run, owner, department, result.dest_rel, suffix=result.suffix)

    operation = store.add_operation(
        ArchiveOperation(
            document_id=document.id,
            root_id=run.root.id,
            origem=result.source_rel,
            destino=result.dest_rel,
            sha256=document.sha256,
            ator=run.actor,
            modo=ArchiveMode.AUTO.value,
        )
    )
    document.caminho = result.dest_rel
    run.move_to(DocumentState.ARQUIVADO)
    run.audit(
        "document.archive",
        RESULT_OK,
        destino=result.dest_rel,
        operation_id=operation.id,
        sufixo=result.suffix,
        primitiva=result.primitive.value if result.primitive is not None else None,
    )
    store.commit()
    log.info("document.archived", destino=result.dest_rel, operation_id=operation.id)
    return Archived(
        document.id,
        document.estado,
        result.dest_rel,
        False,
        operation.id,
        result.suffix,
        owner.id,
        department.id,
    )


def _simulated(
    run: _Run, owner: Owner, department: TemplateDepartment, destino: str, *, suffix: int
) -> Archived:
    """Effects off: everything was decided, nothing was renamed (SPEC: ``simulated``)."""
    run.document.motivo = f"simulado: {destino}"
    run.store.save()
    run.audit("document.archive", RESULT_SIMULATED, destino=destino, sufixo=suffix)
    run.store.commit()
    return Archived(
        run.document.id, run.document.estado, destino, True, None, suffix, owner.id, department.id
    )
