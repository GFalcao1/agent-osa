"""Ollama provider: ``/api/chat`` with the envelope schema in ``format`` (spec §8, ADR-0012).

Hard rules, each with a test:

- every ``/api/chat`` body carries ``format`` and never ``tools``; roles are system and user;
- the server must be on this machine (loopback), checked at configuration and again before
  every request; ``localhost`` is sent as ``127.0.0.1`` (never resolved by name); proxies
  from the environment and redirects are off;
- the prompt must fit the context by an upper-bound estimate made BEFORE any request: the
  server truncates an overflowing prompt in silence (ADR-0012), so nothing it reports back is
  trusted for that;
- a configured digest is fail-closed: a different or unreported digest never reaches the model;
- time is bounded per attempt and for the whole call, retries included; 4xx is never retried,
  nor a slow read of ``/api/chat``; replies are read up to a size ceiling;
- errors and logs hold status codes, counts and exception type names, never text or bodies.

Only the model name, digest and base URL come from configuration; no model name is a constant.
"""

from __future__ import annotations

import json
import math
import re
import threading
import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import date
from types import TracebackType
from typing import TYPE_CHECKING, Any, Literal
from urllib.parse import urlsplit

import httpcore
import httpx
from pydantic import BaseModel

from osa.core.logging import get_logger
from osa.core.settings import is_loopback_url
from osa.llm import prompts
from osa.llm.envelopes import (
    ClassificationEnvelope,
    DepartmentInput,
    QueryEnvelope,
    classification_envelope,
    department_specs,
    parse_envelope,
    query_envelope,
    schema_hash,
    wire_schema,
)
from osa.llm.provider import (
    CallStats,
    ContextOverflow,
    EnvelopeInvalid,
    LLMConfigError,
    LLMError,
    ModelCapabilities,
    ModelDigestMismatch,
    ModelIdentity,
    ModelUnavailable,
)

if TYPE_CHECKING:
    from osa.admin.services import EffectiveModelConfig
    from osa.core.settings import Settings
    from osa.extract.model import ExtractedContent

DEFAULT_NUM_PREDICT_THINKING = 2048
DEFAULT_CTX_MARGIN = 0.9
MAX_NUM_CTX = 49152  # sanity ceiling; the candidates support far more, memory is the cost
MIN_NUM_CTX = 8192
# Upper bound of the prompt in tokens (ADR-0012): one token per UTF-8 BYTE of text. Every token
# of a byte-level BPE or of a SentencePiece vocabulary with byte fallback covers at least one
# byte, so no tokenizer of the candidates can produce more. For Portuguese this is one token
# per character in the worst case (digits and punctuation, which some tokenizers split one by
# one, as in "11.222.333/0001-81") and two per accented letter. Prose usually needs a third of
# that; the bound is deliberately loose.
CHAT_OVERHEAD_TOKENS = 64  # turn markers of a chat template around two messages
# Tokens one page image costs: depends on the model (fixed 256 for Gemma 3, about 1 000 for a
# 1 600 px page in Ministral 3, measured live on 2026-10-26), so it is configuration; the
# default covers the largest measured with margin.
DEFAULT_TOKENS_PER_IMAGE = 1280
# Sizing the context for max_text_chars: worst Portuguese text at 1.25 bytes per character
# (an accent-heavy line such as "ção ê à õ 1.234,56" stays below 1.25). Text with more bytes
# per character (symbols, other scripts) can still overflow: the per-call guard refuses it.
SIZING_BYTES_PER_CHAR = 1.25
_TEMPLATE_SLACK_BYTES = 2500  # department names and descriptions, notes and marks
_WATCHDOG_REPEAT_S = 0.05
MAX_RESPONSE_BYTES = 4 * 1024 * 1024  # an envelope is a few KB; /api/show is the largest reply
_LOAD_FORMAT: dict[str, Any] = {"type": "object"}  # load/unload carry no content, only a format
_THINK_ORDER = ("low", "minimal", "medium", "high")
_ALLOWED_BODY_KEYS = frozenset(
    {"model", "messages", "stream", "format", "options", "keep_alive", "think"}
)
_DIGEST = re.compile(r"[0-9a-f]{12,64}")

_Think = str | bool | None


def _normalise_digest(value: str) -> str:
    text = value.strip().lower()
    return text.removeprefix("sha256:")


