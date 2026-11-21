"""The VU skin select against the fake mestro."""

from __future__ import annotations

from homeassistant.components.select import (
    ATTR_OPTION,
    ATTR_OPTIONS,
    DOMAIN as SELECT_DOMAIN,
    SERVICE_SELECT_OPTION,
)
from homeassistant.const import ATTR_ENTITY_ID, STATE_UNAVAILABLE, STATE_UNKNOWN
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError
from homeassistant.helpers import entity_registry as er
import pytest

from .common import eventually, push, setup_integration
from .fake_mestro import PLAYER_ID, VU_SKINS, FakeMestro

SELECT = "select.wohnzimmer_vu_skin"


@pytest.fixture(autouse=True)
def _real_sockets(socket_enabled: None) -> None:
    """The integration talks to the fake mestro over loopback."""


async def select(hass: HomeAssistant, option: str) -> None:
    """Pick a skin through the service."""
    await hass.services.async_call(
        SELECT_DOMAIN,
        SERVICE_SELECT_OPTION,
        {ATTR_ENTITY_ID: SELECT, ATTR_OPTION: option},
        blocking=True,
    )


async def test_options_and_current_follow_the_state(
    hass: HomeAssistant, fake_mestro: FakeMestro
) -> None:
    await setup_integration(hass, fake_mestro)

    registered = er.async_get(hass).async_get(SELECT)
    assert registered is not None
    assert registered.unique_id == f"{PLAYER_ID}_vu_skin"
    state = hass.states.get(SELECT)
    assert state.attributes[ATTR_OPTIONS] == VU_SKINS
    assert state.state == VU_SKINS[1]

    fake_mestro.state["view"]["vu_index"] = 2
    await push(hass, fake_mestro)
    assert hass.states.get(SELECT).state == VU_SKINS[2]

    fake_mestro.state["view"]["vu_skins"] = ["a", "b"]
    fake_mestro.state["view"]["vu_index"] = 0
    await push(hass, fake_mestro)
    state = hass.states.get(SELECT)
    assert state.attributes[ATTR_OPTIONS] == ["a", "b"]
    assert state.state == "a"


async def test_index_outside_the_cycle_is_unknown(
    hass: HomeAssistant, fake_mestro: FakeMestro
) -> None:
    fake_mestro.state["view"]["vu_index"] = 7
    await setup_integration(hass, fake_mestro)

    assert hass.states.get(SELECT).state == STATE_UNKNOWN


@pytest.mark.parametrize("index", range(len(VU_SKINS)))
async def test_selecting_sends_vu_select_with_the_index(
    hass: HomeAssistant, fake_mestro: FakeMestro, index: int
) -> None:
    await setup_integration(hass, fake_mestro)

    await select(hass, VU_SKINS[index])
    assert fake_mestro.ctl_commands == [f"vu-select:{index}"]


async def test_selecting_an_unknown_skin_sends_nothing(
    hass: HomeAssistant, fake_mestro: FakeMestro
) -> None:
    await setup_integration(hass, fake_mestro)

    with pytest.raises(ServiceValidationError):
        await select(hass, "does-not-exist")
    assert fake_mestro.ctl_commands == []


async def test_a_player_that_rejects_the_index_surfaces_the_error(
    hass: HomeAssistant, fake_mestro: FakeMestro
) -> None:
    await setup_integration(hass, fake_mestro)
    # the cycle shrank on the player before the push arrived
    fake_mestro.state["view"]["vu_skins"] = ["round"]

    with pytest.raises(HomeAssistantError, match="unknown command"):
        await select(hass, VU_SKINS[2])


async def test_no_select_while_the_player_reports_no_skins(
    hass: HomeAssistant, fake_mestro: FakeMestro
) -> None:
    del fake_mestro.state["view"]["vu_skins"]
    await setup_integration(hass, fake_mestro)

    assert hass.states.get(SELECT) is None


async def test_unavailable_while_disconnected(
    hass: HomeAssistant, fake_mestro: FakeMestro
) -> None:
    await setup_integration(hass, fake_mestro)

    fake_mestro.accept_ws = False
    await fake_mestro.drop_connections()
    await eventually(lambda: hass.states.get(SELECT).state == STATE_UNAVAILABLE)


VIEW = "select.wohnzimmer_view"


async def select_view(hass: HomeAssistant, option: str) -> None:
    """Pick a view through the service."""
    await hass.services.async_call(
        SELECT_DOMAIN,
        SERVICE_SELECT_OPTION,
        {ATTR_ENTITY_ID: VIEW, ATTR_OPTION: option},
        blocking=True,
    )


async def test_view_select_follows_the_open_view(
    hass: HomeAssistant, fake_mestro: FakeMestro
) -> None:
    await setup_integration(hass, fake_mestro)

    registered = er.async_get(hass).async_get(VIEW)
    assert registered is not None
    assert registered.unique_id == f"{PLAYER_ID}_view"
    state = hass.states.get(VIEW)
    assert state.attributes[ATTR_OPTIONS] == ["now_playing", "queue", "vu", "home"]

    view = fake_mestro.state["view"]
    view.update(now_playing=False, queue=False, vu=False, standby=False)
    await push(hass, fake_mestro)
    assert hass.states.get(VIEW).state == "home"

    view.update(now_playing=True, queue=True)
    await push(hass, fake_mestro)
    assert hass.states.get(VIEW).state == "queue", "the topmost layer wins"

    view.update(vu=True)
    await push(hass, fake_mestro)
    assert hass.states.get(VIEW).state == "vu"

    view.update(standby=True)
    await push(hass, fake_mestro)
    assert hass.states.get(VIEW).state == STATE_UNKNOWN, "no view in standby"


@pytest.mark.parametrize(
    ("option", "command"),
    [
        ("now_playing", "now-playing"),
        ("queue", "queue"),
        ("vu", "vu"),
        ("home", "home"),
    ],
)
async def test_selecting_a_view_opens_it(
    hass: HomeAssistant, fake_mestro: FakeMestro, option: str, command: str
) -> None:
    await setup_integration(hass, fake_mestro)

    await select_view(hass, option)
    assert fake_mestro.ctl_commands == [command]


async def test_selecting_a_view_wakes_the_player_first(
    hass: HomeAssistant, fake_mestro: FakeMestro
) -> None:
    fake_mestro.state["view"]["standby"] = True
    await setup_integration(hass, fake_mestro)

    await select_view(hass, "vu")
    assert fake_mestro.ctl_commands == ["standby-off", "vu"]
