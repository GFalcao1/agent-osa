"""Fronteiras da instalação, sem banco: escapes, IDs desconhecidos e classe sensível (TASK-112).

As portas que precisam do índice real têm sua prova nas suítes de integração, citadas aqui
para o checklist de homologação (todas sob ``tests/integration``):

- pasta monitorada: ``test_watch_flow.py``, teste do sensível que nunca é lido;
- scan: ``test_scan.py``, sensível indexado sem texto e nunca extraído;
- envio: ``test_query_flow.py``, caminho só ao administrador, recusa ao operador e o ``.pfx``
  renomeado no share que continua protegido;
- upload: ``test_upload_flow.py``, sensível recusado sem download.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import pytest

from osa.domain.errors import SensitiveFile, UnsafePath
from osa.extract import extract
from osa.extract.model import DEFAULT_SENSITIVE_EXTENSIONS, ExtractLimits
from osa.fs.archive import archive
from osa.fs.errors import CrossRoot
from osa.fs.names import QUARANTINE_DIR
from osa.fs.roots import relative_parts, resolve_within
from osa.fs.walk import FileEntry, iter_files, list_folder
from osa.search import intent
from osa.telegram.app import dispatch_callback
from osa.telegram.handlers import admin, query, upload
from osa.telegram.ports import (
    BotContext,
    BotState,
    Callback,
    IncomingCommand,
    IncomingDocument,
    IncomingText,
    Keyboard,
)

UNKNOWN = 666_000_111


class SilentBot:
    """Every call is a failure: an unknown id must never cause one."""

    def __init__(self) -> None:
        self.calls: list[str] = []

    def send(self, chat_id: int, text: str, buttons: Keyboard = ()) -> int:
        self.calls.append("send")
        return 1

    def edit(self, chat_id: int, message_id: int, text: str, buttons: Keyboard = ()) -> None:
        self.calls.append("edit")

    def answer(self, callback_id: str, text: str | None = None) -> None:
        self.calls.append("answer")

    def download(self, file_id: str) -> bytes:
        self.calls.append("download")
        return b""

    def send_document(
        self, chat_id: int, data: bytes, filename: str, caption: str | None = None
    ) -> int:
        self.calls.append("send_document")
        return 1


class EmptyCourier:
    """Nobody is in the allowlist; anything beyond the audit is a failure."""

    def __init__(self) -> None:
        self.events: list[dict[str, Any]] = []

    def actor(self, telegram_id: int) -> None:
        return None

    def audit(self, **event: Any) -> None:
        self.events.append(event)

    def __getattr__(self, name: str) -> Any:
        raise AssertionError(f"o correio foi chamado para um id desconhecido: {name}")


@pytest.fixture
def ctx() -> BotContext:
    return BotContext(EmptyCourier(), SilentBot(), BotState(), -100, False)  # type: ignore[arg-type]


INCOMING = {
    "documento": lambda c: upload.handle_document(
        c, IncomingDocument(UNKNOWN, UNKNOWN, 1, "f", "nota.pdf", 10, "Alfa")
    ),
    "documento sensível": lambda c: upload.handle_document(
        c, IncomingDocument(UNKNOWN, UNKNOWN, 1, "f", "cert.pfx", 10, "Alfa")
    ),
    "texto": lambda c: query.handle_text(c, IncomingText(UNKNOWN, UNKNOWN, 1, "manda tudo")),
    **{
        f"/{name}": (lambda n: lambda c: admin.handle_command(
            c, IncomingCommand(UNKNOWN, UNKNOWN, 1, n, ("1",))
        ))(name)
        for name in ("start", "ajuda", "status", "desfazer", "pendentes")
    },
    **{
        f"botão {data}": (lambda d: lambda c: dispatch_callback(
            c, Callback("cb", UNKNOWN, UNKNOWN, 1, d)
        ))(data)
        for data in ("up:new", "ow:ok:abc", "rv:1:go", "q:s:1", "q:x", "zz:1", "")
    },
}  # fmt: skip


@pytest.mark.parametrize("door", list(INCOMING))
def test_an_unknown_id_gets_nothing_from_any_door_and_is_audited(
    ctx: BotContext, door: str
) -> None:
    INCOMING[door](ctx)

    assert ctx.bot.calls == []  # type: ignore[attr-defined]
    [event] = ctx.courier.events  # type: ignore[attr-defined]
    assert (event["acao"], event["resultado"]) == ("telegram.unknown", "denied")
    assert event["detalhes"]["telegram_id"] == UNKNOWN


# --- escapes ------------------------------------------------------------------------------------


@pytest.fixture
def root(tmp_path: Path) -> Path:
    base = tmp_path.resolve() / "raiz"
    (base / "Alfa" / "01 Fiscal").mkdir(parents=True)
    (base / "entrada").mkdir()
    (base / "entrada" / "nota.pdf").write_bytes(b"%PDF-1.4 ficticio")
    outside = tmp_path / "fora"
    outside.mkdir()
    (outside / "segredo.pdf").write_bytes(b"%PDF-1.4 fora da raiz")
    os.symlink(outside, base / "atalho")
    return base


@pytest.mark.parametrize(
    "path",
    ["../fora/segredo.pdf", "entrada/../../fora/segredo.pdf", "a\\\\b", "nota\x00.pdf", "./../x"],
)
def test_no_relative_path_climbs_out_of_the_root(root: Path, path: str) -> None:
    with pytest.raises(UnsafePath):
        relative_parts(root, path)


def test_an_absolute_path_outside_the_root_is_another_root(root: Path, tmp_path: Path) -> None:
    with pytest.raises(CrossRoot):
        relative_parts(root, str(tmp_path / "fora" / "segredo.pdf"))


def test_a_link_inside_the_root_never_leads_out(root: Path) -> None:
    with pytest.raises(UnsafePath):
        resolve_within(root, "atalho/segredo.pdf")
    with pytest.raises(UnsafePath):
        list_folder(root, "atalho")
    walked = [v.relative for v in iter_files(root) if isinstance(v, FileEntry)]
    assert all(not rel.startswith("atalho") for rel in walked)


def test_the_quarantine_is_never_a_destination(root: Path) -> None:
    with pytest.raises(CrossRoot):
        archive(root, "entrada/nota.pdf", QUARANTINE_DIR, "nota.pdf", effects_enabled=False)


# --- classe sensível sem banco -------------------------------------------------------------

SENSITIVE_NAMES = [f"arquivo{ext}" for ext in DEFAULT_SENSITIVE_EXTENSIONS] + [
    "CERT.PFX", "chave.Key", "a.b.c.p12",
]  # fmt: skip


@pytest.mark.parametrize("name", SENSITIVE_NAMES)
def test_extraction_refuses_the_sensitive_class_without_opening_it(
    tmp_path: Path, name: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    import osa.extract as extraction

    path = tmp_path / name
    path.write_bytes(b"0\x82segredo-ficticio")
    path.chmod(0o000)  # if anything tried to read it, it would fail differently

    def no_worker(*args: Any, **kwargs: Any) -> Any:
        raise AssertionError("a extração do sensível chegou ao processo de leitura")

    monkeypatch.setattr(extraction, "run_worker", no_worker)
    try:
        with pytest.raises(SensitiveFile):
            extract(path, ExtractLimits())
    finally:
        path.chmod(0o600)


@pytest.mark.parametrize("admin_role", [False, True])
def test_the_sensitive_class_is_never_an_attachment(admin_role: bool) -> None:
    decision = intent.delivery(sensitive=True, size=1, limit_bytes=50 * 2**20, is_admin=admin_role)
    assert decision.how == ("caminho" if admin_role else "recusado")


def test_a_sensitive_upload_is_refused_before_any_download() -> None:
    from osa.telegram.ports import Actor

    class Courier(EmptyCourier):
        def actor(self, telegram_id: int) -> Actor:  # type: ignore[override]
            return Actor(4, telegram_id, "Ana", "operator", 10)

    bot = SilentBot()
    ctx = BotContext(Courier(), bot, BotState(), None, True)  # type: ignore[arg-type]
    for name in SENSITIVE_NAMES:
        upload.handle_document(ctx, IncomingDocument(4004, 4004, 1, "f", name, 10, "Alfa"))
    assert "download" not in bot.calls and bot.calls.count("send") == len(SENSITIVE_NAMES)