def estimate_prompt_tokens(rendered: prompts.RenderedPrompt, *, tokens_per_image: int) -> int:
    """Upper bound of the prompt in tokens: UTF-8 bytes, chat overhead and images."""
    text_bytes = len(rendered.system.encode("utf-8")) + len(rendered.user.encode("utf-8"))
    return text_bytes + CHAT_OVERHEAD_TOKENS + tokens_per_image * len(rendered.images_b64)


def required_num_ctx(
    *,
    max_text_chars: int,
    max_image_pages: int,
    num_predict: int,
    margin: float,
    tokens_per_image: int,
) -> int:
    """Smallest context (multiple of 1024, at least 8192) where the worst prompt fits in margin.

    The worst prompt is ``max_text_chars`` of Portuguese at ``SIZING_BYTES_PER_CHAR``, the
    templates, ``max_image_pages`` images and the chat overhead, counted with the same bound
    as ``estimate_prompt_tokens``.
    """
    template = len((prompts.CLASSIFY_SYSTEM + prompts.CLASSIFY_USER).encode("utf-8"))
    worst = math.ceil(max_text_chars * SIZING_BYTES_PER_CHAR) + template + _TEMPLATE_SLACK_BYTES
    worst += CHAT_OVERHEAD_TOKENS + tokens_per_image * max_image_pages
    need = math.ceil(worst / margin) + num_predict
    context = max(MIN_NUM_CTX, math.ceil(need / 1024) * 1024)
    if context > MAX_NUM_CTX:
        raise LLMConfigError("limites de texto e imagem exigem contexto acima do máximo suportado")
    return context


def literal_loopback_url(url: str) -> str:
    """The base URL with ``localhost`` as ``127.0.0.1``, so no name is ever resolved.

    ``/etc/hosts`` or a resolver could map ``localhost`` elsewhere; an IP literal cannot be
    redirected. ``::1`` is already a literal and stays as ``[::1]``. Trailing slashes go.
    """
    if not is_loopback_url(url):
        raise LLMConfigError("o servidor do modelo precisa estar nesta máquina (loopback)")
    parts = urlsplit(url)
    host = parts.hostname or ""
    host = "127.0.0.1" if host == "localhost" else host
    netloc = f"[{host}]" if ":" in host else host
    if parts.port is not None:
        netloc += f":{parts.port}"
    return f"{parts.scheme}://{netloc}{parts.path.rstrip('/')}"


@dataclass(frozen=True, slots=True)
class OllamaConfig:
    """Everything the client needs; the knobs live here, not in ``Settings`` (ADR-0012)."""

    base_url: str
    model: str
    digest: str | None = None
    max_text_chars: int = 12000
    max_image_pages: int = 5
    temperature: float = 0.0
    seed: int = 0
    num_ctx: int = 0  # 0: sized from the limits by ``required_num_ctx``
    tokens_per_image: int = DEFAULT_TOKENS_PER_IMAGE
    num_predict: int = 768
    num_predict_thinking: int = DEFAULT_NUM_PREDICT_THINKING
    keep_alive: str = "15m"
    think: Literal["auto", "off"] = "auto"
    ctx_margin: float = DEFAULT_CTX_MARGIN
    connect_timeout_s: float = 5.0
    read_timeout_s: float = 180.0  # per attempt
    total_budget_s: float = 420.0  # the whole call: digest, capabilities, chat and retries
    max_attempts: int = 3
    backoff_s: tuple[float, ...] = (1.0, 3.0)
    digest_ttl_s: float = 30.0

    def __post_init__(self) -> None:
        if not is_loopback_url(self.base_url):
            raise LLMConfigError("o servidor do modelo precisa estar nesta máquina (loopback)")
        if not self.model.strip():
            raise LLMConfigError("modelo não configurado")
        if self.digest is not None:
            digest = _normalise_digest(self.digest)
            if not _DIGEST.fullmatch(digest):
                raise LLMConfigError("digest do modelo precisa ter de 12 a 64 hexadecimais")
            object.__setattr__(self, "digest", digest)
        positive = (
            self.max_text_chars,
            self.max_image_pages,
            self.tokens_per_image,
            self.num_predict,
            self.num_predict_thinking,
            self.connect_timeout_s,
            self.read_timeout_s,
            self.total_budget_s,
            self.max_attempts,
        )
        if any(value <= 0 for value in positive) or self.digest_ttl_s < 0 or self.temperature < 0:
            raise LLMConfigError("limites do cliente do modelo precisam ser positivos")
        if not 0 < self.ctx_margin <= 1:
            raise LLMConfigError("ctx_margin precisa estar entre 0 e 1")
        if self.num_ctx == 0:
            object.__setattr__(
                self,
                "num_ctx",
                required_num_ctx(
                    max_text_chars=self.max_text_chars,
                    max_image_pages=self.max_image_pages,
                    num_predict=max(self.num_predict, self.num_predict_thinking),
                    margin=self.ctx_margin,
                    tokens_per_image=self.tokens_per_image,
                ),
            )
        if (
            self.num_ctx < 0
            or self.num_ctx > MAX_NUM_CTX
            or self.num_ctx <= max(self.num_predict, self.num_predict_thinking)
        ):
            raise LLMConfigError("num_ctx fora do intervalo suportado")

    @classmethod
    def from_settings(cls, settings: Settings, config: EffectiveModelConfig) -> OllamaConfig:
        """Effective model name and digest (ADR-0007) plus the text/image limits of the settings."""
        return cls.for_model(settings, config.nome, config.digest)

    @classmethod
    def for_model(cls, settings: Settings, model: str, digest: str | None) -> OllamaConfig:
        """A config for any model name (the evaluation compares candidates): nothing is saved."""
        return cls(
            base_url=settings.ollama_base_url,
            model=model,
            digest=digest,
            max_text_chars=settings.max_text_chars,
            max_image_pages=settings.max_image_pages,
        )

    def options(self, num_predict: int) -> dict[str, object]:
        return {
            "temperature": self.temperature,
            "seed": self.seed,
            "num_ctx": self.num_ctx,
            "num_predict": num_predict,
        }


