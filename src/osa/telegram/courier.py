"""``CourierPort`` over the real registry, index, filesystem and model.

Each call opens its own session; the courier modules commit as they go. The model provider
is built once from the effective configuration and shared (it serializes its own calls).
With file effects off, an upload is written to a local holding folder outside any root, the
courier simulates against it and the copy is removed: the only place the bot deletes
anything, and never inside a root.
"""

from __future__ import annotations

import difflib
import hashlib
import os
import shutil
import tempfile
import threading
import uuid
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Protocol, cast

from sqlalchemy.orm import Session

from osa.admin import services
from osa.core.settings import Settings
from osa.courier import owners as courier_owners
from osa.courier import review as courier_review
from osa.courier.intake import Intake, quarantine_suffix, stage_upload
from osa.courier.owners import OwnerRequest
from osa.courier.pipeline import (
    ALVO,
    MOTIVO_SENSIVEL,
    Archived,
    Duplicate,
    Failed,
    Outcome,
    process,
)
from osa.courier.ports import (
    ClassifyPort,
    ContentPort,
    FsPort,
    LocalContent,
    LocalFs,
    read_within,
)
from osa.courier.review import LeftAtOrigin
from osa.courier.store import SqlStore
from osa.courier.undo import UndoResult, undo_operation
from osa.db.audit import RESULT_DENIED, RESULT_FAILED, RESULT_OK, record_event
from osa.db.models import Department, Document, Owner, ReviewCase, User
from osa.domain.document import DocumentState, FileClass, Origin
from osa.domain.errors import Conflict, DomainError
from osa.domain.owner import normalize_text
from osa.domain.user import Role
from osa.extract.classify import class_by_name
from osa.extract.model import ExtractLimits
from osa.llm.envelopes import Department as DepartmentSpec
from osa.llm.envelopes import DepartmentInput, IntentEnvelope, QueryEnvelope
from osa.llm.provider import ExternalNotConfigured
from osa.search import intent
from osa.search.service import SearchLimits, filters_for, run_search
from osa.telegram.ports import (
    Actor,
    CaseInfo,
    DepartmentInfo,
    DocumentInfo,
    FileToSend,
    HitInfo,
    OwnerInfo,
    PathToShow,
    RootSpace,
    SearchResult,
    SendPlan,
    SendRefused,
    StatusInfo,
    Understood,
)

MIN_SIMILARITY = 0.55
ACAO_ENVIO = "document.send"
MOTIVO_SEM_ARQUIVO = "o documento não tem arquivo no servidor"
MOTIVO_SUMIU = "o arquivo não está mais no caminho registrado"
MOTIVO_MUDOU = "o arquivo no servidor mudou desde o registro"
MOTIVO_RAIZ_INATIVA = "a raiz do documento está inativa"


class InterpretPort(Protocol):
    def interpret(self, text: str, departments: Sequence[DepartmentInput]) -> IntentEnvelope: ...


