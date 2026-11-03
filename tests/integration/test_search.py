"""A busca textual em português com filtros, contra o PostgreSQL descartável.

As linhas do índice são gravadas direto (o scan tem a sua suite); a coluna ``texto_tsv`` é
gerada pelo banco, então o que se prova aqui é a consulta real: stemming, E entre palavras,
filtros de dono, departamento, tipo e período, exclusão de ausentes e de documentos sem caminho,
e a CLI ``osa search``.
"""

from __future__ import annotations

import hashlib
from collections.abc import Iterator
from dataclasses import dataclass
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import create_engine
from sqlalchemy.engine import URL
from sqlalchemy.orm import Session, sessionmaker
from typer.testing import CliRunner

from osa import cli
from osa.admin import services
from osa.db.models import Department, Document, Owner
from osa.domain.document import DocumentState, Origin
from osa.llm.envelopes import QueryEnvelope
from osa.search.service import Hit, SearchLimits, filters_for, search


def mounted(_path: Path) -> bool:
    return True


def envelope(**values: Any) -> QueryEnvelope:
    base: dict[str, Any] = {
        "dono": None,
        "departamento": None,
        "tipo_documental": None,
        "periodo_inicio": None,
        "periodo_fim": None,
        "palavras_chave": [],
    }
    return QueryEnvelope(**{**base, **values})


@dataclass
class Index:
    session: Session
    root_id: int
    alfa: Owner
    beta: Owner
    fiscal: Department
    contratos: Department
    ids: dict[str, int]

    def find(self, **values: Any) -> list[Hit]:
        return search(self.session, envelope(**values))

    def names(self, **values: Any) -> list[str]:
        return [hit.nome or "" for hit in self.find(**values)]


def seed(session: Session, root: Path) -> Index:
    services.bootstrap_admin(session, telegram_id=1001, nome="Admin Ficticio", actor="test:boot")
    registered = services.create_root(
        session, caminho=str(root), is_mount_point=mounted, actor="test:setup"
    )
    fiscal = services.create_department(
        session, nome="Fiscal", pasta="01 Fiscal", usa_ano=True, actor="test:setup"
    )
    contratos = services.create_department(
        session, nome="Contratos", pasta="03 Contratos", actor="test:setup"
    )
    alfa = services.create_owner(
        session, nome_fantasia="Alfa Comércio", razao_social="Alfa Comércio Ltda",
        aliases=["Alfa"], actor="test:setup",
    )  # fmt: skip
    beta = services.create_owner(session, nome_fantasia="Beta Serviços", actor="test:setup")
    session.commit()

    def doc(key: str, **values: Any) -> int:
        defaults: dict[str, Any] = {
            "sha256": hashlib.sha256(key.encode()).hexdigest(),
            "root_id": registered.id,
            "origem": Origin.SCAN.value,
            "estado": DocumentState.ARQUIVADO.value,
        }
        row = Document(**{**defaults, **values})
        session.add(row)
        session.flush()
        return row.id

    ids = {
        "boleto_marco": doc(
            "boleto_marco",
            caminho="Alfa Comércio/01 Fiscal/2026/boleto-energia.pdf",
            nome="boleto-energia.pdf",
            owner_id=alfa.id,
            departamento_id=fiscal.id,
            ano=2026,
            tipo_documental="boleto",
            tags=["boleto", "energia"],
            resumo="Boleto de energia elétrica de março de 2026.",
            datas=[date(2026, 3, 31), date(2026, 4, 10)],
            texto="Boleto bancário. Energia elétrica referente ao mês de março de 2026. "
            "Vencimento do boleto em 10/04/2026. Valor R$ 1.234,56.",
        ),
        "boleto_janeiro": doc(
            "boleto_janeiro",
            caminho="Beta Serviços/01 Fiscal/2026/boleto-agua.pdf",
            nome="boleto-agua.pdf",
            owner_id=beta.id,
            departamento_id=fiscal.id,
            ano=2026,
            tipo_documental="boleto",
            resumo="Boleto de água de janeiro de 2026.",
            datas=[date(2026, 1, 31)],
            texto="Boleto de água e esgoto referente a janeiro de 2026.",
        ),
        "contrato": doc(
            "contrato",
            caminho="Alfa Comércio/03 Contratos/contrato-consultoria.pdf",
            nome="contrato-consultoria.pdf",
            owner_id=alfa.id,
            departamento_id=contratos.id,
            ano=2025,
            tipo_documental="contrato de prestação de serviços",
            resumo="Contrato de consultoria assinado em 2025.",
            datas=[date(2025, 1, 15)],
            texto="Contrato de prestação de serviços de consultoria celebrado em janeiro de 2025.",
        ),
        "nota_sem_datas": doc(
            "nota_sem_datas",
            caminho="Alfa Comércio/01 Fiscal/2024/nota.pdf",
            nome="nota.pdf",
            owner_id=alfa.id,
            departamento_id=fiscal.id,
            ano=2024,
            tipo_documental="nota fiscal",
            resumo="Nota fiscal de março.",
            datas=[],
            texto="Nota fiscal de serviço emitida em março, consultoria de manutenção.",
        ),
        "ausente": doc(
            "ausente",
            caminho="Alfa Comércio/01 Fiscal/2026/boleto-antigo.pdf",
            nome="boleto-antigo.pdf",
            owner_id=alfa.id,
            departamento_id=fiscal.id,
            texto="Boleto de março que sumiu do share.",
            ausente_em=datetime.now(UTC),
        ),
        "sem_caminho": doc(
            "sem_caminho",
            caminho=None,
            nome="boleto-simulado.pdf",
            texto="Boleto de março processado com efeitos desligados, fora da raiz.",
            estado=DocumentState.CLASSIFICADO.value,
            origem=Origin.TELEGRAM.value,
        ),
        "ata_sem_tipo": doc(
            "ata_sem_tipo",
            caminho="Beta Serviços/03 Contratos/ata.pdf",
            nome="ata.pdf",
            owner_id=beta.id,
            departamento_id=contratos.id,
            ano=2023,
            tipo_documental=None,  # envelope inválido: só o texto
            datas=[date(2023, 5, 1)],
            texto="Ata de reunião da diretoria sobre o contrato de locação.",
        ),
        "sensivel": doc(
            "sensivel",
            caminho="Alfa Comércio/01 Fiscal/2025/certificado.pfx",
            nome="certificado.pfx",
            owner_id=alfa.id,
            departamento_id=fiscal.id,
            estado=DocumentState.NA_ORIGEM.value,
            texto=None,
        ),
    }
    session.commit()
    return Index(session, registered.id, alfa, beta, fiscal, contratos, ids)


