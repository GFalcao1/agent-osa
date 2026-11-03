"""Prova native do scan contra um share SMB de teste (TASK-109).

Roda só com ``OSA_TEST_SHARE_ROOT`` num mount ``smbfs`` (ver ``conftest.py``); sem isso, todo
teste é pulado com o motivo e a suite fica PENDENTE. Só arquivos fictícios; nenhum teste apaga.

O que o share pode fazer diferente de um volume local, e por isso se prova aqui: a grafia
que ele devolve para nomes com acento (NFC ou NFD), nomes no limite de 255 unidades UTF-16,
pastas que ele lista em outra ordem, os ``._*`` e ``.DS_Store`` que o macOS grava, e o hash
lido pelo descritor sem seguir link. O índice (banco) e o modelo ficam de fora: a suite de
integração prova o scan completo; aqui a caminhada, a colocação pelo caminho e o hash.
"""

from __future__ import annotations

import hashlib
import unicodedata
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest

from osa.courier.decision import TemplateDepartment
from osa.courier.ports import LocalContent, LocalFs, sha256_of_file
from osa.db.models import Document
from osa.extract.model import ExtractedContent
from osa.fs.names import MAX_NAME_UNITS, QUARANTINE_DIR, component_key, name_units
from osa.fs.walk import FileEntry, iter_files
from osa.llm.envelopes import ClassificationEnvelope
from osa.worker.scan import place, scan_root

pytestmark = pytest.mark.native

Note = Callable[[str], None]
PAYLOAD = b"%PDF-1.4\n% documento ficticio do scan\n%%EOF\n"


class _Owner:
    def __init__(self, owner_id: int, pasta: str) -> None:
        self.id = owner_id
        self.pasta = pasta


FISCAL = TemplateDepartment(1, "Fiscal", "01 Fiscal", True)
ALFA = _Owner(10, "Conteúdo & Cia")


def put(path: Path, content: bytes = PAYLOAD) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content)
    return path


def listed(root: Path, after: str | None = None) -> list[FileEntry]:
    return [v for v in iter_files(root, after=after) if isinstance(v, FileEntry)]


def test_scan_walk_finds_accented_names_as_the_share_spells_them(work: Path, note: Note) -> None:
    nfc_name = unicodedata.normalize("NFC", "Conteúdo & Cia")
    put(work / nfc_name / "01 Fiscal" / "2025" / "nota de serviço.pdf")
    put(work / nfc_name / "01 Fiscal" / "2025" / "relatório anual.pdf")

    entries = listed(work)

    assert [e.name for e in entries] == sorted(e.name for e in entries)
    names = {component_key(e.name) for e in entries}
    assert names == {component_key("nota de serviço.pdf"), component_key("relatório anual.pdf")}
    owner_dir = entries[0].parts[0]
    form = (
        "NFC"
        if owner_dir == nfc_name
        else "NFD"
        if unicodedata.normalize("NFD", nfc_name) == owner_dir
        else "outra"
    )
    note(f"grafia devolvida pelo share para nome com acento: {form}")
    owners = {component_key(ALFA.pasta): ALFA}
    departments = {component_key(FISCAL.pasta): FISCAL}
    for entry in entries:
        placed = place(entry.parts, owners, departments)  # type: ignore[arg-type]
        assert placed.in_pattern and placed.year == 2025


def test_scan_walk_handles_a_name_at_the_share_limit(work: Path, note: Note) -> None:
    stem = "ã" * (MAX_NAME_UNITS - len(".pdf"))
    name = f"{stem}.pdf"
    assert name_units(name) == MAX_NAME_UNITS
    try:
        put(work / "Alfa" / "01 Fiscal" / name)
    except OSError as error:
        note(f"share recusou nome com {MAX_NAME_UNITS} unidades UTF-16: {error.errno}")
        pytest.skip("share não aceita nome no limite; medido no resumo")

    [entry] = listed(work)

    assert component_key(entry.name) == component_key(name)
    assert (
        sha256_of_file(LocalFs().locate(work, entry.relative))
        == hashlib.sha256(PAYLOAD).hexdigest()
    )
    note(f"nome com {MAX_NAME_UNITS} unidades listado e lido pelo scan")


def test_scan_walk_skips_quarantine_and_apple_double_files(work: Path) -> None:
    put(work / "Alfa" / "01 Fiscal" / "nota.pdf")
    put(work / QUARANTINE_DIR / "0123abcd.pdf")
    put(work / "Alfa" / "01 Fiscal" / "._nota.pdf", b"\0\5\x16\7")
    put(work / "Alfa" / ".DS_Store", b"\0\0")

    assert [e.relative for e in listed(work)] == ["Alfa/01 Fiscal/nota.pdf"]


