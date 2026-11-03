"""Scan of a root: feed the index with what is already in the tree, moving nothing.

For every regular file under a root (the quarantine and dot-names excluded) the scan hashes
the content, places the document by its path (``<dono>/<departamento>/[ano]/...``, matched
against the registry by folder name) and, for a new document, extracts the text and asks the
model for the metadata (type, tags, summary, dates). The model never chooses owner or
department here: the path is the evidence, and a path outside the pattern leaves the
document ``NA_ORIGEM`` with no owner. Nothing is archived, created or sent to review.

The scan is resumable per root: every file is committed with the root's cursor, so a run
interrupted by a crash or by the model going away (``ScanInterrupted``) continues from the
last committed path, and a document left half-way (``DETECTADO``, ``EXTRAIDO``...) at the
same path is indexed again. A full scan (no ``since``) ends by marking the documents of the
root it did not see as absent; they stay in the index.
"""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, fields
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Final

from osa.core.logging import get_logger, log_context
from osa.courier.decision import MOTIVO_ENVELOPE_INVALIDO, TemplateDepartment
from osa.courier.pipeline import (
    ALVO,
    MOTIVO_MODELO_INDISPONIVEL,
    MOTIVO_SENSIVEL,
    ConfigurationError,
    audit_reason,
    template_of,
)
from osa.courier.ports import ClassifyPort, ContentPort, FsPort
from osa.courier.store import Store
from osa.db.audit import RESULT_DENIED, RESULT_FAILED, RESULT_OK
from osa.db.models import Document, Owner, Root
from osa.domain.document import DocumentState, FileClass, Origin
from osa.domain.errors import (
    Conflict,
    DomainError,
    ExtractionFailed,
    NotFound,
    SensitiveFile,
    UnsafePath,
    Unsupported,
)
from osa.fs.names import component_key, nfc
from osa.fs.walk import FileEntry, SkippedDir, prune_key
from osa.llm.envelopes import ClassificationEnvelope
from osa.llm.envelopes import Department as DepartmentSpec
from osa.llm.provider import EnvelopeInvalid, InvalidDepartments, LLMError

log = get_logger("osa.scan")

MOTIVO_FORA_DO_PADRAO: Final = "caminho fora do padrão dono/departamento"
MOTIVO_MOVIDO: Final = "movido_manualmente"
ALVO_RAIZ: Final = "root"
#: A document at its known path in one of these states is indexed again: it failed, or a
#: previous scan was interrupted between its detection and its final state.
REPROCESS_STATES: Final = frozenset(
    {
        DocumentState.FALHOU.value,
        DocumentState.DETECTADO.value,
        DocumentState.ESTAVEL.value,
        DocumentState.EXTRAIDO.value,
        DocumentState.CLASSIFICADO.value,
    }
)
_YEAR = re.compile(r"^\d{4}$")
_MIN_YEAR, _MAX_YEAR = 1900, 2100


class ScanInterrupted(DomainError):
    """The scan stopped before the end of the root (the model went away, a root problem).

    What was committed stays; the next run resumes from the root's cursor.
    """


@dataclass(frozen=True, slots=True)
class ScanOptions:
    """How to run the scan.

    ``since``: only files modified at or after it (an incremental pass that never marks
    anything absent). ``resume``: continue an interrupted scan from its cursor instead of
    starting over. ``batch_size``: unchanged files seen per commit of the cursor.
    """

    since: datetime | None = None
    resume: bool = True
    batch_size: int = 50
    #: Stop (resumable) after this many documents went to the model, or this many files were
    #: visited (each one hashed), in this call; the worker's periodic rescan uses both so
    #: watching never waits for a whole archive.
    max_indexed: int | None = None
    max_visited: int | None = None


@dataclass(frozen=True, slots=True)
class Placement:
    """Owner, department and year read from the path; ``None`` outside the pattern."""

    owner: Owner | None
    department: TemplateDepartment | None
    year: int | None

    @property
    def in_pattern(self) -> bool:
        return self.owner is not None and self.department is not None


