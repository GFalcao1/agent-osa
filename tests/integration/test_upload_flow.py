"""Entrega 1 pelo bot: upload, dono novo e revisão contra PostgreSQL e uma raiz em ``tmp_path``.

O transporte do Telegram é falso; extração e modelo são falsos; cadastro, índice, auditoria,
quarentena, rename e os handlers do bot são reais (``RealCourier``). Pula quando o volume de
teste não comprova rename exclusivo.
"""

from __future__ import annotations

import os
from collections.abc import Iterator, Sequence
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.engine import URL
from sqlalchemy.orm import Session, sessionmaker

from osa.admin import services
from osa.core.settings import Settings
from osa.courier.ports import LocalContent
from osa.db.models import ArchiveOperation, AuditEvent, Document, Owner, ReviewCase
from osa.extract.model import DEFAULT_SENSITIVE_EXTENSIONS, ExtractedContent, ExtractLimits
from osa.fs import noreplace
from osa.fs.errors import NoReplaceUnavailable
from osa.fs.names import QUARANTINE_DIR
from osa.llm.envelopes import ClassificationEnvelope
from osa.telegram import texts
from osa.telegram.app import dispatch_callback
from osa.telegram.courier import RealCourier
from osa.telegram.handlers import upload
from osa.telegram.ports import (
    BotContext,
    BotState,
    Button,
    Callback,
    IncomingDocument,
    Keyboard,
)

PDF = b"%PDF-1.4\n% documento ficticio do bot\n%%EOF\n"
REVIEWERS_CHAT = -100123
ADMIN_TG, MARIA_TG, ANA_TG = 1001, 2002, 4004


def make_cnpj(base12: str) -> str:
    def digit(digits: str, weights: list[int]) -> str:
        rest = sum(int(d) * w for d, w in zip(digits, weights, strict=True)) % 11
        return "0" if rest < 2 else str(11 - rest)

    first = digit(base12, [5, 4, 3, 2, 9, 8, 7, 6, 5, 4, 3, 2])
    second = digit(base12 + first, [6, 5, 4, 3, 2, 9, 8, 7, 6, 5, 4, 3, 2])
    return base12 + first + second


CNPJ_ALFA = make_cnpj("112223330001")
TEXT = f"NOTA FISCAL DE SERVIÇO\nTomador: Alfa Comércio Ltda, CNPJ {CNPJ_ALFA}\nValor: R$ 10,00"


def mounted(_path: Path) -> bool:
    return True


class FakeContent(LocalContent):
    def __init__(self) -> None:
        super().__init__(ExtractLimits(sensitive_extensions=DEFAULT_SENSITIVE_EXTENSIONS))
        self.text = TEXT

    def extract(self, path: Path) -> ExtractedContent:
        return ExtractedContent(
            text=self.text, images=(), pages_total=1, truncated=False, method="pdf_text"
        )


class FakeLlm:
    def __init__(self) -> None:
        self.envelope = envelope()

    def classify(self, content: ExtractedContent, departments: Sequence[Any]) -> Any:
        return self.envelope


def envelope(*, revisao: bool = False, motivo: str | None = None) -> ClassificationEnvelope:
    return ClassificationEnvelope(
        departamento="Fiscal",
        departamento_alternativo=None,
        tipo_documental="nota fiscal de serviço",
        tags=["nota"],
        resumo="Nota fiscal fictícia.",
        datas=[date(2026, 1, 31)],
        ano_referencia=2026,
        precisa_revisao=revisao,
        motivo_revisao=motivo,
    )


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
        return self.files[file_id]

    def texts_to(self, chat_id: int) -> list[str]:
        return [text for chat, text, _ in self.sent if chat == chat_id]

    def last_buttons(self, chat_id: int) -> list[str]:
        rows = [rows for chat, _, rows in self.sent if chat == chat_id][-1]
        return [b.data for row in rows for b in row]


@dataclass
class Bot:
    engine: Any
    sessions: sessionmaker[Session]
    root: Path
    bot: FakeBot
    llm: FakeLlm
    content: FakeContent
    ctx: BotContext
    ids: dict[str, int]

    def upload(
        self, *, caption: str | None = "Alfa", name: str = "nota.pdf", from_id: int = ANA_TG
    ) -> None:
        upload.handle_document(
            self.ctx, IncomingDocument(from_id, from_id, 1, "f1", name, len(PDF), caption)
        )

    def press(self, data: str, *, from_id: int, chat_id: int, message_id: int = 101) -> None:
        dispatch_callback(self.ctx, Callback("cb", chat_id, from_id, message_id, data))

    def events(self, acao: str) -> list[AuditEvent]:
        with self.sessions() as session:
            return list(
                session.scalars(
                    select(AuditEvent).where(AuditEvent.acao == acao).order_by(AuditEvent.id)
                )
            )

    def documents(self) -> list[Document]:
        with self.sessions() as session:
            return list(session.scalars(select(Document).order_by(Document.id)))

    def operations(self) -> list[ArchiveOperation]:
        with self.sessions() as session:
            return list(session.scalars(select(ArchiveOperation).order_by(ArchiveOperation.id)))

    def cases(self) -> list[ReviewCase]:
        with self.sessions() as session:
            return list(session.scalars(select(ReviewCase).order_by(ReviewCase.id)))


