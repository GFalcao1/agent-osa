"""Envelopes (spec §8): the department enum is built per call from the template."""

from __future__ import annotations

import json
import subprocess
import sys
import unicodedata
from datetime import date
from typing import Any

import pytest

from osa.llm.envelopes import (
    ClassificationEnvelope,
    classification_envelope,
    parse_envelope,
    query_envelope,
    schema_hash,
    schema_json,
    validate_departments,
    wire_schema,
)
from osa.llm.provider import EnvelopeInvalid, InvalidDepartments

DEPARTMENTS = ("Fiscal", "Financeiro", "Jurídico", "Recursos Humanos", "Contábil")

ALLOWED_KEYWORDS = {
    "type",
    "properties",
    "required",
    "additionalProperties",
    "enum",
    "const",
    "anyOf",
    "items",
    "maxItems",
    "minItems",
    "maxLength",
    "minLength",
    "minimum",
    "maximum",
    "format",
}


def valid_reply(**override: Any) -> str:
    reply: dict[str, Any] = {
        "departamento": "Fiscal",
        "departamento_alternativo": None,
        "tipo_documental": "nota fiscal de serviço",
        "tags": ["nota", "serviço"],
        "resumo": "Nota fiscal fictícia.",
        "datas": ["2026-01-02"],
        "ano_referencia": 2026,
        "precisa_revisao": False,
        "motivo_revisao": None,
    }
    reply.update(override)
    return json.dumps(reply)


def department_values(schema: dict[str, Any]) -> list[str]:
    prop = schema["properties"]["departamento"]
    return list(prop["enum"]) if "enum" in prop else [prop["const"]]


def test_schema_enum_is_exactly_active_departments() -> None:
    schema = wire_schema(classification_envelope(DEPARTMENTS))

    assert department_values(schema) == list(DEPARTMENTS)  # accents, spaces and order kept


def test_single_department_uses_const() -> None:
    schema = wire_schema(classification_envelope(["Fiscal"]))

    assert department_values(schema) == ["Fiscal"]


def test_schema_changes_with_template() -> None:
    first = classification_envelope(DEPARTMENTS)
    second = classification_envelope((*DEPARTMENTS, "Operações"))

    assert first is not second
    assert department_values(wire_schema(second))[-1] == "Operações"
    assert schema_hash(wire_schema(first)) != schema_hash(wire_schema(second))
    with pytest.raises(EnvelopeInvalid):
        parse_envelope(first, valid_reply(departamento="Operações"))
    assert parse_envelope(second, valid_reply(departamento="Operações")).departamento == "Operações"


def test_same_departments_give_same_class_and_hash() -> None:
    assert classification_envelope(DEPARTMENTS) is classification_envelope(list(DEPARTMENTS))
    assert schema_hash(wire_schema(classification_envelope(DEPARTMENTS))) == schema_hash(
        wire_schema(classification_envelope(list(DEPARTMENTS)))
    )


def test_no_module_level_department_literal() -> None:
    field = ClassificationEnvelope.model_fields["departamento"]

    assert field.annotation is str  # the static base has no enum: a stale one cannot exist


@pytest.mark.parametrize(
    "name",
    ["Comercial", "fiscal", "Fiscal ", "", "Juridico", unicodedata.normalize("NFD", "Jurídico")],
)
def test_unknown_department_rejected(name: str) -> None:
    with pytest.raises(EnvelopeInvalid):
        parse_envelope(classification_envelope(DEPARTMENTS), valid_reply(departamento=name))


def test_valid_reply_parses_into_the_dynamic_subclass() -> None:
    envelope = parse_envelope(classification_envelope(DEPARTMENTS), valid_reply())

    assert isinstance(envelope, ClassificationEnvelope)
    assert envelope.departamento == "Fiscal"
    assert envelope.ano_referencia == 2026
    assert envelope.datas[0].isoformat() == "2026-01-02"