class _Deadline:
    """The wall-clock deadline of the request in flight (``None``: no request)."""

    def __init__(self) -> None:
        self.at: float | None = None

    def cap(self, timeout: float | None, error: type[Exception]) -> float | None:
        """``timeout`` cut to what is left; past the deadline, ``error`` at once."""
        if self.at is None:
            return timeout
        remaining = self.at - time.monotonic()
        if remaining <= 0:
            raise error("prazo total da chamada esgotado")
        return remaining if timeout is None else min(timeout, remaining)


class _DeadlineStream(httpcore.NetworkStream):
    """A socket stream whose every read, write and TLS step waits at most until the deadline."""

    def __init__(self, inner: httpcore.NetworkStream, deadline: _Deadline) -> None:
        self._inner = inner
        self._deadline = deadline

    def read(self, max_bytes: int, timeout: float | None = None) -> bytes:
        return self._inner.read(max_bytes, self._deadline.cap(timeout, httpcore.ReadTimeout))

    def write(self, buffer: bytes, timeout: float | None = None) -> None:
        self._inner.write(buffer, self._deadline.cap(timeout, httpcore.WriteTimeout))

    def close(self) -> None:
        self._inner.close()

    def start_tls(
        self,
        ssl_context: Any,
        server_hostname: str | None = None,
        timeout: float | None = None,
    ) -> httpcore.NetworkStream:
        cap = self._deadline.cap(timeout, httpcore.ConnectTimeout)
        return _DeadlineStream(
            self._inner.start_tls(ssl_context, server_hostname, cap), self._deadline
        )

    def get_extra_info(self, info: str) -> Any:
        return self._inner.get_extra_info(info)


class _DeadlineBackend(httpcore.NetworkBackend):
    """httpcore's sync backend with the request deadline applied to connect and every I/O.

    httpx's timeouts are per operation: a server that drips bytes, or a connect that ends
    late, would otherwise outlive the total budget. A connect that returns past the deadline
    is closed and refused.
    """

    def __init__(self, deadline: _Deadline) -> None:
        self._inner = httpcore.SyncBackend()
        self._deadline = deadline

    def _wrap(self, stream: httpcore.NetworkStream) -> httpcore.NetworkStream:
        try:
            self._deadline.cap(None, httpcore.ConnectTimeout)
        except httpcore.ConnectTimeout:
            stream.close()
            raise
        return _DeadlineStream(stream, self._deadline)

    def connect_tcp(
        self,
        host: str,
        port: int,
        timeout: float | None = None,
        local_address: str | None = None,
        socket_options: Any = None,
    ) -> httpcore.NetworkStream:
        cap = self._deadline.cap(timeout, httpcore.ConnectTimeout)
        return self._wrap(self._inner.connect_tcp(host, port, cap, local_address, socket_options))

    def connect_unix_socket(
        self, path: str, timeout: float | None = None, socket_options: Any = None
    ) -> httpcore.NetworkStream:
        cap = self._deadline.cap(timeout, httpcore.ConnectTimeout)
        return self._wrap(self._inner.connect_unix_socket(path, cap, socket_options))

    def sleep(self, seconds: float) -> None:
        self._inner.sleep(seconds)


