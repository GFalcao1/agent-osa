"""The single write gateway into the append-only audit table."""

from __future__ import annotations

from typing import Any, Final

from sqlalchemy.orm import Session

from osa.db.models import AuditEvent

RESULT_OK: Final = "ok"
RESULT_DENIED: Final = "denied"
RESULT_FAILED: Final = "failed"
RESULT_SIMULATED: Final = "simulated"
RESULTS: Final = (RESULT_OK, RESULT_DENIED, RESULT_FAILED, RESULT_SIMULATED)


def record_event(
    session: Session,
    *,
    ator: str,
    acao: str,
    alvo_tipo: str,
    alvo_id: str | int | None = None,
    sha256: str | None = None,
    resultado: str,
    detalhes: dict[str, Any] | None = None,
) -> AuditEvent:
    """Add one event to the session and flush it; the caller owns the commit.

    ``detalhes`` must never carry document text (ADR-0002): ids, hashes, reasons only.
    """
    if resultado not in RESULTS:
        raise ValueError(f"resultado must be one of {', '.join(RESULTS)}")
    event = AuditEvent(
        ator=ator,
        acao=acao,
        alvo_tipo=alvo_tipo,
        alvo_id=None if alvo_id is None else str(alvo_id),
        sha256=sha256,
        resultado=resultado,
        detalhes=detalhes or {},
    )
    session.add(event)
    session.flush()
    return event
