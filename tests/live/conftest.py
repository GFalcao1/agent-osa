"""Suite live: um Ollama real, configurado por variável (``OSA_TEST_OLLAMA_*``).

Sem ``OSA_TEST_OLLAMA_URL`` e ``OSA_TEST_OLLAMA_MODEL`` todo teste é pulado com o motivo (visível
em ``-ra``) e a suite live continua PENDENTE: skip não é aprovação. A URL precisa ser loopback
(o túnel SSH do Mac mini aparece como ``http://127.0.0.1:<porta>``); o digest é opcional, mas sem
ele o relatório diz "digest não fixado". Só documentos fictícios do corpus do repositório.

O conftest raiz limpa ``OSA_*`` e bloqueia sockets a cada teste: as variáveis são capturadas no
import e as funções reais de socket voltam só aqui, para os testes de ``tests/live``.
"""

from __future__ import annotations

import os
import socket
from collections.abc import Callable, Iterator
from dataclasses import dataclass, field
from pathlib import Path

import pytest

from osa.core.settings import Settings, is_loopback_url
from osa.extract.model import ExtractLimits
from osa.llm.eval import Corpus, DeterministicNonce, load_corpus
from osa.llm.ollama import OllamaConfig, OllamaProvider
from osa.llm.provider import LLMError, ModelDigestMismatch

ENV_URL = "OSA_TEST_OLLAMA_URL"
ENV_MODEL = "OSA_TEST_OLLAMA_MODEL"
ENV_DIGEST = "OSA_TEST_OLLAMA_DIGEST"
ENV_REPORT_DIR = "OSA_TEST_OLLAMA_REPORT_DIR"
_RAW_URL = os.environ.get(ENV_URL)
_RAW_MODEL = os.environ.get(ENV_MODEL)
_RAW_DIGEST = os.environ.get(ENV_DIGEST) or None
_RAW_REPORT_DIR = os.environ.get(ENV_REPORT_DIR) or None
_REAL_CREATE_CONNECTION = socket.create_connection
_REAL_CONNECT = socket.socket.connect
_REAL_GETADDRINFO = socket.getaddrinfo
_HERE = Path(__file__).resolve().parent
CORPUS_DIR = _HERE.parent / "fixtures" / "corpus"


@dataclass
class LiveEnv:
    settings: Settings
    provider: OllamaProvider
    corpus: Corpus
    limits: ExtractLimits
    report_dir: Path | None = None  # OSA_TEST_OLLAMA_REPORT_DIR: where the reports are kept
    notes: list[str] = field(default_factory=list)


@dataclass
class _State:
    pending: str | None = None  # motivo pelo qual o gate live segue pendente
    env: LiveEnv | None = None


_STATE = _State()


def pytest_collection_modifyitems(items: list[pytest.Item]) -> None:
    for item in items:
        if _HERE in Path(str(item.fspath)).resolve().parents:
            item.add_marker(pytest.mark.live)


@pytest.fixture(autouse=True)
def real_sockets(isolate_osa_environment: None, monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """Desfaz o bloqueio de rede do conftest raiz; depende dele para garantir a ordem."""
    monkeypatch.setattr(socket, "create_connection", _REAL_CREATE_CONNECTION)
    monkeypatch.setattr(socket.socket, "connect", _REAL_CONNECT)
    monkeypatch.setattr(socket, "getaddrinfo", _REAL_GETADDRINFO)
    yield


def _pending(reason: str) -> None:
    _STATE.pending = f"{reason}: suite live PENDENTE"
    pytest.skip(_STATE.pending)


@pytest.fixture(scope="session")
def live() -> LiveEnv:
    """O provedor real, já com digest conferido; pula (e fica PENDENTE) se não houver Ollama."""
    if not _RAW_URL or not _RAW_MODEL:
        _pending(f"{ENV_URL} e {ENV_MODEL} não definidos (precisa de Ollama com o modelo a testar)")
    assert _RAW_URL is not None and _RAW_MODEL is not None
    if not is_loopback_url(_RAW_URL):
        _pending(f"{ENV_URL} precisa ser loopback (127.0.0.1, ::1 ou localhost; use o túnel SSH)")
    settings = Settings(
        _env_file=None,
        ollama_base_url=_RAW_URL,
        ollama_model=_RAW_MODEL,
        ollama_model_digest=_RAW_DIGEST,
    )
    config = OllamaConfig.for_model(settings, _RAW_MODEL, _RAW_DIGEST)
    provider = OllamaProvider(config, nonce_factory=DeterministicNonce(0))
    try:
        observed = provider.check_digest()
    except ModelDigestMismatch as error:
        pytest.fail(f"digest do modelo instalado difere do configurado: {error}")
    except LLMError as error:
        _pending(f"Ollama não respondeu em {_RAW_URL} para {_RAW_MODEL} ({error})")
    env = LiveEnv(
        settings=settings,
        provider=provider,
        corpus=load_corpus(CORPUS_DIR),
        limits=ExtractLimits.from_settings(settings),
        report_dir=Path(_RAW_REPORT_DIR) if _RAW_REPORT_DIR else None,
    )
    env.notes += [
        f"modelo: {_RAW_MODEL}",
        f"digest configurado: {_RAW_DIGEST or 'não fixado'}",
        f"digest observado: {observed}",
    ]
    _STATE.env = env
    return env


@pytest.fixture
def note(live: LiveEnv, request: pytest.FixtureRequest) -> Callable[[str], None]:
    """Registra uma medição no resumo final (vai para o registro de execução da task)."""

    def add(text: str) -> None:
        live.notes.append(f"{request.node.name}: {text}")

    return add


def pytest_terminal_summary(terminalreporter: pytest.TerminalReporter) -> None:
    write = terminalreporter.write_line
    if _STATE.env is not None:
        write("")
        write("== suite live: ambiente e medições ==")
        for line in _STATE.env.notes:
            write(f"  {line}")
        if not _RAW_DIGEST:
            write("  ATENÇÃO: digest não fixado; o gate pede modelo e digest registrados")
    elif _STATE.pending:
        write("")
        write(f"== suite live: {_STATE.pending} ==")
