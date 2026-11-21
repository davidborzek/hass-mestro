"""Base entity: one device per player, availability from the WebSocket."""

from __future__ import annotations

from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .client import MestroConnectionError, MestroRequestError, MestroState
from .const import DOMAIN
from .coordinator import MestroCoordinator


class MestroEntity(CoordinatorEntity[MestroCoordinator]):
    """Common device info and push-driven availability."""

    _attr_has_entity_name = True

    def __init__(self, coordinator: MestroCoordinator, key: str) -> None:
        """Attach to the player's device."""
        super().__init__(coordinator)
        hello = coordinator.hello
        self._attr_unique_id = f"{hello.id}_{key}"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, hello.id)},
            name=hello.name or hello.id,
            manufacturer="mestro",
            model="mestro player",
            sw_version=hello.version or None,
            configuration_url=coordinator.client.base_url,
        )

    @property
    def available(self) -> bool:
        """Available while the WebSocket is connected."""
        return self.coordinator.available

    @property
    def state_data(self) -> MestroState:
        """The latest pushed state."""
        return self.coordinator.data

    async def async_ctl(self, command: str) -> None:
        """Run a `ctl` command; failures surface as a service-call error."""
        try:
            await self.coordinator.client.async_ctl(command)
        except (MestroConnectionError, MestroRequestError) as err:
            raise HomeAssistantError(
                translation_domain=DOMAIN,
                translation_key="command_failed",
                translation_placeholders={"command": command, "error": str(err)},
            ) from err