@pytest.fixture
def index(session: Session, tmp_path: Path) -> Index:
    root = tmp_path.resolve() / "raiz"
    root.mkdir()
    return seed(session, root)


# --- texto ------------------------------------------------------------------------------------


def test_keywords_find_the_right_fixture_and_every_keyword_must_match(index: Index) -> None:
    assert index.names(palavras_chave=["boleto", "março"]) == ["boleto-energia.pdf"]


def test_portuguese_stemming_matches_inflections(index: Index) -> None:
    assert "boleto-energia.pdf" in index.names(palavras_chave=["boletos", "energia"])
    assert index.names(palavras_chave=["contratos", "consultoria"]) == ["contrato-consultoria.pdf"]


def test_name_and_summary_count_as_text(index: Index) -> None:
    # "agua" só está no nome do arquivo (o texto diz "água", outro radical); "esgoto" só no texto
    assert index.names(palavras_chave=["agua"]) == ["boleto-agua.pdf"]
    assert index.names(palavras_chave=["água"]) == ["boleto-agua.pdf"]
    assert index.names(palavras_chave=["esgoto"]) == ["boleto-agua.pdf"]


def test_ranking_puts_the_best_match_first(index: Index) -> None:
    hits = index.find(palavras_chave=["boleto"])
    assert [h.nome for h in hits][:2] == ["boleto-energia.pdf", "boleto-agua.pdf"]
    assert hits[0].rank >= hits[1].rank > 0


def test_type_filter_matches_the_recorded_type_and_the_text_only_without_one(index: Index) -> None:
    assert set(index.names(tipo_documental="boleto")) == {"boleto-energia.pdf", "boleto-agua.pdf"}
    # "contrato" está no texto do boleto? não; na ata (sem tipo) sim: entra pelo texto
    assert set(index.names(tipo_documental="contrato")) == {"contrato-consultoria.pdf", "ata.pdf"}
    assert index.names(tipo_documental="ata") == ["ata.pdf"]
    # um documento com tipo gravado não entra só porque o texto cita a palavra
    assert "contrato-consultoria.pdf" not in index.names(tipo_documental="consultoria")