def _require_noreplace(root: Path) -> None:
    fd = os.open(root, os.O_RDONLY | os.O_DIRECTORY)
    try:
        noreplace.proven_primitive(fd)
    except NoReplaceUnavailable as error:
        pytest.skip(f"volume de teste sem rename exclusivo comprovado: {error}")
    finally:
        os.close(fd)


def _build(
    url: URL, tmp_path: Path, *, effects: bool, quarantine_dir: Path | None = None
) -> Iterator[Bot]:
    root = tmp_path.resolve() / "raiz"
    root.mkdir()
    _require_noreplace(root)
    engine = create_engine(url)
    sessions = sessionmaker(engine, expire_on_commit=False)
    ids: dict[str, int] = {}
    with sessions() as session:
        admin = services.bootstrap_admin(
            session, telegram_id=ADMIN_TG, nome="Admin Ficticio", actor="test:bootstrap"
        )
        registered = services.create_root(
            session, caminho=str(root), is_mount_point=mounted, actor="test:setup"
        )
        fiscal = services.create_department(
            session, nome="Fiscal", pasta="01 Fiscal", usa_ano=True,
            descricao="Notas fiscais e guias", actor="test:setup",
        )  # fmt: skip
        pessoal = services.create_department(
            session, nome="Pessoal", pasta="02 Pessoal", actor="test:setup"
        )
        alfa = services.create_owner(
            session, nome_fantasia="Alfa Comércio", cnpj=CNPJ_ALFA, aliases=["Alfa"],
            actor="test:setup",
        )  # fmt: skip
        maria = services.create_user(
            session, telegram_id=MARIA_TG, nome="Maria Ficticia", papel="reviewer",
            departamento_id=fiscal.id, actor="test:setup",
        )  # fmt: skip
        ana = services.create_user(
            session, telegram_id=ANA_TG, nome="Ana Ficticia", papel="operator",
            departamento_id=fiscal.id, actor="test:setup",
        )  # fmt: skip
        session.commit()
        ids.update(
            admin=admin.id, root=registered.id, fiscal=fiscal.id, pessoal=pessoal.id,
            alfa=alfa.id, maria=maria.id, ana=ana.id,
        )  # fmt: skip
    settings = Settings(enable_file_effects=effects, quarantine_dir=quarantine_dir)
    llm = FakeLlm()
    content = FakeContent()
    courier = RealCourier(sessions, settings, content=content, llm=llm)
    bot = FakeBot()
    ctx = BotContext(courier, bot, BotState(), REVIEWERS_CHAT, effects)
    try:
        yield Bot(engine, sessions, root, bot, llm, content, ctx, ids)
    finally:
        engine.dispose()


@pytest.fixture
def live(migrated_database: URL, tmp_path: Path) -> Iterator[Bot]:
    yield from _build(migrated_database, tmp_path, effects=True)


@pytest.fixture
def simulated(migrated_database: URL, tmp_path: Path) -> Iterator[Bot]:
    holding = tmp_path.resolve() / "espera"
    holding.mkdir()
    yield from _build(migrated_database, tmp_path, effects=False, quarantine_dir=holding)


DEST = "Alfa Comércio/01 Fiscal/2026/nota.pdf"


def test_upload_with_declared_owner_is_archived_by_the_courier(live: Bot) -> None:
    live.upload(caption="alfa")

    [reply] = live.bot.texts_to(ANA_TG)
    assert reply.startswith(f"Arquivado em {DEST}.")
    assert (live.root / DEST).read_bytes() == PDF
    assert not any((live.root / QUARANTINE_DIR).iterdir())
    [document] = live.documents()
    assert (document.estado, document.caminho, document.nome) == ("ARQUIVADO", DEST, "nota.pdf")
    assert (document.owner_id, document.enviado_por) == (live.ids["alfa"], live.ids["ana"])
    [operation] = live.operations()
    assert (operation.modo, operation.ator) == ("auto", f"telegram:{ANA_TG}")
    assert {e.ator for e in live.events("document.archive")} == {f"telegram:{ANA_TG}"}


def test_unknown_telegram_id_is_ignored_and_audited(live: Bot) -> None:
    live.upload(from_id=9999)
    assert live.bot.sent == [] and live.bot.downloads == []
    [event] = live.events("telegram.unknown")
    assert (event.ator, event.resultado) == ("telegram:9999", "denied")


def test_sensitive_upload_is_refused_without_download(live: Bot) -> None:
    live.upload(name="chave.pem")
    assert live.bot.downloads == [] and live.documents() == []
    assert live.bot.texts_to(ANA_TG) == [texts.SENSITIVE_REFUSED]
    [event] = live.events("telegram.upload")
    assert (event.ator, event.resultado) == (f"telegram:{ANA_TG}", "denied")