@pytest.mark.parametrize(
    "override",
    [
        {"tags": ["a"] * 11},
        {"tags": [""]},
        {"tags": ["x" * 41]},
        {"resumo": "x" * 301},
        {"tipo_documental": ""},
        {"tipo_documental": "x" * 81},
        {"datas": ["2026-02-31"]},
        {"datas": ["2026-01-02T00:00:00"]},
        {"datas": [20260102]},
        {"datas": ["2026-01-01"] * 21},
        {"ano_referencia": "2026"},
        {"ano_referencia": 2026.0},
        {"ano_referencia": True},
        {"ano_referencia": 1899},
        {"ano_referencia": 2101},
        {"precisa_revisao": "false"},
        {"departamento_alternativo": "Operações"},  # outside the enum
        {"departamento_alternativo": ""},
        {"departamento_alternativo": ["Fiscal"]},
        {"motivo_revisao": "x" * 201},
        {"extra": 1},
    ],
)
def test_envelope_limits_and_types(override: dict[str, Any]) -> None:
    with pytest.raises(EnvelopeInvalid):
        parse_envelope(classification_envelope(DEPARTMENTS), valid_reply(**override))


@pytest.mark.parametrize(
    "override",
    [{"tags": ["a"] * 10}, {"resumo": "x" * 300}, {"ano_referencia": None}, {"datas": []}],
)
def test_envelope_limits_accept_the_boundary(override: dict[str, Any]) -> None:
    parse_envelope(classification_envelope(DEPARTMENTS), valid_reply(**override))


@pytest.mark.parametrize(
    "missing",
    ["departamento", "departamento_alternativo", "tags", "ano_referencia", "motivo_revisao"],
)
def test_every_field_is_required_even_when_nullable(missing: str) -> None:
    data = json.loads(valid_reply())
    del data[missing]

    with pytest.raises(EnvelopeInvalid):
        parse_envelope(classification_envelope(DEPARTMENTS), json.dumps(data))


def test_review_reason_is_normalised() -> None:
    model = classification_envelope(DEPARTMENTS)

    clean = parse_envelope(model, valid_reply(precisa_revisao=False, motivo_revisao="nenhum"))
    assert clean.motivo_revisao is None
    blank = parse_envelope(model, valid_reply(precisa_revisao=True, motivo_revisao=None))
    assert blank.motivo_revisao == "nao_informado"
    kept = parse_envelope(
        model, valid_reply(precisa_revisao=True, motivo_revisao="instrucao_embutida")
    )
    assert kept.motivo_revisao == "instrucao_embutida"


@pytest.mark.parametrize(
    "raw", ["", "not json", "[]", "null", '{"departamento":', valid_reply() + "\nobrigado"]
)
def test_malformed_or_trailing_text_is_envelope_invalid(raw: str) -> None:
    with pytest.raises(EnvelopeInvalid):
        parse_envelope(classification_envelope(DEPARTMENTS), raw)


def test_invalid_error_never_echoes_the_reply() -> None:
    with pytest.raises(EnvelopeInvalid) as error:
        parse_envelope(
            classification_envelope(DEPARTMENTS),
            valid_reply(departamento="SENTINELA-DO-DOCUMENTO", resumo="x" * 301),
        )

    assert "SENTINELA" not in str(error.value)


def test_query_envelope_all_null_is_valid() -> None:
    model = query_envelope(DEPARTMENTS)
    raw = json.dumps(
        {
            "dono": None,
            "departamento": None,
            "tipo_documental": None,
            "periodo_inicio": None,
            "periodo_fim": None,
            "palavras_chave": [],
        }
    )

    envelope = parse_envelope(model, raw)

    assert envelope.departamento is None and envelope.palavras_chave == []


