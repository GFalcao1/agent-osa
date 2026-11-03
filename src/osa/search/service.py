"""``search``: the query envelope against the index (SPEC §5-B step 3, §9).

Filters come from the registry, never from the model's words alone: the owner the envelope
names is resolved by alias, CNPJ, trade name, legal name or folder (exact after the same
normalisation the registry uses); the department by its template name. An owner or a
department that resolves to nothing is a search with no result, so the caller can say which
filter it did not understand instead of guessing. Keywords and document type go through
``plainto_tsquery('portuguese')`` against the generated ``texto_tsv`` (all keywords must
match); the document type matches the type the index recorded (the text only when the row has
no type); the period matches any of the envelope's dates, or the reference year of a document
without dates.

Only documents that can be found on disk are returned: with a path and not marked absent.
The sensitive class is flagged on the hit (never its content: it has none in the index).
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import date
from typing import Any

from sqlalchemy import Select, and_, func, literal, or_, select, text
from sqlalchemy.orm import Session

from osa.courier import owners as owners_module
from osa.courier.pipeline import MOTIVO_SENSIVEL
from osa.db.models import Department, Document, Owner
from osa.domain.document import FileClass
from osa.extract.classify import class_by_name
from osa.extract.model import DEFAULT_SENSITIVE_EXTENSIONS
from osa.fs.names import component_key, nfc
from osa.llm.envelopes import QueryEnvelope

FTS_CONFIG = "portuguese"
MAX_HITS = 50


@dataclass(frozen=True, slots=True)
class SearchLimits:
    max_hits: int = 5
    sensitive_extensions: tuple[str, ...] = DEFAULT_SENSITIVE_EXTENSIONS

    def __post_init__(self) -> None:
        if not 1 <= self.max_hits <= MAX_HITS:
            raise ValueError(f"max_hits must be between 1 and {MAX_HITS}")


@dataclass(frozen=True, slots=True)
class Hit:
    """One document the index found; never carries the text."""

    document_id: int
    sha256: str
    root_id: int | None
    caminho: str
    nome: str | None
    owner_id: int | None
    departamento_id: int | None
    ano: int | None
    tipo_documental: str | None
    resumo: str | None
    datas: tuple[date, ...]
    estado: str
    sensivel: bool
    rank: float


@dataclass(frozen=True, slots=True)
class Filters:
    """The envelope as the search understood it against the registry."""

    owner_id: int | None
    owner_unresolved: str | None  # the envelope named an owner the registry does not know
    department_id: int | None
    department_unresolved: str | None
    periodo_inicio: date | None
    periodo_fim: date | None
    terms: tuple[str, ...]  # keywords sent to the full-text query
    tipo_documental: str | None

    @property
    def unresolved(self) -> bool:
        return self.owner_unresolved is not None or self.department_unresolved is not None


# --- resolution against the registry (pure over sequences) ----------------------------------


def _owner_keys(owner: Owner) -> set[str]:
    keys = {component_key(owner.nome_fantasia), component_key(owner.pasta)}
    if owner.razao_social:
        keys.add(component_key(owner.razao_social))
    return keys


def match_owner(text_value: str, owners: Iterable[Owner]) -> Owner | None:
    """The active owner whose trade name, legal name or folder equals ``text_value``.

    Exact after case and Unicode normalisation; several matches are no match (ambiguous).
    """
    key = component_key(text_value.strip())
    if not key:
        return None
    found = [o for o in owners if o.ativo and key in _owner_keys(o)]
    return found[0] if len(found) == 1 else None


def match_department(text_value: str, departments: Iterable[Department]) -> Department | None:
    key = component_key(text_value.strip())
    if not key:
        return None
    for department in departments:
        if department.ativo and component_key(department.nome) == key:
            return department
    return None


def resolve_filters(
    envelope: QueryEnvelope,
    *,
    owners: Sequence[Owner],
    departments: Sequence[Department],
    owner_by_alias: Owner | None = None,
) -> Filters:
    """What the envelope asks for, resolved against the registry rows given.

    ``owner_by_alias`` is the owner an exact alias or CNPJ lookup found (the caller does that
    against the database); the name match over ``owners`` is the fallback.
    """
    owner_id: int | None = None
    owner_unresolved: str | None = None
    if envelope.dono:
        owner = owner_by_alias or match_owner(envelope.dono, owners)
        if owner is None:
            owner_unresolved = envelope.dono
        else:
            owner_id = owner.id
    department_id: int | None = None
    department_unresolved: str | None = None
    if envelope.departamento:
        department = match_department(envelope.departamento, departments)
        if department is None:
            department_unresolved = envelope.departamento
        else:
            department_id = department.id
    terms = tuple(term for term in (nfc(k).strip() for k in envelope.palavras_chave) if term)
    tipo = nfc(envelope.tipo_documental).strip() if envelope.tipo_documental else None
    return Filters(
        owner_id,
        owner_unresolved,
        department_id,
        department_unresolved,
        envelope.periodo_inicio,
        envelope.periodo_fim,
        terms,
        tipo or None,
    )


def _owner_by_alias(session: Session, dono: str | None) -> Owner | None:
    """Exact alias or CNPJ, as a declared owner is resolved (``osa.courier.owners``)."""
    return owners_module.resolve_declared_owner(session, dono) if dono else None


def filters_for(session: Session, envelope: QueryEnvelope) -> Filters:
    owners = list(session.scalars(select(Owner).where(Owner.ativo.is_(True)).order_by(Owner.id)))
    departments = list(
        session.scalars(
            select(Department).where(Department.ativo.is_(True)).order_by(Department.id)
        )
    )
    return resolve_filters(
        envelope,
        owners=owners,
        departments=departments,
        owner_by_alias=_owner_by_alias(session, envelope.dono),
    )


# --- the query --------------------------------------------------------------------------------


def _period_clause(inicio: date | None, fim: date | None) -> Any:
    """Any envelope date inside the period, or (no dates) the reference year inside it."""
    conditions = []
    if inicio is not None:
        conditions.append("d >= :inicio")
    if fim is not None:
        conditions.append("d <= :fim")
    dates = text(
        "EXISTS (SELECT 1 FROM unnest(documents.datas) AS d WHERE " + " AND ".join(conditions) + ")"
    )
    params: dict[str, Any] = {}
    if inicio is not None:
        params["inicio"] = inicio
    if fim is not None:
        params["fim"] = fim
    dates = dates.bindparams(**params)
    year_conditions = [func.cardinality(Document.datas) == 0]
    if inicio is not None:
        year_conditions.append(Document.ano >= inicio.year)
    if fim is not None:
        year_conditions.append(Document.ano <= fim.year)
    return or_(dates, and_(*year_conditions))


def build_query(filters: Filters, limits: SearchLimits) -> Select[Any]:
    """The SELECT for ``filters``: never built for an unresolved owner or department."""
    tsquery = None
    if filters.terms:
        tsquery = func.plainto_tsquery(FTS_CONFIG, " ".join(filters.terms))
    rank = func.ts_rank_cd(Document.texto_tsv, tsquery) if tsquery is not None else literal(0.0)
    query = select(Document, rank.label("rank")).where(
        Document.caminho.is_not(None), Document.ausente_em.is_(None)
    )
    if filters.owner_id is not None:
        query = query.where(Document.owner_id == filters.owner_id)
    if filters.department_id is not None:
        query = query.where(Document.departamento_id == filters.department_id)
    if tsquery is not None:
        query = query.where(Document.texto_tsv.op("@@")(tsquery))
    if filters.tipo_documental is not None:
        # The type the model recorded decides; only a row without one (invalid envelope,
        # sensitive, failed) falls back to the words of the type in its text.
        tipo_query = func.plainto_tsquery(FTS_CONFIG, filters.tipo_documental)
        query = query.where(
            or_(
                Document.tipo_documental.ilike(f"%{_escape_like(filters.tipo_documental)}%"),
                and_(Document.tipo_documental.is_(None), Document.texto_tsv.op("@@")(tipo_query)),
            )
        )
    if filters.periodo_inicio is not None or filters.periodo_fim is not None:
        query = query.where(_period_clause(filters.periodo_inicio, filters.periodo_fim))
    return query.order_by(
        text("rank DESC"), Document.atualizado_em.desc(), Document.id.desc()
    ).limit(limits.max_hits)


def _escape_like(value: str) -> str:
    return value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def search(
    session: Session, envelope: QueryEnvelope, limits: SearchLimits = SearchLimits()
) -> list[Hit]:
    """Up to ``limits.max_hits`` documents matching the envelope, best first.

    An owner or department the registry does not know gives no hit (see ``filters_for`` to
    tell the caller which one).
    """
    filters = filters_for(session, envelope)
    if filters.unresolved:
        return []
    return run_search(session, filters, limits)


def run_search(session: Session, filters: Filters, limits: SearchLimits) -> list[Hit]:
    hits: list[Hit] = []
    for document, rank in session.execute(build_query(filters, limits)):
        assert document.caminho is not None
        name = document.nome or document.caminho.rsplit("/", 1)[-1]
        hits.append(
            Hit(
                document_id=document.id,
                sha256=document.sha256,
                root_id=document.root_id,
                caminho=document.caminho,
                nome=document.nome,
                owner_id=document.owner_id,
                departamento_id=document.departamento_id,
                ano=document.ano,
                tipo_documental=document.tipo_documental,
                resumo=document.resumo,
                datas=tuple(document.datas),
                estado=document.estado,
                # By name, or by the record: a sensitive file renamed on the share keeps the
                # marker the scan or the courier gave it.
                sensivel=document.motivo == MOTIVO_SENSIVEL
                or class_by_name(name, limits.sensitive_extensions) is FileClass.SENSITIVE,
                rank=float(rank or 0.0),
            )
        )
    return hits