@dataclass(slots=True)
class ScanSummary:
    root_id: int
    retomado: bool = False
    concluido: bool = False
    arquivos: int = 0  # regular files visited
    novos: int = 0
    vistos: int = 0  # already indexed at the same path, nothing to do
    reprocessados: int = 0  # failed or half-indexed documents indexed again
    movidos: int = 0
    duplicatas: int = 0
    fora_do_padrao: int = 0
    sensiveis: int = 0
    nao_suportados: int = 0
    falhas: int = 0
    pastas_ilegiveis: int = 0
    ilegiveis: int = 0  # files the hash could not read; no absent marking in that run
    ausentes: int = 0
    ignorados_por_data: int = 0

    def as_details(self) -> dict[str, int | bool]:
        return {f.name: getattr(self, f.name) for f in fields(self) if f.name != "root_id"}


# --- placement by path (pure) ------------------------------------------------------------


def now_utc() -> datetime:
    return datetime.now(UTC)


def year_component(part: str) -> int | None:
    """``2025`` as a year; anything else (``2025 - antigo``, ``0001``) is not a year."""
    if not _YEAR.fullmatch(part):
        return None
    year = int(part)
    return year if _MIN_YEAR <= year <= _MAX_YEAR else None


def place(
    parts: Sequence[str],
    owners: Mapping[str, Owner],
    departments: Mapping[str, TemplateDepartment],
) -> Placement:
    """Where the path says the document belongs (SPEC §4): ``<dono>/<departamento>/[ano]/``.

    ``owners`` and ``departments`` are keyed by ``component_key`` of the folder name, so case
    and Unicode form do not matter (the share ignores them too). Deeper subfolders are
    allowed under the department (or the year). Owner without a known department, or the
    reverse, is outside the pattern: both or nothing, as the task states (dono nulo).
    """
    if len(parts) < 3:
        return Placement(None, None, None)
    owner = owners.get(component_key(parts[0]))
    department = departments.get(component_key(parts[1]))
    if owner is None or department is None:
        return Placement(None, None, None)
    year = year_component(parts[2]) if len(parts) >= 4 and department.usa_ano else None
    return Placement(owner, department, year)


def final_state(placement: Placement) -> tuple[str, str | None]:
    """In the pattern the document is where the archive keeps it; outside, it is at origin."""
    if placement.in_pattern:
        return DocumentState.ARQUIVADO.value, None
    return DocumentState.NA_ORIGEM.value, MOTIVO_FORA_DO_PADRAO


# --- the run ------------------------------------------------------------------------------


@dataclass(slots=True)
class _Run:
    store: Store
    fs: FsPort
    content: ContentPort
    llm: ClassifyPort
    actor: str
    root: Root
    owners: dict[str, Owner]
    departments: dict[str, TemplateDepartment]
    specs: list[DepartmentSpec]
    started: datetime
    summary: ScanSummary
    batch_size: int = 50
    watched: tuple[str, ...] = ()  # watched folders of the root: the worker's, never scanned
    pending: int = 0  # unchanged files seen since the last commit
    before: str | None = None  # the cursor before the file in progress (where a stop resumes)

    @property
    def root_path(self) -> Path:
        return Path(self.root.caminho)

    def audit(self, document: Document, acao: str, resultado: str, **detalhes: Any) -> None:
        """Audit on the document: ids, hashes, paths and reasons, never text."""
        self.store.audit(
            ator=self.actor,
            acao=acao,
            alvo_tipo=ALVO,
            alvo_id=document.id,
            sha256=document.sha256,
            resultado=resultado,
            detalhes=detalhes,
        )

    def audit_root(self, acao: str, resultado: str, **detalhes: Any) -> None:
        if acao == "scan.skip":
            self.root.scan_pulou = True  # kept on the root across the parts of the scan
        self.store.audit(
            ator=self.actor,
            acao=acao,
            alvo_tipo=ALVO_RAIZ,
            alvo_id=self.root.id,
            sha256=None,
            resultado=resultado,
            detalhes=detalhes,
        )

    def commit(self, cursor: str | None) -> None:
        self.root.scan_cursor = cursor
        self.store.save()
        self.store.commit()
        self.pending = 0

    def done(self, document: Document) -> None:
        """A document reached its final state: the cursor advances past it."""
        self.commit(document.caminho)

    def touch(self, cursor: str) -> None:
        """An unchanged file: the cursor advances, committed every ``batch_size`` files."""
        self.pending += 1
        self.root.scan_cursor = cursor
        if self.pending >= self.batch_size:
            self.commit(cursor)


