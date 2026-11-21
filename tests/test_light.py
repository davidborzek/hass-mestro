"""The display light against the fake mestro."""

from __future__ import annotations

from homeassistant.components.light import ATTR_BRIGHTNESS, DOMAIN as LIGHT_DOMAIN
from homeassistant.const import (
    ATTR_ENTITY_ID,
    SERVICE_TURN_OFF,
    SERVICE_TURN_ON,
    STATE_OFF,
    STATE_ON,
    STATE_UNAVAILABLE,
)
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er
import pytest

from .common import eventually, push, setup_integration
from .fake_mestro import PLAYER_ID, FakeMestro

LIGHT = "light.wohnzimmer_display"


@pytest.fixture(autouse=True)
def _real_sockets(socket_enabled: None) -> None:
    """The integration talks to the fake mestro over loopback."""


async def call(hass: HomeAssistant, service: str, **data: object) -> None:
    """Call a light service on the entity."""
    await hass.services.async_call(
        LIGHT_DOMAIN, service, {ATTR_ENTITY_ID: LIGHT, **data}, blocking=True
    )


def brightness_writes(server: FakeMestro) -> list[object]:
    """Every `display.brightness` value written so far."""
    return [
        r["value"]
        for r in server.requests
        if r.get("op") == "settings" and r.get("key") == "display.brightness"
    ]


async def test_state_maps_standby_and_brightness(
    hass: HomeAssistant, fake_mestro: FakeMestro
) -> None:
    await setup_integration(hass, fake_mestro)

    registered = er.async_get(hass).async_get(LIGHT)
    assert registered is not None
    assert registered.unique_id == f"{PLAYER_ID}_display"
    state = hass.states.get(LIGHT)
    assert state.state == STATE_ON
    assert state.attributes[ATTR_BRIGHTNESS] == round(80 / 100 * 255)

    fake_mestro.state["view"]["standby"] = True
    fake_mestro.state["display"]["brightness"] = 1
    await push(hass, fake_mestro)
    state = hass.states.get(LIGHT)
    assert state.state == STATE_OFF
    assert state.attributes.get(ATTR_BRIGHTNESS) is None


@pytest.mark.parametrize(
    ("device_brightness", "ha_brightness"), [(1, 3), (50, 128), (100, 255)]
)
async def test_brightness_scales_between_1_100_and_0_255(
    hass: HomeAssistant,
    fake_mestro: FakeMestro,
    device_brightness: int,
    ha_brightness: int,
) -> None:
    fake_mestro.state["display"]["brightness"] = device_brightness
    await setup_integration(hass, fake_mestro)

    assert hass.states.get(LIGHT).attributes[ATTR_BRIGHTNESS] == ha_brightness


@pytest.mark.parametrize(
    ("ha_brightness", "device_brightness"), [(1, 1), (128, 50), (255, 100)]
)
async def test_turn_on_with_brightness_writes_the_setting(
    hass: HomeAssistant,
    fake_mestro: FakeMestro,
    ha_brightness: int,
    device_brightness: int,
) -> None:
    await setup_integration(hass, fake_mestro)

    await call(hass, SERVICE_TURN_ON, **{ATTR_BRIGHTNESS: ha_brightness})
    assert brightness_writes(fake_mestro) == [device_brightness]
    assert fake_mestro.ctl_commands == []


async def test_turn_on_with_brightness_wakes_a_sleeping_player(
    hass: HomeAssistant, fake_mestro: FakeMestro
) -> None:
    fake_mestro.state["view"]["standby"] = True
    await setup_integration(hass, fake_mestro)

    await call(hass, SERVICE_TURN_ON, **{ATTR_BRIGHTNESS: 255})
    assert fake_mestro.ctl_commands == ["standby-off"]
    assert brightness_writes(fake_mestro) == [100]


async def test_on_off_map_to_standby(
    hass: HomeAssistant, fake_mestro: FakeMestro
) -> None:
    await setup_integration(hass, fake_mestro)

    await call(hass, SERVICE_TURN_OFF)
    await call(hass, SERVICE_TURN_ON)
    assert fake_mestro.ctl_commands == ["standby-on", "standby-off"]
    assert brightness_writes(fake_mestro) == []


async def test_no_light_without_a_backlight(
    hass: HomeAssistant, fake_mestro: FakeMestro
) -> None:
    fake_mestro.state["display"] = {"supported": False, "brightness": 100}
    await setup_integration(hass, fake_mestro)

    assert hass.states.get(LIGHT) is None


async def test_light_appears_when_the_backlight_is_reported_later(
    hass: HomeAssistant, fake_mestro: FakeMestro
) -> None:
    fake_mestro.state["display"] = {"supported": False, "brightness": 100}
    await setup_integration(hass, fake_mestro)
    assert hass.states.get(LIGHT) is None

    fake_mestro.state["display"] = {"supported": True, "brightness": 60}
    await push(hass, fake_mestro)
    assert hass.states.get(LIGHT).state == STATE_ON


async def test_unavailable_while_disconnected(
    hass: HomeAssistant, fake_mestro: FakeMestro
) -> None:
    await setup_integration(hass, fake_mestro)

    fake_mestro.accept_ws = False
    await fake_mestro.drop_connections()
    await eventually(lambda: hass.states.get(LIGHT).state == STATE_UNAVAILABLE)
    # HA skips unavailable entities: nothing reaches the player
    await call(hass, SERVICE_TURN_ON, **{ATTR_BRIGHTNESS: 10})
    assert brightness_writes(fake_mestro) == []
