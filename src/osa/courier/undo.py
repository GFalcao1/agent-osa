"""Undo one archiving: the inverse rename, checked by the recorded sha256 (SPEC §5-E).

Never deletes. The file of ``destino`` goes back to the folder and name of ``origem``
(the quarantine or the watched folder), with the collision suffix rule; the index row
returns to ``NA_ORIGEM`` and a new operation with ``modo = undo`` is recorded, so the
history stays append-only.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from osa.courier.ports import FsPort
from osa.courier.store import Store
from osa.db.audit import RESULT_FAILED, RESULT_OK, RESULT_SIMULATED
from osa.db.models import ArchiveOperation
from osa.domain.document import ArchiveMode, DocumentState
from osa.domain.errors import Conflict, DomainError


@dataclass(frozen=True, slots=True)
class UndoResult:
    operation_id: int  # the archiving that was undone
    undo_operation_id: int | None  # None when simulated
    document_id: int
    destino: str  # where the file is now (predicted when simulated), relative to the root
    simulated: bool


def already_undone(operation: ArchiveOperation, history: list[ArchiveOperation]) -> bool:
    """True when a later ``undo`` operation of the same document starts at this destination."""
    return any(
        later.id > operation.id
        and later.modo == ArchiveMode.UNDO.value
        and later.origem == operation.destino
        for later in history
    )


def undo_operation(
    operation_id: int,
    *,
    store: Store,
    fs: FsPort,
    actor: str,
    effects_enabled: bool = False,
) -> UndoResult:
    """Move the archived file back to where it came from; the content must still match."""
    operation = store.operation(operation_id)
    if operation.modo == ArchiveMode.UNDO.value:
        raise Conflict("a operação já é um desfazer")
    if already_undone(operation, list(store.operations_of(operation.document_id))):
        raise Conflict("arquivamento já desfeito")
    root = store.root(operation.root_id)
    document = store.document(operation.document_id)
    try:
        result = fs.undo(
            Path(root.caminho),
            operation.root_id,
            operation.origem,
            operation.destino,
            operation.sha256,
            effects_enabled=effects_enabled,
        )
    except DomainError as error:
        store.audit(
            ator=actor,
            acao="archive.undo",
            alvo_tipo="document",
            alvo_id=document.id,
            sha256=operation.sha256,
            resultado=RESULT_FAILED,
            detalhes={"operation_id": operation.id, "erro": str(error)},
        )
        store.commit()
        raise
    if result.simulated:
        store.audit(
            ator=actor,
            acao="archive.undo",
            alvo_tipo="document",
            alvo_id=document.id,
            sha256=operation.sha256,
            resultado=RESULT_SIMULATED,
            detalhes={"operation_id": operation.id, "destino": result.dest_rel},
        )
        store.commit()
        return UndoResult(operation.id, None, document.id, result.dest_rel, True)
    reverse = store.add_operation(
        ArchiveOperation(
            document_id=document.id,
            root_id=operation.root_id,
            origem=result.source_rel,
            destino=result.dest_rel,
            sha256=operation.sha256,
            ator=actor,
            modo=ArchiveMode.UNDO.value,
        )
    )
    document.caminho = result.dest_rel
    document.estado = DocumentState.NA_ORIGEM.value
    document.motivo = f"desfeito: operação {operation.id}"
    store.save()
    store.audit(
        ator=actor,
        acao="archive.undo",
        alvo_tipo="document",
        alvo_id=document.id,
        sha256=operation.sha256,
        resultado=RESULT_OK,
        detalhes={
            "operation_id": operation.id,
            "undo_operation_id": reverse.id,
            "destino": result.dest_rel,
        },
    )
    store.commit()
    return UndoResult(operation.id, reverse.id, document.id, result.dest_rel, False)
