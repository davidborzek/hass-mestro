"""Config flow: manual, zeroconf, wrong PIN, rate limit, duplicates, reauth."""

from __future__ import annotations

from collections.abc import Generator
from ipaddress import ip_address
from unittest.mock import patch

from homeassistant import config_entries
from homeassistant.config_entries import ConfigEntryState
from homeassistant.const import CONF_HOST, CONF_PIN, CONF_PORT
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
from homeassistant.helpers.service_info.zeroconf import ZeroconfServiceInfo
import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.mestro.client import MestroHello
from custom_components.mestro.const import CONF_TOKEN, DOMAIN

from .fake_mestro import PIN, PLAYER_ID, PLAYER_NAME, FakeMestro, token_for


@pytest.fixture(autouse=True)
def _real_sockets(socket_enabled: None) -> None:
    """The flow talks to the fake mestro over loopback."""


@pytest.fixture
def setup_entry() -> Generator[None]:
    """Keep the flow tests away from the platforms."""
    with patch("custom_components.mestro.async_setup_entry", return_value=True):
        yield


def zeroconf_info(server: FakeMestro, **properties: str) -> ZeroconfServiceInfo:
    """What the zeroconf integration hands over for the fake player."""
    return ZeroconfServiceInfo(
        ip_address=ip_address(server.host),
        ip_addresses=[ip_address(server.host)],
        port=server.port,
        hostname="mestro.local.",
        type="_mestro._tcp.local.",
        name=f"{PLAYER_NAME}._mestro._tcp.local.",
        properties={
            "id": PLAYER_ID,
            "version": "0.1.0",
            "protocol": "1",
            "name": PLAYER_NAME,
            **properties,
        },
    )


def make_entry(server: FakeMestro, **data: object) -> MockConfigEntry:
    """A configured entry for the fake player."""
    return MockConfigEntry(
        domain=DOMAIN,
        unique_id=PLAYER_ID,
        title=PLAYER_NAME,
        data={
            CONF_HOST: server.host,
            CONF_PORT: server.port,
            CONF_TOKEN: token_for(PIN),
            **data,
        },
    )


async def test_manual_flow_pairs_and_creates_the_entry(
    hass: HomeAssistant, fake_mestro: FakeMestro, setup_entry: None
) -> None:
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "user"

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {CONF_HOST: f"{fake_mestro.host}:{fake_mestro.port}"},
    )
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "pin"

    # lowercase and padded input still pairs
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_PIN: f" {PIN.lower()} "}
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["title"] == PLAYER_NAME
    assert result["data"] == {
        CONF_HOST: fake_mestro.host,
        CONF_PORT: fake_mestro.port,
        CONF_TOKEN: token_for(PIN),
    }
    assert result["result"].unique_id == PLAYER_ID


async def test_manual_flow_defaults_to_port_80(
    hass: HomeAssistant, fake_mestro: FakeMestro, setup_entry: None
) -> None:
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    hello = MestroHello(id=PLAYER_ID, name=PLAYER_NAME, version="0.1.0", protocol=1)
    with patch(
        "custom_components.mestro.config_flow.async_pair_and_identify",
        return_value=("token", hello),
    ) as pair:
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {CONF_HOST: "mestro.local"}
        )
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {CONF_PIN: PIN}
        )
    assert pair.call_args.args[1:3] == ("mestro.local", 80)
    assert result["data"][CONF_PORT] == 80


@pytest.mark.parametrize("host", ["", "http://x/y", "a b", "host:99999"])
async def test_manual_flow_rejects_a_bad_host(hass: HomeAssistant, host: str) -> None:
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_HOST: host}
    )
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "user"
    assert result["errors"] == {CONF_HOST: "invalid_host"}


async def test_wrong_pin_shows_an_error_and_allows_a_retry(
    hass: HomeAssistant, fake_mestro: FakeMestro, setup_entry: None
) -> None:
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_HOST: f"{fake_mestro.host}:{fake_mestro.port}"}
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_PIN: "WRONG234"}
    )
    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {"base": "invalid_auth"}

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_PIN: PIN}
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY


async def test_rate_limited_pairing_says_wait(
    hass: HomeAssistant, fake_mestro: FakeMestro
) -> None:
    fake_mestro.pair_failures = 5
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_HOST: f"{fake_mestro.host}:{fake_mestro.port}"}
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_PIN: PIN}
    )
    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {"base": "too_many_attempts"}


async def test_unreachable_player_is_cannot_connect(hass: HomeAssistant) -> None:
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_HOST: "127.0.0.1:1"}
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_PIN: PIN}
    )
    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {"base": "cannot_connect"}


async def test_unsupported_protocol_is_reported(
    hass: HomeAssistant, fake_mestro: FakeMestro
) -> None:
    fake_mestro.hello["protocol"] = 2
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_HOST: f"{fake_mestro.host}:{fake_mestro.port}"}
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_PIN: PIN}
    )
    assert result["errors"] == {"base": "unsupported_player"}


