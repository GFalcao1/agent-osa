"""Fixtures for the extraction tests: the generated fictitious documents."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from types import ModuleType

import pytest

FIXTURES_SCRIPT = Path(__file__).resolve().parents[1] / "fixtures" / "make_fixtures.py"


@pytest.fixture(scope="session")
def fixture_maker() -> ModuleType:
    """The ``make_fixtures`` module (tests/fixtures is not a package)."""
    spec = importlib.util.spec_from_file_location("osa_make_fixtures", FIXTURES_SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules["osa_make_fixtures"] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="session")
def docs(tmp_path_factory: pytest.TempPathFactory, fixture_maker: ModuleType) -> dict[str, Path]:
    """Every fixture file, generated once per session into a temporary directory."""
    built: dict[str, Path] = fixture_maker.build(tmp_path_factory.mktemp("docs"))
    return built