def test_query_envelope_enum_and_period() -> None:
    model = query_envelope(DEPARTMENTS)
    base = {
        "dono": "Alfa Comércio",
        "departamento": "Fiscal",
        "tipo_documental": "contrato",
        "periodo_inicio": "2025-01-01",
        "periodo_fim": "2025-12-31",
        "palavras_chave": ["aluguel"],
    }
    assert parse_envelope(model, json.dumps(base)).departamento == "Fiscal"
    for bad in (
        {"departamento": "Comercial"},
        {"periodo_inicio": "2026-01-01", "periodo_fim": "2025-01-01"},
        {"palavras_chave": ["a"] * 11},
        {"dono": "x" * 121},
    ):
        with pytest.raises(EnvelopeInvalid):
            parse_envelope(model, json.dumps({**base, **bad}))
    branches = wire_schema(model)["properties"]["departamento"]["anyOf"]
    assert department_values({"properties": {"departamento": branches[0]}}) == list(DEPARTMENTS)
    assert {"type": "null"} in branches


def test_wire_schema_is_flat_and_keywords_are_whitelisted() -> None:
    def walk(node: Any, path: str = "") -> None:
        if isinstance(node, dict):
            for key, value in node.items():
                if key in ("properties", "$defs"):
                    assert key == "properties", f"{path}: $defs in wire schema"
                    for prop, sub in value.items():
                        walk(sub, f"{path}.{prop}")
                    continue
                assert key in ALLOWED_KEYWORDS, f"{path}: keyword {key!r} not allowed"
                walk(value, f"{path}.{key}")
        elif isinstance(node, list):
            for index, item in enumerate(node):
                walk(item, f"{path}[{index}]")

    for model in (classification_envelope(DEPARTMENTS), query_envelope(DEPARTMENTS)):
        schema = wire_schema(model)
        walk(schema)
        assert "$ref" not in json.dumps(schema)
        assert schema["additionalProperties"] is False
        assert set(schema["required"]) == set(schema["properties"])  # nullable but never optional


def test_schema_properties_follow_the_spec_order() -> None:
    schema = wire_schema(classification_envelope(DEPARTMENTS))

    assert list(schema["properties"]) == [
        "departamento",
        "departamento_alternativo",
        "tipo_documental",
        "tags",
        "resumo",
        "datas",
        "ano_referencia",
        "precisa_revisao",
        "motivo_revisao",
    ]


def test_schema_hash_is_stable_across_processes() -> None:
    code = (
        "from osa.llm.envelopes import classification_envelope, schema_hash, wire_schema;"
        f"print(schema_hash(wire_schema(classification_envelope({list(DEPARTMENTS)!r}))))"
    )
    out = [
        subprocess.run(
            [sys.executable, "-I", "-c", code], capture_output=True, text=True, check=True
        ).stdout.strip()
        for _ in range(2)
    ]
    here = schema_hash(wire_schema(classification_envelope(DEPARTMENTS)))

    assert out[0] == out[1] == here
    assert len(here) == 64


def test_schema_json_keeps_accents_and_order() -> None:
    text = schema_json(wire_schema(classification_envelope(["Jurídico", "Fiscal"])))

    assert "Jurídico" in text  # ensure_ascii off: the bytes sent are the bytes hashed
    assert text.index("Jurídico") < text.index("Fiscal")
    assert ", " not in text and ": " not in text


@pytest.mark.parametrize(
    "bad",
    [
        [],
        [""],
        ["  "],
        ["Fiscal", "fiscal"],
        ["Fiscal", "FISCAL "],
        ["Jurídico", "Juridico"],
        ["a\nb"],
        ["a\x00b"],
        ["x" * 101],
        ["a\u200bb"],
        [1],
    ],
)
def test_invalid_departments_are_refused(bad: list[Any]) -> None:
    with pytest.raises(InvalidDepartments):
        validate_departments(bad)


def test_departments_are_normalised_to_nfc() -> None:
    decomposed = unicodedata.normalize("NFD", "Jurídico")

    assert validate_departments([decomposed, " Fiscal "]) == ("Jurídico", "Fiscal")


# --- departments with an optional description (TASK-106, decision of 2026-10-26) ---------