def scan_root(
    root_id: int,
    *,
    store: Store,
    fs: FsPort,
    content: ContentPort,
    llm: ClassifyPort,
    actor: str,
    options: ScanOptions = ScanOptions(),
) -> ScanSummary:
    """Scan one active root and return what was done.

    Raises ``ScanInterrupted`` when the root could not be finished (the model went away, the
    root became unreadable): the cursor stays and the next run resumes. Raises
    ``ConfigurationError`` when the template cannot go to the model.
    """
    root = store.root(root_id)
    if not root.ativo:
        raise Conflict("raiz inativa")
    with store.scan_lock(root.id) as held:
        if not held:
            raise Conflict("outro scan desta raiz está em andamento")
        return _scan_held(root, store=store, fs=fs, content=content, llm=llm, actor=actor,
                          options=options)  # fmt: skip


def _scan_held(
    root: Root,
    *,
    store: Store,
    fs: FsPort,
    content: ContentPort,
    llm: ClassifyPort,
    actor: str,
    options: ScanOptions,
) -> ScanSummary:
    owners = {component_key(o.pasta): o for o in store.active_owners()}
    departments_rows = list(store.template())
    departments = {component_key(d.pasta): d for d in template_of(departments_rows)}
    specs = [DepartmentSpec(d.nome, d.descricao) for d in departments_rows]
    now = datetime.now(UTC)
    summary = ScanSummary(root.id)
    resumed = (
        options.resume
        and root.scan_iniciado_em is not None
        and _same_instant(root.scan_desde, options.since)
    )
    if resumed:
        assert root.scan_iniciado_em is not None
        started = root.scan_iniciado_em
        summary.retomado = True
    else:
        # A new scan, also when an interrupted one had a different ``since``: the absent
        # marking of a full scan must not inherit a partial pass.
        started = now
        root.scan_iniciado_em = now
        root.scan_desde = options.since
        root.scan_pulou = False
        root.scan_cursor = None
    run = _Run(
        store, fs, content, llm, actor, root, owners, departments, specs, started, summary,
        batch_size=max(1, options.batch_size),
        # Every watched folder of the root, active or not: the scan never reads one.
        watched=tuple(f.caminho for f in store.watched_folders(root.id, active_only=False)),
    )  # fmt: skip
    run.audit_root(
        "scan.start",
        RESULT_OK,
        retomado=resumed,
        cursor=root.scan_cursor,
        desde=options.since.isoformat() if options.since else None,
    )
    store.commit()
    with log_context(root_id=str(root.id)):
        try:
            paused = _walk(run, options)
        except ConfigurationError as error:
            _stop(run, str(error))
            raise
        except LLMError as error:
            _stop(run, f"modelo indisponível: {error}")
            raise ScanInterrupted(f"modelo indisponível: {error}") from error
        except (UnsafePath, OSError) as error:
            _stop(run, f"raiz ilegível: {error}")
            raise ScanInterrupted(f"raiz ilegível: {error}") from error
        if paused:
            store.commit()
            run.audit_root("scan.pause", RESULT_OK, cursor=root.scan_cursor, **summary.as_details())
            store.commit()
            log.info("scan.paused", **summary.as_details())
            return summary
        # A skipped folder or file in this run or in an earlier part of the same scan: what
        # the scan did not see may still be there, so nothing is marked absent.
        if options.since is None and not root.scan_pulou:
            _mark_absent(run)
        root.scan_cursor = None
        root.scan_iniciado_em = None
        root.scan_desde = None
        root.ultimo_scan = datetime.now(UTC)
        summary.concluido = True
        run.audit_root("scan.finish", RESULT_OK, **summary.as_details())
        store.commit()
    log.info("scan.finished", **summary.as_details())
    return summary


def _same_instant(stored: datetime | None, wanted: datetime | None) -> bool:
    if stored is None or wanted is None:
        return stored is None and wanted is None
    return stored.timestamp() == wanted.timestamp()


def same_path(a: str | None, b: str) -> bool:
    """The same file as the share sees it: component by component, no case, no Unicode form."""
    if a is None:
        return False
    left, right = a.split("/"), b.split("/")
    return len(left) == len(right) and all(
        component_key(x) == component_key(y) for x, y in zip(left, right, strict=True)
    )


