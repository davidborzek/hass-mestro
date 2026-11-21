"""Shared fixtures: the fake mestro and a config entry pointing at it."""

from __future__ import annotations

from collections.abc import AsyncGenerator, Generator
from unittest.mock import patch

import pytest

from .fake_mestro import FakeMestro


@pytest.fixture(autouse=True)
def _enable_custom_integrations(enable_custom_integrations: None) -> None:
    """Let HA load custom_components/mestro."""


@pytest.fixture(autouse=True)
def _fast_reconnect() -> Generator[None]:
    """Collapse the reconnect backoff so tests do not wait."""
    with patch("custom_components.mestro.client.RECONNECT_BACKOFF", (0.01,)):
        yield


@pytest.fixture
async def fake_mestro(socket_enabled: None) -> AsyncGenerator[FakeMestro]:
    """A running fake player on a loopback port (real sockets)."""
    server = FakeMestro()
    await server.start()
    yield server
    await server.stop()
