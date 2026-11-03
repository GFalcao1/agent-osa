"""What the bot handlers see: incoming events, the transport port, the courier port, state.

Nothing here imports python-telegram-bot: the handlers are plain functions over these
types, and the adapter (``osa.telegram.app``) converts updates into them.
"""

from __future__ import annotations

import threading
import time
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Protocol

from osa.courier.owners import OwnerRequest
from osa.courier.pipeline import Archived, Failed, Outcome
from osa.courier.review import LeftAtOrigin
from osa.courier.undo import UndoResult
from osa.domain.user import Role
from osa.extract.model import DEFAULT_SENSITIVE_EXTENSIONS
from osa.llm.envelopes import IntentEnvelope, QueryEnvelope

# --- incoming events ---------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class IncomingDocument:
    chat_id: int
    from_id: int  # Telegram user id
    message_id: int
    file_id: str
    file_name: str
    file_size: int | None
    caption: str | None


@dataclass(frozen=True, slots=True)
class IncomingText:
    chat_id: int
    from_id: int
    message_id: int
    text: str


@dataclass(frozen=True, slots=True)
class IncomingCommand:
    chat_id: int
    from_id: int
    message_id: int
    name: str  # without the slash
    args: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class Callback:
    """An inline button press."""

    callback_id: str
    chat_id: int
    from_id: int
    message_id: int
    data: str


# --- the transport -------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class Button:
    text: str
    data: str  # callback data, at most 64 bytes (Telegram's limit)


Keyboard = Sequence[Sequence[Button]]


class BotPort(Protocol):
    """What the handlers do with Telegram; the adapter implements it."""

    def send(self, chat_id: int, text: str, buttons: Keyboard = ()) -> int:
        """Send a message and return its id."""
        ...

    def edit(self, chat_id: int, message_id: int, text: str, buttons: Keyboard = ()) -> None: ...

    def answer(self, callback_id: str, text: str | None = None) -> None: ...

    def download(self, file_id: str) -> bytes: ...

    def send_document(
        self, chat_id: int, data: bytes, filename: str, caption: str | None = None
    ) -> int:
        """Send a file as a document (never as a photo: no compression) and return its id."""
        ...


# --- the courier as the bot sees it ---------------------------------------------------------


@dataclass(frozen=True, slots=True)
class Actor:
    """An allowlisted, active user (``users`` row)."""

    user_id: int
    telegram_id: int
    nome: str
    papel: str
    departamento_id: int | None

    @property
    def is_admin(self) -> bool:
        return self.papel == Role.ADMIN.value

    @property
    def is_reviewer(self) -> bool:
        return self.papel == Role.REVIEWER.value or self.is_admin

    @property
    def actor(self) -> str:
        """The audit actor string."""
        return f"telegram:{self.telegram_id}"


@dataclass(frozen=True, slots=True)
class OwnerInfo:
    id: int
    nome_fantasia: str
    pasta: str


@dataclass(frozen=True, slots=True)
class DepartmentInfo:
    id: int
    nome: str
    usa_ano: bool


@dataclass(frozen=True, slots=True)
class DocumentInfo:
    id: int
    nome: str | None
    estado: str
    tipo_documental: str | None
    resumo: str | None
    motivo: str | None
    caminho: str | None
    owner_id: int | None
    departamento_id: int | None
    enviado_por: int | None


@dataclass(frozen=True, slots=True)
class CaseInfo:
    id: int
    document_id: int
    estado: str
    motivo: str
    departamento_sugerido_id: int | None
    criado_em: datetime | None


@dataclass(frozen=True, slots=True)
class RootSpace:
    caminho: str
    livre_bytes: int | None  # None when the root cannot be measured
    ultimo_scan: datetime | None = None  # the last finished scan of the root (TASK-109)


@dataclass(frozen=True, slots=True)
class Understood:
    """The filters of a search as the registry understood them, for the reply and the audit.

    ``*_desconhecido`` is what the message named and the registry does not know.
    """

    dono_id: int | None
    dono: str | None
    dono_desconhecido: str | None
    departamento_id: int | None
    departamento: str | None
    departamento_desconhecido: str | None
    tipo_documental: str | None
    periodo_inicio: date | None
    periodo_fim: date | None
    palavras_chave: tuple[str, ...]

    def as_audit(self) -> dict[str, object]:
        """The query as the audit keeps it: ids, the type, the period and the keywords."""
        return {
            "dono_id": self.dono_id,
            "dono_desconhecido": self.dono_desconhecido is not None,
            "departamento_id": self.departamento_id,
            "departamento_desconhecido": self.departamento_desconhecido is not None,
            "tipo_documental": self.tipo_documental,
            "periodo_inicio": self.periodo_inicio.isoformat() if self.periodo_inicio else None,
            "periodo_fim": self.periodo_fim.isoformat() if self.periodo_fim else None,
            "palavras_chave": list(self.palavras_chave),
        }


@dataclass(frozen=True, slots=True)
class HitInfo:
    """One document found, as a button shows it; never its text."""

    document_id: int
    nome: str
    dono: str | None
    departamento: str | None
    ano: int | None
    tipo_documental: str | None
    sensivel: bool


@dataclass(frozen=True, slots=True)
class SearchResult:
    hits: tuple[HitInfo, ...]  # at most ``osa.search.intent.SEARCH_LIMIT``
    filtros: Understood


@dataclass(frozen=True, slots=True)
class FileToSend:
    nome: str
    data: bytes
    sha256: str  # checked against the index before it is returned


@dataclass(frozen=True, slots=True)
class PathToShow:
    caminho: str  # the network path (or the path on this machine without a UNC)
    motivo: str


