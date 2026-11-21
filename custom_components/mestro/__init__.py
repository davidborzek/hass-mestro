"""The mestro integration: control a mestro player from Home Assistant."""

from __future__ import annotations

from aiohttp import DummyCookieJar
from homeassistant.const import CONF_HOST, CONF_PORT, Platform
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed, ConfigEntryNotReady
from homeassistant.helpers.aiohttp_client import async_create_clientsession

from .client import (
    MestroAuthError,
    MestroClient,
    MestroConnectionError,
)
from .const import CONF_TOKEN
from .coordinator import MestroConfigEntry, MestroCoordinator

PLATFORMS: list[Platform] = [Platform.REMOTE]


async def async_setup_entry(hass: HomeAssistant, entry: MestroConfigEntry) -> bool:
    """Connect to the player and set up its entities."""
    # own session: the pairing cookie must never leak into HA's shared one
    session = async_create_clientsession(hass, cookie_jar=DummyCookieJar())
    client = MestroClient(
        session,
        entry.data[CONF_HOST],
        entry.data[CONF_PORT],
        entry.data[CONF_TOKEN],
    )
    try:
        hello = await client.async_connect()
    except MestroAuthError as err:
        raise ConfigEntryAuthFailed("The player rejected the pairing") from err
    except MestroConnectionError as err:
        raise ConfigEntryNotReady(f"Cannot reach the player: {err}") from err

    coordinator = MestroCoordinator(hass, entry, client, hello)
    assert client.state is not None
    coordinator.async_set_updated_data(client.state)
    entry.runtime_data = coordinator
    entry.async_on_unload(coordinator.async_shutdown)
    client.async_start()

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: MestroConfigEntry) -> bool:
    """Unload the platforms and close the connection."""
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
