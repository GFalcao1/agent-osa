"""One polling cycle of a watched folder (SPEC §5-C, TASK-111).

Each cycle lists the folder (``stat`` only, never the content), records every new name as
``DETECTADO`` and counts the cycles in which size and mtime stayed the same. A file is
**stable** when they stayed the same for ``stable_cycles`` cycles and its mtime is at least
``min_age_s`` old; right before the courier runs, a fresh ``stat`` must still match the
listing, and the archiving rename checks the content against the hash that was read (a file
replaced meanwhile is never archived under the old decision). The courier runs in place (no
quarantine), with no declared owner: the owner comes from evidence in the text, or the case
goes to review. A file still being copied is never read.

What a cycle saw is committed before the next step, so a restart re-evaluates instead of
reprocessing blindly: a ``DETECTADO`` row keeps counting, an ``ESTAVEL`` row (the courier
was running) runs again on the same index row (the sha256 is unique), and a ``PROCESSADO``
row is skipped while its size and mtime stay the same. Files are processed one at a time; a
failure isolates the file (``FALHOU``) and the cycle goes on. When the model is away the
stable files wait, at most ``MAX_ATTEMPTS`` times each. A notice to the reviewers is kept on
the row (an outbox) until it was delivered.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Final, Literal

from osa.core.logging import get_logger, log_context
from osa.courier import pipeline
from osa.courier.intake import Intake
from osa.courier.ports import ClassifyPort, ContentPort, FsPort
from osa.courier.store import Store
from osa.db.audit import RESULT_DENIED, RESULT_OK
from osa.db.models import WatchEntry
from osa.domain.document import DocumentState, Origin, ReviewState, WatchState
from osa.domain.errors import Conflict, DomainError, SensitiveFile
from osa.fs.walk import FileEntry, prune_key

log = get_logger("osa.watch")

ALVO: Final = "watch"
#: Times the courier may run on one file without a result before it is given up (notice).
MAX_ATTEMPTS: Final = 3
MOTIVO_SUBSTITUIDO: Final = "conteúdo substituído na pasta monitorada"
MOTIVO_TENTATIVAS: Final = "desistido depois de várias tentativas"


@dataclass(frozen=True, slots=True)
class WatchOptions:
    stable_cycles: int = 2
    min_age_s: float = 60.0
    effects_enabled: bool = False


# --- the stability rule (pure) ------------------------------------------------------------


def next_cycles(entry: WatchEntry, size: int, mtime_ns: int) -> int:
    """Cycles in a row with the same size and mtime: one more, or back to zero on a change."""
    if entry.tamanho == size and entry.mtime_ns == mtime_ns:
        return entry.ciclos_estaveis + 1
    return 0


def is_stable(cycles: int, mtime_ns: int, now: datetime, options: WatchOptions) -> bool:
    """SPEC §5-C step 2: unchanged for ``stable_cycles`` cycles and old enough."""
    age = now.timestamp() - mtime_ns / 1e9
    return cycles >= options.stable_cycles and age >= options.min_age_s


# --- what a cycle reports -------------------------------------------------------------------

NoticeKind = Literal[
    "revisao", "nao_suportado", "duplicata", "falhou", "falhou_movido", "sensivel", "perdido"
]


@dataclass(frozen=True, slots=True)
class Notice:
    """Something the reviewers (or the administrator) hear about once (SPEC §5-C step 4)."""

    kind: NoticeKind
    caminho: str
    motivo: str | None = None
    document_id: int | None = None
    reviewed: pipeline.Reviewed | None = None
    entry_id: int | None = None  # the outbox row


@dataclass(slots=True)
class CycleSummary:
    folder_id: int
    vistos: int = 0
    novos: int = 0
    estaveis: int = 0
    arquivados: int = 0
    revisoes: int = 0
    duplicatas: int = 0
    nao_suportados: int = 0
    falhas: int = 0
    sensiveis: int = 0
    adiados: int = 0  # stable files left for the next cycle (the model is away)
    mudaram: int = 0  # changed between the listing and the read: watched again
    sumiram: int = 0
    notices: list[Notice] = field(default_factory=list)  # new this cycle (also in the outbox)


def _key(caminho: str) -> tuple[str, ...]:
    """The same file as the share sees it: no case, no Unicode form."""
    return prune_key(caminho)


# --- the cycle ------------------------------------------------------------------------------


def watch_cycle(
    folder_id: int,
    *,
    store: Store,
    fs: FsPort,
    content: ContentPort,
    llm: ClassifyPort,
    actor: str,
    options: WatchOptions,
    now: datetime | None = None,
) -> CycleSummary:
    """List the folder once, update what was seen, run the courier on the stable files.

    Raises ``Conflict`` for an inactive folder or root, ``NotFound``/``UnsafePath`` when the
    folder cannot be listed safely, and ``pipeline.ConfigurationError`` (the template cannot
    go to the model; the file stays ``ESTAVEL``): the caller logs and goes on.
    """
    moment = now or datetime.now(UTC)
    folder = store.watched_folder(folder_id)
    root = store.root(folder.root_id)
    if not folder.ativo or not root.ativo:
        raise Conflict("pasta monitorada ou raiz inativa")
    root_path = Path(root.caminho)
    summary = CycleSummary(folder.id)
    unreadable: list[str] = []
    listed = fs.list_folder(root_path, folder.caminho, unreadable=unreadable)
    known = {_key(entry.caminho): entry for entry in store.watch_entries(folder.id)}
    present = {_key(item.relative) for item in listed} | {_key(rel) for rel in unreadable}
    for key, gone in known.items():
        if key not in present:
            _gone(store, gone, root.id, actor, summary)
    store.commit()

    model_away = False
    with log_context(watched_folder_id=str(folder.id)):
        for item in listed:
            summary.vistos += 1
            entry = known.get(_key(item.relative))
            if entry is None:
                _detect(store, folder.id, item, moment, actor)
                summary.novos += 1
                continue
            if not _observe(store, entry, item, moment, options, actor):
                store.commit()
                continue
            summary.estaveis += 1
            if model_away:
                summary.adiados += 1
                store.commit()
                continue
            entry.estado = WatchState.ESTAVEL.value
            entry.tentativas += 1
            store.commit()  # persisted before any read: a crash re-evaluates, never skips
            model_away = _run_courier(
                entry, item, root.id, root_path,
                store=store, fs=fs, content=content, llm=llm, actor=actor,
                options=options, summary=summary,
            )  # fmt: skip
    return summary


def _gone(store: Store, entry: WatchEntry, root_id: int, actor: str, summary: CycleSummary) -> None:
    """The file left the folder (archived, moved or deleted by someone).

    A row with a notice still to send stays until it is delivered. A row that was ``ESTAVEL``
    lost its file while the courier ran: if the index still points at the folder, the
    administrator hears about it (a crash between the rename and its record).
    """
    if entry.aviso is not None and entry.aviso_enviado_em is None:
        return
    if entry.estado == WatchState.ESTAVEL.value:
        document = store.document_at(root_id, entry.caminho)
        if document is not None and document.estado != DocumentState.ARQUIVADO.value:
            store.audit(
                ator=actor, acao="watch.lost", alvo_tipo=ALVO, alvo_id=entry.id,
                sha256=document.sha256, resultado=RESULT_DENIED,
                detalhes={"caminho": entry.caminho, "document_id": document.id},
            )  # fmt: skip
            entry.estado = WatchState.PROCESSADO.value
            _outbox(store, entry, "perdido", f"documento {document.id}", summary, document.id)
            return
    store.drop_watch_entry(entry)
    summary.sumiram += 1


def _detect(store: Store, folder_id: int, item: FileEntry, moment: datetime, actor: str) -> None:
    entry = store.add_watch_entry(
        WatchEntry(
            watched_folder_id=folder_id,
            caminho=item.relative,
            tamanho=item.size,
            mtime_ns=item.mtime_ns,
            ciclos_estaveis=0,
            tentativas=0,
            estado=WatchState.DETECTADO.value,
            primeiro_visto=moment,
            ultimo_visto=moment,
        )
    )
    store.audit(
        ator=actor, acao="watch.detect", alvo_tipo=ALVO, alvo_id=entry.id, sha256=None,
        resultado=RESULT_OK, detalhes={"caminho": item.relative, "bytes": item.size},
    )  # fmt: skip
    store.commit()


def _observe(
    store: Store,
    entry: WatchEntry,
    item: FileEntry,
    moment: datetime,
    options: WatchOptions,
    actor: str,
) -> bool:
    """Update the row with this cycle's ``stat``; True when the file is ready for the courier."""
    changed = entry.tamanho != item.size or entry.mtime_ns != item.mtime_ns
    entry.caminho = item.relative  # the spelling the share lists now
    if entry.estado == WatchState.PROCESSADO.value:
        if not changed:
            return False  # already processed, untouched since: skipped
        _replaced(store, entry, actor)
    cycles = 0 if changed else next_cycles(entry, item.size, item.mtime_ns)
    entry.tamanho, entry.mtime_ns, entry.ciclos_estaveis = item.size, item.mtime_ns, cycles
    entry.ultimo_visto = moment
    if changed and entry.estado == WatchState.ESTAVEL.value:
        entry.estado = WatchState.DETECTADO.value  # it moved again while waiting
    return is_stable(cycles, item.mtime_ns, moment, options)


