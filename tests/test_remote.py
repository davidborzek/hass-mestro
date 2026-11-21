"""The remote platform against the fake mestro."""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, patch

from homeassistant.components.remote import (
    ATTR_ACTIVITY,
    ATTR_ACTIVITY_LIST,
    ATTR_CURRENT_ACTIVITY,
    ATTR_DELAY_SECS,
    ATTR_NUM_REPEATS,
    DOMAIN as REMOTE_DOMAIN,
    SERVICE_SEND_COMMAND,
)
from homeassistant.const import (
    ATTR_COMMAND,
    ATTR_ENTITY_ID,
    SERVICE_TURN_OFF,
    SERVICE_TURN_ON,
    STATE_OFF,
    STATE_ON,
    STATE_UNAVAILABLE,
)
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError
from homeassistant.helpers import device_registry as dr, entity_registry as er
import pytest

from .common import eventually, push, setup_integration
from .fake_mestro import PLAYER_ID, FakeMestro

REMOTE = "remote.wohnzimmer"


@pytest.fixture(autouse=True)
def _real_sockets(socket_enabled: None) -> None:
    """The integration talks to the fake mestro over loopback."""


async def call(hass: HomeAssistant, service: str, **data: object) -> None:
    """Call a remote service on the entity."""
    await hass.services.async_call(
        REMOTE_DOMAIN, service, {ATTR_ENTITY_ID: REMOTE, **data}, blocking=True
    )


async def test_device_and_entity_registry(
    hass: HomeAssistant, fake_mestro: FakeMestro
) -> None:
    entry = await setup_integration(hass, fake_mestro)

    device = dr.async_get(hass).async_get_device_by_identifier(
        ("mestro", PLAYER_ID), entry.entry_id
    )
    assert device is not None
    assert device.name == "wohnzimmer"
    assert device.sw_version == "0.1.0 (abc1234)"
    registered = er.async_get(hass).async_get(REMOTE)
    assert registered is not None
    assert registered.unique_id == f"{PLAYER_ID}_remote"


async def test_on_off_follows_standby(
    hass: HomeAssistant, fake_mestro: FakeMestro
) -> None:
    await setup_integration(hass, fake_mestro)
    assert hass.states.get(REMOTE).state == STATE_ON

    fake_mestro.state["view"]["standby"] = True
    await push(hass, fake_mestro)
    assert hass.states.get(REMOTE).state == STATE_OFF
    assert hass.states.get(REMOTE).attributes[ATTR_CURRENT_ACTIVITY] is None

    fake_mestro.state["view"]["standby"] = False
    await push(hass, fake_mestro)
    assert hass.states.get(REMOTE).state == STATE_ON


@pytest.mark.parametrize(
    ("view", "activity"),
    [
        ({}, "Home"),
        ({"now_playing": True}, "Now playing"),
        ({"now_playing": True, "queue": True}, "Queue"),
        ({"now_playing": True, "queue": True, "vu": True}, "VU"),
        ({"now_playing": True, "vu": True}, "VU"),
    ],
)
async def test_current_activity_from_the_view(
    hass: HomeAssistant, fake_mestro: FakeMestro, view: dict, activity: str
) -> None:
    fake_mestro.state["view"].update(view)
    await setup_integration(hass, fake_mestro)

    attributes = hass.states.get(REMOTE).attributes
    assert attributes[ATTR_ACTIVITY_LIST] == ["Now playing", "Queue", "VU", "Home"]
    assert attributes[ATTR_CURRENT_ACTIVITY] == activity


async def test_turn_off_and_on_send_standby(
    hass: HomeAssistant, fake_mestro: FakeMestro
) -> None:
    await setup_integration(hass, fake_mestro)

    await call(hass, SERVICE_TURN_OFF)
    await call(hass, SERVICE_TURN_ON)
    assert fake_mestro.ctl_commands == ["standby-on", "standby-off"]


