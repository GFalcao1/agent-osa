"""Consulta e envio pelo bot com o correio real (TASK-110), contra o PostgreSQL descartável.

O acervo de teste é indexado pelo scan da TASK-109 (extração e classificação falsas); o
pedido passa por um interpretador falso que registra exatamente o que recebe, para provar que
só o texto do pedido vai ao modelo. Índice, busca, decisão de entrega, leitura conferida pelo
hash e auditoria são reais. O transporte do Telegram é falso.
"""

from __future__ import annotations

import dataclasses
import os
from collections.abc import Iterator, Sequence
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.engine import URL
from sqlalchemy.orm import Session, sessionmaker

from osa.admin import services
from osa.core.settings import Settings
from osa.courier.ports import LocalContent, LocalFs
from osa.courier.store import SqlStore
from osa.db.models import AuditEvent, Document
from osa.extract.model import DEFAULT_SENSITIVE_EXTENSIONS, ExtractedContent, ExtractLimits
from osa.llm.envelopes import ClassificationEnvelope, IntentEnvelope
from osa.search import intent
from osa.telegram import courier as courier_module
from osa.telegram import texts
from osa.telegram.app import dispatch_callback
from osa.telegram.courier import RealCourier
from osa.telegram.handlers import query
from osa.telegram.ports import BotContext, BotState, Button, Callback, IncomingText, Keyboard
from osa.worker.scan import scan_root

ADMIN_TG, ANA_TG = 1001, 4004
UNC = "\\\\srv\\acervo"
MB = 1024 * 1024


def mounted(_path: Path) -> bool:
    return True


def pdf(label: str, size: int = 0) -> bytes:
    body = f"%PDF-1.4\n% {label}\n".encode()
    return body + b"0" * max(0, size - len(body) - 6) + b"%%EOF\n"


def put(path: Path, data: bytes) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    return path


class ScanContent(LocalContent):
    def __init__(self) -> None:
        super().__init__(ExtractLimits(sensitive_extensions=DEFAULT_SENSITIVE_EXTENSIONS))

    def extract(self, path: Path) -> ExtractedContent:
        return ExtractedContent(
            text=f"Documento fictício {path.stem.replace('-', ' ')}",
            images=(), pages_total=1, truncated=False, method="pdf_text",
        )  # fmt: skip


class ScanLlm:
    """Classification for the scan: type from the file name, 2025 dates for "2025" names."""

    def classify(self, content: ExtractedContent, departments: Sequence[Any]) -> Any:
        text = content.text.lower()
        tipo = "contrato" if "contrato" in text else "boleto" if "boleto" in text else "documento"
        dated = [date(2025, 6, 1)] if "2025" in text else [date(2024, 3, 1)]
        return ClassificationEnvelope(
            departamento="Contratos", departamento_alternativo=None, tipo_documental=tipo,
            tags=[], resumo=content.text, datas=dated, ano_referencia=dated[0].year,
            precisa_revisao=False, motivo_revisao=None,
        )  # fmt: skip


@dataclass
class Interpreter:
    """The model as the query flow sees it: records every call; answers what the test sets."""

    reply: IntentEnvelope | None = None
    calls: list[tuple[str, tuple[str, ...]]] = field(default_factory=list)

    def interpret(self, text: str, departments: Sequence[Any]) -> IntentEnvelope:
        self.calls.append((text, tuple(d.nome for d in departments)))
        assert self.reply is not None
        return self.reply


class FakeBot:
    def __init__(self) -> None:
        self.sent: list[tuple[int, str, list[list[Button]]]] = []
        self.documents: list[tuple[int, bytes, str]] = []
        self.answers: list[tuple[str, str | None]] = []

    with_buttons = 0  # id of the last message sent with buttons

    def send(self, chat_id: int, text: str, buttons: Keyboard = ()) -> int:
        self.sent.append((chat_id, text, [list(row) for row in buttons]))
        if buttons:
            self.with_buttons = 100 + len(self.sent)
        return 100 + len(self.sent)

    def edit(self, chat_id: int, message_id: int, text: str, buttons: Keyboard = ()) -> None:
        pass

    def answer(self, callback_id: str, text: str | None = None) -> None:
        self.answers.append((callback_id, text))

    def download(self, file_id: str) -> bytes:
        raise AssertionError("a consulta nunca baixa nada")

    def send_document(
        self, chat_id: int, data: bytes, filename: str, caption: str | None = None
    ) -> int:
        self.documents.append((chat_id, data, filename))
        return 900

    def last(self) -> str:
        return self.sent[-1][1]

    def buttons(self) -> list[str]:
        return [b.data for row in self.sent[-1][2] for b in row]


