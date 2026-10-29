"""The index and the registry as the courier reads and writes them.

``Store`` is the port; ``SqlStore`` is the SQLAlchemy implementation. The pipeline commits
at every state transition (the state is persisted before any effect, SPEC §9), so the
store, not the caller, owns the commit. Audit details never carry document text.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any, Protocol

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from osa.courier.evidence import OwnerCandidate
from osa.db.audit import record_event
from osa.db.models import (
    ArchiveOperation,
    Department,
    Document,
    Owner,
    OwnerAlias,
    ReviewCase,
    Root,
    User,
)
from osa.domain.document import ReviewState
from osa.domain.errors import NotFound


class Store(Protocol):
    def root(self, root_id: int) -> Root: ...

    def active_roots(self) -> Sequence[Root]: ...

    def owner(self, owner_id: int) -> Owner: ...

    def owner_candidates(self) -> Sequence[OwnerCandidate]: ...

    def user(self, user_id: int) -> User: ...

    def template(self) -> Sequence[Department]: ...

    def document(self, document_id: int) -> Document: ...

    def document_by_sha256(self, sha256: str) -> Document | None: ...

    def add_document(self, document: Document) -> Document: ...

    def add_review_case(self, case: ReviewCase) -> ReviewCase: ...

    def add_operation(self, operation: ArchiveOperation) -> ArchiveOperation: ...

    def operation(self, operation_id: int) -> ArchiveOperation: ...

    def operations_of(self, document_id: int) -> Sequence[ArchiveOperation]: ...

    def review_case(self, case_id: int) -> ReviewCase: ...

    def open_review_cases(self) -> Sequence[ReviewCase]: ...

    def user_by_telegram_id(self, telegram_id: int) -> User | None: ...

    def active_users(self) -> Sequence[User]: ...

    def active_owners(self) -> Sequence[Owner]: ...

    def documents_by_state(self) -> dict[str, int]: ...

    def audit(
        self,
        *,
        ator: str,
        acao: str,
        alvo_tipo: str,
        alvo_id: int | None,
        sha256: str | None,
        resultado: str,
        detalhes: dict[str, Any],
    ) -> None: ...

    def save(self) -> None:
        """Flush pending changes (ids are assigned); nothing is committed."""
        ...

    def commit(self) -> None: ...


class SqlStore:
    """``Store`` over a SQLAlchemy session."""

    def __init__(self, session: Session) -> None:
        self._session = session

    def _get[T](self, model: type[T], entity_id: int, label: str) -> T:
        found = self._session.get(model, entity_id)
        if found is None:
            raise NotFound(f"{label} não encontrado")
        return found

    def root(self, root_id: int) -> Root:
        return self._get(Root, root_id, "raiz")

    def active_roots(self) -> Sequence[Root]:
        return list(
            self._session.scalars(select(Root).where(Root.ativo.is_(True)).order_by(Root.id))
        )

    def owner(self, owner_id: int) -> Owner:
        return self._get(Owner, owner_id, "dono")

    def owner_candidates(self) -> Sequence[OwnerCandidate]:
        aliases: dict[int, list[str]] = {}
        for owner_id, alias in self._session.execute(
            select(OwnerAlias.owner_id, OwnerAlias.alias).order_by(OwnerAlias.id)
        ):
            aliases.setdefault(owner_id, []).append(alias)
        return [
            OwnerCandidate(owner.id, owner.cnpj, tuple(aliases.get(owner.id, ())))
            for owner in self._session.scalars(
                select(Owner).where(Owner.ativo.is_(True)).order_by(Owner.id)
            )
        ]

    def user(self, user_id: int) -> User:
        return self._get(User, user_id, "usuário")

    def template(self) -> Sequence[Department]:
        return list(
            self._session.scalars(
                select(Department)
                .where(Department.ativo.is_(True))
                .order_by(Department.ordem, Department.id)
            )
        )

    def document(self, document_id: int) -> Document:
        return self._get(Document, document_id, "documento")

    def document_by_sha256(self, sha256: str) -> Document | None:
        return self._session.scalar(select(Document).where(Document.sha256 == sha256))

    def add_document(self, document: Document) -> Document:
        self._session.add(document)
        self._session.flush()
        return document

    def add_review_case(self, case: ReviewCase) -> ReviewCase:
        self._session.add(case)
        self._session.flush()
        return case

    def add_operation(self, operation: ArchiveOperation) -> ArchiveOperation:
        self._session.add(operation)
        self._session.flush()
        return operation

    def operation(self, operation_id: int) -> ArchiveOperation:
        return self._get(ArchiveOperation, operation_id, "operação de arquivamento")

    def operations_of(self, document_id: int) -> Sequence[ArchiveOperation]:
        return list(
            self._session.scalars(
                select(ArchiveOperation)
                .where(ArchiveOperation.document_id == document_id)
                .order_by(ArchiveOperation.id)
            )
        )

    def review_case(self, case_id: int) -> ReviewCase:
        return self._get(ReviewCase, case_id, "caso de revisão")

    def open_review_cases(self) -> Sequence[ReviewCase]:
        return list(
            self._session.scalars(
                select(ReviewCase)
                .where(ReviewCase.estado == ReviewState.ABERTO.value)
                .order_by(ReviewCase.id)
            )
        )

    def user_by_telegram_id(self, telegram_id: int) -> User | None:
        return self._session.scalar(select(User).where(User.telegram_id == telegram_id))

    def active_users(self) -> Sequence[User]:
        return list(
            self._session.scalars(select(User).where(User.ativo.is_(True)).order_by(User.id))
        )

    def active_owners(self) -> Sequence[Owner]:
        return list(
            self._session.scalars(
                select(Owner).where(Owner.ativo.is_(True)).order_by(Owner.nome_fantasia, Owner.id)
            )
        )

    def documents_by_state(self) -> dict[str, int]:
        rows = self._session.execute(
            select(Document.estado, func.count()).group_by(Document.estado)
        )
        return {str(estado): int(count) for estado, count in rows}

    def audit(
        self,
        *,
        ator: str,
        acao: str,
        alvo_tipo: str,
        alvo_id: int | None,
        sha256: str | None,
        resultado: str,
        detalhes: dict[str, Any],
    ) -> None:
        record_event(
            self._session,
            ator=ator,
            acao=acao,
            alvo_tipo=alvo_tipo,
            alvo_id=alvo_id,
            sha256=sha256,
            resultado=resultado,
            detalhes=detalhes,
        )

    def save(self) -> None:
        self._session.flush()

    def commit(self) -> None:
        self._session.commit()