# --- filtros ----------------------------------------------------------------------------------


def test_owner_filter_excludes_the_other_owners(index: Index) -> None:
    assert index.names(dono="Alfa", palavras_chave=["boleto"]) == ["boleto-energia.pdf"]
    assert index.names(dono="Beta Serviços", palavras_chave=["boleto"]) == ["boleto-agua.pdf"]
    assert index.names(dono="alfa comércio ltda", tipo_documental="boleto") == [
        "boleto-energia.pdf"
    ]


def test_unknown_owner_or_department_gives_no_hit_and_is_named(index: Index) -> None:
    assert index.find(dono="Delta", palavras_chave=["boleto"]) == []
    assert index.find(departamento="Jurídico") == []
    filters = filters_for(index.session, envelope(dono="Delta", departamento="Jurídico"))
    assert (filters.owner_unresolved, filters.department_unresolved) == ("Delta", "Jurídico")


def test_department_filter(index: Index) -> None:
    assert set(index.names(departamento="contratos")) == {"contrato-consultoria.pdf", "ata.pdf"}
    assert "contrato-consultoria.pdf" not in index.names(departamento="Fiscal")


def test_period_matches_the_envelope_dates(index: Index) -> None:
    assert index.names(periodo_inicio=date(2026, 3, 1), periodo_fim=date(2026, 3, 31)) == [
        "boleto-energia.pdf"
    ]
    assert index.names(periodo_inicio=date(2026, 1, 1), periodo_fim=date(2026, 1, 31)) == [
        "boleto-agua.pdf"
    ]
    assert index.names(periodo_fim=date(2025, 12, 31), palavras_chave=["consultoria"]) == [
        "contrato-consultoria.pdf",
        "nota.pdf",
    ]


def test_period_falls_back_to_the_year_when_there_are_no_dates(index: Index) -> None:
    assert index.names(periodo_inicio=date(2024, 1, 1), periodo_fim=date(2024, 12, 31)) == [
        "nota.pdf"
    ]
    assert "nota.pdf" not in index.names(periodo_inicio=date(2026, 3, 1))


def test_absent_and_pathless_documents_are_never_returned(index: Index) -> None:
    names = index.names(palavras_chave=["boleto", "março"])
    assert "boleto-antigo.pdf" not in names and "boleto-simulado.pdf" not in names


def test_sensitive_hit_is_flagged_and_has_no_text(index: Index) -> None:
    hits = index.find(dono="Alfa", departamento="Fiscal")
    flagged = [h for h in hits if h.sensivel]
    assert [h.nome for h in flagged] == ["certificado.pfx"]
    assert flagged[0].tipo_documental is None and flagged[0].resumo is None
    assert "texto" not in Hit.__dataclass_fields__


def test_no_filter_at_all_lists_the_most_recent_up_to_the_limit(index: Index) -> None:
    assert len(index.find()) == 5  # 6 elegíveis no índice
    assert len(search(index.session, envelope(), SearchLimits(max_hits=2))) == 2


# --- CLI --------------------------------------------------------------------------------------


@pytest.fixture
def cli_index(
    migrated_database: URL, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> Iterator[Index]:
    engine = create_engine(migrated_database)
    sessions = sessionmaker(engine, expire_on_commit=False)
    monkeypatch.setattr(cli, "_session_factory", lambda: sessions)
    root = tmp_path.resolve() / "raiz"
    root.mkdir()
    with sessions() as session:
        index = seed(session, root)
        yield index
    engine.dispose()


def test_cli_search_prints_hits_without_text(cli_index: Index) -> None:
    runner = CliRunner()

    result = runner.invoke(cli.app, ["search", "boleto março", "--dono", "Alfa"])

    assert result.exit_code == 0, result.output
    assert "boleto-energia.pdf" in result.output and "[ARQUIVADO]" in result.output
    assert "1.234,56" not in result.output  # texto nunca sai; só tipo, ano e resumo


def test_cli_search_reports_unknown_owner_and_no_hit_with_exit_two(cli_index: Index) -> None:
    runner = CliRunner()

    unknown = runner.invoke(cli.app, ["search", "boleto", "--dono", "Delta"])
    nothing = runner.invoke(cli.app, ["search", "inexistente"])

    assert unknown.exit_code == 2 and "Delta" in unknown.output
    assert nothing.exit_code == 2 and "nenhum documento" in nothing.output
