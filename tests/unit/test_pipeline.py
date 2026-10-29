"""O pipeline do correio com portas falsas: cada estado final da spec §9, sem bot, modelo ou share.

``MemoryStore`` guarda os objetos do índice em memória e registra o que foi confirmado
(``commit``); ``FakeFs`` é uma árvore em memória que confere, no momento do rename, que a
decisão já estava persistida; ``FakeContent`` e ``FakeLlm`` devolvem o que o caso pede.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from osa.courier.decision import (
    MOTIVO_ANO_AUSENTE,
    MOTIVO_ARQUIVAMENTO_RECUSADO,
    MOTIVO_DEPARTAMENTO_INVALIDO,
    MOTIVO_DONO_AMBIGUO,
    MOTIVO_DONO_DIVERGENTE,
    MOTIVO_ENVELOPE_INVALIDO,
    MOTIVO_SEM_DONO,
    TemplateDepartment,
)
from osa.courier.evidence import OwnerCandidate
from osa.courier.intake import Intake
from osa.courier.pipeline import (
    MOTIVO_LIVRE,
    Archived,
    ArchiveRefused,
    ConfigurationError,
    Duplicate,
    Failed,
    NotSupported,
    Reviewed,
    process,
)
from osa.courier.review import LeftAtOrigin, decide_case, leave_at_origin, year_for
from osa.courier.undo import undo_operation
from osa.db.models import ArchiveOperation, Department, Document, Owner, ReviewCase, Root, User
from osa.domain.document import FileClass, Origin
from osa.domain.errors import (
    AccessDenied,
    Conflict,
    ExtractionFailed,
    NotFound,
    SensitiveFile,
    UnsafePath,
    Unsupported,
)
from osa.extract.classify import class_by_name
from osa.extract.model import DEFAULT_SENSITIVE_EXTENSIONS, ExtractedContent
from osa.fs.errors import DestinationMissing, IdentityChanged, NameTooLong
from osa.fs.names import QUARANTINE_DIR, nfc, split_name
from osa.llm.envelopes import ClassificationEnvelope
from osa.llm.envelopes import Department as DepartmentSpec
from osa.llm.provider import DepartmentDescriptionRejected, EnvelopeInvalid, ModelUnavailable


def make_cnpj(base12: str) -> str:
    def digit(digits: str, weights: list[int]) -> str:
        rest = sum(int(d) * w for d, w in zip(digits, weights, strict=True)) % 11
        return "0" if rest < 2 else str(11 - rest)

    first = digit(base12, [5, 4, 3, 2, 9, 8, 7, 6, 5, 4, 3, 2])
    second = digit(base12 + first, [6, 5, 4, 3, 2, 9, 8, 7, 6, 5, 4, 3, 2])
    return base12 + first + second


CNPJ_ALFA = make_cnpj("112223330001")
CNPJ_BETA = make_cnpj("445556660001")
ACTOR = "test:courier"
PDF = b"%PDF-1.4 conteudo ficticio"
TEXT = "NOTA FISCAL DE SERVIÇO\nTomador: Alfa Comércio Ltda\nValor: R$ 100,00"


# --- fakes -------------------------------------------------------------------------------


class MemoryStore:
    """The index in memory; ``committed`` holds the audit events confirmed by ``commit``."""

    def __init__(
        self,
        roots: Sequence[Root],
        owners: Sequence[Owner],
        aliases: dict[int, list[str]],
        departments: Sequence[Department],
        users: Sequence[User] = (),
    ) -> None:
        self.roots = {r.id: r for r in roots}
        self.owners = {o.id: o for o in owners}
        self.aliases = aliases
        self.departments = list(departments)
        self.users = {u.id: u for u in users}
        self.documents: dict[int, Document] = {}
        self.reviews: list[ReviewCase] = []
        self.operations: list[ArchiveOperation] = []
        self.pending: list[dict[str, Any]] = []
        self.committed: list[dict[str, Any]] = []
        self.states: list[dict[int, str]] = []  # estado of every document at each commit

    def root(self, root_id: int) -> Root:
        return self._get(self.roots, root_id, "raiz")

    def active_roots(self) -> list[Root]:
        return [r for r in self.roots.values() if r.ativo]

    def owner(self, owner_id: int) -> Owner:
        return self._get(self.owners, owner_id, "dono")

    def owner_candidates(self) -> list[OwnerCandidate]:
        return [
            OwnerCandidate(o.id, o.cnpj, tuple(self.aliases.get(o.id, [])))
            for o in self.owners.values()
            if o.ativo
        ]

    def user(self, user_id: int) -> User:
        return self._get(self.users, user_id, "usuário")

    def template(self) -> list[Department]:
        return sorted((d for d in self.departments if d.ativo), key=lambda d: (d.ordem, d.id))

    def document(self, document_id: int) -> Document:
        return self._get(self.documents, document_id, "documento")

    def document_by_sha256(self, sha256: str) -> Document | None:
        return next((d for d in self.documents.values() if d.sha256 == sha256), None)

    def add_document(self, document: Document) -> Document:
        document.id = len(self.documents) + 1
        self.documents[document.id] = document
        return document

    def add_review_case(self, case: ReviewCase) -> ReviewCase:
        case.id = len(self.reviews) + 1
        self.reviews.append(case)
        return case

    def add_operation(self, operation: ArchiveOperation) -> ArchiveOperation:
        operation.id = len(self.operations) + 1
        self.operations.append(operation)
        return operation

    def operation(self, operation_id: int) -> ArchiveOperation:
        found = next((o for o in self.operations if o.id == operation_id), None)
        if found is None:
            raise NotFound("operação de arquivamento não encontrado")
        return found

    def operations_of(self, document_id: int) -> list[ArchiveOperation]:
        return [o for o in self.operations if o.document_id == document_id]

    def review_case(self, case_id: int, *, for_update: bool = False) -> ReviewCase:
        found = next((c for c in self.reviews if c.id == case_id), None)
        if found is None:
            raise NotFound("caso de revisão não encontrado")
        return found

    def open_review_cases(self) -> list[ReviewCase]:
        return [c for c in self.reviews if c.estado == "aberto"]

    def user_by_telegram_id(self, telegram_id: int) -> User | None:
        return next((u for u in self.users.values() if u.telegram_id == telegram_id), None)

    def active_users(self) -> list[User]:
        return [u for u in self.users.values() if u.ativo]

    def active_owners(self) -> list[Owner]:
        return [o for o in self.owners.values() if o.ativo]

    def documents_by_state(self) -> dict[str, int]:
        counts: dict[str, int] = {}
        for d in self.documents.values():
            counts[d.estado] = counts.get(d.estado, 0) + 1
        return counts

    def audit(self, **event: Any) -> None:
        self.pending.append(event)

    def save(self) -> None:
        pass

    def commit(self) -> None:
        self.committed.extend(self.pending)
        self.pending.clear()
        self.states.append({d.id: d.estado for d in self.documents.values()})

    @staticmethod
    def _get[T](table: dict[int, T], key: int, label: str) -> T:
        if key not in table:
            raise NotFound(f"{label} não encontrado")
        return table[key]

    def actions(self) -> list[str]:
        return [str(e["acao"]) for e in self.committed]


class FakeFs:
    """Files and folders by root-relative POSIX path; renames only, never over a name."""

    root = Path("/raiz")

    def __init__(self, store: MemoryStore) -> None:
        self.store = store
        self.files: dict[str, bytes] = {}
        self.folders: set[str] = {QUARANTINE_DIR}
        self.archive_error: Exception | None = None
        self.archive_calls: list[tuple[str, str, str, bool]] = []
        self.state_at_rename: dict[int, tuple[str, int | None, int | None]] | None = None

    def locate(self, root: Path, relative: str) -> Path:
        assert root == self.root
        if relative not in self.files:
            raise NotFound("caminho não existe")
        return self.root / relative

    def has_folder(self, root: Path, relative: str) -> bool:
        return relative in self.folders

    def ensure_owner_tree(
        self,
        root: Path,
        owner_folder: str,
        departments: Sequence[TemplateDepartment],
        year: int | None,
        *,
        effects_enabled: bool,
    ) -> Any:
        levels = [owner_folder, *(f"{owner_folder}/{d.pasta}" for d in departments)]
        if year is not None:
            levels += [f"{owner_folder}/{d.pasta}/{year}" for d in departments if d.usa_ano]
        created = [level for level in levels if level not in self.folders]
        existing = [level for level in levels if level in self.folders]
        if effects_enabled:
            self.folders.update(created)
        return SimpleNamespace(
            owner_dir=self.root / owner_folder,
            created=tuple(self.root / c for c in created),
            existing=tuple(self.root / e for e in existing),
            simulated=not effects_enabled,
        )

    def _free_name(self, folder: str, name: str) -> tuple[str, int]:
        stem, extension = split_name(nfc(name))
        n, candidate = 1, nfc(name)
        while f"{folder}/{candidate}" in self.files:
            n += 1
            candidate = f"{stem} ({n}){extension}"
        return candidate, n

    def archive(
        self, root: Path, src: str, dest_dir: str, name: str, *, effects_enabled: bool
    ) -> Any:
        self.archive_calls.append((src, dest_dir, name, effects_enabled))
        # The courier must have persisted the decision before asking for the effect.
        assert any(e["acao"] == "archive.intent" for e in self.store.committed), (
            "effect requested before the decision was committed"
        )
        self.state_at_rename = {
            d.id: (d.estado, d.owner_id, d.departamento_id) for d in self.store.documents.values()
        }
        if self.archive_error is not None:
            raise self.archive_error
        if src not in self.files:
            raise NotFound("origem não existe")
        if dest_dir not in self.folders:
            raise DestinationMissing("pasta de destino não existe")
        candidate, n = self._free_name(dest_dir, name)
        dest_rel = f"{dest_dir}/{candidate}"
        if effects_enabled:
            self.files[dest_rel] = self.files.pop(src)
        return SimpleNamespace(
            root=self.root,
            source_rel=src,
            dest_rel=dest_rel,
            final_name=candidate,
            suffix=n,
            simulated=not effects_enabled,
            size=len(self.files.get(dest_rel, self.files.get(src, b""))),
            primitive=None if not effects_enabled else SimpleNamespace(value="fake"),
        )

    def undo(
        self,
        root: Path,
        root_id: int,
        origem: str,
        destino: str,
        sha256: str,
        *,
        effects_enabled: bool,
    ) -> Any:
        if destino not in self.files:
            raise NotFound("destino não existe")
        if hashlib.sha256(self.files[destino]).hexdigest() != sha256:
            raise IdentityChanged("o conteúdo não é mais o do registro (sha256 diferente)")
        folder, name = origem.rsplit("/", 1)
        if folder not in self.folders:
            raise DestinationMissing("pasta de destino não existe")
        candidate, n = self._free_name(folder, name)
        dest_rel = f"{folder}/{candidate}"
        if effects_enabled:
            self.files[dest_rel] = self.files.pop(destino)
        return SimpleNamespace(
            root=self.root,
            source_rel=destino,
            dest_rel=dest_rel,
            final_name=candidate,
            suffix=n,
            simulated=not effects_enabled,
            size=0,
            primitive=None,
        )


class FakeContent:
    def __init__(
        self,
        fs: FakeFs,
        *,
        text: str = TEXT,
        classes: dict[str, FileClass] | None = None,
        extract_error: Exception | None = None,
        sniff_error: Exception | None = None,
    ) -> None:
        self.fs = fs
        self.text = text
        self.classes = classes or {}
        self.extract_error = extract_error
        self.sniff_error = sniff_error
        self.external: dict[Path, bytes] = {}
        self.reads: list[Path] = []

    def class_by_name(self, name: str) -> FileClass:
        return class_by_name(name, DEFAULT_SENSITIVE_EXTENSIONS)

    def file_class(self, path: Path) -> FileClass:
        if self.sniff_error is not None:
            raise self.sniff_error
        return self.classes.get(path.name, self.class_by_name(path.name))

    def _bytes(self, path: Path) -> bytes:
        self.reads.append(path)
        if path in self.external:
            return self.external[path]
        return self.fs.files[path.relative_to(self.fs.root).as_posix()]

    def sha256(self, path: Path) -> str:
        return hashlib.sha256(self._bytes(path)).hexdigest()

    def extract(self, path: Path) -> ExtractedContent:
        if self.extract_error is not None:
            raise self.extract_error
        return ExtractedContent(
            text=self.text, images=(), pages_total=1, truncated=False, method="pdf_text"
        )


class FakeLlm:
    def __init__(
        self, envelope: ClassificationEnvelope | None = None, error: Exception | None = None
    ) -> None:
        self.envelope = envelope
        self.error = error
        self.calls: list[tuple[Any, ...]] = []

    def classify(self, content: ExtractedContent, departments: Sequence[Any]) -> Any:
        self.calls.append(tuple(departments))
        if self.error is not None:
            raise self.error
        assert self.envelope is not None
        return self.envelope


def envelope(
    departamento: str = "Fiscal",
    *,
    alternativo: str | None = None,
    revisao: bool = False,
    motivo: str | None = None,
    ano: int | None = 2026,
) -> ClassificationEnvelope:
    return ClassificationEnvelope(
        departamento=departamento,
        departamento_alternativo=alternativo,
        tipo_documental="nota fiscal de serviço",
        tags=["nota", "serviço"],
        resumo="Nota fiscal fictícia de serviço prestado.",
        datas=[date(2026, 1, 31)],
        ano_referencia=ano,
        precisa_revisao=revisao,
        motivo_revisao=motivo,
    )


# --- scenario ----------------------------------------------------------------------------


@dataclass
class Scenario:
    store: MemoryStore
    fs: FakeFs
    content: FakeContent
    llm: FakeLlm
    intake: Intake
    effects: bool = True
    outcomes: list[Any] = field(default_factory=list)

    def run(self, **overrides: Any) -> Any:
        intake = self.intake
        if overrides:
            intake = Intake(**{**_fields(intake), **overrides})
        outcome = process(
            intake,
            store=self.store,
            fs=self.fs,
            content=self.content,
            llm=self.llm,
            actor=ACTOR,
            effects_enabled=self.effects,
        )
        self.outcomes.append(outcome)
        return outcome

    @property
    def document(self) -> Document:
        [document] = self.store.documents.values()
        return document

    def audit_json(self) -> str:
        return json.dumps(self.store.committed, default=str, ensure_ascii=False)


def _fields(intake: Intake) -> dict[str, Any]:
    return {
        "root_id": intake.root_id,
        "source": intake.source,
        "relative_path": intake.relative_path,
        "original_name": intake.original_name,
        "origin": intake.origin,
        "declared_owner": intake.declared_owner,
        "sender": intake.sender,
    }


STAGED = f"{QUARANTINE_DIR}/0123456789abcdef0123456789abcdef.pdf"


def scenario(
    *,
    declared: int | None = 1,
    text: str = TEXT,
    env: ClassificationEnvelope | None = None,
    llm_error: Exception | None = None,
    effects: bool = True,
    origin: Origin = Origin.TELEGRAM,
    relative: str | None = STAGED,
    name: str = "nota.pdf",
) -> Scenario:
    root = Root(id=1, caminho=str(FakeFs.root), ativo=True)
    alfa = Owner(
        id=1, nome_fantasia="Alfa Comércio", pasta="Alfa Comércio", cnpj=CNPJ_ALFA, ativo=True
    )
    beta = Owner(
        id=2, nome_fantasia="Beta Serviços", pasta="Beta Serviços", cnpj=CNPJ_BETA, ativo=True
    )
    departments = [
        Department(id=1, nome="Fiscal", pasta="01 Fiscal", ordem=1, usa_ano=True, ativo=True,
                   descricao="Notas fiscais e guias de impostos"),
        Department(id=2, nome="Pessoal", pasta="02 Pessoal", ordem=2, usa_ano=False, ativo=True),
        Department(id=3, nome="Antigo", pasta="03 Antigo", ordem=3, usa_ano=False, ativo=False),
    ]  # fmt: skip
    users = [
        User(id=1, telegram_id=1001, nome="Admin", papel="admin", departamento_id=None, ativo=True),
        User(id=2, telegram_id=2002, nome="Maria", papel="reviewer", departamento_id=1, ativo=True),
        User(id=3, telegram_id=3003, nome="João", papel="reviewer", departamento_id=2, ativo=True),
        User(id=4, telegram_id=4004, nome="Ana", papel="operator", departamento_id=1, ativo=True),
    ]
    store = MemoryStore(
        [root], [alfa, beta], {1: ["alfa", "alfa comercio"], 2: ["beta", "beta servicos"]},
        departments, users,
    )  # fmt: skip
    fs = FakeFs(store)
    if relative is not None:
        fs.folders.add(relative.rsplit("/", 1)[0])
        fs.files[relative] = PDF
        source = fs.root / relative
    else:
        source = Path("/fora/da/raiz") / name
    content = FakeContent(fs, text=text)
    if relative is None:
        content.external[source] = PDF
    llm = FakeLlm(env if env is not None else envelope(), error=llm_error)
    intake = Intake(1, source, relative, name, origin, declared_owner=declared)
    return Scenario(store, fs, content, llm, intake, effects=effects)


DEST = "Alfa Comércio/01 Fiscal/2026/nota.pdf"


# --- ARQUIVADO -----------------------------------------------------------------------------


def test_declared_owner_archives_by_rename_and_records_everything() -> None:
    sc = scenario()

    outcome = sc.run()

    assert outcome == Archived(1, "ARQUIVADO", DEST, False, 1, 1, 1, 1)
    assert DEST in sc.fs.files and STAGED not in sc.fs.files
    document = sc.document
    assert (document.estado, document.caminho, document.motivo) == ("ARQUIVADO", DEST, None)
    assert (document.owner_id, document.departamento_id, document.ano) == (1, 1, 2026)
    assert (document.tipo_documental, document.tags) == (
        "nota fiscal de serviço",
        ["nota", "serviço"],
    )
    assert document.datas == [date(2026, 1, 31)] and document.texto == TEXT
    assert (document.origem, document.root_id, document.sha256) == (
        "telegram",
        1,
        hashlib.sha256(PDF).hexdigest(),
    )
    [operation] = sc.store.operations
    assert (operation.root_id, operation.origem, operation.destino) == (1, STAGED, DEST)
    assert (operation.sha256, operation.ator, operation.modo) == (document.sha256, ACTOR, "auto")
    assert sc.store.actions() == [
        "document.detect", "document.extract", "document.classify", "archive.intent",
        "document.archive",
    ]  # fmt: skip
    assert all(
        e["resultado"] == "ok" and e["sha256"] == document.sha256 for e in sc.store.committed
    )


def test_every_state_of_the_flow_is_committed_in_order() -> None:
    sc = scenario()
    sc.run()
    assert [s[1] for s in sc.store.states] == [
        "DETECTADO", "EXTRAIDO", "CLASSIFICADO", "CLASSIFICADO", "ARQUIVADO"
    ]  # fmt: skip


def test_the_decision_is_persisted_before_the_rename() -> None:
    sc = scenario()
    sc.run()
    assert sc.fs.state_at_rename == {1: ("CLASSIFICADO", 1, 1)}
    intent = next(e for e in sc.store.committed if e["acao"] == "archive.intent")
    assert intent["detalhes"] == {
        "owner_id": 1, "departamento_id": 1, "ano": 2026,
        "destino_dir": "Alfa Comércio/01 Fiscal/2026", "origem": STAGED, "modo": "auto",
        "evidencia": "declarado",
    }  # fmt: skip


def test_the_model_only_sees_active_department_names_and_descriptions() -> None:
    sc = scenario()
    sc.run()
    assert sc.llm.calls == [
        (
            DepartmentSpec("Fiscal", "Notas fiscais e guias de impostos"),
            DepartmentSpec("Pessoal", None),
        )
    ]


def test_audit_never_carries_document_text() -> None:
    sc = scenario(text="Texto SEGREDO do documento, valor R$ 1.234,56")
    sc.run()
    assert "SEGREDO" not in sc.audit_json() and "1.234,56" not in sc.audit_json()


def test_owner_inferred_from_a_cnpj_in_the_text_without_declaration() -> None:
    sc = scenario(
        declared=None,
        text=f"Recibo de Beta Serviços CNPJ {CNPJ_BETA}",
        origin=Origin.WATCHED,
        relative="entrada/nota.pdf",
    )
    outcome = sc.run()
    assert isinstance(outcome, Archived)
    assert outcome.destino == "Beta Serviços/01 Fiscal/2026/nota.pdf"
    assert sc.document.owner_id == 2
    assert sc.store.operations[0].origem == "entrada/nota.pdf"
    intent = next(e for e in sc.store.committed if e["acao"] == "archive.intent")
    assert intent["detalhes"]["evidencia"] == "texto"


def test_declared_owner_stands_when_the_text_names_it_among_others() -> None:
    sc = scenario(text=f"Contrato entre Alfa Comércio e Beta Serviços ({CNPJ_BETA})")
    assert isinstance(sc.run(), Archived)


def test_year_folder_is_created_only_for_the_chosen_department() -> None:
    sc = scenario()
    sc.run()
    assert "Alfa Comércio/01 Fiscal/2026" in sc.fs.folders
    assert "Alfa Comércio/02 Pessoal" in sc.fs.folders
    assert not any(f.endswith("02 Pessoal/2026") for f in sc.fs.folders)
    assert not any("03 Antigo" in f for f in sc.fs.folders)


def test_collision_gets_a_suffix_and_the_record_keeps_the_final_name() -> None:
    sc = scenario()
    sc.fs.folders.update(
        {"Alfa Comércio", "Alfa Comércio/01 Fiscal", "Alfa Comércio/01 Fiscal/2026"}
    )
    sc.fs.files[DEST] = b"outro arquivo"
    outcome = sc.run()
    assert isinstance(outcome, Archived)
    assert (outcome.destino, outcome.suffix) == ("Alfa Comércio/01 Fiscal/2026/nota (2).pdf", 2)
    assert sc.store.operations[0].destino == outcome.destino


# --- EM_REVISAO -----------------------------------------------------------------------------


def _assert_review(sc: Scenario, outcome: Any, motivo: str, suggested: int | None) -> None:
    assert isinstance(outcome, Reviewed)
    assert (outcome.motivo, outcome.departamento_sugerido_id) == (motivo, suggested)
    [case] = sc.store.reviews
    assert (case.motivo, case.departamento_sugerido_id, case.estado) == (
        motivo,
        suggested,
        "aberto",
    )
    assert (sc.document.estado, sc.document.motivo) == ("EM_REVISAO", motivo)
    assert sc.store.operations == [] and sc.fs.archive_calls == []
    assert sc.store.actions()[-1] == "document.review"


def test_no_evidence_and_no_declaration_is_a_review() -> None:
    sc = scenario(declared=None, text="Recibo sem nenhum dono identificável")
    _assert_review(sc, sc.run(), MOTIVO_SEM_DONO, 1)
    assert STAGED in sc.fs.files


def test_two_owners_in_the_text_without_declaration_is_ambiguous() -> None:
    sc = scenario(declared=None, text=f"Alfa Comércio x Beta Serviços {CNPJ_BETA}")
    _assert_review(sc, sc.run(), MOTIVO_DONO_AMBIGUO, 1)


def test_text_naming_only_another_owner_contradicts_the_declaration() -> None:
    sc = scenario(declared=1, text=f"Holerite de Beta Serviços, CNPJ {CNPJ_BETA}")
    _assert_review(sc, sc.run(), MOTIVO_DONO_DIVERGENTE, 1)
    evidence = next(e for e in sc.store.committed if e["acao"] == "document.evidence")
    assert evidence["detalhes"] == {"declarado": 1, "divergente": [2], "tipo": "cnpj"}


def test_model_reservation_opens_a_review_with_its_reason() -> None:
    sc = scenario(env=envelope(revisao=True, motivo="texto_insuficiente"))
    _assert_review(sc, sc.run(), "texto_insuficiente", 1)


def test_alternative_department_is_a_review() -> None:
    sc = scenario(env=envelope("Fiscal", alternativo="Pessoal"))
    _assert_review(sc, sc.run(), "departamentos_ambiguos", 1)


def test_department_outside_the_active_template_is_reviewed() -> None:
    sc = scenario(env=envelope("Antigo"))
    _assert_review(sc, sc.run(), MOTIVO_DEPARTAMENTO_INVALIDO, None)


def test_missing_year_for_a_department_by_year_is_reviewed() -> None:
    sc = scenario(env=envelope(ano=None))
    _assert_review(sc, sc.run(), MOTIVO_ANO_AUSENTE, 1)
    assert sc.document.ano is None


def test_invalid_envelope_is_a_review_without_classification_data() -> None:
    sc = scenario(llm_error=EnvelopeInvalid("envelope fora do schema (departamento:literal_error)"))
    _assert_review(sc, sc.run(), MOTIVO_ENVELOPE_INVALIDO, None)
    assert sc.document.tipo_documental is None and sc.document.texto == TEXT


def test_refused_archiving_goes_to_review_with_nothing_moved() -> None:
    sc = scenario()
    sc.fs.archive_error = NameTooLong("nome de arquivo excede 255 unidades UTF-16")
    outcome = sc.run()
    assert isinstance(outcome, Reviewed)
    assert outcome.motivo == MOTIVO_ARQUIVAMENTO_RECUSADO
    assert STAGED in sc.fs.files and sc.store.operations == []
    review = next(e for e in sc.store.committed if e["acao"] == "document.review")
    assert review["detalhes"]["erro"].startswith("nome de arquivo excede")


# --- NAO_SUPORTADO, FALHOU, duplicata, sensível ---------------------------------------------


def test_unsupported_by_content_sniff_stays_at_the_origin() -> None:
    sc = scenario()
    sc.content.classes[STAGED.rsplit("/", 1)[1]] = FileClass.UNSUPPORTED
    outcome = sc.run()
    assert isinstance(outcome, NotSupported)
    assert sc.document.estado == "NAO_SUPORTADO" and STAGED in sc.fs.files
    assert sc.llm.calls == []


def test_unsupported_raised_by_the_extraction() -> None:
    sc = scenario()
    sc.content.extract_error = Unsupported("formato não suportado")
    assert isinstance(sc.run(), NotSupported)
    assert sc.document.motivo == "formato não suportado"


def test_extraction_failure_is_falhou_with_the_reason() -> None:
    sc = scenario()
    sc.content.extract_error = ExtractionFailed("arquivo protegido por senha")
    outcome = sc.run()
    assert outcome == Failed(1, "FALHOU", "extração falhou: arquivo protegido por senha")
    assert sc.store.actions()[-1] == "document.fail"


def test_model_failure_is_falhou_without_effect() -> None:
    sc = scenario(llm_error=ModelUnavailable("tempo total esgotado"))
    outcome = sc.run()
    assert (
        isinstance(outcome, Failed)
        and outcome.motivo == "modelo indisponível: tempo total esgotado"
    )
    assert sc.document.estado == "FALHOU" and sc.document.texto == TEXT
    assert STAGED in sc.fs.files and sc.fs.archive_calls == [] and sc.store.operations == []


def test_unexpected_exception_leaves_falhou_instead_of_crashing() -> None:
    sc = scenario(llm_error=RuntimeError("boom"))
    outcome = sc.run()
    assert outcome == Failed(1, "FALHOU", "falha inesperada (RuntimeError)")


def test_identity_changed_after_the_rename_is_falhou_with_the_destination() -> None:
    sc = scenario()
    sc.fs.archive_error = IdentityChanged("a origem foi trocada", destino=DEST)
    outcome = sc.run()
    assert isinstance(outcome, Failed) and outcome.motivo.startswith("identidade da origem mudou")
    assert sc.document.caminho == DEST
    assert sc.store.committed[-1]["detalhes"]["destino"] == DEST


def test_rejected_department_description_is_a_visible_configuration_error() -> None:
    sc = scenario(
        llm_error=DepartmentDescriptionRejected("descrição de departamento parece instrução")
    )
    with pytest.raises(ConfigurationError, match="configuração do template"):
        sc.run()
    assert sc.document.estado == "FALHOU" and sc.document.motivo is not None
    assert sc.document.motivo.startswith("configuração do template recusada")


def test_duplicate_of_an_archived_document_is_informed_not_archived_again() -> None:
    sc = scenario()
    first = sc.run()
    sc.fs.files["entrada/copia.pdf"] = PDF
    sc.fs.folders.add("entrada")
    second = sc.run(
        relative_path="entrada/copia.pdf", original_name="copia.pdf", origin=Origin.WATCHED
    )
    assert second == Duplicate(first.document_id, "ARQUIVADO", DEST)
    assert len(sc.store.documents) == 1 and len(sc.llm.calls) == 1
    assert "entrada/copia.pdf" in sc.fs.files
    assert sc.store.committed[-1]["acao"] == "document.duplicate"


def test_document_under_review_is_a_duplicate_too() -> None:
    sc = scenario(env=envelope(revisao=True, motivo="duvida"))
    sc.run()
    second = sc.run()
    assert isinstance(second, Duplicate) and second.estado == "EM_REVISAO"
    assert len(sc.store.reviews) == 1


def test_failed_document_is_reprocessed_under_the_same_row() -> None:
    sc = scenario(llm_error=ModelUnavailable("fora do ar"))
    failed = sc.run()
    sc.llm.error = None
    archived = sc.run()
    assert isinstance(failed, Failed) and isinstance(archived, Archived)
    assert failed.document_id == archived.document_id and len(sc.store.documents) == 1
    detect = [e for e in sc.store.committed if e["acao"] == "document.detect"]
    assert [e["detalhes"]["reprocessado"] for e in detect] == [False, True]


def test_sensitive_file_is_refused_by_name_before_any_read() -> None:
    sc = scenario(name="certificado.pfx")
    with pytest.raises(SensitiveFile):
        sc.run()
    assert sc.store.documents == {} and sc.content.reads == []
    [event] = sc.store.committed
    assert (event["acao"], event["resultado"], event["alvo_id"]) == (
        "document.intake",
        "denied",
        None,
    )
    assert event["detalhes"]["motivo"] == "classe sensível: nunca lida"


def test_sensitive_name_on_disk_is_refused_even_with_a_harmless_original_name() -> None:
    sc = scenario(relative="entrada/chave.pem", name="nota.pdf")
    with pytest.raises(SensitiveFile):
        sc.run()
    assert sc.store.documents == {} and sc.content.reads == []


def test_sensitive_class_found_after_the_hash_leaves_the_file_at_the_origin() -> None:
    sc = scenario()
    sc.content.extract_error = SensitiveFile("arquivo de classe sensível")
    outcome = sc.run()
    assert isinstance(outcome, NotSupported) and outcome.estado == "NA_ORIGEM"
    assert sc.document.texto is None and sc.llm.calls == []
    assert sc.store.committed[-1]["acao"] == "document.intake"
    sc2 = scenario()
    sc2.content.classes[STAGED.rsplit("/", 1)[1]] = FileClass.SENSITIVE
    assert sc2.run().estado == "NA_ORIGEM"


def test_unreadable_or_oversized_file_is_falhou_with_the_sniff_reason() -> None:
    sc = scenario()
    sc.content.sniff_error = ExtractionFailed("arquivo grande demais")
    outcome = sc.run()
    assert outcome == Failed(1, "FALHOU", "extração falhou: arquivo grande demais")
    assert sc.llm.calls == []


def test_identity_changed_after_the_rename_records_the_move_that_happened() -> None:
    sc = scenario()
    sc.fs.archive_error = IdentityChanged("a origem foi trocada", destino=DEST)
    assert isinstance(sc.run(), Failed)
    [operation] = sc.store.operations
    assert (operation.origem, operation.destino, operation.modo) == (STAGED, DEST, "auto")
    sc2 = scenario()
    sc2.fs.archive_error = IdentityChanged("a origem mudou enquanto era conferida")
    assert isinstance(sc2.run(), Failed)
    assert sc2.store.operations == [] and sc2.document.caminho == STAGED


def test_reprocessing_starts_from_a_clean_row() -> None:
    sc = scenario()
    sc.fs.archive_error = IdentityChanged("trocado", destino=DEST)
    failed = sc.run()
    assert isinstance(failed, Failed) and sc.document.owner_id == 1
    sc.fs.archive_error = None
    sc.fs.files[STAGED] = PDF  # the file is back at the origin (say, moved by hand)
    sc.content.extract_error = Unsupported("formato não suportado")
    outcome = sc.run()
    assert isinstance(outcome, NotSupported)
    document = sc.document
    assert (document.owner_id, document.departamento_id, document.ano) == (None, None, None)
    assert (document.tipo_documental, document.tags, document.texto) == (None, [], None)


def test_the_model_free_reason_is_only_flagged_in_the_audit() -> None:
    sc = scenario(env=envelope(revisao=True, motivo="Fala do CNPJ 12.345.678/0001-95 do cliente"))
    outcome = sc.run()
    assert isinstance(outcome, Reviewed)
    assert outcome.motivo.startswith("Fala do CNPJ")  # the case keeps the model's words
    assert sc.store.reviews[0].motivo == outcome.motivo
    assert "12.345.678" not in sc.audit_json()
    classify = next(e for e in sc.store.committed if e["acao"] == "document.classify")
    review = next(e for e in sc.store.committed if e["acao"] == "document.review")
    assert classify["detalhes"]["motivo_revisao"] == MOTIVO_LIVRE
    assert review["detalhes"]["motivo"] == MOTIVO_LIVRE


def test_known_reasons_are_audited_verbatim() -> None:
    sc = scenario(env=envelope(revisao=True, motivo="texto_insuficiente"))
    sc.run()
    classify = next(e for e in sc.store.committed if e["acao"] == "document.classify")
    assert classify["detalhes"]["motivo_revisao"] == "texto_insuficiente"


def test_owner_contradiction_joins_another_reason_instead_of_being_dropped() -> None:
    sc = scenario(
        declared=1, text=f"Holerite de Beta Serviços, CNPJ {CNPJ_BETA}", env=envelope(ano=None)
    )
    outcome = sc.run()
    assert isinstance(outcome, Reviewed)
    assert outcome.motivo == f"{MOTIVO_ANO_AUSENTE}; {MOTIVO_DONO_DIVERGENTE}"
    review = next(e for e in sc.store.committed if e["acao"] == "document.review")
    assert review["detalhes"]["motivo"] == outcome.motivo  # both are known codes


# --- efeitos desligados ---------------------------------------------------------------------


def test_effects_off_runs_everything_and_records_a_simulated_archive() -> None:
    sc = scenario(effects=False)
    outcome = sc.run()
    assert outcome == Archived(1, "CLASSIFICADO", DEST, True, None, 1, 1, 1)
    assert STAGED in sc.fs.files and DEST not in sc.fs.files
    assert sc.store.operations == []
    assert sc.document.motivo == f"simulado: {DEST}"
    assert sc.store.committed[-1]["resultado"] == "simulated"
    assert sc.fs.folders == {QUARANTINE_DIR}  # nothing created either


def test_effects_off_on_a_file_outside_the_root_predicts_the_destination() -> None:
    sc = scenario(effects=False, relative=None)
    outcome = sc.run()
    assert isinstance(outcome, Archived) and outcome.simulated
    assert outcome.destino == DEST and sc.fs.archive_calls == []
    assert sc.document.caminho is None


def test_effects_on_require_the_file_inside_the_root() -> None:
    sc = scenario(relative=None)
    with pytest.raises(UnsafePath, match="quarentena"):
        sc.run()
    assert sc.store.documents == {} and sc.store.committed == []


# --- cadastro ---------------------------------------------------------------------------------


def test_inactive_root_or_owner_is_refused_before_the_index() -> None:
    sc = scenario()
    sc.store.owners[1].ativo = False
    with pytest.raises(Conflict, match="dono inativo"):
        sc.run()
    sc.store.owners[1].ativo = True
    sc.store.roots[1].ativo = False
    with pytest.raises(Conflict, match="raiz inativa"):
        sc.run()
    assert sc.store.documents == {}


def test_unknown_owner_is_not_found() -> None:
    sc = scenario(declared=9)
    with pytest.raises(NotFound):
        sc.run()


# --- desfazer (NA_ORIGEM) ---------------------------------------------------------------------


def _undo(sc: Scenario, operation_id: int, *, effects: bool = True) -> Any:
    return undo_operation(
        operation_id, store=sc.store, fs=sc.fs, actor="test:undo", effects_enabled=effects
    )


def test_undo_moves_the_file_back_and_records_the_reverse_operation() -> None:
    sc = scenario()
    archived = sc.run()
    assert archived.operation_id == 1

    result = _undo(sc, 1)

    assert (result.operation_id, result.undo_operation_id, result.simulated) == (1, 2, False)
    assert result.destino == STAGED and STAGED in sc.fs.files and DEST not in sc.fs.files
    reverse = sc.store.operations[1]
    assert (reverse.modo, reverse.origem, reverse.destino) == ("undo", DEST, STAGED)
    assert (reverse.root_id, reverse.sha256) == (1, sc.document.sha256)
    assert (sc.document.estado, sc.document.caminho) == ("NA_ORIGEM", STAGED)
    assert sc.document.motivo == "desfeito: operação 1"
    assert sc.store.committed[-1]["acao"] == "archive.undo"


def test_undo_twice_and_undo_of_an_undo_are_refused() -> None:
    sc = scenario()
    sc.run()
    _undo(sc, 1)
    with pytest.raises(Conflict, match="já desfeito"):
        _undo(sc, 1)
    with pytest.raises(Conflict, match="já é um desfazer"):
        _undo(sc, 2)
    with pytest.raises(NotFound):
        _undo(sc, 9)


def test_undo_refuses_a_file_whose_content_changed() -> None:
    sc = scenario()
    sc.run()
    sc.fs.files[DEST] = b"conteudo trocado"
    with pytest.raises(IdentityChanged):
        _undo(sc, 1)
    assert sc.document.estado == "ARQUIVADO" and DEST in sc.fs.files
    assert sc.store.committed[-1]["resultado"] == "failed"


def test_undo_clears_the_destination_fields_of_the_row() -> None:
    sc = scenario()
    sc.run()
    _undo(sc, 1)
    document = sc.document
    assert (document.owner_id, document.departamento_id, document.ano) == (None, None, None)
    assert document.tipo_documental == "nota fiscal de serviço"  # the classification stays


def test_undo_identity_changed_after_the_rename_back_is_recorded() -> None:
    sc = scenario()
    sc.run()
    original = sc.fs.undo

    def swapped_after_rename(*args: Any, **kwargs: Any) -> Any:
        result = original(*args, **kwargs)
        raise IdentityChanged("trocado depois do rename", destino=result.dest_rel)

    sc.fs.undo = swapped_after_rename  # type: ignore[method-assign]
    with pytest.raises(IdentityChanged):
        _undo(sc, 1)
    reverse = sc.store.operations[1]
    assert (reverse.modo, reverse.origem, reverse.destino) == ("undo", DEST, STAGED)
    assert (sc.document.estado, sc.document.caminho) == ("NA_ORIGEM", STAGED)
    assert sc.document.motivo == "desfeito com identidade alterada: operação 1"
    assert sc.store.committed[-1]["resultado"] == "failed"
    with pytest.raises(Conflict, match="já desfeito"):
        _undo(sc, 1)


def test_undo_simulated_moves_nothing() -> None:
    sc = scenario()
    sc.run()
    result = _undo(sc, 1, effects=False)
    assert result.simulated and result.undo_operation_id is None and result.destino == STAGED
    assert DEST in sc.fs.files and len(sc.store.operations) == 1
    assert sc.document.estado == "ARQUIVADO"


# --- a reviewer's decision through the courier ---------------------------------------------


def _review_scenario(**kwargs: Any) -> Scenario:
    sc = scenario(env=envelope(revisao=True, motivo="texto_insuficiente"), **kwargs)
    outcome = sc.run()
    assert isinstance(outcome, Reviewed)
    return sc


def _decide(
    sc: Scenario, *, owner_id: int = 2, department_id: int = 2, effects: bool = True
) -> Any:
    return decide_case(
        1, owner_id=owner_id, department_id=department_id, store=sc.store, fs=sc.fs,
        actor="telegram:2002", decided_by=2, effects_enabled=effects,
    )  # fmt: skip


def test_review_decision_archives_with_mode_review_and_closes_the_case() -> None:
    sc = _review_scenario()

    outcome = _decide(sc)

    assert isinstance(outcome, Archived) and not outcome.simulated
    assert outcome.destino == "Beta Serviços/02 Pessoal/nota.pdf"
    assert outcome.destino in sc.fs.files and STAGED not in sc.fs.files
    [operation] = sc.store.operations
    assert (operation.modo, operation.ator, operation.origem) == ("review", "telegram:2002", STAGED)
    case = sc.store.reviews[0]
    assert (case.estado, case.decidido_por) == ("decidido", 2)
    document = sc.document
    assert (document.estado, document.owner_id, document.departamento_id) == ("ARQUIVADO", 2, 2)
    decided = next(e for e in sc.store.committed if e["acao"] == "review.decide")
    assert decided["resultado"] == "ok" and decided["detalhes"]["decidido_por"] == 2
    assert decided["detalhes"]["ano_assumido"] is False
    intent = [e for e in sc.store.committed if e["acao"] == "archive.intent"][-1]
    assert (intent["detalhes"]["modo"], intent["detalhes"]["evidencia"]) == ("review", "revisor")


def test_review_decision_uses_the_document_name_not_the_quarantine_id() -> None:
    sc = _review_scenario(name="Contrato Alfa.pdf")
    outcome = _decide(sc)
    assert outcome.destino == "Beta Serviços/02 Pessoal/Contrato Alfa.pdf"


def test_review_into_a_department_by_year_assumes_the_year_when_missing() -> None:
    sc = _review_scenario()
    sc.document.ano = None
    outcome = _decide(sc, owner_id=1, department_id=1)
    assert outcome.destino == "Alfa Comércio/01 Fiscal/2026/nota.pdf"  # from datas
    decided = next(e for e in sc.store.committed if e["acao"] == "review.decide")
    assert decided["detalhes"]["ano_assumido"] is True and decided["detalhes"]["ano"] == 2026


def test_year_for_rules() -> None:
    from datetime import date as _date

    from osa.courier.decision import TemplateDepartment

    fiscal = TemplateDepartment(1, "Fiscal", "01 Fiscal", True)
    pessoal = TemplateDepartment(2, "Pessoal", "02 Pessoal", False)
    document = Document(sha256="a" * 64, tags=[], datas=[])
    assert year_for(document, pessoal) == (None, False)
    document.ano = 2024
    assert year_for(document, fiscal) == (2024, False)
    document.ano = None
    document.datas = [_date(2023, 5, 1), _date(2025, 1, 1)]
    assert year_for(document, fiscal) == (2025, True)
    document.datas = []
    year, assumed = year_for(document, fiscal)
    assert assumed and year == _date.today().year


def test_review_decision_refused_by_the_share_keeps_the_case_open() -> None:
    sc = _review_scenario()
    sc.fs.archive_error = NameTooLong("nome de arquivo excede 255 unidades UTF-16")
    with pytest.raises(ArchiveRefused):
        _decide(sc)
    assert sc.store.reviews[0].estado == "aberto"
    assert sc.document.estado == "EM_REVISAO" and STAGED in sc.fs.files
    assert sc.store.committed[-1]["acao"] == "review.decide"
    assert sc.store.committed[-1]["resultado"] == "denied"


def test_review_decision_is_refused_twice_and_for_unknown_targets() -> None:
    sc = _review_scenario()
    with pytest.raises(NotFound):
        _decide(sc, owner_id=9)
    with pytest.raises(NotFound):
        _decide(sc, department_id=3)  # inactive department
    _decide(sc)
    with pytest.raises(Conflict, match="já decidido"):
        _decide(sc)
    with pytest.raises(Conflict, match="já decidido"):
        leave_at_origin(1, store=sc.store, actor="telegram:1001", decided_by=1)


def test_review_decision_simulated_predicts_and_keeps_the_case() -> None:
    sc = _review_scenario(effects=False)
    outcome = _decide(sc, effects=False)
    assert isinstance(outcome, Archived) and outcome.simulated
    assert sc.store.reviews[0].estado == "decidido" and STAGED in sc.fs.files


def test_leave_at_origin_closes_the_case_without_moving() -> None:
    sc = _review_scenario()
    result = leave_at_origin(1, store=sc.store, actor="telegram:2002", decided_by=2)
    assert result == LeftAtOrigin(1, "NA_ORIGEM", 1)
    assert (sc.document.estado, sc.store.reviews[0].estado) == ("NA_ORIGEM", "origem")
    assert sc.store.reviews[0].decidido_por == 2 and STAGED in sc.fs.files
    assert sc.store.committed[-1]["acao"] == "review.origin"


def test_document_only_simulated_cannot_be_decided() -> None:
    sc = scenario(effects=False, relative=None, env=envelope(revisao=True, motivo="x"))
    sc.run()
    with pytest.raises(Conflict, match="simulado"):
        decide_case(
            1, owner_id=1, department_id=1, store=sc.store, fs=sc.fs, actor="t", decided_by=1,
            effects_enabled=False,
        )  # fmt: skip


def test_only_the_department_reviewers_or_the_admin_decide_in_the_courier() -> None:
    sc = _review_scenario()  # suggested department: Fiscal (id 1)
    for user_id in (3, 4):  # a reviewer of Pessoal, an operator
        with pytest.raises(AccessDenied):
            decide_case(
                1, owner_id=2, department_id=2, store=sc.store, fs=sc.fs, actor="t",
                decided_by=user_id, effects_enabled=True,
            )  # fmt: skip
        with pytest.raises(AccessDenied):
            leave_at_origin(1, store=sc.store, actor="t", decided_by=user_id)
    assert sc.store.reviews[0].estado == "aberto" and sc.store.operations == []
    outcome = decide_case(
        1, owner_id=2, department_id=2, store=sc.store, fs=sc.fs, actor="telegram:1001",
        decided_by=1, effects_enabled=True,
    )  # fmt: skip
    assert isinstance(outcome, Archived)


def test_a_case_without_department_is_for_any_reviewer_but_not_an_operator() -> None:
    sc = scenario(declared=None, text="recibo sem dono")
    outcome = sc.run()
    assert isinstance(outcome, Reviewed) and outcome.departamento_sugerido_id == 1
    sc.store.reviews[0].departamento_sugerido_id = None
    with pytest.raises(AccessDenied):
        leave_at_origin(1, store=sc.store, actor="t", decided_by=4)
    result = leave_at_origin(1, store=sc.store, actor="telegram:3003", decided_by=3)
    assert result.estado == "NA_ORIGEM"


def test_review_decision_that_fails_after_the_rename_still_closes_the_case() -> None:
    sc = _review_scenario()
    sc.fs.archive_error = IdentityChanged("trocado", destino="Beta Serviços/02 Pessoal/nota.pdf")
    outcome = _decide(sc)
    assert isinstance(outcome, Failed)
    assert (sc.store.reviews[0].estado, sc.store.reviews[0].decidido_por) == ("decidido", 2)
    assert sc.document.estado == "FALHOU"
    assert sc.store.committed[-1]["acao"] == "review.decide"
    assert sc.store.committed[-1]["resultado"] == "failed"


def test_the_case_row_is_locked_for_the_decision() -> None:
    sc = _review_scenario()
    seen: list[bool] = []
    original = sc.store.review_case

    def spying(case_id: int, *, for_update: bool = False) -> Any:
        seen.append(for_update)
        return original(case_id, for_update=for_update)

    sc.store.review_case = spying  # type: ignore[method-assign]
    _decide(sc)
    assert seen == [True]