@pytest.mark.parametrize(
    ("activity", "command"),
    [
        ("Now playing", "now-playing"),
        ("Queue", "queue"),
        ("VU", "vu"),
        ("Home", "home"),
    ],
)
async def test_turn_on_with_an_activity_opens_it(
    hass: HomeAssistant, fake_mestro: FakeMestro, activity: str, command: str
) -> None:
    fake_mestro.state["view"]["standby"] = True
    await setup_integration(hass, fake_mestro)

    await call(hass, SERVICE_TURN_ON, **{ATTR_ACTIVITY: activity})
    assert fake_mestro.ctl_commands == ["standby-off", command]


async def test_activity_on_an_awake_player_skips_the_wake(
    hass: HomeAssistant, fake_mestro: FakeMestro
) -> None:
    await setup_integration(hass, fake_mestro)

    await call(hass, SERVICE_TURN_ON, **{ATTR_ACTIVITY: "Queue"})
    assert fake_mestro.ctl_commands == ["queue"]


async def test_unknown_activity_is_rejected(
    hass: HomeAssistant, fake_mestro: FakeMestro
) -> None:
    await setup_integration(hass, fake_mestro)

    with pytest.raises(ServiceValidationError):
        await call(hass, SERVICE_TURN_ON, **{ATTR_ACTIVITY: "Jukebox"})
    assert fake_mestro.ctl_commands == []


async def test_send_command_passes_any_ctl_command(
    hass: HomeAssistant, fake_mestro: FakeMestro
) -> None:
    await setup_integration(hass, fake_mestro)

    await call(
        hass, SERVICE_SEND_COMMAND, **{ATTR_COMMAND: ["play-pause", "vu-select:2"]}
    )
    assert fake_mestro.ctl_commands == ["play-pause", "vu-select:2"]


async def test_send_command_honors_repeats_and_delay(
    hass: HomeAssistant, fake_mestro: FakeMestro
) -> None:
    await setup_integration(hass, fake_mestro)
    sleeps = AsyncMock()

    with patch("custom_components.mestro.remote.asyncio.sleep", sleeps):
        await call(
            hass,
            SERVICE_SEND_COMMAND,
            **{
                ATTR_COMMAND: ["volume-up", "volume-down"],
                ATTR_NUM_REPEATS: 2,
                ATTR_DELAY_SECS: 0.25,
            },
        )

    assert fake_mestro.ctl_commands == ["volume-up", "volume-down"] * 2
    assert [c.args[0] for c in sleeps.await_args_list] == [0.25] * 3


async def test_send_command_failure_surfaces_the_player_error(
    hass: HomeAssistant, fake_mestro: FakeMestro
) -> None:
    await setup_integration(hass, fake_mestro)

    with pytest.raises(HomeAssistantError, match="unknown command"):
        await call(hass, SERVICE_SEND_COMMAND, **{ATTR_COMMAND: ["nonsense"]})


async def test_unavailable_while_disconnected_then_recovers(
    hass: HomeAssistant, fake_mestro: FakeMestro
) -> None:
    await setup_integration(hass, fake_mestro)
    assert hass.states.get(REMOTE).state == STATE_ON

    fake_mestro.accept_ws = False
    await fake_mestro.drop_connections()
    await eventually(lambda: hass.states.get(REMOTE).state == STATE_UNAVAILABLE)

    fake_mestro.state["view"]["standby"] = True
    fake_mestro.accept_ws = True
    await eventually(lambda: hass.states.get(REMOTE).state == STATE_OFF)
    await asyncio.sleep(0)


async def test_send_command_failure_is_a_translated_error(
    hass: HomeAssistant, fake_mestro: FakeMestro
) -> None:
    await setup_integration(hass, fake_mestro)

    with pytest.raises(HomeAssistantError) as raised:
        await call(hass, SERVICE_SEND_COMMAND, **{ATTR_COMMAND: ["nonsense"]})
    assert raised.value.translation_domain == "mestro"
    assert raised.value.translation_key == "command_failed"
    assert raised.value.translation_placeholders is not None
    assert raised.value.translation_placeholders["command"] == "nonsense"