def test_department_is_a_small_immutable_name_and_description() -> None:
    import dataclasses

    from osa.llm.envelopes import Department

    fiscal = Department("Fiscal", "tributos e guias de recolhimento")

    assert (fiscal.nome, fiscal.descricao) == ("Fiscal", "tributos e guias de recolhimento")
    assert Department("Fiscal").descricao is None
    with pytest.raises(dataclasses.FrozenInstanceError):
        fiscal.nome = "Outro"  # type: ignore[misc]


def test_descriptions_never_enter_the_enum_or_the_schema() -> None:
    from osa.llm.envelopes import Department

    described = [Department("Fiscal", "tributos"), Department("Financeiro"), "Jurídico"]

    schema = wire_schema(classification_envelope(described))

    assert department_values(schema) == ["Fiscal", "Financeiro", "Jurídico"]
    assert "tributos" not in schema_json(schema)
    assert schema_hash(schema) == schema_hash(
        wire_schema(classification_envelope(["Fiscal", "Financeiro", "Jurídico"]))
    )
    assert validate_departments(described) == ("Fiscal", "Financeiro", "Jurídico")


def test_department_specs_keep_descriptions_and_refuse_bad_ones() -> None:
    from osa.llm.envelopes import MAX_DESCRIPTION_LEN, Department, department_specs

    specs = department_specs([Department(" Fiscal ", "  tributos  "), "Financeiro"])

    assert specs == (Department("Fiscal", "tributos"), Department("Financeiro", None))
    assert department_specs([Department("Fiscal", "   ")]) == (Department("Fiscal", None),)
    from osa.llm.provider import DepartmentDescriptionRejected

    assert MAX_DESCRIPTION_LEN == 160
    with pytest.raises(DepartmentDescriptionRejected):  # also an InvalidDepartments
        department_specs([Department("Fiscal", "x" * (MAX_DESCRIPTION_LEN + 1))])
    with pytest.raises(InvalidDepartments):
        department_specs([Department("Fiscal", 3)])  # type: ignore[arg-type]


# --- departamento_alternativo: a second plausible department forces review -----------------

# The wire schema for DEPARTMENTS before the field existed (prompt v3): the change is visible.
SCHEMA_HASH_BEFORE_ALTERNATIVE = "ef8d426d62984997e216190d1d2d3300d5e6399e033c22b444755048cb925594"
SCHEMA_HASH_PINNED = "398f6d2e5e751a3d3f74f5ab48c5211d1af449b53ed9adc892eeb1707eb7bddf"


def test_alternative_is_required_nullable_and_takes_the_same_enum() -> None:
    schema = wire_schema(classification_envelope(DEPARTMENTS))

    assert "departamento_alternativo" in schema["required"]
    assert schema["properties"]["departamento_alternativo"] == {
        "anyOf": [{"enum": list(DEPARTMENTS), "type": "string"}, {"type": "null"}]
    }
    single = wire_schema(classification_envelope(["Fiscal"]))
    assert single["properties"]["departamento_alternativo"] == {
        "anyOf": [{"const": "Fiscal", "type": "string"}, {"type": "null"}]
    }


def test_schema_hash_changed_with_the_alternative_field() -> None:
    here = schema_hash(wire_schema(classification_envelope(DEPARTMENTS)))

    assert here == SCHEMA_HASH_PINNED
    assert here != SCHEMA_HASH_BEFORE_ALTERNATIVE


def test_alternative_equal_to_department_is_dropped_without_review() -> None:
    model = classification_envelope(DEPARTMENTS)

    same = parse_envelope(model, valid_reply(departamento_alternativo="Fiscal"))

    assert same.departamento_alternativo is None
    assert (same.precisa_revisao, same.motivo_revisao) == (False, None)