def _stop(run: _Run, motivo: str) -> None:
    """Record why the scan stopped and put the cursor before the file in progress.

    What that file's steps committed (``FALHOU`` with the reason, its text) stays; the next
    run sees it again at the same path and indexes it again.
    """
    run.store.commit()
    run.root.scan_cursor = run.before
    run.audit_root("scan.stop", RESULT_FAILED, motivo=motivo, cursor=run.before)
    run.store.commit()


def _walk(run: _Run, options: ScanOptions) -> bool:
    """Walk from the cursor; True when it stopped on a budget (``max_indexed``/``max_visited``)."""
    since = options.since.timestamp() if options.since is not None else None
    budget = options.max_indexed
    for visit in run.fs.walk(run.root_path, after=run.root.scan_cursor, skip=run.watched):
        if isinstance(visit, SkippedDir):
            run.summary.pastas_ilegiveis += 1
            run.audit_root(
                "scan.skip", RESULT_DENIED, caminho="/".join(visit.parts), motivo=visit.motivo
            )
            continue
        run.summary.arquivos += 1
        if since is not None and visit.mtime < since:
            run.summary.ignorados_por_data += 1
            run.touch(visit.relative)
            continue
        _file(run, visit)
        indexed = run.summary.novos + run.summary.reprocessados
        if (budget is not None and indexed >= budget) or (
            options.max_visited is not None and run.summary.arquivos >= options.max_visited
        ):
            if run.pending:
                run.commit(run.root.scan_cursor or "")
            return True
    if run.pending:
        run.commit(run.root.scan_cursor or "")
    return False


def _file(run: _Run, entry: FileEntry) -> None:
    store, content = run.store, run.content
    rel = entry.relative
    run.before = run.root.scan_cursor
    try:
        path = run.fs.locate(run.root_path, rel)
    except (NotFound, UnsafePath):
        run.touch(rel)  # vanished, or replaced by a link, between the listing and the read
        return
    try:
        sha256 = content.sha256(path)
    except UnsafePath as error:
        # Unreadable (permission): what the index knows at this path counts as seen, and the
        # run never marks anything absent (the file may be one of many it could not read).
        run.summary.ilegiveis += 1
        known = store.document_at(run.root.id, rel)
        if known is not None:
            known.visto_em = now_utc()
        run.audit_root("scan.skip", RESULT_DENIED, caminho=rel, motivo=f"arquivo ilegível: {error}")
        run.commit(rel)
        return
    existing = store.document_by_sha256(sha256)
    placement = place(entry.parts, run.owners, run.departments)
    now = now_utc()

    if existing is None:
        document = Document(sha256=sha256, origem=Origin.SCAN.value)
        _detect(run, document, rel, entry.name, placement, now, reprocessado=False)
        run.commit(run.before)
        run.summary.novos += 1
        with log_context(document_id=str(document.id)):
            _index(run, document, path, entry.name, placement)
        return

    if existing.root_id == run.root.id and same_path(existing.caminho, rel):
        existing.visto_em = now
        existing.ausente_em = None
        existing.caminho = rel  # the spelling the share lists now (case, Unicode form)
        if existing.estado in REPROCESS_STATES:
            run.summary.reprocessados += 1
            # A second model failure on the same document is the document's, not an outage:
            # it stays FALHOU and the scan goes on instead of stopping on it forever.
            strike = (existing.estado == DocumentState.FALHOU.value) and (
                existing.motivo or ""
            ).startswith(MOTIVO_MODELO_INDISPONIVEL)
            _detect(run, existing, rel, entry.name, placement, now, reprocessado=True)
            run.commit(run.before)
            with log_context(document_id=str(existing.id)):
                _index(run, existing, path, entry.name, placement, last_chance=strike)
            return
        run.summary.vistos += 1
        run.touch(rel)
        return

    if _still_there(run, existing):
        # Same content at two paths: the index keeps the one it knows (SPEC §9, duplicata).
        run.summary.duplicatas += 1
        run.audit(
            existing, "document.duplicate", RESULT_DENIED, estado=existing.estado,
            origem=Origin.SCAN.value, root_id=run.root.id, caminho=rel,
        )  # fmt: skip
        run.commit(rel)
        return

    # Moved by hand: the hash is the identity and the path follows the file (SPEC §9).
    previous_root, previous_path = existing.root_id, existing.caminho
    _locate(existing, run.root, rel, entry.name, placement)
    existing.visto_em = now
    existing.ausente_em = None
    reprocess = existing.estado in REPROCESS_STATES
    if _placed_by_path(existing):
        existing.estado, existing.motivo = final_state(placement)
    run.audit(
        existing, "document.move", RESULT_OK, motivo=MOTIVO_MOVIDO,
        origem_root_id=previous_root, origem=previous_path,
        root_id=run.root.id, destino=rel, estado=existing.estado,
    )  # fmt: skip
    run.summary.movidos += 1
    if not reprocess:
        run.commit(rel)
        return
    run.commit(run.before)
    run.summary.reprocessados += 1
    _detect(run, existing, rel, entry.name, placement, now, reprocessado=True)
    run.commit(run.before)
    with log_context(document_id=str(existing.id)):
        _index(run, existing, path, entry.name, placement)