@dataclass(frozen=True, slots=True)
class SendRefused:
    motivo: str


SendPlan = FileToSend | PathToShow | SendRefused


@dataclass(frozen=True, slots=True)
class StatusInfo:
    documentos: dict[str, int]
    revisoes_abertas: int
    modelo: str
    digest: str | None
    efeitos: bool
    raizes: tuple[RootSpace, ...]
    versao: str = ""  # the running osa version (TASK-112)


class CourierPort(Protocol):
    """Everything the handlers ask of the courier and the registry."""

    def actor(self, telegram_id: int) -> Actor | None: ...

    def admins(self) -> Sequence[Actor]: ...

    def users_of_department(self, department_id: int) -> Sequence[Actor]: ...

    def audit(
        self, *, ator: str, acao: str, alvo_tipo: str, resultado: str, detalhes: dict[str, object]
    ) -> None: ...

    def resolve_owner(self, text: str) -> OwnerInfo | None: ...

    def owner_name_problem(self, text: str) -> str | None:
        """Why the registry would refuse ``text`` as a new owner's name; ``None`` if fine."""
        ...

    def suggest_owners(self, text: str, limit: int = 5) -> Sequence[OwnerInfo]: ...

    def owners(self) -> Sequence[OwnerInfo]: ...

    def owner(self, owner_id: int) -> OwnerInfo: ...

    def departments(self) -> Sequence[DepartmentInfo]: ...

    def document(self, document_id: int) -> DocumentInfo: ...

    def process_upload(
        self, data: bytes, name: str, *, owner_id: int, sender: Actor
    ) -> Outcome: ...

    def confirm_owner(self, request: OwnerRequest, *, admin: Actor) -> OwnerInfo: ...

    def refuse_owner(self, request: OwnerRequest, *, admin: Actor) -> None: ...

    def case(self, case_id: int) -> CaseInfo: ...

    def open_cases(self) -> Sequence[CaseInfo]: ...

    def decide_case(
        self, case_id: int, *, owner_id: int, department_id: int, actor: Actor
    ) -> Archived | Failed: ...

    def leave_at_origin(self, case_id: int, *, actor: Actor) -> LeftAtOrigin: ...

    def undo(self, operation_id: int, *, actor: Actor) -> UndoResult: ...

    def status(self) -> StatusInfo: ...

    def interpret(self, text: str) -> IntentEnvelope:
        """The intent envelope of an operator's message: only the message goes to the model."""
        ...

    def search(self, query: QueryEnvelope, *, actor: Actor) -> SearchResult:
        """The index searched with the query; audited (``query.search``), never the text."""
        ...

    def prepare_send(
        self, document_id: int, *, actor: Actor, consulta: dict[str, object]
    ) -> SendPlan:
        """How the document reaches ``actor``; a path or a refusal is audited here."""
        ...

    def record_send(
        self,
        document_id: int,
        *,
        actor: Actor,
        consulta: dict[str, object],
        ok: bool,
        erro: str | None = None,
    ) -> None:
        """The audit of a file sent through Telegram (``document.send``), after the attempt."""
        ...


# --- dialog state ----------------------------------------------------------------------------


@dataclass(slots=True)
class PendingUpload:
    """A document received and not yet processed: the owner is still being asked."""

    chat_id: int
    actor: Actor
    file_id: str
    file_name: str
    file_size: int | None
    owner_text: str | None = None
    created_at: float = field(default_factory=time.monotonic)


@dataclass(slots=True)
class PendingOwnerRequest:
    token: str
    request: OwnerRequest
    requester: Actor
    requester_chat_id: int
    upload: PendingUpload | None
    admin_messages: list[tuple[int, int]] = field(default_factory=list)  # (chat_id, message_id)


@dataclass(slots=True)
class Offer:
    """The documents a search offered to one user in one chat, with the query that found them.

    A button press is honoured only for a document in the offer: callback data can be forged
    by a modified client, and the audit needs the query behind each send.
    """

    document_ids: tuple[int, ...]
    consulta: dict[str, object]
    message_id: int  # the message whose buttons carry this offer


@dataclass(slots=True)
class ReviewDraft:
    """What the reviewer has chosen so far for one case; lives until the bot restarts."""

    case_id: int
    chat_id: int
    message_id: int
    owner_id: int | None = None
    department_id: int | None = None


class BotState:
    """In-memory dialogs, guarded by one lock: the bot handles updates in worker threads.

    A restart loses pending dialogs: the operator sends the document again, the reviewer
    chooses again. Nothing here is a record; records live in the index and the audit.
    """

    def __init__(self) -> None:
        self.lock = threading.RLock()
        self.uploads: dict[tuple[int, int], PendingUpload] = {}
        self.owner_requests: dict[str, PendingOwnerRequest] = {}
        self.drafts: dict[int, ReviewDraft] = {}
        self.offers: dict[tuple[int, int], Offer] = {}  # (chat_id, users.id)
        self._case_locks: dict[int, threading.Lock] = {}

    def case_lock(self, case_id: int) -> threading.Lock:
        """One lock per case: two decisions on the same case never run at the same time."""
        with self.lock:
            return self._case_locks.setdefault(case_id, threading.Lock())


@dataclass(frozen=True, slots=True)
class BotContext:
    """What every handler receives."""

    courier: CourierPort
    bot: BotPort
    state: BotState
    reviewers_chat_id: int | None
    effects_enabled: bool
    download_limit: int = 20 * 1024 * 1024  # the Bot API download limit
    sensitive_extensions: Sequence[str] = DEFAULT_SENSITIVE_EXTENSIONS  # the installation's list
