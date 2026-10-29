"""One file from the quarantine (or a watched folder) to its destination or to review.

Flow A of SPEC §5 from step 3, with every effect behind a port: class by name (a sensitive
file is never opened), hash, duplicate, class by content, extraction, envelope, owner
evidence, decision, template tree, archiving, ``archive_operations`` and audit. The index
row moves through the states of SPEC §9 and every transition is committed before the next
step, so the state is persisted **before** the rename and a failure anywhere leaves
``FALHOU`` with a reason and the file where it was. With effects off the whole flow runs and
the archiving is recorded as simulated, without a rename.

``archive_now`` is the one place that turns a decided owner and department into a rename:
the automatic decision uses it here and the reviewer's decision uses it in
``osa.courier.review``.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Final

from osa.core.logging import get_logger, log_context
from osa.courier.decision import (
    MOTIVO_ANO_AUSENTE,
    MOTIVO_ARQUIVAMENTO_RECUSADO,
    MOTIVO_DEPARTAMENTO_INVALIDO,
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
from osa.llm.envelopes import (
    FALLBACK_REASON,
    MOTIVO_DEPARTAMENTOS_AMBIGUOS,
    ClassificationEnvelope,
)
from osa.llm.envelopes import Department as DepartmentSpec
from osa.llm.prompts import MOTIVO_INSTRUCAO_EMBUTIDA, MOTIVO_TEXTO_INSUFICIENTE
from osa.llm.provider import EnvelopeInvalid, InvalidDepartments, LLMError

log = get_logger("osa.courier")

ALVO: Final = "document"
MOTIVO_SENSIVEL: Final = "classe sensível: nunca lida"
MOTIVO_LIVRE: Final = "motivo_livre"
#: Reasons the audit may quote verbatim; anything else is model text and is only flagged.
KNOWN_REASONS: Final = frozenset(
    {
        FALLBACK_REASON,
        MOTIVO_DEPARTAMENTOS_AMBIGUOS,
        MOTIVO_INSTRUCAO_EMBUTIDA,
        MOTIVO_TEXTO_INSUFICIENTE,
        MOTIVO_ARQUIVAMENTO_RECUSADO,
        MOTIVO_DONO_AMBIGUO,
        MOTIVO_DONO_DIVERGENTE,
        MOTIVO_ENVELOPE_INVALIDO,
        MOTIVO_SEM_DONO,
        MOTIVO_ANO_AUSENTE,
        MOTIVO_DEPARTAMENTO_INVALIDO,
    }
)


def audit_reason(motivo: str | None) -> str | None:
    """The reason as the audit may store it: known codes kept, free model text flagged."""
    if motivo is None:
        return None
    parts = [part if part in KNOWN_REASONS else MOTIVO_LIVRE for part in motivo.split("; ")]
    return "; ".join(parts)


class ConfigurationError(DomainError):
    """The registry cannot be used as it is (a department description the prompt refuses).

    Raised after the document is left ``FALHOU`` with the reason: visible to the operator and
    the administrator, never turned into an archiving decision.
    """


class ArchiveRefused(DomainError):
    """The share refused the rename and nothing was moved (SPEC §5-A step 7).

    Name that does not fit, no proven exclusive rename, suffixes exhausted, missing or
    unsafe destination folder. The automatic flow turns it into a review; a reviewer's
    decision keeps the case open and shows the reason.
    """

    def __init__(self, message: str, *, destino_dir: str) -> None:
        super().__init__(message)
        self.destino_dir = destino_dir


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
class Ctx:
    """What every step of the courier needs about one document: index, filesystem, actor."""

    store: Store
    fs: FsPort
    actor: str
    effects_enabled: bool
    root: Root
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

    def left_at_origin(self, motivo: str) -> NotSupported:
        """A file the courier refuses to read: it stays where it is, ``NA_ORIGEM``."""
        self.move_to(DocumentState.NA_ORIGEM, motivo)
        self.audit("document.intake", RESULT_DENIED, motivo=motivo)
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
            motivo=audit_reason(motivo),
            case_id=case.id,
            departamento_sugerido_id=case.departamento_sugerido_id,
            **detalhes,
        )
        self.store.commit()
        return Reviewed(
            self.document.id, self.document.estado, case.id, motivo, case.departamento_sugerido_id
        )


@dataclass(slots=True)
class _Run(Ctx):
    intake: Intake
    content: ContentPort
    llm: ClassifyPort
    owner: Owner | None
    path: Path


def template_of(departments: Sequence[Department]) -> list[TemplateDepartment]:
    """The active template as the decision and the tree see it (names in NFC, as the enum)."""
    return [TemplateDepartment(d.id, nfc(d.nome), d.pasta, d.usa_ano) for d in departments]


def _is_sensitive(content: ContentPort, intake: Intake) -> bool:
    """By the original name **and** by the name on disk: neither is trusted alone."""
    on_disk = (
        intake.relative_path.rsplit("/", 1)[-1]
        if intake.relative_path is not None
        else intake.source.name
    )
    return any(
        content.class_by_name(name) is FileClass.SENSITIVE
        for name in (intake.original_name, on_disk)
    )


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
    if _is_sensitive(content, intake):
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

    document = existing if existing is not None else Document(sha256=sha256)
    document.root_id = root.id
    document.caminho = intake.relative_path
    document.nome = nfc(intake.original_name)
    document.origem = intake.origin.value
    document.enviado_por = intake.sender
    document.estado = DocumentState.DETECTADO.value
    document.motivo = None
    # A reprocessed row starts clean: nothing of the previous run survives detection.
    _clear_classification(document)
    _clear_destination(document)
    if existing is None:
        store.add_document(document)
    else:
        store.save()
    run = _Run(store, fs, actor, effects_enabled, root, document, intake, content, llm, owner, path)
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


def _clear_classification(document: Document) -> None:
    document.tipo_documental = None
    document.tags = []
    document.resumo = None
    document.datas = []
    document.texto = None


def _clear_destination(document: Document) -> None:
    """Owner, department and year stay null until an archiving (model docstring)."""
    document.owner_id = None
    document.departamento_id = None
    document.ano = None


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
    try:
        cls = content.file_class(run.path)
        if cls is FileClass.SENSITIVE:
            return run.left_at_origin(MOTIVO_SENSIVEL)
        if cls is FileClass.UNSUPPORTED:
            return run.unsupported("formato não suportado ou conteúdo diferente da extensão")
        extracted = content.extract(run.path)
    except SensitiveFile:
        return run.left_at_origin(MOTIVO_SENSIVEL)
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
        motivo_revisao=audit_reason(envelope.motivo_revisao),
        ano_referencia=envelope.ano_referencia,
    )
    store.commit()

    # 6. evidence and decision (SPEC §6)
    template = template_of(departments)
    evidence, reserva = _owner_evidence(run, extracted)
    decision = decide(evidence, envelope, template)
    if reserva is not None:
        # An ambiguous or contradicted owner is a reason of its own: it replaces "no
        # evidence" and joins any other reason, so the reviewer sees both.
        if isinstance(decision, AutoArchive) or decision.motivo == MOTIVO_SEM_DONO:
            decision = NeedsReview(reserva, decision.department)
        else:
            decision = NeedsReview(f"{decision.motivo}; {reserva}", decision.department)
    if isinstance(decision, NeedsReview):
        return run.review(
            decision.motivo,
            decision.department,
            evidencia=evidence.kind if evidence is not None else None,
        )
    owner = run.owner if run.owner is not None else store.owner(decision.owner_id)
    try:
        return archive_now(
            run,
            owner=owner,
            department=decision.department,
            template=template,
            year=decision.year,
            source_rel=intake.relative_path,
            original_name=intake.original_name,
            modo=ArchiveMode.AUTO.value,
            evidencia="declarado" if run.owner is not None else "texto",
        )
    except ArchiveRefused as error:
        # Nothing was moved and the case goes to review (SPEC §5-A step 7).
        return run.review(
            MOTIVO_ARQUIVAMENTO_RECUSADO,
            decision.department,
            erro=str(error),
            destino_dir=error.destino_dir,
        )


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


def archive_now(
    ctx: Ctx,
    *,
    owner: Owner,
    department: TemplateDepartment,
    template: Sequence[TemplateDepartment],
    year: int | None,
    source_rel: str | None,
    original_name: str,
    modo: str,
    evidencia: str,
) -> Archived | Failed:
    """Turn a decided owner, department and year into the rename (SPEC §5-A step 7).

    The decision is persisted and audited (``archive.intent``) **before** any effect. The
    template tree is completed, then the file is renamed; ``ArchiveRefused`` means the
    share refused it and nothing was moved; ``Failed`` means the rename happened but the
    source changed meanwhile (recorded, never rolled back). With effects off the
    destination is predicted and recorded as simulated.
    """
    store, fs, document = ctx.store, ctx.fs, ctx.document
    root = Path(ctx.root.caminho)
    parts = [owner.pasta, department.pasta]
    if year is not None:
        parts.append(str(year))
    dest_dir = "/".join(parts)

    document.owner_id = owner.id
    document.departamento_id = department.id
    if year is not None:
        document.ano = year
    store.save()
    ctx.audit(
        "archive.intent",
        RESULT_OK,
        owner_id=owner.id,
        departamento_id=department.id,
        ano=year,
        destino_dir=dest_dir,
        origem=source_rel,
        modo=modo,
        evidencia=evidencia,
    )
    store.commit()

    try:
        fs.ensure_owner_tree(root, owner.pasta, template, None, effects_enabled=ctx.effects_enabled)
        if year is not None:
            fs.ensure_owner_tree(
                root, owner.pasta, [department], year, effects_enabled=ctx.effects_enabled
            )
        if source_rel is None or (not ctx.effects_enabled and not fs.has_folder(root, dest_dir)):
            # Simulated run (effects are off) on a file outside the root or into a folder
            # that only the real run would create: predicted, without the collision check.
            predicted = f"{dest_dir}/{nfc(original_name)}"
            return _simulated(ctx, owner, department, predicted, suffix=1)
        result = fs.archive(
            root, source_rel, dest_dir, original_name, effects_enabled=ctx.effects_enabled
        )
    except IdentityChanged as error:
        # The rename happened and the identity check came after it: no automatic rollback.
        # The move is recorded (SPEC §5-E) so the file can be found; an undo is refused by
        # the hash, because the content is not the one of the record.
        if error.destino is not None and source_rel is not None:
            document.caminho = error.destino
            store.add_operation(
                ArchiveOperation(
                    document_id=document.id,
                    root_id=ctx.root.id,
                    origem=source_rel,
                    destino=error.destino,
                    sha256=document.sha256,
                    ator=ctx.actor,
                    modo=modo,
                )
            )
        return ctx.failed(f"identidade da origem mudou: {error}", destino=error.destino)
    except DomainError as error:
        ctx.audit("document.archive", RESULT_DENIED, erro=str(error), destino_dir=dest_dir)
        store.commit()
        raise ArchiveRefused(str(error), destino_dir=dest_dir) from error
    if result.simulated:
        return _simulated(ctx, owner, department, result.dest_rel, suffix=result.suffix)

    operation = store.add_operation(
        ArchiveOperation(
            document_id=document.id,
            root_id=ctx.root.id,
            origem=result.source_rel,
            destino=result.dest_rel,
            sha256=document.sha256,
            ator=ctx.actor,
            modo=modo,
        )
    )
    document.caminho = result.dest_rel
    ctx.move_to(DocumentState.ARQUIVADO)
    ctx.audit(
        "document.archive",
        RESULT_OK,
        destino=result.dest_rel,
        operation_id=operation.id,
        sufixo=result.suffix,
        modo=modo,
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
    ctx: Ctx, owner: Owner, department: TemplateDepartment, destino: str, *, suffix: int
) -> Archived:
    """Effects off: everything was decided, nothing was renamed (SPEC: ``simulated``)."""
    ctx.document.motivo = f"simulado: {destino}"
    ctx.store.save()
    ctx.audit("document.archive", RESULT_SIMULATED, destino=destino, sufixo=suffix)
    ctx.store.commit()
    return Archived(
        ctx.document.id, ctx.document.estado, destino, True, None, suffix, owner.id, department.id
    )