def _placed_by_path(document: Document) -> bool:
    """Only a state the path decided is recomputed on a move; ``EM_REVISAO``,
    ``NAO_SUPORTADO``, the sensitive marker and a failed or half-indexed row keep theirs."""
    if document.estado == DocumentState.ARQUIVADO.value:
        return True
    return (
        document.estado == DocumentState.NA_ORIGEM.value
        and document.motivo == MOTIVO_FORA_DO_PADRAO
    )


def _still_there(run: _Run, existing: Document) -> bool:
    """The file the index knows for this hash still exists where the index says."""
    if existing.caminho is None or existing.root_id is None:
        return False
    try:
        root = run.root if existing.root_id == run.root.id else run.store.root(existing.root_id)
        run.fs.locate(Path(root.caminho), existing.caminho)
    except (DomainError, OSError):
        return False
    return True


def _locate(document: Document, root: Root, rel: str, name: str, placement: Placement) -> None:
    """Path and placement of the document as the scan sees them now."""
    document.root_id = root.id
    document.caminho = rel
    document.nome = nfc(name)
    document.owner_id = placement.owner.id if placement.owner is not None else None
    document.departamento_id = placement.department.id if placement.department is not None else None
    if placement.year is not None:
        document.ano = placement.year


def _detect(
    run: _Run,
    document: Document,
    rel: str,
    name: str,
    placement: Placement,
    now: datetime,
    *,
    reprocessado: bool,
) -> None:
    """``DETECTADO`` with a clean classification: nothing of a previous run survives."""
    _locate(document, run.root, rel, name, placement)
    document.tipo_documental = None
    document.tags = []
    document.resumo = None
    document.datas = []
    document.texto = None
    document.estado = DocumentState.DETECTADO.value
    document.motivo = None
    document.visto_em = now
    document.ausente_em = None
    if document.id is None:
        run.store.add_document(document)
    run.audit(
        document, "document.detect", RESULT_OK, origem=Origin.SCAN.value,
        root_id=run.root.id, caminho=rel, reprocessado=reprocessado,
    )  # fmt: skip


