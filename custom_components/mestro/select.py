"""Select platform: pick the VU skin the player shows."""

from __future__ import annotations

from homeassistant.components.select import SelectEntity
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .coordinator import MestroConfigEntry, MestroCoordinator
from .entity import MestroEntity

PARALLEL_UPDATES = 0


async def async_setup_entry(
    hass: HomeAssistant,
    entry: MestroConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Add the VU skin select once the player reports its skins."""
    coordinator = entry.runtime_data
    added = False

    @callback
    def _add_when_skins_known() -> None:
        nonlocal added
        if not added and coordinator.data.vu_skins:
            added = True
            async_add_entities([MestroVuSkinSelect(coordinator)])

    _add_when_skins_known()
    entry.async_on_unload(coordinator.async_add_listener(_add_when_skins_known))


class MestroVuSkinSelect(MestroEntity, SelectEntity):
    """Options are the player's skin names in cycle order."""

    _attr_translation_key = "vu_skin"

    def __init__(self, coordinator: MestroCoordinator) -> None:
        """Create the VU skin select."""
        super().__init__(coordinator, "vu_skin")

    @property
    def options(self) -> list[str]:
        """The configured VU cycle."""
        return list(self.state_data.vu_skins)

    @property
    def current_option(self) -> str | None:
        """The active skin, `None` if the index points outside the cycle."""
        state = self.state_data
        if 0 <= state.vu_index < len(state.vu_skins):
            return state.vu_skins[state.vu_index]
        return None

    async def async_select_option(self, option: str) -> None:
        """Open the VU view on the chosen skin."""
        # HA already validated `option` against `options`
        index = self.state_data.vu_skins.index(option)
        await self.async_ctl(f"vu-select:{index}")
