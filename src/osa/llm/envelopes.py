"""The two envelopes the model returns (spec §8) and the schema sent in ``format``.

The base classes are static for typing; the department enum is built at each call from the
template's active departments, so a stale enum cannot exist. The schema sent to Ollama is
flat (no ``$ref``) and lists every field as required, nullable ones included: a field with a
default would be optional in the grammar and the model could omit it.
"""

from __future__ import annotations

import hashlib
import json
import unicodedata
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date
from functools import lru_cache
from typing import Annotated, Any, Literal, Self, cast

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    ValidationError,
    create_model,
    model_validator,
)
from pydantic.json_schema import GenerateJsonSchema

from osa.domain.owner import normalize_text
from osa.llm.provider import DepartmentDescriptionRejected, EnvelopeInvalid, InvalidDepartments

# Ceilings stricter than the spec's text: with a grammar an unbounded list could repeat until
# the token limit. The year range is the one of ``osa.fs.template``.
MAX_TAGS = 10
MAX_RESUMO = 300
MAX_TIPO = 80
MAX_TAG_LEN = 40
MAX_DATAS = 20
MAX_MOTIVO = 200
MAX_DONO = 120
MAX_PALAVRAS = 10
MIN_YEAR = 1900
MAX_YEAR = 2100
MAX_DEPARTMENT_LEN = 100
MAX_DESCRIPTION_LEN = 160
FALLBACK_REASON = "nao_informado"
MOTIVO_DEPARTAMENTOS_AMBIGUOS = "departamentos_ambiguos"

_Tag = Annotated[str, Field(min_length=1, max_length=MAX_TAG_LEN)]
_STRICT = ConfigDict(extra="forbid", frozen=True, strict=True)


class ClassificationEnvelope(BaseModel):
    """What the model says a document is. Never carries an owner (ADR-0001)."""

    model_config = _STRICT

    departamento: str
    departamento_alternativo: str | None
    tipo_documental: Annotated[str, Field(min_length=1, max_length=MAX_TIPO)]
    tags: Annotated[list[_Tag], Field(max_length=MAX_TAGS)]
    resumo: Annotated[str, Field(max_length=MAX_RESUMO)]
    datas: Annotated[list[date], Field(max_length=MAX_DATAS)]
    ano_referencia: Annotated[int, Field(ge=MIN_YEAR, le=MAX_YEAR)] | None
    precisa_revisao: bool
    motivo_revisao: Annotated[str, Field(max_length=MAX_MOTIVO)] | None

    @model_validator(mode="after")
    def _coherent_reason(self) -> Self:
        """Models like to write "nenhum" when nothing is wrong, and forget a reason when it is.

        The courier only needs ``precisa_revisao``; an incoherent pair is repaired, not an error.
        A second plausible department is doubt the model states more reliably than it flags
        review (ADR-0012), so code turns it into review: an alternative equal to
        ``departamento`` is dropped; any other one forces ``precisa_revisao`` and, unless the
        model gave its own reason with ``true``, sets ``departamentos_ambiguos`` (no reason,
        ``nao_informado`` and anything written next to ``false`` are not reasons). Only then
        does the pair rule run: false clears the reason, true without one gets
        ``nao_informado``.
        (After, not before: a before-validator would hand strict mode Python values, where an
        ISO date string no longer parses.) The repair is made on ``self`` and ``self`` is
        returned: under ``__init__`` pydantic discards any other instance a model validator
        returns, so a copy would be lost on that path. The model is frozen, hence
        ``object.__setattr__``; validation is still running, so nobody holds it yet.
        """
        alternative = self.departamento_alternativo
        if alternative is not None and alternative == self.departamento:
            alternative = None
        review = self.precisa_revisao
        reason = self.motivo_revisao
        if reason is not None and not reason.strip():
            reason = None
        if alternative is not None:
            # A reason written next to ``false`` is filler ("nenhum"), not a reason.
            if not review or reason is None or reason.strip() == FALLBACK_REASON:
                reason = MOTIVO_DEPARTAMENTOS_AMBIGUOS
            review = True
        if not review:
            reason = None
        elif reason is None:
            reason = FALLBACK_REASON
        object.__setattr__(self, "departamento_alternativo", alternative)
        object.__setattr__(self, "precisa_revisao", review)
        object.__setattr__(self, "motivo_revisao", reason)
        return self


class QueryEnvelope(BaseModel):
    """What the model says a request is asking for (spec §8)."""

    model_config = _STRICT

    dono: Annotated[str, Field(min_length=1, max_length=MAX_DONO)] | None
    departamento: str | None
    tipo_documental: Annotated[str, Field(min_length=1, max_length=MAX_TIPO)] | None
    periodo_inicio: date | None
    periodo_fim: date | None
    palavras_chave: Annotated[list[_Tag], Field(max_length=MAX_PALAVRAS)]

    @model_validator(mode="after")
    def _ordered_period(self) -> Self:
        if self.periodo_inicio and self.periodo_fim and self.periodo_inicio > self.periodo_fim:
            raise ValueError("periodo_inicio must not be after periodo_fim")
        return self


