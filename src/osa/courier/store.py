"""The index and the registry as the courier reads and writes them.

``Store`` is the port; ``SqlStore`` is the SQLAlchemy implementation. The pipeline commits
at every state transition (the state is persisted before any effect, SPEC §9), so the
store, not the caller, owns the commit. Audit details never carry document text.
"""

from __future__ import annotations

from collections.abc import Iterator, Sequence
from contextlib import AbstractContextManager, contextmanager
from datetime import datetime
from typing import Any, Final, Protocol

from sqlalchemy import Connection, Engine, func, select
from sqlalchemy.orm import Session

from osa.courier.evidence import OwnerCandidate
from osa.db.audit import record_event
from osa.db.models import (
    ArchiveOperation,
    AuditEvent,
    Department,
    Document,
    Owner,
    OwnerAlias,
    ReviewCase,
    Root,
    User,
    WatchedFolder,
    WatchEntry,
)
from osa.domain.document import ReviewState
from osa.domain.errors import NotFound
from osa.fs.names import QUARANTINE_DIR

#: Advisory lock namespaces (two-key form): one scan per root, one worker per installation.
LOCK_SCAN: Final = 0x05CA
LOCK_WORKER: Final = 0x0A5C


@contextmanager
def try_advisory_lock(engine: Engine, namespace: int, key: int) -> Iterator[bool]:
    """A session-level PostgreSQL advisory lock on a connection of its own, held for the block.

    The courier commits as it goes (the session's connection changes), so the lock lives on a
    dedicated connection, released at the end of the block (or when the process dies).
    """
    with engine.connect() as connection:
        got = bool(connection.execute(select(func.pg_try_advisory_lock(namespace, key))).scalar())
        try:
            yield got
        finally:
            if got:
                connection.execute(select(func.pg_advisory_unlock(namespace, key)))
            connection.commit()


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

    def review_case(self, case_id: int, *, for_update: bool = False) -> ReviewCase:
        """The case; ``for_update`` locks its row until the commit (a decision in progress)."""
        ...

    def open_review_cases(self) -> Sequence[ReviewCase]: ...

    def user_by_telegram_id(self, telegram_id: int) -> User | None: ...

    def active_users(self) -> Sequence[User]: ...

    def active_owners(self) -> Sequence[Owner]: ...

    def documents_by_state(self) -> dict[str, int]: ...

    def document_at(self, root_id: int, caminho: str) -> Document | None:
        """The document the index knows at this root-relative path (exact spelling)."""
        ...

    def watched_folders(
        self, root_id: int | None = None, *, active_only: bool = True
    ) -> Sequence[WatchedFolder]:
        """Watched folders of one root (or of every active root); active ones by default."""
        ...

    def watched_folder(self, folder_id: int) -> WatchedFolder: ...

    def watch_entries(self, folder_id: int) -> Sequence[WatchEntry]: ...

    def add_watch_entry(self, entry: WatchEntry) -> WatchEntry: ...

    def drop_watch_entry(self, entry: WatchEntry) -> None:
        """A file left the folder: its observation goes (a work row, never the index)."""
        ...

    def pending_watch_notices(self) -> Sequence[WatchEntry]:
        """Rows with a notice not delivered yet (the outbox), oldest first."""
        ...

    def mark_watch_notice_sent(self, entry_id: int, when: datetime) -> None: ...

    def scan_lock(self, root_id: int) -> AbstractContextManager[bool]:
        """Hold the scan of one root (True) or learn another process holds it (False)."""
        ...

    def worker_lock(self) -> AbstractContextManager[bool]:
        """Hold the worker of this installation (True) or learn another one runs (False)."""
        ...

    def count_events(
        self,
        *,
        acao: str,
        alvo_tipo: str | None = None,
        alvo_id: int | None = None,
        since: datetime | None = None,
    ) -> int:
        """How many audit events match (the trail is read, never changed)."""
        ...

    def unseen_documents(self, root_id: int, since: datetime) -> Sequence[Document]:
        """Documents of the root with a path in the tree that no scan saw since ``since``.

        The quarantine is not scanned, so its documents are never "unseen"; a document
        already marked absent is not listed again.
        """
        ...

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

    def review_case(self, case_id: int, *, for_update: bool = False) -> ReviewCase:
        found = self._session.get(ReviewCase, case_id, with_for_update=for_update or None)
        if found is None:
            raise NotFound("caso de revisão não encontrado")
        return found

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

    def document_at(self, root_id: int, caminho: str) -> Document | None:
        return self._session.scalar(
            select(Document).where(Document.root_id == root_id, Document.caminho == caminho)
        )

    def watched_folders(
        self, root_id: int | None = None, *, active_only: bool = True
    ) -> Sequence[WatchedFolder]:
        query = (
            select(WatchedFolder)
            .join(Root, Root.id == WatchedFolder.root_id)
            .order_by(WatchedFolder.id)
        )
        if active_only:
            query = query.where(WatchedFolder.ativo.is_(True), Root.ativo.is_(True))
        if root_id is not None:
            query = query.where(WatchedFolder.root_id == root_id)
        return list(self._session.scalars(query))

    def watched_folder(self, folder_id: int) -> WatchedFolder:
        return self._get(WatchedFolder, folder_id, "pasta monitorada")

    def watch_entries(self, folder_id: int) -> Sequence[WatchEntry]:
        return list(
            self._session.scalars(
                select(WatchEntry)
                .where(WatchEntry.watched_folder_id == folder_id)
                .order_by(WatchEntry.id)
            )
        )

    def add_watch_entry(self, entry: WatchEntry) -> WatchEntry:
        self._session.add(entry)
        self._session.flush()
        return entry

    def drop_watch_entry(self, entry: WatchEntry) -> None:
        self._session.delete(entry)
        self._session.flush()

    def pending_watch_notices(self) -> Sequence[WatchEntry]:
        return list(
            self._session.scalars(
                select(WatchEntry)
                .where(WatchEntry.aviso.is_not(None), WatchEntry.aviso_enviado_em.is_(None))
                .order_by(WatchEntry.id)
            )
        )

    def mark_watch_notice_sent(self, entry_id: int, when: datetime) -> None:
        entry = self._session.get(WatchEntry, entry_id)
        if entry is not None:
            entry.aviso_enviado_em = when
            self._session.flush()

    def scan_lock(self, root_id: int) -> AbstractContextManager[bool]:
        return try_advisory_lock(self._engine(), LOCK_SCAN, root_id)

    def worker_lock(self) -> AbstractContextManager[bool]:
        return try_advisory_lock(self._engine(), LOCK_WORKER, 0)

    def _engine(self) -> Engine:
        bind = self._session.get_bind()
        return bind.engine if isinstance(bind, Connection) else bind

    def count_events(
        self,
        *,
        acao: str,
        alvo_tipo: str | None = None,
        alvo_id: int | None = None,
        since: datetime | None = None,
    ) -> int:
        query = select(func.count()).select_from(AuditEvent).where(AuditEvent.acao == acao)
        if alvo_tipo is not None:
            query = query.where(AuditEvent.alvo_tipo == alvo_tipo)
        if alvo_id is not None:
            query = query.where(AuditEvent.alvo_id == str(alvo_id))
        if since is not None:
            query = query.where(AuditEvent.timestamp >= since)
        return int(self._session.scalar(query) or 0)

    def unseen_documents(self, root_id: int, since: datetime) -> Sequence[Document]:
        return list(
            self._session.scalars(
                select(Document)
                .where(
                    Document.root_id == root_id,
                    Document.caminho.is_not(None),
                    Document.caminho.not_like(f"{QUARANTINE_DIR}/%"),
                    Document.ausente_em.is_(None),
                    (Document.visto_em.is_(None)) | (Document.visto_em < since),
                )
                .order_by(Document.id)
            )
        )

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
