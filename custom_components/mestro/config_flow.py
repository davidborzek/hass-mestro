"""Config flow: find a player (manual or zeroconf), pair with its PIN."""

from __future__ import annotations

from collections.abc import Mapping
import logging
from typing import Any

from aiohttp import DummyCookieJar
from homeassistant.config_entries import ConfigFlow, ConfigFlowResult
from homeassistant.const import CONF_HOST, CONF_PIN, CONF_PORT
from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_create_clientsession
from homeassistant.helpers.service_info.zeroconf import ZeroconfServiceInfo
import voluptuous as vol
from yarl import URL

from .client import (
    MestroAuthError,
    MestroClient,
    MestroConnectionError,
    MestroHello,
    MestroProtocolError,
    MestroRateLimitError,
    async_pair,
)
from .const import CONF_TOKEN, DEFAULT_PORT, DOMAIN, SUPPORTED_PROTOCOL

_LOGGER = logging.getLogger(__name__)

STEP_USER_SCHEMA = vol.Schema({vol.Required(CONF_HOST): str})
STEP_PIN_SCHEMA = vol.Schema({vol.Required(CONF_PIN): str})


def parse_host(text: str) -> tuple[str, int]:
    """Split `host[:port]` (IPv6 in brackets) into host and port."""
    text = text.strip()
    if any(char.isspace() for char in text):
        raise ValueError(text)
    url = URL(f"http://{text}")
    if not url.host or url.path not in ("", "/") or url.user or url.query_string:
        raise ValueError(text)
    return url.host, url.explicit_port or DEFAULT_PORT


async def async_pair_and_identify(
    hass: HomeAssistant, host: str, port: int, pin: str
) -> tuple[str, MestroHello]:
    """Pair with the PIN, then open the socket once to learn who answers."""
    session = async_create_clientsession(
        hass, auto_cleanup=False, cookie_jar=DummyCookieJar()
    )
    try:
        token = await async_pair(session, host, port, pin.strip().upper())
        client = MestroClient(session, host, port, token)
        hello = await client.async_connect()
        await client.async_stop()
    finally:
        session.detach()
    return token, hello


class MestroConfigFlow(ConfigFlow, domain=DOMAIN):
    """Pairing works like a phone: the PIN buys a token, the token is kept."""

    VERSION = 1

    def __init__(self) -> None:
        """Start without a target."""
        self._host = ""
        self._port = DEFAULT_PORT
        self._name = ""

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Ask for `host[:port]`."""
        errors: dict[str, str] = {}
        if user_input is not None:
            try:
                self._host, self._port = parse_host(user_input[CONF_HOST])
            except ValueError:
                errors[CONF_HOST] = "invalid_host"
            else:
                return await self.async_step_pin()
        return self.async_show_form(
            step_id="user",
            data_schema=self.add_suggested_values_to_schema(
                STEP_USER_SCHEMA, user_input
            ),
            errors=errors,
        )

    async def async_step_zeroconf(
        self, discovery_info: ZeroconfServiceInfo
    ) -> ConfigFlowResult:
        """A player announced itself."""
        properties = discovery_info.properties
        player_id = properties.get("id")
        if not player_id or properties.get("protocol") != str(SUPPORTED_PROTOCOL):
            return self.async_abort(reason="unsupported_player")
        await self.async_set_unique_id(player_id)
        self._host = discovery_info.host
        self._port = discovery_info.port or DEFAULT_PORT
        self._abort_if_unique_id_configured(
            updates={CONF_HOST: self._host, CONF_PORT: self._port}
        )
        self._name = properties.get("name") or discovery_info.name.split(".")[0]
        self.context["title_placeholders"] = {"name": self._name}
        return await self.async_step_zeroconf_confirm()

    async def async_step_zeroconf_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Tell the user which player was found, then ask for its PIN."""
        if user_input is None:
            return self.async_show_form(
                step_id="zeroconf_confirm",
                description_placeholders={"name": self._name, "host": self._host},
            )
        return await self.async_step_pin()

    async def async_step_pin(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Ask for the PIN shown on the player and pair."""
        errors: dict[str, str] = {}
        if user_input is not None:
            try:
                token, hello = await async_pair_and_identify(
                    self.hass, self._host, self._port, user_input[CONF_PIN]
                )
            except MestroAuthError:
                errors["base"] = "invalid_auth"
            except MestroRateLimitError:
                errors["base"] = "too_many_attempts"
            except MestroProtocolError:
                errors["base"] = "unsupported_player"
            except MestroConnectionError:
                errors["base"] = "cannot_connect"
            else:
                await self.async_set_unique_id(hello.id)
                self._abort_if_unique_id_configured(
                    updates={
                        CONF_HOST: self._host,
                        CONF_PORT: self._port,
                        CONF_TOKEN: token,
                    }
                )
                return self.async_create_entry(
                    title=hello.name or hello.id,
                    data={
                        CONF_HOST: self._host,
                        CONF_PORT: self._port,
                        CONF_TOKEN: token,
                    },
                )
        return self.async_show_form(
            step_id="pin",
            data_schema=STEP_PIN_SCHEMA,
            errors=errors,
            description_placeholders={"host": self._host},
        )

    async def async_step_reauth(
        self, entry_data: Mapping[str, Any]
    ) -> ConfigFlowResult:
        """The player no longer accepts our token (new PIN)."""
        return await self.async_step_reauth_confirm()

    async def async_step_reauth_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Ask for the player's current PIN."""
        entry = self._get_reauth_entry()
        errors: dict[str, str] = {}
        if user_input is not None:
            try:
                token, hello = await async_pair_and_identify(
                    self.hass,
                    entry.data[CONF_HOST],
                    entry.data[CONF_PORT],
                    user_input[CONF_PIN],
                )
            except MestroAuthError:
                errors["base"] = "invalid_auth"
            except MestroRateLimitError:
                errors["base"] = "too_many_attempts"
            except MestroProtocolError:
                errors["base"] = "unsupported_player"
            except MestroConnectionError:
                errors["base"] = "cannot_connect"
            else:
                await self.async_set_unique_id(hello.id)
                self._abort_if_unique_id_mismatch(reason="wrong_device")
                return self.async_update_reload_and_abort(
                    entry, data_updates={CONF_TOKEN: token}
                )
        return self.async_show_form(
            step_id="reauth_confirm",
            data_schema=STEP_PIN_SCHEMA,
            errors=errors,
            description_placeholders={
                "name": entry.title,
                "host": entry.data[CONF_HOST],
            },
        )
