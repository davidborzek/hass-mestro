"""Remote platform: player power, views and any `ctl` command."""

from __future__ import annotations

import asyncio
from collections.abc import Iterable
from typing import Any

from homeassistant.components.remote import (
    ATTR_ACTIVITY,
    ATTR_DELAY_SECS,
    ATTR_NUM_REPEATS,
    DEFAULT_DELAY_SECS,
    DEFAULT_NUM_REPEATS,
    RemoteEntity,
    RemoteEntityFeature,
)
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ServiceValidationError
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .const import DOMAIN
from .coordinator import MestroConfigEntry, MestroCoordinator
from .entity import MestroEntity, open_view

PARALLEL_UPDATES = 0

# activity name -> the view it opens
ACTIVITIES: dict[str, str] = {
    "Now playing": "now_playing",
    "Queue": "queue",
    "VU": "vu",
    "Home": "home",
}
ACTIVITY_OF_VIEW = {view: activity for activity, view in ACTIVITIES.items()}


async def async_setup_entry(
    hass: HomeAssistant,
    entry: MestroConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Add the player's remote."""
    async_add_entities([MestroRemote(entry.runtime_data)])


class MestroRemote(MestroEntity, RemoteEntity):
    """On = awake, off = standby; the views are activities."""

    _attr_name = None
    _attr_supported_features = RemoteEntityFeature.ACTIVITY
    _attr_activity_list: list[str] | None = None

    def __init__(self, coordinator: MestroCoordinator) -> None:
        """Create the remote entity."""
        super().__init__(coordinator, "remote")
        self._attr_activity_list = list(ACTIVITIES)

    @property
    def is_on(self) -> bool:
        """Awake unless the standby face is up."""
        return not self.state_data.standby

    @property
    def current_activity(self) -> str | None:
        """The open view, `None` in standby."""
        view = open_view(self.state_data)
        return ACTIVITY_OF_VIEW[view] if view else None

    async def async_turn_on(self, **kwargs: Any) -> None:
        """Wake the player, optionally straight into an activity."""
        activity = kwargs.get(ATTR_ACTIVITY)
        if activity is not None and activity not in ACTIVITIES:
            raise ServiceValidationError(
                translation_domain=DOMAIN,
                translation_key="unknown_activity",
                translation_placeholders={"activity": str(activity)},
            )
        if activity is None:
            await self.async_ctl("standby-off")
        else:
            await self.async_open_view(ACTIVITIES[activity])

    async def async_turn_off(self, **kwargs: Any) -> None:
        """Put the player on standby."""
        await self.async_ctl("standby-on")

    async def async_send_command(self, command: Iterable[str], **kwargs: Any) -> None:
        """Send `ctl` commands, `num_repeats` times with `delay_secs` between."""
        commands = list(command)
        repeats = kwargs.get(ATTR_NUM_REPEATS, DEFAULT_NUM_REPEATS)
        delay = kwargs.get(ATTR_DELAY_SECS, DEFAULT_DELAY_SECS)
        sequence = commands * repeats
        for position, cmd in enumerate(sequence):
            if position:
                await asyncio.sleep(delay)
            await self.async_ctl(cmd)
