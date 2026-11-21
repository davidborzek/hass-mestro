"""Light platform: the display backlight; on/off maps to standby."""

from __future__ import annotations

from typing import Any

from homeassistant.components.light import ATTR_BRIGHTNESS, ColorMode, LightEntity
from homeassistant.core import HomeAssistant, callback
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.util.color import brightness_to_value, value_to_brightness

from .client import MestroConnectionError, MestroRequestError
from .const import DOMAIN
from .coordinator import MestroConfigEntry, MestroCoordinator
from .entity import MestroEntity

PARALLEL_UPDATES = 0

BRIGHTNESS_RANGE = (1, 100)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: MestroConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Add the display light once the player reports a backlight."""
    coordinator = entry.runtime_data
    added = False

    @callback
    def _add_when_supported() -> None:
        nonlocal added
        if not added and coordinator.data.display_supported:
            added = True
            async_add_entities([MestroDisplayLight(coordinator)])

    _add_when_supported()
    entry.async_on_unload(coordinator.async_add_listener(_add_when_supported))


class MestroDisplayLight(MestroEntity, LightEntity):
    """The backlight has no power of its own: off means standby."""

    _attr_translation_key = "display"
    _attr_color_mode = ColorMode.BRIGHTNESS
    _attr_supported_color_modes: set[ColorMode] | None = None

    def __init__(self, coordinator: MestroCoordinator) -> None:
        """Create the display light."""
        super().__init__(coordinator, "display")
        self._attr_supported_color_modes = {ColorMode.BRIGHTNESS}

    @property
    def is_on(self) -> bool:
        """Lit while the player is awake."""
        return not self.state_data.standby

    @property
    def brightness(self) -> int:
        """Backlight level 1..100 as 0..255."""
        return value_to_brightness(BRIGHTNESS_RANGE, self.state_data.brightness)

    async def async_turn_on(self, **kwargs: Any) -> None:
        """Wake the player and/or set the backlight level."""
        if self.state_data.standby or ATTR_BRIGHTNESS not in kwargs:
            await self.async_ctl("standby-off")
        if ATTR_BRIGHTNESS in kwargs:
            level = round(
                brightness_to_value(BRIGHTNESS_RANGE, kwargs[ATTR_BRIGHTNESS])
            )
            level = max(BRIGHTNESS_RANGE[0], min(BRIGHTNESS_RANGE[1], level))
            try:
                await self.coordinator.client.async_set_brightness(level)
            except (MestroConnectionError, MestroRequestError) as err:
                raise HomeAssistantError(
                    translation_domain=DOMAIN,
                    translation_key="brightness_failed",
                    translation_placeholders={"level": str(level), "error": str(err)},
                ) from err

    async def async_turn_off(self, **kwargs: Any) -> None:
        """Put the player on standby."""
        await self.async_ctl("standby-on")