def test_scan_walk_resumes_in_the_same_order_the_share_lists(work: Path) -> None:
    for owner in ("Beta", "Alfa", "Álvaro", "alfa2"):
        put(work / owner / "01 Fiscal" / "a.pdf")
        put(work / owner / "01 Fiscal" / "b.pdf")

    everything = [e.relative for e in listed(work)]
    cursor = everything[2]
    resumed = [e.relative for e in listed(work, after=cursor)]

    assert everything == sorted(everything)
    assert resumed == everything[3:]


# --- o scan inteiro contra o share, com índice em memória ------------------------------------


class _Row:
    """Uma linha de cadastro só com o que o scan lê."""

    def __init__(self, **values: Any) -> None:
        self.__dict__.update(values)


class MemoryStore:
    """``Store`` mínimo para o scan: raiz, cadastro e documentos em memória; auditoria em lista."""

    def __init__(self, root_path: Path) -> None:
        self.root_row = _Row(
            id=1, caminho=str(root_path), ativo=True,
            scan_iniciado_em=None, scan_desde=None, scan_cursor=None, ultimo_scan=None,
        )  # fmt: skip
        self.owners = [_Row(id=10, pasta=ALFA.pasta, ativo=True)]
        self.departments = [
            _Row(id=1, nome="Fiscal", pasta="01 Fiscal", usa_ano=True, descricao=None, ativo=True)
        ]
        self.documents: list[Document] = []
        self.events: list[dict[str, Any]] = []
        self.commits = 0

    def root(self, root_id: int) -> Any:
        assert root_id == 1
        return self.root_row

    def active_owners(self) -> Any:
        return self.owners

    def template(self) -> Any:
        return self.departments

    def document_by_sha256(self, sha256: str) -> Document | None:
        return next((d for d in self.documents if d.sha256 == sha256), None)

    def document_at(self, root_id: int, caminho: str) -> Document | None:
        return next((d for d in self.documents if d.caminho == caminho), None)

    def add_document(self, document: Document) -> Document:
        document.id = len(self.documents) + 1
        self.documents.append(document)
        return document

    def unseen_documents(self, root_id: int, since: Any) -> list[Document]:
        return [
            d for d in self.documents
            if d.caminho and d.ausente_em is None and (d.visto_em is None or d.visto_em < since)
        ]  # fmt: skip

    def audit(self, **event: Any) -> None:
        self.events.append(event)

    def save(self) -> None:
        pass

    def commit(self) -> None:
        self.commits += 1


class FakeContent(LocalContent):
    def extract(self, path: Path) -> ExtractedContent:
        return ExtractedContent(
            text=f"Documento fictício {path.name}", images=(), pages_total=1, truncated=False,
            method="pdf_text",
        )  # fmt: skip


class FakeLlm:
    def classify(self, content: ExtractedContent, departments: Any) -> ClassificationEnvelope:
        return ClassificationEnvelope(
            departamento="Fiscal", departamento_alternativo=None, tipo_documental="documento",
            tags=[], resumo=content.text, datas=[], ano_referencia=None, precisa_revisao=False,
            motivo_revisao=None,
        )  # fmt: skip


def _run_scan(store: MemoryStore) -> Any:
    return scan_root(
        1, store=store, fs=LocalFs(), content=FakeContent(), llm=FakeLlm(), actor="test:native"
    )


def test_scan_root_on_the_share_sees_the_same_files_twice(work: Path, note: Note) -> None:
    """Acentos e nomes longos: a segunda passada reconhece tudo (nem duplicata nem ausente)."""
    long_name = "ú" * (MAX_NAME_UNITS - len(".pdf")) + ".pdf"
    base = work / unicodedata.normalize("NFC", ALFA.pasta) / "01 Fiscal" / "2025"
    put(base / "nota de serviço.pdf", PAYLOAD + b"1")
    put(base / "relatório anual.pdf", PAYLOAD + b"2")
    try:
        put(base / long_name, PAYLOAD + b"3")
    except OSError:
        note("share recusou o nome no limite; o teste segue com dois arquivos")
    put(work / "solto.pdf", PAYLOAD + b"4")
    store = MemoryStore(work)

    first = _run_scan(store)
    second = _run_scan(store)

    expected = len(listed(work))
    assert first.concluido and first.novos == expected and first.fora_do_padrao == 1
    assert (second.vistos, second.duplicatas, second.movidos, second.ausentes) == (
        expected,
        0,
        0,
        0,
    )
    assert len(store.documents) == expected
    for doc in store.documents:
        assert doc.texto and doc.ausente_em is None
        if doc.caminho != "solto.pdf":
            assert (doc.owner_id, doc.departamento_id, doc.ano) == (10, 1, 2025)
    note(f"scan_root no share: {expected} arquivos indexados e reconhecidos na segunda passada")


def test_scan_hash_reads_through_the_root_descriptor(work: Path) -> None:
    put(work / "Alfa" / "01 Fiscal" / "nota.pdf")
    [entry] = listed(work)
    path = LocalFs().locate(work, entry.relative)

    assert sha256_of_file(path) == hashlib.sha256(PAYLOAD).hexdigest()
    assert entry.size == len(PAYLOAD)
