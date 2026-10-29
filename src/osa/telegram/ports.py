"""What the bot handlers see: incoming events, the transport port, the courier port, state.

Nothing here imports python-telegram-bot: the handlers are plain functions over these
types, and the adapter (``osa.telegram.app``) converts updates into them.
"""

from __future__ import annotations

import threading
import time
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import datetime
from typing import Protocol

from osa.courier.owners import OwnerRequest
from osa.courier.pipeline import Archived, Failed, Outcome
from osa.courier.review import LeftAtOrigin
from osa.courier.undo import UndoResult
from osa.domain.user import Role
from osa.extract.model import DEFAULT_SENSITIVE_EXTENSIONS

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
    """The five things the handlers do with Telegram; the adapter implements them."""

    def send(self, chat_id: int, text: str, buttons: Keyboard = ()) -> int:
        """Send a message and return its id."""
        ...

    def edit(self, chat_id: int, message_id: int, text: str, buttons: Keyboard = ()) -> None: ...

    def answer(self, callback_id: str, text: str | None = None) -> None: ...

    def download(self, file_id: str) -> bytes: ...


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


@dataclass(frozen=True, slots=True)
class StatusInfo:
    documentos: dict[str, int]
    revisoes_abertas: int
    modelo: str
    digest: str | None
    efeitos: bool
    raizes: tuple[RootSpace, ...]


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