def ask(
    acao: str = "buscar", *, dono: str | None = None, departamento: str | None = None,
    tipo: str | None = None, de: date | None = None, ate: date | None = None,
    palavras: Sequence[str] = (),
) -> IntentEnvelope:  # fmt: skip
    return IntentEnvelope(
        acao=acao, dono=dono, departamento=departamento, tipo_documental=tipo,  # type: ignore[arg-type]
        periodo_inicio=de, periodo_fim=ate, palavras_chave=list(palavras),
    )  # fmt: skip


@dataclass
class Flow:
    sessions: sessionmaker[Session]
    root: Path
    bot: FakeBot
    interpreter: Interpreter
    ctx: BotContext
    files: dict[str, bytes]

    def say(self, text: str, reply: IntentEnvelope, *, from_id: int = ANA_TG) -> None:
        self.interpreter.reply = reply
        query.handle_text(self.ctx, IncomingText(from_id, from_id, 1, text))

    def press(self, data: str, *, from_id: int = ANA_TG) -> None:
        """Press a button of the last message that had buttons (where the offer lives)."""
        dispatch_callback(self.ctx, Callback("cb", from_id, from_id, self.bot.with_buttons, data))

    def doc_id(self, name: str) -> int:
        with self.sessions() as session:
            return session.scalars(select(Document.id).where(Document.nome == name)).one()

    def events(self, acao: str, *, fase: str | None = "resultado") -> list[AuditEvent]:
        """Events of ``acao``; for ``document.send``, only the given ``fase`` (None: all)."""
        with self.sessions() as session:
            found = list(
                session.scalars(
                    select(AuditEvent).where(AuditEvent.acao == acao).order_by(AuditEvent.id)
                )
            )
        if acao != "document.send" or fase is None:
            return found
        return [e for e in found if e.detalhes.get("fase") == fase]


@pytest.fixture
def flow(migrated_database: URL, tmp_path: Path) -> Iterator[Flow]:
    root = tmp_path.resolve() / "raiz"
    root.mkdir()
    files = {
        "Alfa Comércio/03 Contratos/contrato-consultoria-2025.pdf": pdf("contrato alfa 2025"),
        "Alfa Comércio/03 Contratos/contrato-locacao-2024.pdf": pdf("contrato alfa 2024"),
        "Alfa Comércio/03 Contratos/contrato-anexo-grande-2025.pdf": pdf("grande", 2 * MB),
        "Alfa Comércio/04 Jurídico/certificado.pfx": b"0\x82segredo-ficticio",
        **{
            f"Beta Serviços/01 Fiscal/2025/boleto-{n}-2025.pdf": pdf(f"boleto beta {n}")
            for n in range(1, 8)
        },
        "Beta Serviços/03 Contratos/contrato-a-2025.pdf": pdf("contrato beta a"),
        "Beta Serviços/03 Contratos/contrato-b-2025.pdf": pdf("contrato beta b"),
        "Beta Serviços/03 Contratos/contrato-c-2025.pdf": pdf("contrato beta c"),
    }
    for rel, data in files.items():
        put(root / rel, data)
    engine = create_engine(migrated_database)
    sessions = sessionmaker(engine, expire_on_commit=False)
    with sessions() as session:
        services.bootstrap_admin(session, telegram_id=ADMIN_TG, nome="Admin", actor="test:boot")
        registered = services.create_root(
            session, caminho=str(root), unc=UNC, is_mount_point=mounted, actor="test:setup"
        )
        fiscal = services.create_department(
            session, nome="Fiscal", pasta="01 Fiscal", usa_ano=True, actor="test:setup"
        )
        for nome, pasta in (("Contratos", "03 Contratos"), ("Jurídico", "04 Jurídico")):
            services.create_department(session, nome=nome, pasta=pasta, actor="test:setup")
        services.create_owner(
            session, nome_fantasia="Alfa Comércio", aliases=["Alfa"], actor="test:setup"
        )
        services.create_owner(
            session, nome_fantasia="Beta Serviços", aliases=["Beta"], actor="test:setup"
        )
        services.create_user(
            session, telegram_id=ANA_TG, nome="Ana Ficticia", papel="operator",
            departamento_id=fiscal.id, actor="test:setup",
        )  # fmt: skip
        session.commit()
        summary = scan_root(
            registered.id, store=SqlStore(session), fs=LocalFs(), content=ScanContent(),
            llm=ScanLlm(), actor="test:scan",
        )  # fmt: skip
        assert summary.concluido and summary.novos == len(files)
    settings = Settings(telegram_send_limit_mb=1)
    interpreter = Interpreter()
    courier = RealCourier(sessions, settings, query_llm=interpreter)
    bot = FakeBot()
    ctx = BotContext(courier, bot, BotState(), None, False)
    try:
        yield Flow(sessions, root, bot, interpreter, ctx, files)
    finally:
        engine.dispose()


