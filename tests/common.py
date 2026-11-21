"""Test helpers shared across modules."""

from __future__ import annotations

import asyncio
from collections.abc import Callable
import copy

from homeassistant.const import CONF_HOST, CONF_PORT
from homeassistant.core import HomeAssistant
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.mestro.client import MestroState
from custom_components.mestro.const import CONF_TOKEN, DOMAIN

from .fake_mestro import PLAYER_ID, PLAYER_NAME, FakeMestro, token_for

POLL_LIMIT = 2.0


async def eventually(predicate: Callable[[], object]) -> None:
    """Poll until the predicate holds or the limit passes."""
    async with asyncio.timeout(POLL_LIMIT):
        while not predicate():  # noqa: ASYNC110
            await asyncio.sleep(0.005)


async def setup_integration(hass: HomeAssistant, server: FakeMestro) -> MockConfigEntry:
    """Add a configured entry for the fake player and set it up."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        unique_id=PLAYER_ID,
        title=PLAYER_NAME,
        data={
            CONF_HOST: server.host,
            CONF_PORT: server.port,
            CONF_TOKEN: token_for(server.pin),
        },
    )
    entry.add_to_hass(hass)
    await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    return entry


async def push(hass: HomeAssistant, server: FakeMestro) -> None:
    """Push the fake's state and wait until HA has taken it in."""
    expected = copy.deepcopy(server.state)
    await server.push_state()
    coordinator = hass.config_entries.async_entries(DOMAIN)[0].runtime_data
    await eventually(
        lambda: coordinator.client.state == MestroState.from_wire(expected)
    )
    await hass.async_block_till_done()