@pytest.mark.parametrize(
    ("review", "reason"),
    [
        (False, None),
        (False, "nenhum"),  # filler next to false is not a reason
        (False, "instrucao_embutida"),
        (True, None),
        (True, ""),
        (True, "  "),
        (True, "nao_informado"),
    ],
)
def test_alternative_forces_review_with_ambiguous_departments(
    review: bool, reason: str | None
) -> None:
    from osa.llm import prompts

    envelope = parse_envelope(
        classification_envelope(DEPARTMENTS),
        valid_reply(
            departamento_alternativo="Financeiro", precisa_revisao=review, motivo_revisao=reason
        ),
    )

    assert envelope.departamento_alternativo == "Financeiro"
    assert envelope.precisa_revisao is True
    assert envelope.motivo_revisao == "departamentos_ambiguos"
    assert prompts.MOTIVO_DEPARTAMENTOS_AMBIGUOS == "departamentos_ambiguos"


def test_alternative_keeps_a_more_specific_reason_from_the_model() -> None:
    envelope = parse_envelope(
        classification_envelope(DEPARTMENTS),
        valid_reply(
            departamento_alternativo="Recursos Humanos",
            precisa_revisao=True,
            motivo_revisao="instrucao_embutida",
        ),
    )

    assert (envelope.precisa_revisao, envelope.motivo_revisao) == (True, "instrucao_embutida")


def test_null_alternative_keeps_the_pair_rules() -> None:
    model = classification_envelope(DEPARTMENTS)

    clean = parse_envelope(model, valid_reply(motivo_revisao="nenhum"))
    assert (clean.departamento_alternativo, clean.motivo_revisao) == (None, None)
    doubt = parse_envelope(model, valid_reply(precisa_revisao=True, motivo_revisao=None))
    assert (doubt.precisa_revisao, doubt.motivo_revisao) == (True, "nao_informado")


# --- the repair holds on every construction path -------------------------------------------

BuildPath = str  # "init", "dict" or "json"


def build(path: BuildPath, **override: Any) -> ClassificationEnvelope:
    model = classification_envelope(DEPARTMENTS)
    raw = valid_reply(**override)
    if path == "init":
        data = json.loads(raw)
        data["datas"] = [date.fromisoformat(d) for d in data["datas"]]  # strict: Python types
        return model(**data)
    if path == "dict":
        data = json.loads(raw)
        data["datas"] = [date.fromisoformat(d) for d in data["datas"]]
        return model.model_validate(data)
    return parse_envelope(model, raw)


PATHS = ["init", "dict", "json"]


@pytest.mark.filterwarnings("error")  # pydantic warns when a validator returns another instance
@pytest.mark.parametrize("path", PATHS)
@pytest.mark.parametrize(
    ("override", "expected"),
    [
        ({"departamento_alternativo": "Fiscal"}, (None, False, None)),
        (
            {"departamento_alternativo": "Financeiro"},
            ("Financeiro", True, "departamentos_ambiguos"),
        ),
        (
            {"departamento_alternativo": "Financeiro", "motivo_revisao": "nenhum"},
            ("Financeiro", True, "departamentos_ambiguos"),
        ),
        ({"precisa_revisao": False, "motivo_revisao": "nenhum"}, (None, False, None)),
        ({"precisa_revisao": True, "motivo_revisao": None}, (None, True, "nao_informado")),
        ({"precisa_revisao": True, "motivo_revisao": " "}, (None, True, "nao_informado")),
    ],
)
def test_repair_is_the_same_via_init_dict_and_json(
    path: BuildPath, override: dict[str, Any], expected: tuple[Any, ...]
) -> None:
    envelope = build(path, **override)

    got = (envelope.departamento_alternativo, envelope.precisa_revisao, envelope.motivo_revisao)
    assert got == expected


@pytest.mark.parametrize("path", PATHS)
def test_repaired_envelope_is_still_frozen_and_revalidates_to_itself(path: BuildPath) -> None:
    import pydantic

    envelope = build(path, departamento_alternativo="Financeiro")

    with pytest.raises(pydantic.ValidationError):
        envelope.precisa_revisao = False  # type: ignore[misc]
    assert type(envelope).model_validate_json(envelope.model_dump_json()) == envelope