CONTRATO_ALFA_2025 = ask(dono="Alfa", tipo="contrato", de=date(2025, 1, 1), ate=date(2025, 12, 31))


# --- o pedido da task, ponta a ponta ------------------------------------------------------------


def test_contract_of_alfa_2025_is_found_offered_and_sent_with_audit(flow: Flow) -> None:
    flow.say("manda o contrato da Alfa de 2025", CONTRATO_ALFA_2025)

    # contrato de 2025 da Alfa: o de consultoria e o anexo grande (o de 2024 fica fora)
    assert "Encontrei 2 documentos" in flow.bot.last()
    consultoria = flow.doc_id("contrato-consultoria-2025.pdf")
    assert f"q:s:{consultoria}" in flow.bot.buttons()

    flow.press(f"q:s:{consultoria}")

    rel = "Alfa Comércio/03 Contratos/contrato-consultoria-2025.pdf"
    assert flow.bot.documents == [(ANA_TG, flow.files[rel], "contrato-consultoria-2025.pdf")]
    attempt, sent = flow.events("document.send", fase=None)
    assert (attempt.resultado, attempt.detalhes["fase"]) == ("failed", "tentativa")
    assert attempt.id < sent.id  # the attempt is on record before the bytes leave
    assert (sent.ator, sent.resultado, sent.alvo_id) == (
        f"telegram:{ANA_TG}",
        "ok",
        str(consultoria),
    )
    assert sent.sha256 is not None and sent.detalhes["caminho"] == rel
    assert sent.detalhes["entrega"] == "arquivo"
    consulta = sent.detalhes["consulta"]
    assert consulta["tipo_documental"] == "contrato" and consulta["dono_id"] is not None
    assert (consulta["periodo_inicio"], consulta["periodo_fim"]) == ("2025-01-01", "2025-12-31")
    [searched] = flow.events("query.search")
    assert searched.detalhes["resultados"] == 2


def test_the_model_receives_only_the_text_of_the_request(flow: Flow) -> None:
    flow.say("manda o contrato da Alfa de 2025", CONTRATO_ALFA_2025)
    flow.press(f"q:s:{flow.doc_id('contrato-consultoria-2025.pdf')}")
    flow.say("boletos da Beta", ask(dono="Beta", tipo="boleto"))

    assert [text for text, _ in flow.interpreter.calls] == [
        "manda o contrato da Alfa de 2025",
        "boletos da Beta",
    ]
    # nada do acervo (nomes, caminhos, conteúdo) chega ao modelo; só o template, como dado
    for _, departments in flow.interpreter.calls:
        assert departments == ("Fiscal", "Contratos", "Jurídico")