class _DeadlineTransport(httpx.HTTPTransport):
    """``httpx.HTTPTransport`` (same defaults) over ``_DeadlineBackend``."""

    def __init__(self, deadline: _Deadline) -> None:
        super().__init__()
        self._pool.close()
        self._pool = httpcore.ConnectionPool(
            ssl_context=httpx.create_ssl_context(),
            max_connections=100,
            max_keepalive_connections=20,
            keepalive_expiry=5.0,
            network_backend=_DeadlineBackend(deadline),
        )


def build_chat_body(
    *,
    model: str,
    messages: Sequence[Mapping[str, object]],
    schema: Mapping[str, Any],
    options: Mapping[str, object],
    keep_alive: str | int,
    think: _Think = None,
) -> dict[str, Any]:
    """The only place a ``/api/chat`` body is made: ``format`` is required, ``tools`` cannot be."""
    body: dict[str, Any] = {
        "model": model,
        "messages": [dict(message) for message in messages],
        "stream": False,
        "format": dict(schema),
        "options": dict(options),
        "keep_alive": keep_alive,
    }
    if think is not None:
        body["think"] = think
    if not set(body) <= _ALLOWED_BODY_KEYS or any(
        m.get("role") not in ("system", "user") for m in body["messages"]
    ):
        raise LLMError("corpo de requisição fora da lista permitida")
    return body


class _Budget:
    def __init__(self, total_s: float, monotonic: Callable[[], float]) -> None:
        self._monotonic = monotonic
        self._deadline = monotonic() + total_s

    def remaining(self) -> float:
        return self._deadline - self._monotonic()


def _full_name(name: str) -> str:
    return name if ":" in name.rsplit("/", 1)[-1] else name + ":latest"


