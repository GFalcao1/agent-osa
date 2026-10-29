"""A reviewer's decision goes through the same courier (SPEC §5-D).

The reviewer chooses the owner and the department (or leaves the file where it is); the
courier revalidates both against the registry, completes the template tree and renames,
recording the operation with ``modo = review`` and the decision with its actor. The model
never takes part and nothing is deleted.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date

from osa.courier.decision import TemplateDepartment
from osa.courier.pipeline import (
    Archived,
    ArchiveRefused,
    Ctx,
    Failed,
    archive_now,
    template_of,
)
from osa.courier.ports import FsPort
from osa.courier.store import Store
from osa.db.audit import RESULT_DENIED, RESULT_FAILED, RESULT_OK
from osa.db.models import Document, ReviewCase
from osa.domain.document import ArchiveMode, DocumentState, ReviewState
from osa.domain.errors import Conflict, NotFound


@dataclass(frozen=True, slots=True)
class LeftAtOrigin:
    document_id: int
    estado: str
    case_id: int


def _open_case(store: Store, case_id: int) -> tuple[ReviewCase, Document]:
    case = store.review_case(case_id)
    if case.estado != ReviewState.ABERTO.value:
        raise Conflict("caso de revisão já decidido")
    document = store.document(case.document_id)
    if document.estado != DocumentState.EM_REVISAO.value:
        raise Conflict("documento não está em revisão")
    return case, document


def year_for(document: Document, department: TemplateDepartment) -> tuple[int | None, bool]:
    """The year subfolder of a reviewed document and whether it had to be assumed.

    The envelope's reference year when it exists; else the year of the latest date the
    envelope found; else the current year. The assumption is audited and shown to the
    reviewer. Departments that do not file by year get no year.
    """
    if not department.usa_ano:
        return None, False
    if document.ano is not None:
        return document.ano, False
    if document.datas:
        return max(document.datas).year, True
    return date.today().year, True


def decide_case(
    case_id: int,
    *,
    owner_id: int,
    department_id: int,
    store: Store,
    fs: FsPort,
    actor: str,
    decided_by: int,
    effects_enabled: bool,
) -> Archived | Failed:
    """Archive the reviewed document under the chosen owner and department.

    ``Conflict`` for a case already decided, a document that is not in review or one that
    was only simulated (no file in the root); ``NotFound`` for an inactive owner or a
    department outside the active template; ``ArchiveRefused`` when the share refuses the
    rename (the case stays open). The case closes only on ``Archived``.
    """
    case, document = _open_case(store, case_id)
    if document.caminho is None or document.root_id is None:
        raise Conflict("documento sem arquivo na raiz: foi só simulado")
    owner = store.owner(owner_id)
    if not owner.ativo:
        raise NotFound("dono não encontrado")
    template = template_of(store.template())
    department = next((d for d in template if d.id == department_id), None)
    if department is None:
        raise NotFound("departamento não encontrado")
    root = store.root(document.root_id)
    year, assumed = year_for(document, department)
    ctx = Ctx(store, fs, actor, effects_enabled, root, document)
    source_rel = document.caminho
    original_name = document.nome or source_rel.rsplit("/", 1)[-1]
    try:
        outcome = archive_now(
            ctx,
            owner=owner,
            department=department,
            template=template,
            year=year,
            source_rel=source_rel,
            original_name=original_name,
            modo=ArchiveMode.REVIEW.value,
            evidencia="revisor",
        )
    except ArchiveRefused as error:
        store.audit(
            ator=actor,
            acao="review.decide",
            alvo_tipo="review_case",
            alvo_id=case.id,
            sha256=document.sha256,
            resultado=RESULT_DENIED,
            detalhes={"decidido_por": decided_by, "erro": str(error)},
        )
        store.commit()
        raise
    if isinstance(outcome, Failed):
        store.audit(
            ator=actor,
            acao="review.decide",
            alvo_tipo="review_case",
            alvo_id=case.id,
            sha256=document.sha256,
            resultado=RESULT_FAILED,
            detalhes={"decidido_por": decided_by, "motivo": outcome.motivo},
        )
        store.commit()
        return outcome
    case.estado = ReviewState.DECIDIDO.value
    case.decidido_por = decided_by
    store.save()
    store.audit(
        ator=actor,
        acao="review.decide",
        alvo_tipo="review_case",
        alvo_id=case.id,
        sha256=document.sha256,
        resultado=RESULT_OK,
        detalhes={
            "decidido_por": decided_by,
            "owner_id": owner.id,
            "departamento_id": department.id,
            "ano": year,
            "ano_assumido": assumed,
            "operation_id": outcome.operation_id,
            "simulado": outcome.simulated,
        },
    )
    store.commit()
    return outcome


def leave_at_origin(case_id: int, *, store: Store, actor: str, decided_by: int) -> LeftAtOrigin:
    """Close the case leaving the file where it is: ``NA_ORIGEM``, nothing moved."""
    case, document = _open_case(store, case_id)
    document.estado = DocumentState.NA_ORIGEM.value
    document.motivo = "deixado na origem pela revisão"
    case.estado = ReviewState.ORIGEM.value
    case.decidido_por = decided_by
    store.save()
    store.audit(
        ator=actor,
        acao="review.origin",
        alvo_tipo="review_case",
        alvo_id=case.id,
        sha256=document.sha256,
        resultado=RESULT_OK,
        detalhes={"decidido_por": decided_by},
    )
    store.commit()
    return LeftAtOrigin(document.id, document.estado, case.id)