@pytest.mark.parametrize(
    ("request_text", "envelope", "expected"),
    [
        ("contrato da Alfa de 2023", ask(dono="Alfa", tipo="contrato", de=date(2023, 1, 1),
                                        ate=date(2023, 12, 31)), 0),
        ("contrato de consultoria da Alfa", ask(dono="Alfa", palavras=["consultoria"]), 1),
        ("contratos da Beta", ask(dono="Beta", tipo="contrato"), 3),
        ("boletos da Beta", ask(dono="Beta", tipo="boleto"), 7),
    ],
)  # fmt: skip
def test_zero_one_three_and_seven_results(
    flow: Flow, request_text: str, envelope: IntentEnvelope, expected: int
) -> None:
    flow.say(request_text, envelope)

    reply, buttons = flow.bot.last(), flow.bot.buttons()
    if expected == 0:
        assert reply.startswith("Não encontrei") and buttons == []
    elif expected == 1:
        assert "Encontrei 1 documento" in reply and buttons[-1] == "q:x" and len(buttons) == 2
    elif expected == 3:
        assert "Encontrei 3 documentos" in reply and len(buttons) == 4
    else:
        assert "mais de 5" in reply and buttons == []
    assert "Entendi:" in reply


def test_unknown_owner_is_named_and_nothing_is_guessed(flow: Flow) -> None:
    flow.say("contratos da Gama", ask(dono="Gama", tipo="contrato"))

    assert "Não encontrei" in flow.bot.last() and "«Gama» (não cadastrado)" in flow.bot.last()


# --- limites de tamanho e classe ----------------------------------------------------------------


def test_file_above_the_limit_answers_the_unc_path(flow: Flow) -> None:
    flow.say("anexo grande", ask(dono="Alfa", palavras=["grande"]))
    big = flow.doc_id("contrato-anexo-grande-2025.pdf")

    flow.press(f"q:s:{big}")

    assert flow.bot.documents == []
    expected = UNC + "\\Alfa Comércio\\03 Contratos\\contrato-anexo-grande-2025.pdf"
    assert expected in flow.bot.last() and intent.MOTIVO_GRANDE in flow.bot.last()
    [event] = flow.events("document.send")
    assert (event.resultado, event.detalhes["entrega"]) == ("ok", "caminho")


