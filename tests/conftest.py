from __future__ import annotations

import os
import socket
from collections.abc import Iterator

import pytest

from osa.core.settings import Settings


@pytest.fixture(autouse=True)
def isolate_osa_environment(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """Prevent OSA settings and network access from leaking between tests."""

    for name in tuple(os.environ):
        if name.startswith("OSA_"):
            monkeypatch.delenv(name, raising=False)
    monkeypatch.setitem(Settings.model_config, "env_file", None)

    def reject_network(*args: object, **kwargs: object) -> None:
        del args, kwargs
        raise AssertionError("network access is disabled in portable tests")

    monkeypatch.setattr(socket, "create_connection", reject_network)
    monkeypatch.setattr(socket.socket, "connect", reject_network)
    yield