class OllamaProvider:
    """``LLMProvider`` backed by a local Ollama. One inference at a time per instance."""

    def __init__(
        self,
        config: OllamaConfig,
        *,
        transport: httpx.BaseTransport | None = None,
        nonce_factory: Callable[[], str] = prompts.default_nonce,
        sleep: Callable[[float], None] = time.sleep,
        monotonic: Callable[[], float] = time.monotonic,
        today: Callable[[], date] = date.today,
    ) -> None:
        self.config = config
        self.last_call: CallStats | None = None
        self.loaded_memory: dict[str, int] | None = None
        self._transport = transport
        self._deadline = _Deadline()
        self._active_transport: httpx.BaseTransport | None = None
        self._client = self._new_client()
        self._nonce_factory = nonce_factory
        self._sleep = sleep
        self._monotonic = monotonic
        self._today = today
        self._lock = threading.Lock()
        self._digest_observed: str | None = None
        self._digest_verified = False
        self._digest_checked_at: float | None = None
        self._warned_unpinned = False
        self._capabilities_cache: tuple[tuple[str, str | None], ModelCapabilities] | None = None
        self._think_sent: _Think = None

    # --- lifecycle -----------------------------------------------------------

    def _new_client(self) -> httpx.Client:
        """A client whose transport enforces the request deadline in every phase.

        An injected transport (tests, tools) is used as given: there the watchdog is the only
        bound.
        """
        transport = self._transport or _DeadlineTransport(self._deadline)
        self._active_transport = transport
        return httpx.Client(transport=transport, trust_env=False, follow_redirects=False)

    @property
    def http_client(self) -> httpx.Client:
        return self._client

    def close(self) -> None:
        self._client.close()

    def __enter__(self) -> OllamaProvider:
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        self.close()

    # --- LLMProvider ---------------------------------------------------------

    def classify(
        self, content: ExtractedContent, departments: Sequence[DepartmentInput]
    ) -> ClassificationEnvelope:
        specs = department_specs(departments)
        model = classification_envelope(specs)

        def render(send_images: bool, nonce: Callable[[], str]) -> prompts.RenderedPrompt:
            return prompts.render_classify(
                content,
                specs,
                max_text_chars=self.config.max_text_chars,
                max_image_pages=self.config.max_image_pages,
                send_images=send_images,
                nonce_factory=nonce,
            )

        with self._lock:
            # Largest form first (every image, the larger output budget), before any request.
            rendered = render(True, self._nonce_factory)
            self._check_fits(
                rendered, max(self.config.num_predict, self.config.num_predict_thinking)
            )
            budget = self._new_budget()
            self._ensure_digest(budget)
            caps = self._capabilities(budget)
            if content.images and not caps.vision:
                nonce = rendered.nonce
                rendered = render(False, lambda: nonce)
            envelope = self._chat(budget, rendered, model, caps)
        return self._guard(content, rendered, envelope)

    @staticmethod
    def _guard(
        content: ExtractedContent,
        rendered: prompts.RenderedPrompt,
        envelope: ClassificationEnvelope,
    ) -> ClassificationEnvelope:
        """Code checks that complement the model and override its answer (spec §8).

        A fake delimiter or a ready-made envelope in the text (or echoed in the envelope) is an
        embedded instruction whatever the model said; too little text without images is a
        review for lack of text.
        """
        echoed = " ".join((envelope.tipo_documental, envelope.resumo, *envelope.tags))
        if prompts.has_embedded_instruction_markers(
            content.text
        ) or prompts.has_embedded_instruction_markers(echoed):
            return envelope.model_copy(
                update={
                    "precisa_revisao": True,
                    "motivo_revisao": prompts.MOTIVO_INSTRUCAO_EMBUTIDA,
                }
            )
        if not rendered.images_b64 and not prompts.text_is_sufficient(content.text):
            # No vision (or no pages): the text is all the model had, and it was too little.
            if not envelope.precisa_revisao:
                return envelope.model_copy(
                    update={
                        "precisa_revisao": True,
                        "motivo_revisao": prompts.MOTIVO_TEXTO_INSUFICIENTE,
                    }
                )
        return envelope

    def interpret(self, text: str, departments: Sequence[DepartmentInput]) -> QueryEnvelope:
        specs = department_specs(departments)
        model = query_envelope(specs)
        with self._lock:
            rendered = prompts.render_query(
                text, specs, today=self._today(), nonce_factory=self._nonce_factory
            )
            self._check_fits(
                rendered, max(self.config.num_predict, self.config.num_predict_thinking)
            )
            budget = self._new_budget()
            self._ensure_digest(budget)
            caps = self._capabilities(budget)
            return self._chat(budget, rendered, model, caps)

    # --- evaluation and operations -------------------------------------------

    def check_digest(self) -> str | None:
        """Read the installed digest now (raises on mismatch or when not installed)."""
        with self._lock:
            self._digest_checked_at = None
            self._ensure_digest(self._new_budget())
        return self._digest_observed

    def capabilities(self) -> ModelCapabilities:
        with self._lock:
            budget = self._new_budget()
            self._ensure_digest(budget)
            return self._capabilities(budget)

    def warm_up(self) -> None:
        """Load the model (so the first measured call does not pay for it) and note its memory."""
        with self._lock:
            budget = self._new_budget()
            self._ensure_digest(budget)
            body = build_chat_body(
                model=self.config.model,
                messages=[],
                schema=_LOAD_FORMAT,
                options=self.config.options(self.config.num_predict),
                keep_alive=self.config.keep_alive,
            )
            self._request(budget, "POST", "/api/chat", body=body)
            try:
                data = self._json(self._request(budget, "GET", "/api/ps")[0])
            except ModelUnavailable:
                return
            wanted = _full_name(self.config.model)
            for entry in data.get("models", []):
                if isinstance(entry, dict) and wanted in (entry.get("name"), entry.get("model")):
                    self.loaded_memory = {
                        key: int(entry[key])
                        for key in ("size", "size_vram")
                        if isinstance(entry.get(key), int)
                    }

    def unload(self) -> None:
        """Free the model's memory (``keep_alive: 0``); a failure is a warning, not an error."""
        with self._lock:
            try:
                body = build_chat_body(
                    model=self.config.model,
                    messages=[],
                    schema=_LOAD_FORMAT,
                    options=self.config.options(self.config.num_predict),
                    keep_alive=0,
                )
                self._request(self._new_budget(), "POST", "/api/chat", body=body)
            except LLMError as error:
                get_logger(__name__).warning("llm.unload_failed", error=type(error).__name__)

    def identity(self, departments: Sequence[DepartmentInput]) -> ModelIdentity:
        names = department_specs(departments)
        cached = self._capabilities_cache[1] if self._capabilities_cache else None
        return ModelIdentity(
            provider="ollama",
            model=self.config.model,
            digest_configured=self.config.digest,
            digest_observed=self._digest_observed,
            digest_verified=self._digest_verified,
            prompt_hash=prompts.prompt_hash(),
            schema_hash=schema_hash(wire_schema(classification_envelope(names))),
            options={
                **self.config.options(self.config.num_predict),
                "num_predict_thinking": self.config.num_predict_thinking,
                "keep_alive": self.config.keep_alive,
                "think": self.config.think,
                "ctx_margin": self.config.ctx_margin,
                "tokens_por_imagem": self.config.tokens_per_image,
            },
            capabilities=cached,
            think=self._think_sent,
            departments_hash=prompts.departments_hash(names),
        )

    # --- internals -----------------------------------------------------------

    def _new_budget(self) -> _Budget:
        return _Budget(self.config.total_budget_s, self._monotonic)

    def _base_url(self) -> str:
        # Checked on every request, not only at construction: the config can be rebuilt or
        # mutated, and this is the last step before document content leaves the process.
        return literal_loopback_url(self.config.base_url)

    def _check_fits(self, rendered: prompts.RenderedPrompt, num_predict: int) -> None:
        """Refuse, before the network, a prompt whose upper bound does not fit the context."""
        config = self.config
        limit = int((config.num_ctx - num_predict) * config.ctx_margin)
        if estimate_prompt_tokens(rendered, tokens_per_image=config.tokens_per_image) > limit:
            raise ContextOverflow("contexto estimado do prompt acima do limite")

    def _request(
        self,
        budget: _Budget,
        method: str,
        path: str,
        *,
        body: dict[str, Any] | None = None,
        read_timeout: float | None = None,
    ) -> tuple[bytes, int]:
        """One logical request: per-try timeout cut to the budget, retries inside the budget.

        Returns the body of a 200 reply, read up to ``MAX_RESPONSE_BYTES``.
        """
        config = self.config
        read_limit = config.read_timeout_s if read_timeout is None else read_timeout
        attempts = 0
        slow_retries = 0
        while True:
            url = self._base_url() + path
            remaining = budget.remaining()
            if remaining <= 0:
                raise ModelUnavailable("tempo total da chamada ao modelo esgotado")
            attempts += 1
            timeout = httpx.Timeout(
                connect=min(config.connect_timeout_s, remaining),
                read=min(read_limit, remaining),
                write=min(30.0, remaining),
                pool=min(5.0, remaining),
            )
            slow = False
            try:
                status, content = self._send(method, url, body, timeout, budget)
            except httpx.ReadTimeout:
                if budget.remaining() <= 0:
                    raise ModelUnavailable("tempo total da chamada ao modelo esgotado") from None
                if path == "/api/chat":
                    # A second try would queue behind the generation still running there.
                    raise ModelUnavailable(
                        "servidor do modelo indisponível (ReadTimeout)"
                    ) from None
                slow, reason = True, "ReadTimeout"
            except (httpx.NetworkError, httpx.TimeoutException, httpx.RemoteProtocolError) as error:
                reason = type(error).__name__
            except httpx.HTTPError as error:
                raise ModelUnavailable(
                    f"falha ao falar com o modelo ({type(error).__name__})"
                ) from None
            else:
                if status == 200 and content is not None:
                    return content, attempts
                if status not in (500, 502, 503, 504):  # 4xx and anything else: no retry
                    raise ModelUnavailable(f"servidor do modelo respondeu HTTP {status}")
                slow, reason = status == 500, f"HTTP {status}"
            if budget.remaining() <= 0:
                raise ModelUnavailable("tempo total da chamada ao modelo esgotado")
            if attempts >= config.max_attempts or (slow and slow_retries >= 1):
                raise ModelUnavailable(f"servidor do modelo indisponível ({reason})")
            slow_retries += int(slow)
            wait = (
                config.backoff_s[min(attempts - 1, len(config.backoff_s) - 1)]
                if config.backoff_s
                else 0.0
            )
            if wait >= budget.remaining():
                raise ModelUnavailable("tempo total da chamada ao modelo esgotado")
            get_logger(__name__).info(
                "llm.retry", path=path, attempt=attempts, reason=reason, wait_s=wait
            )
            self._sleep(wait)

    def _send(
        self,
        method: str,
        url: str,
        body: dict[str, Any] | None,
        timeout: httpx.Timeout,
        budget: _Budget,
    ) -> tuple[int, bytes | None]:
        """Status and, for a 200, the body read in pieces up to ``MAX_RESPONSE_BYTES``.

        httpx's timeouts are per operation: a slow connect, headers or a body that trickle in
        could outlive the total budget. Three bounds: the transport caps connect, TLS and every
        read and write at the deadline (``_DeadlineBackend``); the deadline is checked after
        every piece of the body; and a watchdog closes the client and its transport when the
        deadline passes, again every 50 ms until the request ends (for an injected transport,
        the only bound). The next request gets a new client. No timer outlives the call.
        """
        if self._client.is_closed:
            self._client = self._new_client()
        client, transport = self._client, self._active_transport
        remaining = max(budget.remaining(), 0.0)
        self._deadline.at = time.monotonic() + remaining
        expired, done = threading.Event(), threading.Event()
        timers: list[threading.Timer] = []
        guard = threading.Lock()

        def arm(delay: float) -> None:
            with guard:
                if done.is_set():
                    return
                timer = threading.Timer(delay, abort)
                timer.daemon = True
                timers.append(timer)
                timer.start()

        def abort() -> None:
            if done.is_set():
                return
            expired.set()
            client.close()
            if transport is not None:
                transport.close()
            arm(_WATCHDOG_REPEAT_S)

        arm(remaining)
        try:
            return self._send_once(client, method, url, body, timeout, budget)
        except Exception:
            deadline = self._deadline.at
            if expired.is_set() or (deadline is not None and time.monotonic() >= deadline):
                raise ModelUnavailable("tempo total da chamada ao modelo esgotado") from None
            raise
        finally:
            with guard:
                done.set()
                for timer in timers:
                    timer.cancel()
            for timer in timers:
                timer.join()
            self._deadline.at = None

    def _send_once(
        self,
        client: httpx.Client,
        method: str,
        url: str,
        body: dict[str, Any] | None,
        timeout: httpx.Timeout,
        budget: _Budget,
    ) -> tuple[int, bytes | None]:
        request = client.build_request(method, url, json=body, timeout=timeout)
        response = client.send(request, stream=True)
        try:
            if response.status_code != 200:
                return response.status_code, None
            chunks: list[bytes] = []
            size = 0
            for chunk in response.iter_bytes():
                if budget.remaining() <= 0:
                    raise ModelUnavailable("tempo total da chamada ao modelo esgotado")
                size += len(chunk)
                if size > MAX_RESPONSE_BYTES:
                    raise ModelUnavailable("resposta do servidor do modelo grande demais")
                chunks.append(chunk)
            return 200, b"".join(chunks)
        finally:
            response.close()

    @staticmethod
    def _json(raw: bytes) -> dict[str, Any]:
        try:
            data = json.loads(raw)
        except ValueError:
            raise ModelUnavailable("resposta do servidor do modelo não é JSON") from None
        if not isinstance(data, dict):
            raise ModelUnavailable("resposta do servidor do modelo não é um objeto")
        return data

    def _ensure_digest(self, budget: _Budget) -> None:
        now = self._monotonic()
        if (
            self._digest_checked_at is not None
            and now - self._digest_checked_at < self.config.digest_ttl_s
        ):
            return
        data = self._json(self._request(budget, "GET", "/api/tags", read_timeout=10.0)[0])
        wanted = _full_name(self.config.model)
        entry = next(
            (
                m
                for m in data.get("models", [])
                if isinstance(m, dict) and wanted in (m.get("name"), m.get("model"))
            ),
            None,
        )
        if entry is None:
            raise ModelUnavailable("modelo não instalado no servidor")
        raw = entry.get("digest")
        observed = _normalise_digest(raw) if isinstance(raw, str) else None
        self._digest_observed = observed
        self._digest_verified = False
        expected = self.config.digest
        if expected is not None:
            if observed is None or not observed.startswith(expected):
                self._digest_checked_at = None
                raise ModelDigestMismatch("o digest instalado não é o configurado")
            self._digest_verified = True
        elif not self._warned_unpinned:
            self._warned_unpinned = True
            get_logger(__name__).warning("llm.digest_not_pinned", model=self.config.model)
        self._digest_checked_at = now

    def _capabilities(self, budget: _Budget) -> ModelCapabilities:
        key = (self.config.model, self._digest_observed)
        if self._capabilities_cache is not None and self._capabilities_cache[0] == key:
            return self._capabilities_cache[1]
        data = self._json(
            self._request(
                budget, "POST", "/api/show", body={"model": self.config.model}, read_timeout=30.0
            )[0]
        )
        raw = data.get("capabilities")
        if not isinstance(raw, list):  # an older server: say nothing, claim nothing
            caps = ModelCapabilities(vision=False, thinking=False, known=False)
        else:
            names = {item for item in raw if isinstance(item, str)}
            thinking = data.get("thinking")
            values = thinking.get("values") if isinstance(thinking, dict) else None
            caps = ModelCapabilities(
                vision="vision" in names,
                thinking="thinking" in names,
                known=True,
                thinking_values=tuple(v for v in values if isinstance(v, str))
                if isinstance(values, list)
                else (),
            )
        self._capabilities_cache = (key, caps)
        return caps

    def _think_param(self, caps: ModelCapabilities) -> _Think:
        """``think`` only for a model that can think: its lowest level, else ``false``."""
        if self.config.think == "off" or not caps.thinking:
            return None
        for level in _THINK_ORDER:
            if level in caps.thinking_values:
                return level
        return caps.thinking_values[0] if caps.thinking_values else False

    def _chat[T: BaseModel](
        self,
        budget: _Budget,
        rendered: prompts.RenderedPrompt,
        model: type[T],
        caps: ModelCapabilities,
    ) -> T:
        config = self.config
        think = self._think_param(caps)
        self._think_sent = think
        thinking_on = think is not None and think is not False
        num_predict = config.num_predict_thinking if thinking_on else config.num_predict
        self._check_fits(rendered, num_predict)
        user: dict[str, object] = {"role": "user", "content": rendered.user}
        if rendered.images_b64:
            user["images"] = list(rendered.images_b64)
        body = build_chat_body(
            model=config.model,
            messages=[{"role": "system", "content": rendered.system}, user],
            schema=wire_schema(model),
            options=config.options(num_predict),
            keep_alive=config.keep_alive,
            think=think,
        )
        started = self._monotonic()
        raw, attempts = self._request(budget, "POST", "/api/chat", body=body)
        data = self._json(raw)
        # A measurement for the report only: Ollama truncates an overflowing prompt in silence
        # and reports the smaller count (0.35.1, live 2026-10-26), so it proves nothing.
        prompt_tokens = data.get("prompt_eval_count")
        prompt_tokens = prompt_tokens if isinstance(prompt_tokens, int) else None
        output_tokens = data.get("eval_count")
        total_ns, load_ns = data.get("total_duration"), data.get("load_duration")
        self.last_call = CallStats(
            attempts=attempts,
            prompt_tokens=prompt_tokens,
            output_tokens=output_tokens if isinstance(output_tokens, int) else None,
            total_ms=total_ns / 1e6
            if isinstance(total_ns, int)
            else (self._monotonic() - started) * 1000,
            load_ms=load_ns / 1e6 if isinstance(load_ns, int) else None,
            images_sent=len(rendered.images_b64),
        )
        get_logger(__name__).info(
            "llm.call",
            model=config.model,
            attempts=attempts,
            prompt_tokens=prompt_tokens,
            output_tokens=self.last_call.output_tokens,
            images=len(rendered.images_b64),
        )
        if "error" in data:  # a model failure (memory, load), not a bad envelope: FALHOU
            raise ModelUnavailable("o servidor do modelo devolveu erro no corpo da resposta")
        if data.get("done_reason") == "length":
            raise EnvelopeInvalid("resposta cortada pelo limite de tokens")
        message = data.get("message")
        reply = message.get("content") if isinstance(message, dict) else None
        if not isinstance(reply, str) or not reply.strip():
            raise EnvelopeInvalid("resposta vazia do modelo")
        return parse_envelope(model, reply)