def _replaced(store: Store, entry: WatchEntry, actor: str) -> None:
    """New content under a processed name: a new file for the worker.

    The document of the old content no longer is at that path: it loses the path, so a review
    case of it cannot be decided onto the new content and the search stops offering it.
    """
    if entry.document_id is not None:
        document = store.document(entry.document_id)
        if document.caminho is not None and _key(document.caminho) == _key(entry.caminho):
            document.caminho = None
            document.motivo = MOTIVO_SUBSTITUIDO
            store.audit(
                ator=actor, acao="watch.replaced", alvo_tipo="document", alvo_id=document.id,
                sha256=document.sha256, resultado=RESULT_OK, detalhes={"caminho": entry.caminho},
            )  # fmt: skip
    entry.estado = WatchState.DETECTADO.value
    entry.document_id = None
    entry.tentativas = 0
    entry.aviso = None
    entry.aviso_motivo = None
    entry.aviso_enviado_em = None


def _run_courier(
    entry: WatchEntry,
    item: FileEntry,
    root_id: int,
    root_path: Path,
    *,
    store: Store,
    fs: FsPort,
    content: ContentPort,
    llm: ClassifyPort,
    actor: str,
    options: WatchOptions,
    summary: CycleSummary,
) -> bool:
    """Flow A from step 3, in place and without a declared owner; True if the model is away."""
    if fs.fingerprint(root_path, item.relative) != (item.size, item.mtime_ns):
        # Changed (or gone) between the listing and now: watched again, nothing read.
        entry.estado = WatchState.DETECTADO.value
        entry.ciclos_estaveis = 0
        entry.tentativas = max(0, entry.tentativas - 1)
        summary.mudaram += 1
        store.commit()
        return False
    try:
        source = fs.locate(root_path, item.relative)
        outcome = pipeline.process(
            Intake(root_id, source, item.relative, item.name, Origin.WATCHED),
            store=store,
            fs=fs,
            content=content,
            llm=llm,
            actor=actor,
            effects_enabled=options.effects_enabled,
        )
    except SensitiveFile:  # never read; audited by the courier; stays where it is
        summary.sensiveis += 1
        _outbox(store, entry, "sensivel", pipeline.MOTIVO_SENSIVEL, summary, None)
        _processed(store, entry, None)
        return False
    except (Conflict, pipeline.ConfigurationError):
        raise  # the root went inactive, or the template cannot go to the model
    except DomainError as error:
        # Gone, replaced by a link or unreadable before the index row existed.
        store.audit(
            ator=actor, acao="watch.skip", alvo_tipo=ALVO, alvo_id=entry.id, sha256=None,
            resultado=RESULT_DENIED, detalhes={"caminho": item.relative, "motivo": str(error)},
        )  # fmt: skip
        if entry.tentativas >= MAX_ATTEMPTS:
            summary.falhas += 1
            _outbox(store, entry, "falhou", f"{MOTIVO_TENTATIVAS}: {error}", summary, None)
            _processed(store, entry, None)
            return False
        entry.estado = WatchState.DETECTADO.value
        entry.ciclos_estaveis = 0
        store.commit()
        return False

    match outcome:
        case pipeline.Failed(motivo=motivo) if motivo.startswith(
            pipeline.MOTIVO_MODELO_INDISPONIVEL
        ):
            if entry.tentativas < MAX_ATTEMPTS:
                # Maybe not the file's fault: it stays ESTAVEL and runs again next cycle.
                summary.adiados += 1
                store.commit()
                return True
            summary.falhas += 1  # always the same file: given up, with a notice
            _outbox(
                store, entry, "falhou", f"{MOTIVO_TENTATIVAS}: {motivo}", summary,
                outcome.document_id,
            )  # fmt: skip
        case pipeline.Archived(simulated=False):
            summary.arquivados += 1
            store.drop_watch_entry(entry)  # renamed out of the folder: nothing left to track
            store.commit()
            return False
        case pipeline.Archived():
            summary.arquivados += 1  # simulated: the file stays and is skipped while unchanged
        case pipeline.Reviewed(motivo=motivo):
            summary.revisoes += 1
            _outbox(store, entry, "revisao", motivo, summary, outcome.document_id, outcome)
        case pipeline.Duplicate(estado=estado, caminho=caminho) if (
            estado == DocumentState.EM_REVISAO.value
            and caminho is not None
            and _key(caminho) == _key(item.relative)
        ):
            # Its own review case, from a run that stopped before this row was PROCESSADO.
            summary.revisoes += 1
            _outbox(store, entry, "revisao", None, summary, outcome.document_id)
        case pipeline.Duplicate(estado=estado):
            summary.duplicatas += 1
            _outbox(store, entry, "duplicata", estado, summary, outcome.document_id)
        case pipeline.NotSupported(motivo=motivo):
            summary.nao_suportados += 1
            _outbox(store, entry, "nao_suportado", motivo, summary, outcome.document_id)
        case pipeline.Failed(motivo=motivo):
            summary.falhas += 1
            moved = store.document(outcome.document_id).caminho
            if moved is not None and _key(moved) != _key(item.relative):
                _outbox(
                    store, entry, "falhou_movido", f"{motivo} (agora em {moved})", summary,
                    outcome.document_id,
                )  # fmt: skip
            else:
                _outbox(store, entry, "falhou", motivo, summary, outcome.document_id)
    _processed(store, entry, outcome.document_id)
    return False


