"""Setup, unload, reconnect and reauth of the config entry."""

from __future__ import annotations

from homeassistant.config_entries import SOURCE_REAUTH, ConfigEntryState
from homeassistant.const import STATE_ON, STATE_UNAVAILABLE
from homeassistant.core import HomeAssistant
import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.mestro.const import CONF_TOKEN, DOMAIN

from .common import eventually, setup_integration
from .fake_mestro import PIN, PLAYER_ID, FakeMestro, token_for

ENTITIES = (
    "remote.wohnzimmer",
    "light.wohnzimmer_display",
    "select.wohnzimmer_vu_skin",
)


@pytest.fixture(autouse=True)
def _real_sockets(socket_enabled: None) -> None:
    """The integration talks to the fake mestro over loopback."""


async def test_setup_creates_one_device_with_every_entity(
    hass: HomeAssistant, fake_mestro: FakeMestro
) -> None:
    entry = await setup_integration(hass, fake_mestro)

    assert entry.state is ConfigEntryState.LOADED
    for entity_id in ENTITIES:
        assert hass.states.get(entity_id).state != STATE_UNAVAILABLE
    assert hass.states.get("remote.wohnzimmer").state == STATE_ON


async def test_unload_closes_the_socket_and_stops_reconnecting(
    hass: HomeAssistant, fake_mestro: FakeMestro
) -> None:
    entry = await setup_integration(hass, fake_mestro)

    assert await hass.config_entries.async_unload(entry.entry_id)
    await hass.async_block_till_done()
    assert entry.state is ConfigEntryState.NOT_LOADED
    await eventually(lambda: not fake_mestro.sockets or fake_mestro.sockets[0].closed)
    attempts = fake_mestro.ws_attempts
    await fake_mestro.drop_connections()
    assert fake_mestro.ws_attempts == attempts


async def test_an_unreachable_player_retries_the_setup(
    hass: HomeAssistant, fake_mestro: FakeMestro
) -> None:
    fake_mestro.accept_ws = False
    entry = await setup_integration(hass, fake_mestro)

    assert entry.state is ConfigEntryState.SETUP_RETRY


async def test_a_rejected_token_at_setup_starts_reauth(
    hass: HomeAssistant, fake_mestro: FakeMestro
) -> None:
    entry = MockConfigEntry(
        domain=DOMAIN,
        unique_id=PLAYER_ID,
        data={"host": fake_mestro.host, "port": fake_mestro.port, CONF_TOKEN: "stale"},
    )
    entry.add_to_hass(hass)
    await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    assert entry.state is ConfigEntryState.SETUP_ERROR
    assert [
        f["context"]["source"] for f in hass.config_entries.flow.async_progress()
    ] == [SOURCE_REAUTH]


async def test_a_401_on_reconnect_starts_reauth_and_a_new_pin_recovers(
    hass: HomeAssistant, fake_mestro: FakeMestro
) -> None:
    entry = await setup_integration(hass, fake_mestro)

    fake_mestro.rotate_pin("NEWPIN23")
    await fake_mestro.drop_connections()
    await eventually(hass.config_entries.flow.async_progress)
    assert hass.states.get("remote.wohnzimmer").state == STATE_UNAVAILABLE
    attempts = fake_mestro.ws_attempts

    # reauth with the new PIN reloads the entry with the new token
    flow = hass.config_entries.flow.async_progress()[0]
    assert flow["context"]["source"] == SOURCE_REAUTH
    result = await hass.config_entries.flow.async_configure(
        flow["flow_id"], {"pin": "NEWPIN23"}
    )
    await hass.async_block_till_done()
    assert result["reason"] == "reauth_successful"
    assert entry.data[CONF_TOKEN] == token_for("NEWPIN23")
    assert entry.state is ConfigEntryState.LOADED
    assert hass.states.get("remote.wohnzimmer").state == STATE_ON
    assert fake_mestro.ws_attempts > attempts


async def test_reconnect_after_a_player_reboot_keeps_the_entities(
    hass: HomeAssistant, fake_mestro: FakeMestro
) -> None:
    await setup_integration(hass, fake_mestro)
    assert fake_mestro.pin == PIN

    fake_mestro.accept_ws = False
    await fake_mestro.drop_connections()
    await eventually(lambda: hass.states.get(ENTITIES[0]).state == STATE_UNAVAILABLE)
    for entity_id in ENTITIES:
        assert hass.states.get(entity_id).state == STATE_UNAVAILABLE

    fake_mestro.accept_ws = True
    await eventually(lambda: hass.states.get(ENTITIES[0]).state == STATE_ON)
    assert len(hass.states.async_entity_ids("remote")) == 1
    assert len(hass.states.async_entity_ids("light")) == 1
    assert len(hass.states.async_entity_ids("select")) == 1