class RealCourier:
    def __init__(
        self,
        session_factory: Callable[[], Session],
        settings: Settings,
        *,
        fs: FsPort | None = None,
        content: ContentPort | None = None,
        llm: ClassifyPort | None = None,
        query_llm: InterpretPort | None = None,
    ) -> None:
        self._sessions = session_factory
        self.settings = settings
        self.fs: FsPort = fs if fs is not None else LocalFs()
        self.content: ContentPort = (
            content if content is not None else LocalContent(ExtractLimits.from_settings(settings))
        )
        self._llm = llm
        self._llm_fixed = llm is not None
        self._llm_config: tuple[str, str | None] | None = None
        self._llm_lock = threading.Lock()
        self._query_llm = query_llm

    # --- registry -----------------------------------------------------------------------

    def actor(self, telegram_id: int) -> Actor | None:
        with self._sessions() as session:
            user = SqlStore(session).user_by_telegram_id(telegram_id)
            return _actor(user) if user is not None and user.ativo else None

    def admins(self) -> Sequence[Actor]:
        with self._sessions() as session:
            return [
                _actor(u) for u in SqlStore(session).active_users() if u.papel == Role.ADMIN.value
            ]

    def users_of_department(self, department_id: int) -> Sequence[Actor]:
        with self._sessions() as session:
            return [
                _actor(u)
                for u in SqlStore(session).active_users()
                if u.departamento_id == department_id and u.papel == Role.REVIEWER.value
            ]

    def audit(
        self, *, ator: str, acao: str, alvo_tipo: str, resultado: str, detalhes: dict[str, object]
    ) -> None:
        with self._sessions() as session:
            record_event(
                session,
                ator=ator,
                acao=acao,
                alvo_tipo=alvo_tipo,
                resultado=resultado,
                detalhes=detalhes,
            )
            session.commit()

    def resolve_owner(self, text: str) -> OwnerInfo | None:
        with self._sessions() as session:
            owner = courier_owners.resolve_declared_owner(session, text)
            return _owner(owner) if owner is not None else None

    def owner_name_problem(self, text: str) -> str | None:
        try:
            services.check_owner_name(text)
        except ValueError as error:
            return str(error)
        return None

    def suggest_owners(self, text: str, limit: int = 5) -> Sequence[OwnerInfo]:
        """The registered owners closest to what the operator wrote (names and aliases)."""
        query = normalize_text(text)
        if not query:
            return []
        with self._sessions() as session:
            store = SqlStore(session)
            aliases = {c.id: c.aliases for c in store.owner_candidates()}
            scored: list[tuple[float, Owner]] = []
            for owner in store.active_owners():
                keys = {normalize_text(owner.nome_fantasia), *aliases.get(owner.id, ())}
                if owner.razao_social:
                    keys.add(normalize_text(owner.razao_social))
                score = max(_similarity(query, key) for key in keys if key)
                if score >= MIN_SIMILARITY:
                    scored.append((score, owner))
            scored.sort(key=lambda item: (-item[0], item[1].nome_fantasia))
            return [_owner(owner) for _, owner in scored[:limit]]

    def owners(self) -> Sequence[OwnerInfo]:
        with self._sessions() as session:
            return [_owner(o) for o in SqlStore(session).active_owners()]

    def owner(self, owner_id: int) -> OwnerInfo:
        with self._sessions() as session:
            return _owner(SqlStore(session).owner(owner_id))

    def departments(self) -> Sequence[DepartmentInfo]:
        with self._sessions() as session:
            return [_department(d) for d in SqlStore(session).template()]

    def document(self, document_id: int) -> DocumentInfo:
        with self._sessions() as session:
            d = SqlStore(session).document(document_id)
            return DocumentInfo(
                d.id, d.nome, d.estado, d.tipo_documental, d.resumo, d.motivo, d.caminho,
                d.owner_id, d.departamento_id, d.enviado_por,
            )  # fmt: skip

    # --- the courier --------------------------------------------------------------------

    def _provider(self, session: Session) -> ClassifyPort:
        """The provider for the model configured now; rebuilt when the panel changes it."""
        with self._llm_lock:
            if self._llm_fixed:
                assert self._llm is not None
                return self._llm
            from osa.llm.provider import build_local_provider

            config = services.effective_model_config(session, self.settings)
            key = (config.nome, config.digest)
            if self._llm is None or key != self._llm_config:
                self._llm = build_local_provider(self.settings, config)
                self._llm_config = key
            return self._llm

    def process_upload(self, data: bytes, name: str, *, owner_id: int, sender: Actor) -> Outcome:
        effects = self.settings.enable_file_effects
        with self._sessions() as session:
            store = SqlStore(session)
            owner = store.owner(owner_id)
            if not owner.ativo:
                raise Conflict("dono inativo")
            # A duplicate is known from the bytes alone: nothing is staged for it (SPEC §5-A
            # step 3), so the quarantine never keeps a copy no record points at.
            sha256 = hashlib.sha256(data).hexdigest()
            existing = store.document_by_sha256(sha256)
            if existing is not None and existing.estado in (
                DocumentState.ARQUIVADO.value,
                DocumentState.EM_REVISAO.value,
            ):
                store.audit(
                    ator=sender.actor,
                    acao="document.duplicate",
                    alvo_tipo=ALVO,
                    alvo_id=existing.id,
                    sha256=sha256,
                    resultado=RESULT_DENIED,
                    detalhes={"estado": existing.estado, "origem": Origin.TELEGRAM.value},
                )
                store.commit()
                return Duplicate(existing.id, existing.estado, existing.caminho)
            root = courier_owners.upload_root(session, owner, fs=self.fs)
            llm = self._provider(session)
            holding: Path | None = None
            if effects:
                staged = stage_upload(
                    Path(root.caminho),
                    data,
                    name,
                    sensitive_extensions=self.settings.sensitive_extensions,
                    effects_enabled=True,
                )
                source, relative = staged.path, staged.relative_path
            else:
                holding = self._holding_copy(data, name)
                source, relative = holding, None
            try:
                return process(
                    Intake(
                        root.id,
                        source,
                        relative,
                        name,
                        Origin.TELEGRAM,
                        declared_owner=owner.id,
                        sender=sender.user_id,
                    ),  # fmt: skip
                    store=store,
                    fs=self.fs,
                    content=self.content,
                    llm=llm,
                    actor=sender.actor,
                    effects_enabled=effects,
                )
            finally:
                if holding is not None:
                    shutil.rmtree(holding.parent, ignore_errors=True)

    def _holding_copy(self, data: bytes, name: str) -> Path:
        """A local copy outside every root, for a simulated run; removed afterwards."""
        base = self.settings.quarantine_dir or Path(tempfile.gettempdir()) / "osa-uploads"
        folder = base / uuid.uuid4().hex
        folder.mkdir(parents=True, mode=0o700)
        # Named by an id plus the original's extension, like the quarantine: the class sniff
        # needs the extension and nothing else of the name, which Telegram does not validate.
        path = folder / f"{uuid.uuid4().hex}{quarantine_suffix(name)}"
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "wb") as handle:
            handle.write(data)
        return path

    def confirm_owner(self, request: OwnerRequest, *, admin: Actor) -> OwnerInfo:
        with self._sessions() as session:
            owner = courier_owners.confirm_owner_request(
                session,
                request,
                admin_id=admin.user_id,
                fs=self.fs,
                effects_enabled=self.settings.enable_file_effects,
                actor=admin.actor,
            )
            return _owner(owner)

    def refuse_owner(self, request: OwnerRequest, *, admin: Actor) -> None:
        with self._sessions() as session:
            courier_owners.refuse_owner_request(
                session, request, admin_id=admin.user_id, actor=admin.actor
            )
            session.commit()

    def case(self, case_id: int) -> CaseInfo:
        with self._sessions() as session:
            return _case(SqlStore(session).review_case(case_id))

    def open_cases(self) -> Sequence[CaseInfo]:
        with self._sessions() as session:
            return [_case(c) for c in SqlStore(session).open_review_cases()]

    def decide_case(
        self, case_id: int, *, owner_id: int, department_id: int, actor: Actor
    ) -> Archived | Failed:
        with self._sessions() as session:
            return courier_review.decide_case(
                case_id,
                owner_id=owner_id,
                department_id=department_id,
                store=SqlStore(session),
                fs=self.fs,
                actor=actor.actor,
                decided_by=actor.user_id,
                effects_enabled=self.settings.enable_file_effects,
            )

    def leave_at_origin(self, case_id: int, *, actor: Actor) -> LeftAtOrigin:
        with self._sessions() as session:
            return courier_review.leave_at_origin(
                case_id, store=SqlStore(session), actor=actor.actor, decided_by=actor.user_id
            )

    def undo(self, operation_id: int, *, actor: Actor) -> UndoResult:
        with self._sessions() as session:
            return undo_operation(
                operation_id,
                store=SqlStore(session),
                fs=self.fs,
                actor=actor.actor,
                effects_enabled=self.settings.enable_file_effects,
            )

    def status(self) -> StatusInfo:
        with self._sessions() as session:
            store = SqlStore(session)
            config = services.effective_model_config(session, self.settings)
            roots = []
            for root in store.active_roots():
                try:
                    free: int | None = shutil.disk_usage(root.caminho).free
                except OSError:
                    free = None
                roots.append(RootSpace(root.caminho, free, root.ultimo_scan))
            return StatusInfo(
                documentos=store.documents_by_state(),
                revisoes_abertas=len(store.open_review_cases()),
                modelo=config.nome,
                digest=config.digest,
                efeitos=self.settings.enable_file_effects,
                raizes=tuple(roots),
            )

    # --- query and send (TASK-110) -------------------------------------------------------------

    def _interpreter(self, session: Session) -> InterpretPort:
        if self._query_llm is not None:
            return self._query_llm
        config = services.effective_model_config(session, self.settings)
        if config.provedor_externo_ativo:
            # No external transport exists in this version: no silent fallback to local.
            raise ExternalNotConfigured(
                "provedor externo ligado sem transporte configurado: desligue-o no painel"
            )
        return cast(InterpretPort, self._provider(session))

    def interpret(self, text: str) -> IntentEnvelope:
        with self._sessions() as session:
            specs = [DepartmentSpec(d.nome, d.descricao) for d in SqlStore(session).template()]
            interpreter = self._interpreter(session)
        return interpreter.interpret(text, specs)

    def search(self, query: QueryEnvelope, *, actor: Actor) -> SearchResult:
        with self._sessions() as session:
            store = SqlStore(session)
            filters = filters_for(session, query)
            limits = SearchLimits(
                max_hits=intent.SEARCH_LIMIT,
                sensitive_extensions=tuple(self.settings.sensitive_extensions),
            )
            hits = [] if filters.unresolved else run_search(session, filters, limits)
            owners = {o.id: o.nome_fantasia for o in store.active_owners()}
            departments = {d.id: d.nome for d in store.template()}
            understood = Understood(
                dono_id=filters.owner_id,
                dono=owners.get(filters.owner_id) if filters.owner_id is not None else None,
                dono_desconhecido=filters.owner_unresolved,
                departamento_id=filters.department_id,
                departamento=departments.get(filters.department_id)
                if filters.department_id is not None
                else None,
                departamento_desconhecido=filters.department_unresolved,
                tipo_documental=filters.tipo_documental,
                periodo_inicio=filters.periodo_inicio,
                periodo_fim=filters.periodo_fim,
                palavras_chave=filters.terms,
            )
            store.audit(
                ator=actor.actor,
                acao="query.search",
                alvo_tipo="query",
                alvo_id=None,
                sha256=None,
                resultado=RESULT_OK,
                detalhes={
                    **understood.as_audit(),
                    "resultados": len(hits),
                    "mais_que_o_limite": len(hits) >= intent.SEARCH_LIMIT,
                },
            )
            store.commit()
            return SearchResult(
                tuple(
                    HitInfo(
                        document_id=hit.document_id,
                        nome=hit.nome or hit.caminho.rsplit("/", 1)[-1],
                        dono=owners.get(hit.owner_id) if hit.owner_id is not None else None,
                        departamento=departments.get(hit.departamento_id)
                        if hit.departamento_id is not None
                        else None,
                        ano=hit.ano,
                        tipo_documental=hit.tipo_documental,
                        sensivel=hit.sensivel,
                    )
                    for hit in hits
                ),
                understood,
            )

    def prepare_send(
        self, document_id: int, *, actor: Actor, consulta: dict[str, object]
    ) -> SendPlan:
        """The delivery of one document (SPEC §5-B step 4); never reads a sensitive file."""
        limit = self.settings.telegram_send_limit_mb * 1024 * 1024
        with self._sessions() as session:
            store = SqlStore(session)
            document = store.document(document_id)

            def refuse(motivo: str, resultado: str = RESULT_FAILED) -> SendRefused:
                self._audit_send(store, document, actor, consulta, "recusado", resultado, motivo)
                return SendRefused(motivo)

            if document.caminho is None or document.root_id is None or document.ausente_em:
                return refuse(MOTIVO_SEM_ARQUIVO)
            root = store.root(document.root_id)
            if not root.ativo:
                return refuse(MOTIVO_RAIZ_INATIVA)
            # Sensitive by the indexed name, the path's name or the record itself (a ``.pfx``
            # renamed on the share keeps its marker). Decided before any disk access, so a
            # refused operator learns nothing about the file.
            sensitive = self._is_sensitive(document, document.caminho.rsplit("/", 1)[-1])
            if sensitive and not actor.is_admin:
                return refuse(intent.MOTIVO_SENSIVEL, RESULT_DENIED)
            try:
                path = self.fs.locate(Path(root.caminho), document.caminho)
                size = os.stat(path, follow_symlinks=False).st_size
            except (DomainError, OSError):
                return refuse(MOTIVO_SUMIU)
            sensitive = sensitive or self._is_sensitive(document, path.name)
            decision = intent.delivery(
                sensitive=sensitive, size=size, limit_bytes=limit, is_admin=actor.is_admin
            )
            if decision.how == "recusado":
                assert decision.motivo is not None
                return refuse(decision.motivo, RESULT_DENIED)
            if decision.how == "caminho":
                assert decision.motivo is not None
                where = intent.network_path(root.unc, root.caminho, document.caminho)
                self._audit_send(
                    store, document, actor, consulta, "caminho", RESULT_OK, decision.motivo
                )
                return PathToShow(where, decision.motivo)
            try:
                data, sha256 = read_within(Path(root.caminho), document.caminho, max_bytes=limit)
            except (DomainError, OSError):
                return refuse(MOTIVO_SUMIU)
            if sha256 != document.sha256:
                return refuse(MOTIVO_MUDOU)
            # The attempt is on record before a byte leaves (like the external provider): a
            # trail that cannot be written means no send; the result follows ``record_send``.
            self._audit_send(
                store, document, actor, consulta, "arquivo", RESULT_FAILED, None,
                fase="tentativa",
            )  # fmt: skip
            name = document.nome or document.caminho.rsplit("/", 1)[-1]
            return FileToSend(name, data, sha256)

    def _is_sensitive(self, document: Document, *names: str) -> bool:
        if document.motivo == MOTIVO_SENSIVEL:
            return True
        return any(
            class_by_name(name, self.settings.sensitive_extensions) is FileClass.SENSITIVE
            for name in (document.nome or "", *names)
            if name
        )

    def record_send(
        self,
        document_id: int,
        *,
        actor: Actor,
        consulta: dict[str, object],
        ok: bool,
        erro: str | None = None,
    ) -> None:
        with self._sessions() as session:
            store = SqlStore(session)
            document = store.document(document_id)
            self._audit_send(
                store, document, actor, consulta, "arquivo", RESULT_OK if ok else RESULT_FAILED,
                erro,
            )  # fmt: skip

    @staticmethod
    def _audit_send(
        store: SqlStore,
        document: Document,
        actor: Actor,
        consulta: dict[str, object],
        entrega: str,
        resultado: str,
        motivo: str | None,
        *,
        fase: str = "resultado",
    ) -> None:
        """Who asked, the query, the hash, the path and the result (SPEC §5-B step 5).

        A file send is two events: ``fase = tentativa`` before the bytes leave (stored as
        ``failed``: the table has no "pending") and ``fase = resultado`` after.
        """
        store.audit(
            ator=actor.actor,
            acao=ACAO_ENVIO,
            alvo_tipo=ALVO,
            alvo_id=document.id,
            sha256=document.sha256,
            resultado=resultado,
            detalhes={
                "consulta": consulta,
                "caminho": document.caminho,
                "root_id": document.root_id,
                "entrega": entrega,
                "motivo": motivo,
                "fase": fase,
            },
        )
        store.commit()


# --- row to info ----------------------------------------------------------------------------


def _actor(user: User) -> Actor:
    return Actor(user.id, user.telegram_id, user.nome, user.papel, user.departamento_id)


def _owner(owner: Owner) -> OwnerInfo:
    return OwnerInfo(owner.id, owner.nome_fantasia, owner.pasta)


def _department(department: Department) -> DepartmentInfo:
    return DepartmentInfo(department.id, department.nome, department.usa_ano)


def _case(case: ReviewCase) -> CaseInfo:
    return CaseInfo(
        case.id, case.document_id, case.estado, case.motivo, case.departamento_sugerido_id,
        case.criado_em,
    )  # fmt: skip


def _similarity(query: str, key: str) -> float:
    if query == key:
        return 1.0
    if query in key or key in query:
        return 0.9
    return difflib.SequenceMatcher(None, query, key).ratio()