def _index(
    run: _Run,
    document: Document,
    path: Path,
    name: str,
    placement: Placement,
    *,
    last_chance: bool = False,
) -> None:
    """Text and metadata of a detected document; the final state comes from the path."""
    store, content = run.store, run.content
    if not placement.in_pattern:
        run.summary.fora_do_padrao += 1
    by_name = content.class_by_name(name)
    if by_name is FileClass.SENSITIVE:
        _sensitive(run, document)
        return
    if by_name is FileClass.UNSUPPORTED:
        _unsupported(run, document, "formato não suportado")
        return
    try:
        cls = content.file_class(path)
        if cls is FileClass.SENSITIVE:
            _sensitive(run, document)
            return
        if cls is FileClass.UNSUPPORTED:
            _unsupported(run, document, "formato não suportado ou conteúdo diferente da extensão")
            return
        extracted = content.extract(path)
    except SensitiveFile:
        _sensitive(run, document)
        return
    except Unsupported as error:
        _unsupported(run, document, str(error))
        return
    except ExtractionFailed as error:
        _failed(run, document, f"extração falhou: {error}")
        return
    document.texto = extracted.text
    document.estado = DocumentState.EXTRAIDO.value
    store.save()
    run.audit(
        document, "document.extract", RESULT_OK, metodo=extracted.method,
        paginas=extracted.pages_total, truncado=extracted.truncated,
        caracteres=len(extracted.text),
    )  # fmt: skip
    run.commit(run.before)

    try:
        envelope = run.llm.classify(extracted, run.specs)
    except InvalidDepartments as error:
        motivo = f"configuração do template recusada: {error}"
        _failed(run, document, motivo)
        raise ConfigurationError(motivo) from error
    except EnvelopeInvalid as error:
        # The text is indexed; the metadata is not. A scan has nothing to send to review.
        run.audit(
            document, "document.classify", RESULT_FAILED,
            motivo=MOTIVO_ENVELOPE_INVALIDO, erro=str(error),
        )  # fmt: skip
        estado, reason = final_state(placement)
        document.estado = estado
        document.motivo = reason or MOTIVO_ENVELOPE_INVALIDO
        _finish(run, document, placement, None)
        return
    except LLMError as error:
        _failed(run, document, f"{MOTIVO_MODELO_INDISPONIVEL}: {error}")
        if last_chance:
            return  # failed twice on this document: the scan goes on
        raise
    document.tipo_documental = envelope.tipo_documental
    document.tags = list(envelope.tags)
    document.resumo = envelope.resumo
    document.datas = list(envelope.datas)
    if placement.year is None:
        document.ano = envelope.ano_referencia
    document.estado = DocumentState.CLASSIFICADO.value
    store.save()
    run.audit(
        document, "document.classify", RESULT_OK, departamento=envelope.departamento,
        departamento_alternativo=envelope.departamento_alternativo,
        precisa_revisao=envelope.precisa_revisao,
        motivo_revisao=audit_reason(envelope.motivo_revisao),
        ano_referencia=envelope.ano_referencia,
    )  # fmt: skip
    document.estado, document.motivo = final_state(placement)
    _finish(run, document, placement, envelope)


def _finish(
    run: _Run, document: Document, placement: Placement, envelope: ClassificationEnvelope | None
) -> None:
    """The final state, with what the model said against what the path says."""
    run.store.save()
    divergent = (
        envelope is not None
        and placement.department is not None
        and envelope.departamento != placement.department.nome
    )
    run.audit(
        document, "document.index", RESULT_OK, estado=document.estado,
        owner_id=document.owner_id, departamento_id=document.departamento_id,
        ano=document.ano, motivo=document.motivo,
        departamento_modelo=envelope.departamento if envelope is not None else None,
        departamento_divergente=divergent,
    )  # fmt: skip
    run.done(document)


def _sensitive(run: _Run, document: Document) -> None:
    """Hash, path and class only (SPEC §9): never extracted, never sent to the model."""
    document.texto = None
    document.estado = DocumentState.NA_ORIGEM.value
    document.motivo = MOTIVO_SENSIVEL
    run.store.save()
    run.audit(document, "document.sensitive", RESULT_DENIED, motivo=MOTIVO_SENSIVEL)
    run.done(document)
    run.summary.sensiveis += 1


def _unsupported(run: _Run, document: Document, motivo: str) -> None:
    document.estado = DocumentState.NAO_SUPORTADO.value
    document.motivo = motivo
    run.store.save()
    run.audit(document, "document.unsupported", RESULT_DENIED, motivo=motivo)
    run.done(document)
    run.summary.nao_suportados += 1


def _failed(run: _Run, document: Document, motivo: str) -> None:
    document.estado = DocumentState.FALHOU.value
    document.motivo = motivo
    run.store.save()
    run.audit(document, "document.fail", RESULT_FAILED, motivo=motivo)
    run.done(document)
    run.summary.falhas += 1
    log.warning("scan.document_failed", motivo=motivo)


def _inside_any(caminho: str | None, folders: Sequence[tuple[str, ...]]) -> bool:
    if caminho is None:
        return False
    key = prune_key(caminho)
    return any(key[: len(folder)] == folder for folder in folders)


def _mark_absent(run: _Run) -> None:
    """A full scan ended: what it did not see is absent, and stays in the index (SPEC §9)."""
    now = now_utc()
    watched = [prune_key(folder) for folder in run.watched]
    for document in run.store.unseen_documents(run.root.id, run.started):
        if _inside_any(document.caminho, watched):
            continue  # in a watched folder: the worker tracks it, the scan never looked
        document.ausente_em = now
        run.audit(document, "document.absent", RESULT_OK, caminho=document.caminho)
        run.summary.ausentes += 1
    run.store.save()