def _outbox(
    store: Store,
    entry: WatchEntry,
    kind: NoticeKind,
    motivo: str | None,
    summary: CycleSummary,
    document_id: int | None,
    reviewed: pipeline.Reviewed | None = None,
) -> None:
    """Keep the notice on the row (sent later, until delivered) and report it this cycle."""
    entry.aviso, entry.aviso_motivo, entry.aviso_enviado_em = kind, motivo, None
    if document_id is not None:
        entry.document_id = document_id
    store.save()
    summary.notices.append(
        Notice(kind, entry.caminho, motivo, document_id, reviewed, entry_id=entry.id)
    )


def _processed(store: Store, entry: WatchEntry, document_id: int | None) -> None:
    entry.estado = WatchState.PROCESSADO.value
    if document_id is not None:
        entry.document_id = document_id
    store.commit()


# --- the outbox -----------------------------------------------------------------------------


def pending_notices(store: Store) -> list[Notice]:
    """Notices not delivered yet, of every watched folder, oldest first.

    A review notice carries its open case (rebuilt from the index), so it can be posted with
    the buttons; a case already decided needs no notice and is marked delivered.
    """
    notices: list[Notice] = []
    open_cases = {c.document_id: c for c in store.open_review_cases()}
    for entry in store.pending_watch_notices():
        assert entry.aviso is not None
        kind: NoticeKind = entry.aviso  # type: ignore[assignment]
        reviewed = None
        if kind == "revisao" and entry.document_id is not None:
            case = open_cases.get(entry.document_id)
            if case is None or case.estado != ReviewState.ABERTO.value:
                mark_delivered(store, entry.id)
                continue
            reviewed = pipeline.Reviewed(
                entry.document_id, DocumentState.EM_REVISAO.value, case.id, case.motivo,
                case.departamento_sugerido_id,
            )  # fmt: skip
        notices.append(
            Notice(kind, entry.caminho, entry.aviso_motivo, entry.document_id, reviewed, entry.id)
        )
    return notices


def mark_delivered(store: Store, entry_id: int) -> None:
    store.mark_watch_notice_sent(entry_id, datetime.now(UTC))
    store.commit()