def test_new_owner_confirmed_by_the_admin_gets_the_tree_and_the_upload(live: Bot) -> None:
    live.content.text = "RECIBO DE SERVIÇO\nSem identificação do tomador.\nValor: R$ 10,00"
    live.upload(caption="Gama Indústria")
    assert live.bot.texts_to(ANA_TG) == [texts.NO_OWNER_MATCH]
    assert live.bot.last_buttons(ANA_TG) == ["up:new", "up:x"]

    live.press("up:new", from_id=ANA_TG, chat_id=ANA_TG)
    token = live.bot.last_buttons(ADMIN_TG)[0].split(":")[1]
    assert live.documents() == []

    live.press(f"ow:{token}:y", from_id=ADMIN_TG, chat_id=ADMIN_TG)

    with live.sessions() as session:
        gama = session.scalar(select(Owner).where(Owner.nome_fantasia == "Gama Indústria"))
        assert gama is not None
    assert (live.root / "Gama Indústria" / "01 Fiscal").is_dir()
    assert (live.root / "Gama Indústria" / "02 Pessoal").is_dir()
    assert (live.root / "Gama Indústria" / "01 Fiscal" / "2026" / "nota.pdf").read_bytes() == PDF
    assert live.bot.texts_to(ANA_TG)[-1].startswith("Arquivado em Gama Indústria/01 Fiscal/2026/")
    [confirm] = live.events("owner.request.confirm")
    assert (confirm.ator, confirm.detalhes["solicitante"]) == (
        f"telegram:{ADMIN_TG}",
        live.ids["ana"],
    )
    assert live.events("owner.tree")[0].detalhes["pastas_criadas"] == 3


def test_admin_refusal_creates_nothing(live: Bot) -> None:
    live.upload(caption="Delta")
    live.press("up:new", from_id=ANA_TG, chat_id=ANA_TG)
    token = live.bot.last_buttons(ADMIN_TG)[0].split(":")[1]

    live.press(f"ow:{token}:n", from_id=ADMIN_TG, chat_id=ADMIN_TG)

    with live.sessions() as session:
        assert [o.nome_fantasia for o in session.scalars(select(Owner))] == ["Alfa Comércio"]
    assert not (live.root / "Delta").exists() and live.documents() == []
    assert live.events("owner.request.refuse")[0].resultado == "denied"
    assert texts.OWNER_REQUEST_REFUSED.format(nome="Delta") in live.bot.texts_to(ANA_TG)


def test_review_decided_by_the_reviewer_archives_with_mode_review(live: Bot) -> None:
    live.llm.envelope = envelope(revisao=True, motivo="texto_insuficiente")
    live.upload(caption="alfa")
    assert "Foi para revisão (caso 1)" in live.bot.texts_to(ANA_TG)[-1]
    [case] = live.cases()
    assert (case.estado, case.departamento_sugerido_id) == ("aberto", live.ids["fiscal"])
    posted = [m for m in live.bot.sent if m[0] == REVIEWERS_CHAT]
    assert len(posted) == 1 and "Maria Ficticia" in posted[0][1]

    # Ana is an operator: her button press is ignored; Maria reviews Fiscal cases.
    live.press("rv:1:go", from_id=ANA_TG, chat_id=REVIEWERS_CHAT)
    assert live.cases()[0].estado == "aberto"
    live.press("rv:1:dep", from_id=MARIA_TG, chat_id=REVIEWERS_CHAT)
    live.press(f"rv:1:d:{live.ids['pessoal']}", from_id=MARIA_TG, chat_id=REVIEWERS_CHAT)
    live.press("rv:1:go", from_id=MARIA_TG, chat_id=REVIEWERS_CHAT)

    [case] = live.cases()
    assert (case.estado, case.decidido_por) == ("decidido", live.ids["maria"])
    [document] = live.documents()
    assert (document.estado, document.caminho) == ("ARQUIVADO", "Alfa Comércio/02 Pessoal/nota.pdf")
    assert (live.root / "Alfa Comércio" / "02 Pessoal" / "nota.pdf").read_bytes() == PDF
    [operation] = live.operations()
    assert (operation.modo, operation.ator) == ("review", f"telegram:{MARIA_TG}")
    assert "Decidido por Maria Ficticia" in live.bot.edits[-1][2]
    decided = live.events("review.decide")[-1]
    assert (decided.resultado, decided.detalhes["decidido_por"]) == ("ok", live.ids["maria"])

    live.press("rv:1:orig", from_id=ADMIN_TG, chat_id=REVIEWERS_CHAT)
    assert live.bot.answers[-1] == ("cb", texts.ALREADY_DECIDED)


def test_effects_off_simulates_from_a_holding_copy_outside_the_root(simulated: Bot) -> None:
    simulated.upload(caption="alfa")

    [reply] = simulated.bot.texts_to(ANA_TG)
    assert reply.startswith("Simulado (efeitos de arquivo desligados): iria para " + DEST)
    assert not (simulated.root / "Alfa Comércio").exists()
    assert not (simulated.root / QUARANTINE_DIR).exists()
    holding = simulated.ctx.courier.settings.quarantine_dir  # type: ignore[attr-defined]
    assert holding is not None and list(holding.iterdir()) == []
    [document] = simulated.documents()
    assert (document.estado, document.caminho) == ("CLASSIFICADO", None)
    assert simulated.operations() == []