async def test_zeroconf_flow_confirms_then_pairs(
    hass: HomeAssistant, fake_mestro: FakeMestro, setup_entry: None
) -> None:
    result = await hass.config_entries.flow.async_init(
        DOMAIN,
        context={"source": config_entries.SOURCE_ZEROCONF},
        data=zeroconf_info(fake_mestro),
    )
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "zeroconf_confirm"
    assert result["description_placeholders"] == {
        "name": PLAYER_NAME,
        "host": fake_mestro.host,
    }

    result = await hass.config_entries.flow.async_configure(result["flow_id"], {})
    assert result["step_id"] == "pin"
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_PIN: PIN}
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["data"][CONF_PORT] == fake_mestro.port
    assert result["result"].unique_id == PLAYER_ID


async def test_zeroconf_wrong_pin_stays_on_the_pin_step(
    hass: HomeAssistant, fake_mestro: FakeMestro
) -> None:
    result = await hass.config_entries.flow.async_init(
        DOMAIN,
        context={"source": config_entries.SOURCE_ZEROCONF},
        data=zeroconf_info(fake_mestro),
    )
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {})
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_PIN: "WRONG234"}
    )
    assert result["step_id"] == "pin"
    assert result["errors"] == {"base": "invalid_auth"}


@pytest.mark.parametrize(
    "properties", [{"id": ""}, {"protocol": "2"}, {"protocol": ""}]
)
async def test_zeroconf_ignores_players_it_cannot_speak_to(
    hass: HomeAssistant, fake_mestro: FakeMestro, properties: dict[str, str]
) -> None:
    result = await hass.config_entries.flow.async_init(
        DOMAIN,
        context={"source": config_entries.SOURCE_ZEROCONF},
        data=zeroconf_info(fake_mestro, **properties),
    )
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "unsupported_player"


async def test_zeroconf_takes_the_ipv4_address(
    hass: HomeAssistant, fake_mestro: FakeMestro, setup_entry: None
) -> None:
    # the player listens on IPv4 only: an IPv6 address first must not win
    info = zeroconf_info(fake_mestro)
    info.ip_addresses = [ip_address("fd00::1"), ip_address(fake_mestro.host)]
    info.ip_address = info.ip_addresses[0]
    info.hostname = "mestro.local."
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_ZEROCONF}, data=info
    )
    assert result["step_id"] == "zeroconf_confirm"
    assert result["description_placeholders"]["host"] == fake_mestro.host
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {})
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_PIN: PIN}
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["data"][CONF_HOST] == fake_mestro.host


async def test_zeroconf_aborts_without_an_ipv4_address(
    hass: HomeAssistant, fake_mestro: FakeMestro
) -> None:
    info = zeroconf_info(fake_mestro)
    info.ip_addresses = [ip_address("fd00::1"), ip_address("fe80::1")]
    info.ip_address = info.ip_addresses[0]
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_ZEROCONF}, data=info
    )
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "not_ipv4_device"


async def test_zeroconf_for_a_known_player_updates_its_address(
    hass: HomeAssistant, fake_mestro: FakeMestro
) -> None:
    entry = make_entry(fake_mestro, **{CONF_HOST: "10.0.0.99", CONF_PORT: 80})
    entry.add_to_hass(hass)
    with patch("custom_components.mestro.async_setup_entry", return_value=True):
        result = await hass.config_entries.flow.async_init(
            DOMAIN,
            context={"source": config_entries.SOURCE_ZEROCONF},
            data=zeroconf_info(fake_mestro),
        )
        await hass.async_block_till_done()
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "already_configured"
    assert entry.data[CONF_HOST] == fake_mestro.host
    assert entry.data[CONF_PORT] == fake_mestro.port


async def test_manual_flow_for_a_known_player_aborts(
    hass: HomeAssistant, fake_mestro: FakeMestro
) -> None:
    make_entry(fake_mestro).add_to_hass(hass)
    with patch("custom_components.mestro.async_setup_entry", return_value=True):
        result = await hass.config_entries.flow.async_init(
            DOMAIN, context={"source": config_entries.SOURCE_USER}
        )
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {CONF_HOST: f"{fake_mestro.host}:{fake_mestro.port}"}
        )
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {CONF_PIN: PIN}
        )
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "already_configured"


async def test_reauth_with_the_new_pin_replaces_the_token(
    hass: HomeAssistant, fake_mestro: FakeMestro
) -> None:
    entry = make_entry(fake_mestro, **{CONF_TOKEN: "stale"})
    entry.add_to_hass(hass)
    fake_mestro.rotate_pin("NEWPIN23")

    with patch("custom_components.mestro.async_setup_entry", return_value=True):
        result = await entry.start_reauth_flow(hass)
        assert result["type"] is FlowResultType.FORM
        assert result["step_id"] == "reauth_confirm"

        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {CONF_PIN: PIN}
        )
        assert result["errors"] == {"base": "invalid_auth"}

        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {CONF_PIN: "NEWPIN23"}
        )
        await hass.async_block_till_done()
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "reauth_successful"
    assert entry.data[CONF_TOKEN] == token_for("NEWPIN23")


async def test_reauth_against_another_player_is_refused(
    hass: HomeAssistant, fake_mestro: FakeMestro
) -> None:
    entry = make_entry(fake_mestro, **{CONF_TOKEN: "stale"})
    entry.add_to_hass(hass)
    fake_mestro.hello["id"] = "112233445566"

    result = await entry.start_reauth_flow(hass)
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_PIN: PIN}
    )
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "wrong_device"
    assert entry.data[CONF_TOKEN] == "stale"
    assert entry.state is ConfigEntryState.NOT_LOADED
