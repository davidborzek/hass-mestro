"""Holds the latest pushed state of one player and fans it out to entities."""

from __future__ import annotations

import logging

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator

from .client import MestroClient, MestroHello, MestroState
from .const import DOMAIN

_LOGGER = logging.getLogger(__name__)

type MestroConfigEntry = ConfigEntry[MestroCoordinator]


class MestroCoordinator(DataUpdateCoordinator[MestroState]):
    """Push-driven: no polling, the client's callbacks feed the data."""

    config_entry: MestroConfigEntry

    def __init__(
        self,
        hass: HomeAssistant,
        entry: MestroConfigEntry,
        client: MestroClient,
        hello: MestroHello,
    ) -> None:
        """Wire the client's callbacks; the caller starts the client."""
        super().__init__(hass, _LOGGER, config_entry=entry, name=DOMAIN)
        self.client = client
        self.hello = hello
        client.set_callbacks(
            on_state=self._handle_state,
            on_disconnected=self.async_update_listeners,
            on_auth_failed=self._handle_auth_failed,
        )

    @property
    def available(self) -> bool:
        """Entities are available while the WebSocket is up."""
        return self.client.connected

    def _handle_state(self, state: MestroState) -> None:
        self.async_set_updated_data(state)

    def _handle_auth_failed(self) -> None:
        self.async_update_listeners()
        self.config_entry.async_start_reauth(self.hass)

    async def async_shutdown(self) -> None:
        """Stop the client together with the coordinator."""
        await super().async_shutdown()
        await self.client.async_stop()