@dataclass(frozen=True, slots=True)
class Department:
    """A template department as the model sees it: the enum name and an optional description.

    The description is installation data (what the department files, in a short phrase); it
    goes to the prompt, sanitised, and never to the schema or the enum.
    """

    nome: str
    descricao: str | None = None


DepartmentInput = Department | str  # a bare name is a department without description


def department_specs(departments: Sequence[DepartmentInput]) -> tuple[Department, ...]:
    """Validated departments: names as in ``validate_departments``, descriptions stripped.

    A blank description is no description; one that is not text or is longer than
    ``MAX_DESCRIPTION_LEN`` is a caller bug (``InvalidDepartments``). The prompt sanitises
    the text again before use.
    """
    items = [d if isinstance(d, Department) else Department(d) for d in departments]
    names = validate_departments([item.nome for item in items])
    specs: list[Department] = []
    for name, item in zip(names, items, strict=True):
        description = item.descricao
        if description is not None:
            if not isinstance(description, str):
                raise InvalidDepartments("descrição de departamento inválida")
            description = unicodedata.normalize("NFC", description).strip()
            if len(description) > MAX_DESCRIPTION_LEN:
                raise DepartmentDescriptionRejected("descrição de departamento longa demais")
        specs.append(Department(name, description or None))
    return tuple(specs)


def validate_departments(departments: Sequence[DepartmentInput]) -> tuple[str, ...]:
    """The names as NFC, stripped, unique ignoring case and accents; else InvalidDepartments."""
    names: list[str] = []
    seen: set[str] = set()
    for raw in departments:
        item = raw.nome if isinstance(raw, Department) else raw
        if not isinstance(item, str):
            raise InvalidDepartments("nome de departamento inválido")
        name = unicodedata.normalize("NFC", item).strip()
        if not name or len(name) > MAX_DEPARTMENT_LEN:
            raise InvalidDepartments("nome de departamento vazio ou longo demais")
        if any(unicodedata.category(c)[0] == "C" for c in name):
            raise InvalidDepartments("nome de departamento com caractere de controle")
        key = normalize_text(name)
        if not key or key in seen:
            raise InvalidDepartments("departamentos repetidos ou sem letras")
        seen.add(key)
        names.append(name)
    if not names:
        raise InvalidDepartments("nenhum departamento ativo")
    return tuple(names)


def _literal(names: tuple[str, ...]) -> Any:
    return cast(Any, Literal)[names]


@lru_cache(maxsize=32)
def _classification(names: tuple[str, ...]) -> type[ClassificationEnvelope]:
    return create_model(
        "ClassificationEnvelope",
        __base__=ClassificationEnvelope,
        departamento=(_literal(names), ...),
        departamento_alternativo=(_literal(names) | None, ...),
    )


@lru_cache(maxsize=32)
def _query(names: tuple[str, ...]) -> type[QueryEnvelope]:
    return create_model(
        "QueryEnvelope",
        __base__=QueryEnvelope,
        departamento=(_literal(names) | None, ...),
    )


def classification_envelope(
    departments: Sequence[DepartmentInput],
) -> type[ClassificationEnvelope]:
    """The classification envelope whose department fields take exactly these names."""
    return _classification(validate_departments(departments))


def query_envelope(departments: Sequence[DepartmentInput]) -> type[QueryEnvelope]:
    return _query(validate_departments(departments))


class _WireSchema(GenerateJsonSchema):
    """Pydantic's schema without the ``title`` noise, so the bytes sent stay small and stable."""

    def field_title_should_be_set(self, schema: Any) -> bool:
        return False

    def _update_class_schema(self, json_schema: Any, cls: Any, config: Any) -> None:
        super()._update_class_schema(json_schema, cls, config)
        json_schema.pop("title", None)


def wire_schema(model: type[BaseModel]) -> dict[str, Any]:
    """The flat JSON schema sent in ``format``."""
    return model.model_json_schema(schema_generator=_WireSchema)


def schema_json(schema: dict[str, Any]) -> str:
    """Canonical text of a schema: compact, accents kept, key order as generated.

    The order is not sorted on purpose: the grammar emits properties in schema order, so the
    order is behaviour and the hash must cover it.
    """
    return json.dumps(schema, ensure_ascii=False, separators=(",", ":"))


def schema_hash(schema: dict[str, Any]) -> str:
    return hashlib.sha256(schema_json(schema).encode("utf-8")).hexdigest()


def parse_envelope[T: BaseModel](model: type[T], raw: str) -> T:
    """Validate a model reply; anything off-schema is ``EnvelopeInvalid`` (a review)."""
    try:
        return model.model_validate_json(raw)
    except ValidationError as error:
        # Locations and error types only: ``input`` could echo document text.
        kinds = sorted(
            {f"{'.'.join(map(str, e['loc'])) or '-'}:{e['type']}" for e in error.errors()}
        )
        raise EnvelopeInvalid("envelope fora do schema (" + ", ".join(kinds[:8]) + ")") from None
