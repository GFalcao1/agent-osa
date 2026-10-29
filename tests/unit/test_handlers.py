"""Os handlers do bot com transporte e correio falsos: upload, dono novo, revisão, comandos."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

import pytest

from osa.courier.owners import OwnerRequest
from osa.courier.pipeline import (
    Archived,
    ArchiveRefused,
    ConfigurationError,
    Duplicate,
    Failed,
    NotSupported,
    Outcome,
    Reviewed,
)
from osa.courier.review import LeftAtOrigin
from osa.courier.undo import UndoResult
from osa.domain.errors import Conflict, NotFound, SensitiveFile
from osa.domain.owner import normalize_text
from osa.telegram import jobs, texts
from osa.telegram.app import dispatch_callback
from osa.telegram.handlers import admin, owners, review, upload
from osa.telegram.ports import (
    Actor,
    BotContext,
    BotState,
    Button,
    Callback,
    CaseInfo,
    DepartmentInfo,
    DocumentInfo,
    IncomingCommand,
    IncomingDocument,
    IncomingText,
    Keyboard,
    OwnerInfo,
    RootSpace,
    StatusInfo,
)

ADMIN = Actor(1, 1001, "Admin Ficticio", "admin", None)
MARIA = Actor(2, 2002, "Maria Ficticia", "reviewer", 10)  # Fiscal
JOAO = Actor(3, 3003, "João Ficticio", "reviewer", 20)  # Pessoal
ANA = Actor(4, 4004, "Ana Ficticia", "operator", 10)
REVIEWERS_CHAT = -100123
PDF = b"%PDF-1.4 ficticio"


# --- fakes -----------------------------------------------------------------------------


class FakeBot:
    def __init__(self) -> None:
        self.sent: list[tuple[int, str, list[list[Button]]]] = []
        self.edits: list[tuple[int, int, str, list[list[Button]]]] = []
        self.answers: list[tuple[str, str | None]] = []
        self.files: dict[str, bytes] = {"f1": PDF}
        self.downloads: list[str] = []
        self._next_id = 100

    def send(self, chat_id: int, text: str, buttons: Keyboard = ()) -> int:
        self._next_id += 1
        self.sent.append((chat_id, text, [list(row) for row in buttons]))
        return self._next_id

    def edit(self, chat_id: int, message_id: int, text: str, buttons: Keyboard = ()) -> None:
        self.edits.append((chat_id, message_id, text, [list(row) for row in buttons]))

    def answer(self, callback_id: str, text: str | None = None) -> None:
        self.answers.append((callback_id, text))

    def download(self, file_id: str) -> bytes:
        self.downloads.append(file_id)
        if file_id not in self.files:
            raise RuntimeError("download falhou")
        return self.files[file_id]

    def texts_to(self, chat_id: int) -> list[str]:
        return [text for chat, text, _ in self.sent if chat == chat_id]

    def buttons_of(self, index: int = -1) -> list[str]:
        return [button.data for row in self.sent[index][2] for button in row]


@dataclass
class FakeCourier:
    users: dict[int, Actor] = field(default_factory=dict)
    owners_by_id: dict[int, OwnerInfo] = field(default_factory=dict)
    aliases: dict[str, int] = field(default_factory=dict)
    departments_: list[DepartmentInfo] = field(default_factory=list)
    documents: dict[int, DocumentInfo] = field(default_factory=dict)
    cases: dict[int, CaseInfo] = field(default_factory=dict)
    events: list[dict[str, Any]] = field(default_factory=list)
    uploads: list[tuple[bytes, str, int, Actor]] = field(default_factory=list)
    next_outcome: Outcome | Exception | None = None
    confirmed: list[tuple[OwnerRequest, Actor]] = field(default_factory=list)
    refused: list[tuple[OwnerRequest, Actor]] = field(default_factory=list)
    decisions: list[tuple[int, int, int, Actor]] = field(default_factory=list)
    decide_error: Exception | None = None
    origins: list[tuple[int, Actor]] = field(default_factory=list)
    undos: list[tuple[int, Actor]] = field(default_factory=list)

    def actor(self, telegram_id: int) -> Actor | None:
        return self.users.get(telegram_id)

    def admins(self) -> Sequence[Actor]:
        return [u for u in self.users.values() if u.is_admin]

    def users_of_department(self, department_id: int) -> Sequence[Actor]:
        return [
            u for u in self.users.values() if u.departamento_id == department_id and u.is_reviewer
        ]

    def audit(self, **event: Any) -> None:
        self.events.append(event)

    def resolve_owner(self, text: str) -> OwnerInfo | None:
        owner_id = self.aliases.get(normalize_text(text) if text.strip() else "")
        return self.owners_by_id.get(owner_id) if owner_id is not None else None

    def suggest_owners(self, text: str, limit: int = 5) -> Sequence[OwnerInfo]:
        key = normalize_text(text)[:3]
        return [
            o for o in self.owners_by_id.values() if normalize_text(o.nome_fantasia).startswith(key)
        ][:limit]

    def owners(self) -> Sequence[OwnerInfo]:
        return list(self.owners_by_id.values())

    def owner(self, owner_id: int) -> OwnerInfo:
        if owner_id not in self.owners_by_id:
            raise NotFound("dono não encontrado")
        return self.owners_by_id[owner_id]

    def departments(self) -> Sequence[DepartmentInfo]:
        return list(self.departments_)

    def document(self, document_id: int) -> DocumentInfo:
        if document_id not in self.documents:
            raise NotFound("documento não encontrado")
        return self.documents[document_id]

    def process_upload(self, data: bytes, name: str, *, owner_id: int, sender: Actor) -> Outcome:
        self.uploads.append((data, name, owner_id, sender))
        if isinstance(self.next_outcome, Exception):
            raise self.next_outcome
        assert self.next_outcome is not None
        outcome = self.next_outcome
        self.documents.setdefault(
            outcome.document_id,
            DocumentInfo(outcome.document_id, name, outcome.estado, "nota fiscal",
                         "Nota fiscal fictícia.", None, None, None, None, sender.user_id),
        )  # fmt: skip
        if isinstance(outcome, Reviewed):
            self.cases.setdefault(
                outcome.case_id,
                CaseInfo(outcome.case_id, outcome.document_id, "aberto", outcome.motivo,
                         outcome.departamento_sugerido_id, datetime(2026, 10, 28)),
            )  # fmt: skip
        return outcome

    def confirm_owner(self, request: OwnerRequest, *, admin: Actor) -> OwnerInfo:
        self.confirmed.append((request, admin))
        owner = OwnerInfo(len(self.owners_by_id) + 1, request.nome, request.nome)
        self.owners_by_id[owner.id] = owner
        self.aliases[normalize_text(request.nome)] = owner.id
        return owner

    def refuse_owner(self, request: OwnerRequest, *, admin: Actor) -> None:
        self.refused.append((request, admin))

    def case(self, case_id: int) -> CaseInfo:
        if case_id not in self.cases:
            raise NotFound("caso de revisão não encontrado")
        return self.cases[case_id]

    def open_cases(self) -> Sequence[CaseInfo]:
        return [c for c in self.cases.values() if c.estado == "aberto"]

    def decide_case(
        self, case_id: int, *, owner_id: int, department_id: int, actor: Actor
    ) -> Archived | Failed:
        self.decisions.append((case_id, owner_id, department_id, actor))
        if self.decide_error is not None:
            raise self.decide_error
        case = self.cases[case_id]
        self.cases[case_id] = CaseInfo(case.id, case.document_id, "decidido", case.motivo,
                                       case.departamento_sugerido_id, case.criado_em)  # fmt: skip
        owner = self.owners_by_id[owner_id]
        return Archived(case.document_id, "ARQUIVADO", f"{owner.pasta}/X/nota.pdf", False, 7, 1,
                        owner_id, department_id)  # fmt: skip

    def leave_at_origin(self, case_id: int, *, actor: Actor) -> LeftAtOrigin:
        self.origins.append((case_id, actor))
        case = self.cases[case_id]
        self.cases[case_id] = CaseInfo(case.id, case.document_id, "origem", case.motivo,
                                       case.departamento_sugerido_id, case.criado_em)  # fmt: skip
        return LeftAtOrigin(case.document_id, "NA_ORIGEM", case_id)

    def undo(self, operation_id: int, *, actor: Actor) -> UndoResult:
        self.undos.append((operation_id, actor))
        if operation_id == 404:
            raise NotFound("operação de arquivamento não encontrado")
        return UndoResult(operation_id, 9, 1, ".osa-quarentena/abc.pdf", False)

    def status(self) -> StatusInfo:
        return StatusInfo(
            {"ARQUIVADO": 3, "EM_REVISAO": 1},
            1,
            "modelo-ficticio:1b",
            "sha256:abcdef0123456789",
            False,
            (RootSpace("/srv/raiz", 10 * 2**30),),
        )


@dataclass
class World:
    courier: FakeCourier
    bot: FakeBot
    ctx: BotContext

    def document(self, chat_id: int = 4004, from_id: int = 4004, caption: str | None = "Alfa",
                 name: str = "nota.pdf", size: int | None = 1234) -> None:  # fmt: skip
        upload.handle_document(
            self.ctx, IncomingDocument(chat_id, from_id, 1, "f1", name, size, caption)
        )

    def text(self, text: str, chat_id: int = 4004, from_id: int = 4004) -> bool:
        return upload.handle_text(self.ctx, IncomingText(chat_id, from_id, 2, text))

    def press(self, data: str, *, from_id: int = 4004, chat_id: int | None = None,
              message_id: int = 101) -> None:  # fmt: skip
        dispatch_callback(
            self.ctx, Callback("cb1", chat_id if chat_id is not None else from_id, from_id,
                               message_id, data)
        )  # fmt: skip

    def command(self, name: str, *args: str, from_id: int = 1001) -> None:
        admin.handle_command(self.ctx, IncomingCommand(from_id, from_id, 3, name, tuple(args)))


def archived(document_id: int = 1) -> Archived:
    return Archived(
        document_id, "ARQUIVADO", "Alfa Comércio/01 Fiscal/2026/nota.pdf", False, 5, 1, 1, 10
    )


def reviewed(
    case_id: int = 1, *, department: int | None = 10, motivo: str = "texto_insuficiente"
) -> Reviewed:
    return Reviewed(1, "EM_REVISAO", case_id, motivo, department)


@pytest.fixture
def world() -> World:
    courier = FakeCourier(
        users={a.telegram_id: a for a in (ADMIN, MARIA, JOAO, ANA)},
        owners_by_id={1: OwnerInfo(1, "Alfa Comércio", "Alfa Comércio"),
                      2: OwnerInfo(2, "Beta Serviços", "Beta Serviços")},
        aliases={"alfa": 1, "alfa comercio": 1, "beta": 2},
        departments_=[DepartmentInfo(10, "Fiscal", True), DepartmentInfo(20, "Pessoal", False)],
        next_outcome=archived(),
    )  # fmt: skip
    bot = FakeBot()
    ctx = BotContext(courier, bot, BotState(), REVIEWERS_CHAT, True)
    return World(courier, bot, ctx)


# --- upload ------------------------------------------------------------------------------


def test_unknown_id_gets_no_reply_and_is_audited(world: World) -> None:
    world.document(chat_id=9999, from_id=9999)
    assert world.bot.sent == [] and world.bot.downloads == [] and world.courier.uploads == []
    [event] = world.courier.events
    assert (event["acao"], event["resultado"]) == ("telegram.unknown", "denied")


def test_sensitive_extension_is_refused_before_any_download(world: World) -> None:
    world.document(name="certificado.PFX")
    assert world.bot.downloads == [] and world.courier.uploads == []
    assert world.bot.texts_to(4004) == [texts.SENSITIVE_REFUSED]
    [event] = world.courier.events
    assert (event["acao"], event["resultado"]) == ("telegram.upload", "denied")
    assert "sensível" in event["detalhes"]["motivo"]


def test_file_above_the_download_limit_is_sent_to_the_share(world: World) -> None:
    world.document(size=21 * 1024 * 1024)
    assert world.bot.downloads == [] and world.bot.texts_to(4004) == [texts.TOO_BIG]


def test_upload_with_a_valid_owner_calls_the_pipeline_and_replies_the_destination(
    world: World,
) -> None:
    world.document(caption="ALFA Comércio")

    [(data, name, owner_id, sender)] = world.courier.uploads
    assert (data, name, owner_id, sender) == (PDF, "nota.pdf", 1, ANA)
    assert world.bot.downloads == ["f1"]
    [reply] = world.bot.texts_to(4004)
    assert reply.startswith("Arquivado em Alfa Comércio/01 Fiscal/2026/nota.pdf.")
    assert "nota fiscal" in reply and "Nota fiscal fictícia." in reply
    assert world.ctx.state.uploads == {}


def test_without_caption_the_bot_asks_for_the_owner_and_the_next_text_answers(
    world: World,
) -> None:
    world.document(caption=None)
    assert world.bot.texts_to(4004) == [texts.ASK_OWNER] and world.courier.uploads == []

    assert world.text("alfa") is True

    assert len(world.courier.uploads) == 1
    assert world.bot.texts_to(4004)[-1].startswith("Arquivado em")


def test_text_without_a_pending_upload_is_ignored(world: World) -> None:
    assert world.text("oi") is False
    assert world.bot.sent == []


def test_unknown_owner_offers_suggestions_creation_and_cancel(world: World) -> None:
    world.document(caption="Betaa Servicos")

    assert world.courier.uploads == []
    chat, text, _ = world.bot.sent[-1]
    assert (chat, text) == (4004, texts.NO_OWNER_MATCH)
    assert world.bot.buttons_of() == ["up:o:2", "up:new", "up:x"]

    world.press("up:o:2")

    assert world.courier.uploads[0][2] == 2
    assert world.bot.edits[-1][2] == "Dono: Beta Serviços."


def test_cancel_drops_the_upload(world: World) -> None:
    world.document(caption="ninguem")
    world.press("up:x")
    assert world.courier.uploads == [] and world.ctx.state.uploads == {}
    assert world.bot.edits[-1][2] == texts.UPLOAD_CANCELLED


def test_another_user_cannot_press_the_owner_buttons(world: World) -> None:
    world.document(caption="ninguem")
    world.press("up:o:1", from_id=2002, chat_id=4004)
    assert world.courier.uploads == []
    assert world.bot.answers[-1] == ("cb1", texts.DIALOG_LOST)


def test_download_failure_is_reported_and_nothing_is_processed(world: World) -> None:
    world.bot.files.clear()
    world.document()
    assert world.courier.uploads == []
    assert world.bot.texts_to(4004) == [texts.DOWNLOAD_FAILED]


@pytest.mark.parametrize(
    ("outcome", "needle"),
    [
        (
            Archived(1, "CLASSIFICADO", "Alfa/01 Fiscal/2026/nota.pdf", True, None, 1, 1, 10),
            "Simulado",
        ),
        (Duplicate(1, "ARQUIVADO", "Alfa/01 Fiscal/2026/nota.pdf"), "já está no acervo"),
        (NotSupported(1, "NAO_SUPORTADO", "formato não suportado"), "Não processado"),
        (Failed(1, "FALHOU", "modelo indisponível"), "Falhou"),
        (SensitiveFile("sensível"), texts.SENSITIVE_REFUSED),
        (ConfigurationError("descrição recusada"), "cadastro impede"),
        (Conflict("raiz inativa"), "Não foi possível processar: raiz inativa"),
    ],
)
def test_every_outcome_has_a_reply(world: World, outcome: Any, needle: str) -> None:
    world.courier.next_outcome = outcome
    world.document()
    assert needle in world.bot.texts_to(4004)[-1]


def test_replies_never_carry_document_text(world: World) -> None:
    world.courier.next_outcome = archived()
    world.document()
    world.courier.documents[1] = DocumentInfo(
        1, "nota.pdf", "ARQUIVADO", "nota fiscal", "Resumo curto.", None, None, 1, 10, 4
    )
    for _, text, _ in world.bot.sent:
        assert "SEGREDO" not in text


# --- owner request -----------------------------------------------------------------------


def test_new_owner_request_goes_to_the_admin_with_buttons(world: World) -> None:
    world.document(caption="Gama Indústria")
    world.press("up:new")

    [(chat, text, rows)] = [m for m in world.bot.sent if m[0] == 1001]
    assert "Gama Indústria" in text and "Ana Ficticia" in text
    data = [b.data for row in rows for b in row]
    assert len(data) == 2 and data[0].startswith("ow:") and data[0].endswith(":y")
    assert world.bot.edits[-1][2] == texts.OWNER_REQUEST_SENT.format(nome="Gama Indústria")
    assert world.courier.uploads == []


def test_admin_confirmation_creates_the_owner_and_resumes_the_upload(world: World) -> None:
    world.document(caption="Gama Indústria")
    world.press("up:new")
    token = [b.data for row in world.bot.sent[-1][2] for b in row][0].split(":")[1]

    world.press(f"ow:{token}:y", from_id=1001)

    [(request, admin_actor)] = world.courier.confirmed
    assert (request.nome, request.requested_by, admin_actor) == ("Gama Indústria", 4, ADMIN)
    assert world.courier.uploads[0][2] == 3  # the owner just created
    assert texts.OWNER_CREATED.format(nome="Gama Indústria") in world.bot.texts_to(4004)
    assert world.bot.edits[-1][2].startswith("Dono «Gama Indústria» criado")
    assert world.ctx.state.owner_requests == {}


def test_admin_refusal_creates_nothing(world: World) -> None:
    world.document(caption="Gama Indústria")
    world.press("up:new")
    token = [b.data for row in world.bot.sent[-1][2] for b in row][0].split(":")[1]

    world.press(f"ow:{token}:n", from_id=1001)

    assert world.courier.confirmed == [] and world.courier.uploads == []
    assert len(world.courier.refused) == 1 and len(world.courier.owners_by_id) == 2
    assert texts.OWNER_REQUEST_REFUSED.format(nome="Gama Indústria") in world.bot.texts_to(4004)


def test_only_an_admin_answers_an_owner_request(world: World) -> None:
    world.document(caption="Gama Indústria")
    world.press("up:new")
    token = [b.data for row in world.bot.sent[-1][2] for b in row][0].split(":")[1]

    world.press(f"ow:{token}:y", from_id=2002)

    assert world.courier.confirmed == [] and world.bot.answers[-1] == ("cb1", texts.ONLY_ADMIN)
    assert token in world.ctx.state.owner_requests


def test_without_an_admin_the_request_is_refused_up_front(world: World) -> None:
    del world.courier.users[1001]
    world.document(caption="Gama Indústria")
    world.press("up:new")
    assert world.bot.edits[-1][2] == texts.OWNER_REQUEST_NO_ADMIN


# --- review ------------------------------------------------------------------------------


def _open_review(world: World, **kwargs: Any) -> int:
    world.courier.next_outcome = reviewed(**kwargs)
    world.document()
    posted = [m for m in world.bot.sent if m[0] == REVIEWERS_CHAT]
    assert len(posted) == 1
    return 1


def test_review_is_posted_to_the_reviewers_group_with_buttons(world: World) -> None:
    _open_review(world)
    chat, text, rows = world.bot.sent[-1]
    assert chat == REVIEWERS_CHAT
    assert "Revisão 1" in text and "texto insuficiente" in text
    assert "Dono: Alfa Comércio" in text and "Departamento: Fiscal" in text
    assert "Maria Ficticia" in text  # the reviewers of the suggested department
    data = [b.data for row in rows for b in row]
    assert data == ["rv:1:own", "rv:1:dep", "rv:1:go", "rv:1:orig"]
    assert "Foi para revisão (caso 1)" in world.bot.texts_to(4004)[-1]


def test_button_from_another_department_is_ignored(world: World) -> None:
    _open_review(world)
    world.press("rv:1:go", from_id=3003, chat_id=REVIEWERS_CHAT)
    assert world.courier.decisions == []
    assert world.bot.answers[-1] == ("cb1", texts.NOT_YOUR_DEPARTMENT.format(departamento="Fiscal"))


def test_operator_cannot_decide(world: World) -> None:
    _open_review(world)
    world.press("rv:1:orig", from_id=4004, chat_id=REVIEWERS_CHAT)
    assert world.courier.origins == []


def test_reviewer_of_the_department_archives_through_the_courier(world: World) -> None:
    _open_review(world)
    world.press("rv:1:own", from_id=2002, chat_id=REVIEWERS_CHAT)
    assert [b.data for row in world.bot.edits[-1][3] for b in row] == [
        "rv:1:o:1",
        "rv:1:o:2",
        "rv:1:m",
    ]
    world.press("rv:1:o:2", from_id=2002, chat_id=REVIEWERS_CHAT)
    assert "Dono: Beta Serviços" in world.bot.edits[-1][2]
    world.press("rv:1:dep", from_id=2002, chat_id=REVIEWERS_CHAT)
    world.press("rv:1:d:20", from_id=2002, chat_id=REVIEWERS_CHAT)
    assert "Departamento: Pessoal" in world.bot.edits[-1][2]

    world.press("rv:1:go", from_id=2002, chat_id=REVIEWERS_CHAT)

    assert world.courier.decisions == [(1, 2, 20, MARIA)]
    assert world.courier.cases[1].estado == "decidido"
    assert "Arquivado em Beta Serviços/X/nota.pdf" in world.bot.edits[-1][2]
    assert "Decidido por Maria Ficticia" in world.bot.edits[-1][2]
    assert world.ctx.state.drafts == {}


def test_repeated_decision_is_ignored(world: World) -> None:
    _open_review(world)
    world.press("rv:1:go", from_id=1001, chat_id=REVIEWERS_CHAT)
    world.press("rv:1:go", from_id=2002, chat_id=REVIEWERS_CHAT)
    assert len(world.courier.decisions) == 1
    assert world.bot.answers[-1] == ("cb1", texts.ALREADY_DECIDED)


def test_leave_at_origin_closes_the_case(world: World) -> None:
    _open_review(world)
    world.press("rv:1:orig", from_id=2002, chat_id=REVIEWERS_CHAT)
    assert world.courier.origins == [(1, MARIA)] and world.courier.cases[1].estado == "origem"
    assert "ficou na origem" in world.bot.edits[-1][2]


def test_case_without_department_goes_to_every_reviewer(world: World) -> None:
    _open_review(world, department=None, motivo="sem_evidencia_de_dono")
    assert "Revisores" not in world.bot.sent[-1][1]
    assert [b.data for row in world.bot.sent[-1][2] for b in row] == [
        "rv:1:own",
        "rv:1:dep",
        "rv:1:orig",
    ]
    world.press("rv:1:go", from_id=3003, chat_id=REVIEWERS_CHAT)
    assert world.bot.answers[-1] == ("cb1", "Escolha o dono e o departamento antes.")


def test_refused_archiving_keeps_the_case_open_with_the_reason(world: World) -> None:
    _open_review(world)
    world.courier.decide_error = ArchiveRefused("nome de arquivo excede 255 unidades UTF-16",
                                                destino_dir="Alfa/01 Fiscal/2026")  # fmt: skip
    world.press("rv:1:go", from_id=1001, chat_id=REVIEWERS_CHAT)
    assert world.courier.cases[1].estado == "aberto"
    assert "O servidor recusou" in world.bot.edits[-1][2] and world.bot.edits[-1][3]


def test_review_without_a_group_goes_to_the_operator_chat(world: World) -> None:
    world.ctx = BotContext(world.courier, world.bot, world.ctx.state, None, True)
    _open_review(world) if False else None
    world.courier.next_outcome = reviewed()
    world.document()
    assert [m[0] for m in world.bot.sent] == [4004, 4004]
    assert world.bot.sent[-1][2]  # buttons still there


def test_draft_is_rebuilt_after_a_restart(world: World) -> None:
    _open_review(world)
    world.ctx.state.drafts.clear()
    world.press("rv:1:m", from_id=1001, chat_id=REVIEWERS_CHAT)
    assert "Dono: —" in world.bot.edits[-1][2] and "Departamento: Fiscal" in world.bot.edits[-1][2]


# --- commands and reminder -----------------------------------------------------------


def test_status_is_for_the_admin_only(world: World) -> None:
    world.command("status", from_id=4004)
    assert world.bot.texts_to(4004) == [texts.ONLY_ADMIN]
    world.command("status")
    text = world.bot.texts_to(1001)[-1]
    assert "3 arquivados" in text and "1 em revisão" in text and "modelo-ficticio:1b" in text
    assert "desligados" in text and "/srv/raiz: 10.0 GB livres" in text


def test_undo_command(world: World) -> None:
    world.command("desfazer", "5")
    assert world.courier.undos == [(5, ADMIN)]
    assert "Desfeito: documento 1" in world.bot.texts_to(1001)[-1]
    world.command("desfazer", "404")
    assert "Não desfeito" in world.bot.texts_to(1001)[-1]
    world.command("desfazer", "x")
    assert "Use: /desfazer" in world.bot.texts_to(1001)[-1]
    world.command("desfazer", "5", from_id=2002)
    assert world.bot.texts_to(2002) == [texts.ONLY_ADMIN]


def test_pending_lists_only_the_reviewer_cases(world: World) -> None:
    world.courier.cases = {
        1: CaseInfo(1, 1, "aberto", "texto_insuficiente", 10, datetime(2026, 10, 1)),
        2: CaseInfo(2, 2, "aberto", "sem_evidencia_de_dono", None, None),
        3: CaseInfo(3, 3, "aberto", "ano_ausente", 20, None),
        4: CaseInfo(4, 4, "decidido", "ano_ausente", 10, None),
    }
    for i in (1, 2, 3, 4):
        world.courier.documents[i] = DocumentInfo(
            i, f"{i}.pdf", "EM_REVISAO", "recibo", None, None, None, None, None, 4
        )
    world.command("pendentes", from_id=2002)
    text = world.bot.texts_to(2002)[-1]
    assert "Revisões pendentes (2)" in text and "caso 1" in text and "caso 2" in text
    assert "caso 3" not in text and "caso 4" not in text
    world.command("pendentes", from_id=1001)
    assert "Revisões pendentes (3)" in world.bot.texts_to(1001)[-1]
    world.command("pendentes", from_id=4004)
    assert world.bot.texts_to(4004) == [texts.ONLY_REVIEWER]


def test_daily_reminder_lists_only_open_cases(world: World) -> None:
    assert jobs.daily_reminder(world.ctx) is False and world.bot.sent == []
    world.courier.cases = {
        1: CaseInfo(1, 1, "aberto", "texto_insuficiente", 10, None),
        2: CaseInfo(2, 2, "origem", "ano_ausente", 10, None),
    }
    world.courier.documents[1] = DocumentInfo(
        1, "a.pdf", "EM_REVISAO", "recibo", None, None, None, None, None, 4
    )
    assert jobs.daily_reminder(world.ctx) is True
    chat, text, _ = world.bot.sent[-1]
    assert chat == REVIEWERS_CHAT and "caso 1" in text and "caso 2" not in text


def test_reminder_without_a_group_sends_nothing(world: World) -> None:
    world.ctx = BotContext(world.courier, world.bot, world.ctx.state, None, True)
    world.courier.cases = {1: CaseInfo(1, 1, "aberto", "x", None, None)}
    assert jobs.daily_reminder(world.ctx) is False and world.bot.sent == []


def test_unknown_callback_prefix_is_just_answered(world: World) -> None:
    world.press("zz:1")
    assert world.bot.answers == [("cb1", None)]


def test_review_handler_owner_pages(world: World) -> None:
    world.courier.owners_by_id = {
        i: OwnerInfo(i, f"Dono {i:02d}", f"Dono {i:02d}") for i in range(1, 12)
    }
    _open_review(world)
    world.press("rv:1:own", from_id=1001, chat_id=REVIEWERS_CHAT)
    data = [b.data for row in world.bot.edits[-1][3] for b in row]
    assert data[:8] == [f"rv:1:o:{i}" for i in range(1, 9)] and "rv:1:op:1" in data
    world.press("rv:1:op:1", from_id=1001, chat_id=REVIEWERS_CHAT)
    data = [b.data for row in world.bot.edits[-1][3] for b in row]
    assert data[:3] == ["rv:1:o:9", "rv:1:o:10", "rv:1:o:11"] and "rv:1:op:0" in data


def test_start_greets_known_users_only(world: World) -> None:
    world.command("start", from_id=4004)
    assert world.bot.texts_to(4004)[-1].startswith("Olá, Ana Ficticia")
    world.command("start", from_id=9999)
    assert world.bot.texts_to(9999) == []


def test_owner_and_review_modules_expose_their_prefixes() -> None:
    assert (upload.PREFIX, owners.PREFIX, review.PREFIX) == ("up:", "ow:", "rv:")
