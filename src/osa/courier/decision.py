"""The automatic-archiving rule (SPEC §6) as one pure function.

Automatic only when every condition holds: deterministic owner evidence, a department of
the active template named by the envelope, no reservation from the model
(``precisa_revisao`` false and no ``departamento_alternativo``) and, when the department
files by year, a reference year. Anything else is a review with a reason.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Final

from osa.courier.evidence import OwnerEvidence
from osa.llm.envelopes import FALLBACK_REASON, ClassificationEnvelope

MOTIVO_SEM_DONO: Final = "sem_evidencia_de_dono"
MOTIVO_DONO_AMBIGUO: Final = "dono_ambiguo"
MOTIVO_DONO_DIVERGENTE: Final = "dono_divergente"
MOTIVO_DEPARTAMENTO_INVALIDO: Final = "departamento_invalido"
MOTIVO_ANO_AUSENTE: Final = "ano_ausente"
MOTIVO_ENVELOPE_INVALIDO: Final = "envelope_invalido"
MOTIVO_ARQUIVAMENTO_RECUSADO: Final = "arquivamento_recusado"


@dataclass(frozen=True, slots=True)
class TemplateDepartment:
    """An active department of the template as the decision sees it."""

    id: int
    nome: str
    pasta: str
    usa_ano: bool


@dataclass(frozen=True, slots=True)
class AutoArchive:
    owner_id: int
    department: TemplateDepartment
    year: int | None  # the year subfolder; None when the department does not file by year


@dataclass(frozen=True, slots=True)
class NeedsReview:
    motivo: str
    department: TemplateDepartment | None  # the suggestion, when the envelope named a valid one


def decide(
    evidence: OwnerEvidence | None,
    envelope: ClassificationEnvelope,
    template: Sequence[TemplateDepartment],
) -> AutoArchive | NeedsReview:
    """Automatic archiving or a review with its reason; never raises for document content.

    Reasons, in the order they are checked: a department outside the template (the model
    and the registry disagree), the model's own reservation, missing owner evidence, and a
    year the department needs but the envelope lacks.
    """
    department = next((d for d in template if d.nome == envelope.departamento), None)
    if department is None:
        return NeedsReview(MOTIVO_DEPARTAMENTO_INVALIDO, None)
    if envelope.precisa_revisao or envelope.departamento_alternativo is not None:
        return NeedsReview(envelope.motivo_revisao or FALLBACK_REASON, department)
    if evidence is None:
        return NeedsReview(MOTIVO_SEM_DONO, department)
    year: int | None = None
    if department.usa_ano:
        if envelope.ano_referencia is None:
            return NeedsReview(MOTIVO_ANO_AUSENTE, department)
        year = envelope.ano_referencia
    return AutoArchive(evidence.owner_id, department, year)
