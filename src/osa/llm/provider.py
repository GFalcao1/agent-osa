"""The provider interface, its errors and the factories that pick a provider from config.

The model returns envelopes and nothing else (ADR-0001). Callers map the errors:
``EnvelopeInvalid`` becomes a review, ``ModelUnavailable`` and ``ModelDigestMismatch`` become
``FALHOU`` without effect. Messages never carry document text, prompts or secrets.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from typing import TYPE_CHECKING, Protocol

from osa.domain.errors import DomainError

if TYPE_CHECKING:
    import httpx

    from osa.admin.services import EffectiveModelConfig
    from osa.core.settings import Settings
    from osa.extract.model import ExtractedContent
    from osa.llm.envelopes import ClassificationEnvelope, DepartmentInput, QueryEnvelope
    from osa.llm.external import AuditSink, ExternalTransport, OwnerNames
    from osa.llm.ollama import OllamaProvider


class LLMError(DomainError):
    """Base class of every model-boundary failure."""


class LLMConfigError(LLMError, ValueError):
    """Configuration the provider refuses to run with (non-loopback URL, bad digest, ...)."""


class ModelUnavailable(LLMError):
    """Network, HTTP error, exhausted time budget or model not installed: the case is FALHOU."""


class ModelDigestMismatch(LLMError):
    """The installed model is not the configured digest; the model is never called."""


class EnvelopeInvalid(LLMError):
    """Reply that is not a valid envelope (bad JSON, out of schema or enum, cut off): review."""


class ContextOverflow(EnvelopeInvalid):
    """The prompt's upper-bound estimate does not fit the context: refused before the network.

    A review, like an invalid envelope: the server would truncate in silence (ADR-0012).
    """


class QueryTooLong(LLMError):
    """A request longer than the Telegram limit; refused before masking, audit or network."""


class InvalidDepartments(LLMError, ValueError):
    """The department list is empty, duplicated or has an unusable name (a caller bug)."""


class DepartmentDescriptionRejected(LLMConfigError, InvalidDepartments):
    """A department description that reads as an instruction to the model, or is too long.

    The administrator is trusted, but a description is data: one that addresses the model is
    refused before any call. For the courier it is a visible configuration error, never an
    archiving decision.
    """


class ExternalNotConfigured(LLMError):
    """The external provider is switched on but has no transport, owners source or audit."""


class LLMProvider(Protocol):
    """What the courier (TASK-107) and the query flow (TASK-110) depend on.

    ``departments`` are the active template departments, in template order: a ``Department``
    (name and optional short description) or a bare name. Only the names make the enum; the
    descriptions go to the prompt as installation data. The envelope returns one of those
    exact names; mapping a name to a folder is the courier's job.
    """

    def classify(
        self, content: ExtractedContent, departments: Sequence[DepartmentInput]
    ) -> ClassificationEnvelope: ...

    def interpret(self, text: str, departments: Sequence[DepartmentInput]) -> QueryEnvelope: ...


@dataclass(frozen=True, slots=True)
class ModelCapabilities:
    """What the server says the model can do; unknown means text only (fail safe)."""

    vision: bool
    thinking: bool
    known: bool
    thinking_values: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class ModelIdentity:
    """What a report or an audit event records about the model that answered."""

    provider: str
    model: str
    digest_configured: str | None
    digest_observed: str | None  # None if never read from the server
    digest_verified: bool  # True only if a digest was configured and matched
    prompt_hash: str
    schema_hash: str
    options: Mapping[str, object]
    capabilities: ModelCapabilities | None = None
    think: str | bool | None = None  # what is sent as ``think``; None means omitted
    departments_hash: str | None = None  # names and descriptions as the prompt lists them


@dataclass(frozen=True, slots=True)
class CallStats:
    """Numbers of the last call; never text."""

    attempts: int
    prompt_tokens: int | None
    output_tokens: int | None
    total_ms: float
    load_ms: float | None
    images_sent: int


def build_local_provider(
    settings: Settings,
    config: EffectiveModelConfig,
    *,
    transport: httpx.BaseTransport | None = None,
) -> OllamaProvider:
    """The Ollama provider for the effective model name and digest."""
    from osa.llm.ollama import OllamaConfig, OllamaProvider

    return OllamaProvider(OllamaConfig.from_settings(settings, config), transport=transport)


def build_query_provider(
    settings: Settings,
    config: EffectiveModelConfig,
    *,
    transport: ExternalTransport | None = None,
    owners_source: Callable[[], Sequence[OwnerNames]] | None = None,
    audit: AuditSink | None = None,
    actor: str = "system",
    local_transport: httpx.BaseTransport | None = None,
) -> LLMProvider:
    """The provider for the query envelope: local, or external only when switched on.

    Off, the external module is never imported ("no key, no registered class"). On without
    a transport, owners source and audit sink there is no silent fallback to the local
    model: the incomplete configuration is an error.
    """
    if not config.provedor_externo_ativo:
        return build_local_provider(settings, config, transport=local_transport)
    if transport is None or owners_source is None or audit is None:
        raise ExternalNotConfigured(
            "provedor externo ligado sem transporte, cadastro de donos e auditoria configurados"
        )
    from osa.llm.external import ExternalQueryProvider

    return ExternalQueryProvider(
        enabled=True,
        transport=transport,
        owners_source=owners_source,
        audit=audit,
        actor=actor,
    )
