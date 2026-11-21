"""Base entity: one device per player, availability from the WebSocket."""

from __future__ import annotations

from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .client import MestroConnectionError, MestroRequestError, MestroState
from .const import DOMAIN
from .coordinator import MestroCoordinator

# view key -> the `ctl` command that opens it (keys double as the view
# select's translated options)
VIEW_COMMANDS: dict[str, str] = {
    "now_playing": "now-playing",
    "queue": "queue",
    "vu": "vu",
    "home": "home",
}


def open_view(state: MestroState) -> str | None:
    """The open view key, `None` in standby; the layers stack, so the topmost wins."""
    if state.standby:
        return None
    if state.vu:
        return "vu"
    if state.queue:
        return "queue"
    if state.now_playing:
        return "now_playing"
    return "home"


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

    async def async_open_view(self, view: str) -> None:
        """Wake the player if needed, then open `view` (a VIEW_COMMANDS key)."""
        if self.state_data.standby:
            await self.async_ctl("standby-off")
        await self.async_ctl(VIEW_COMMANDS[view])

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
