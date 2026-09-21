from __future__ import annotations

from dataclasses import FrozenInstanceError
from uuid import UUID

import pytest

from osa.domain.collection import CollectionRef
from osa.domain.document import Category, DocumentType

COLLECTION_ID = UUID("00000000-0000-4000-8000-000000000001")
OTHER_ID = UUID("00000000-0000-4000-8000-000000000002")


@pytest.mark.parametrize("name", ["Arquivo pessoal", "Paciente fictício Alfa", "Projeto Aurora"])
def test_st002_1_collections_need_no_external_identifier(name: str) -> None:
    collection = CollectionRef(collection_id=COLLECTION_ID, name=name)
    assert collection.collection_id == COLLECTION_ID
    assert collection.name == name


def test_st002_1_same_name_does_not_merge_collections() -> None:
    first = CollectionRef(collection_id=COLLECTION_ID, name="Arquivo pessoal")
    second = CollectionRef(collection_id=OTHER_ID, name="Arquivo pessoal")
    assert first != second


@pytest.mark.parametrize("name", ["", " \t\n", None, 123])
def test_st002_1_rejects_invalid_names(name: object) -> None:
    with pytest.raises(ValueError, match="name"):
        CollectionRef(collection_id=COLLECTION_ID, name=name)


@pytest.mark.parametrize("identifier", ["not-a-uuid", str(COLLECTION_ID), None, 123])
def test_st002_1_requires_uuid_value(identifier: object) -> None:
    with pytest.raises(ValueError, match="collection_id"):
        CollectionRef(collection_id=identifier, name="Projeto Aurora")


def test_st002_1_collection_identity_is_immutable() -> None:
    collection = CollectionRef(collection_id=COLLECTION_ID, name="Arquivo pessoal")
    with pytest.raises(FrozenInstanceError):
        collection.collection_id = OTHER_ID


@pytest.mark.parametrize("value_type", [Category, DocumentType])
@pytest.mark.parametrize("name", ["Saúde", "Fotografias", "Estudos", "Relatório de projeto"])
def test_st002_2_vocabulary_is_not_restricted_to_one_sector(value_type: type, name: str) -> None:
    value = value_type(id=OTHER_ID, collection_id=COLLECTION_ID, name=name)
    assert value.name == name
    assert value.collection_id == COLLECTION_ID


@pytest.mark.parametrize("value_type", [Category, DocumentType])
@pytest.mark.parametrize(
    "invalid", [{"id": "bad"}, {"collection_id": "bad"}, {"name": " "}, {"name": None}]
)
def test_st002_2_vocabulary_validates_public_constructor(value_type: type, invalid: dict) -> None:
    fields = {"id": OTHER_ID, "collection_id": COLLECTION_ID, "name": "Saúde"}
    fields.update(invalid)
    with pytest.raises(ValueError):
        value_type(**fields)


@pytest.mark.parametrize("value_type", [Category, DocumentType])
def test_st002_2_vocabulary_is_immutable(value_type: type) -> None:
    value = value_type(id=OTHER_ID, collection_id=COLLECTION_ID, name="Saúde")
    with pytest.raises(FrozenInstanceError):
        value.collection_id = OTHER_ID