@pytest.fixture
def never_read(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    """Fails the send if the courier reads any file's bytes; records what it tried."""
    reads: list[str] = []

    def refuse(root: Path, relative: str, *, max_bytes: int) -> tuple[bytes, str]:
        reads.append(relative)
        raise AssertionError(f"leu o arquivo {relative}")

    monkeypatch.setattr(courier_module, "read_within", refuse)
    return reads


def test_sensitive_goes_as_unc_only_to_the_administrator_and_is_refused_to_an_operator(
    flow: Flow, never_read: list[str]
) -> None:
    pfx = flow.doc_id("certificado.pfx")
    certificado = ask(dono="Alfa", departamento="Jurídico")

    flow.say("certificado da Alfa", certificado)
    flow.press(f"q:s:{pfx}")

    assert flow.bot.documents == []
    assert flow.bot.last() == texts.SEND_REFUSED.format(motivo=intent.MOTIVO_SENSIVEL)

    flow.say("certificado da Alfa", certificado, from_id=ADMIN_TG)
    flow.press(f"q:s:{pfx}", from_id=ADMIN_TG)

    assert flow.bot.documents == []
    assert UNC + "\\Alfa Comércio\\04 Jurídico\\certificado.pfx" in flow.bot.last()
    refused, shown = flow.events("document.send")
    assert (refused.ator, refused.resultado) == (f"telegram:{ANA_TG}", "denied")
    assert (shown.ator, shown.resultado, shown.detalhes["entrega"]) == (
        f"telegram:{ADMIN_TG}", "ok", "caminho"
    )  # fmt: skip
    assert all("segredo" not in str(e.detalhes) for e in flow.events("document.send"))
    assert never_read == []


def test_a_sensitive_file_renamed_on_the_share_keeps_its_protection(
    flow: Flow, never_read: list[str]
) -> None:
    jur = flow.root / "Alfa Comércio" / "04 Jurídico"
    os.rename(jur / "certificado.pfx", jur / "certificado.pdf")  # alguém renomeou no share
    with flow.sessions() as session:
        root_id = session.scalars(select(Document.root_id)).first()
        assert root_id is not None
        summary = scan_root(
            root_id, store=SqlStore(session), fs=LocalFs(), content=ScanContent(),
            llm=ScanLlm(), actor="test:scan",
        )  # fmt: skip
        assert summary.movidos == 1
    pfx = flow.doc_id("certificado.pdf")

    flow.say("certificado da Alfa", ask(dono="Alfa", departamento="Jurídico"))
    assert "[sensível] certificado.pdf" in flow.bot.last()  # one hit: the label is in the text
    flow.press(f"q:s:{pfx}")

    assert flow.bot.documents == [] and never_read == []
    assert flow.bot.last() == texts.SEND_REFUSED.format(motivo=intent.MOTIVO_SENSIVEL)
    [refused] = flow.events("document.send")
    assert refused.resultado == "denied"


def test_an_operator_refused_a_sensitive_file_learns_nothing_about_the_disk(
    flow: Flow, never_read: list[str]
) -> None:
    (flow.root / "Alfa Comércio" / "04 Jurídico" / "certificado.pfx").unlink()

    flow.say("certificado da Alfa", ask(dono="Alfa", departamento="Jurídico"))
    flow.press(f"q:s:{flow.doc_id('certificado.pfx')}")

    assert flow.bot.last() == texts.SEND_REFUSED.format(motivo=intent.MOTIVO_SENSIVEL)
    [refused] = flow.events("document.send")
    assert refused.resultado == "denied"  # never "failed: o arquivo não está mais..."


def test_external_provider_switched_on_refuses_the_query_with_a_configuration_message(
    flow: Flow, monkeypatch: pytest.MonkeyPatch
) -> None:
    real = services.effective_model_config

    def external_on(session: Session, settings: Settings) -> Any:
        return dataclasses.replace(real(session, settings), provedor_externo_ativo=True)

    monkeypatch.setattr(services, "effective_model_config", external_on)
    courier = RealCourier(flow.sessions, Settings())  # no injected interpreter
    ctx = BotContext(courier, flow.bot, BotState(), None, False)

    query.handle_text(ctx, IncomingText(ANA_TG, ANA_TG, 1, "contrato da Alfa"))

    assert "provedor externo" in flow.bot.last() and "painel" in flow.bot.last()
    assert flow.events("query.search") == []


def test_a_file_changed_on_the_share_is_refused_and_audited(flow: Flow) -> None:
    flow.say("contrato de consultoria", ask(dono="Alfa", palavras=["consultoria"]))
    path = flow.root / "Alfa Comércio" / "03 Contratos" / "contrato-consultoria-2025.pdf"
    path.write_bytes(pdf("trocado por outro"))  # código de teste: alguém trocou o arquivo

    flow.press(f"q:s:{flow.doc_id('contrato-consultoria-2025.pdf')}")

    assert flow.bot.documents == []
    assert "mudou" in flow.bot.last()
    [event] = flow.events("document.send")
    assert event.resultado == "failed" and event.detalhes["entrega"] == "recusado"


def test_a_missing_file_is_refused_and_audited(flow: Flow) -> None:
    flow.say("contrato de consultoria", ask(dono="Alfa", palavras=["consultoria"]))
    os.unlink(flow.root / "Alfa Comércio" / "03 Contratos" / "contrato-consultoria-2025.pdf")

    flow.press(f"q:s:{flow.doc_id('contrato-consultoria-2025.pdf')}")

    assert flow.bot.documents == [] and "não está mais" in flow.bot.last()
    [event] = flow.events("document.send")
    assert event.resultado == "failed"


def test_a_forged_button_for_a_document_not_offered_sends_nothing(flow: Flow) -> None:
    flow.say("contrato de consultoria", ask(dono="Alfa", palavras=["consultoria"]))

    flow.press(f"q:s:{flow.doc_id('boleto-1-2025.pdf')}")

    assert flow.bot.documents == [] and flow.events("document.send") == []
    assert flow.bot.answers[-1] == ("cb", texts.NOT_IN_OFFER)


def test_listing_owners_never_touches_the_index(flow: Flow) -> None:
    flow.say("quais empresas?", ask("listar_donos"))

    assert flow.bot.last() == "Donos cadastrados (2):\n- Alfa Comércio\n- Beta Serviços"
    assert flow.events("query.search") == []
