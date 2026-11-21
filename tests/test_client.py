"""The WebSocket client against the fake mestro."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncGenerator

import aiohttp
import pytest

from custom_components.mestro.client import (
    RECONNECT_BACKOFF,
    MestroAuthError,
    MestroClient,
    MestroConnectionError,
    MestroRateLimitError,
    MestroRequestError,
    MestroState,
    async_pair,
)

from .common import eventually
from .fake_mestro import PIN, PLAYER_ID, PLAYER_NAME, VU_SKINS, FakeMestro, token_for


@pytest.fixture
async def session() -> AsyncGenerator[aiohttp.ClientSession]:
    """A plain aiohttp session (the integration passes HA's)."""
    async with aiohttp.ClientSession() as session:
        yield session


def make_client(
    session: aiohttp.ClientSession, server: FakeMestro, **kwargs
) -> MestroClient:
    """A client with the right token and an instant backoff."""
    return MestroClient(
        session,
        server.host,
        server.port,
        token_for(PIN),
        backoff=(0.01,),
        **kwargs,
    )


async def test_pair_returns_the_cookie_token(session, fake_mestro: FakeMestro) -> None:
    token = await async_pair(session, fake_mestro.host, fake_mestro.port, PIN)
    assert token == token_for(PIN)


async def test_pair_wrong_pin_is_an_auth_error(
    session, fake_mestro: FakeMestro
) -> None:
    with pytest.raises(MestroAuthError):
        await async_pair(session, fake_mestro.host, fake_mestro.port, "WRONG234")


async def test_pair_rate_limit_is_surfaced(session, fake_mestro: FakeMestro) -> None:
    for _ in range(4):
        with pytest.raises(MestroAuthError):
            await async_pair(session, fake_mestro.host, fake_mestro.port, "WRONG234")
    with pytest.raises(MestroRateLimitError):
        await async_pair(session, fake_mestro.host, fake_mestro.port, "WRONG234")
    with pytest.raises(MestroRateLimitError):
        await async_pair(session, fake_mestro.host, fake_mestro.port, PIN)


async def test_pair_unreachable_host(session, socket_enabled: None) -> None:
    with pytest.raises(MestroConnectionError):
        await async_pair(session, "127.0.0.1", 1, PIN)


async def test_connect_reads_hello_and_first_state(
    session, fake_mestro: FakeMestro
) -> None:
    client = make_client(session, fake_mestro)
    hello = await client.async_connect()
    try:
        assert hello.id == PLAYER_ID
        assert hello.name == PLAYER_NAME
        assert hello.protocol == 1
        assert client.connected
        assert client.state == MestroState(
            standby=False,
            vu_index=1,
            vu_skins=tuple(VU_SKINS),
            display_supported=True,
            brightness=80,
        )
    finally:
        await client.async_stop()


async def test_connect_with_a_stale_token_is_an_auth_error(
    session, fake_mestro: FakeMestro
) -> None:
    client = MestroClient(session, fake_mestro.host, fake_mestro.port, "stale")
    with pytest.raises(MestroAuthError):
        await client.async_connect()


async def test_connect_refused_is_a_connection_error(
    session, fake_mestro: FakeMestro
) -> None:
    fake_mestro.accept_ws = False
    client = make_client(session, fake_mestro)
    with pytest.raises(MestroConnectionError):
        await client.async_connect()


async def test_connect_rejects_an_unsupported_protocol(
    session, fake_mestro: FakeMestro
) -> None:
    fake_mestro.hello["protocol"] = 2
    client = make_client(session, fake_mestro)
    with pytest.raises(MestroConnectionError, match="protocol 2"):
        await client.async_connect()
    assert not client.connected


async def test_connect_rejects_a_player_without_identity(
    session, fake_mestro: FakeMestro
) -> None:
    del fake_mestro.hello["id"]
    client = make_client(session, fake_mestro)
    with pytest.raises(MestroConnectionError):
        await client.async_connect()


async def test_request_reply_by_id_and_error(session, fake_mestro: FakeMestro) -> None:
    client = make_client(session, fake_mestro)
    await client.async_connect()
    try:
        await client.async_ctl("home")
        await client.async_ctl("vu-select:2")
        assert fake_mestro.ctl_commands == ["home", "vu-select:2"]
        with pytest.raises(MestroRequestError, match="unknown command"):
            await client.async_ctl("vu-select:9")
        # concurrent requests find their own replies
        await asyncio.gather(*(client.async_ctl("play-pause") for _ in range(5)))
        assert fake_mestro.ctl_commands.count("play-pause") == 5
    finally:
        await client.async_stop()


async def test_set_brightness_writes_the_display_setting(
    session, fake_mestro: FakeMestro
) -> None:
    client = make_client(session, fake_mestro)
    await client.async_connect()
    try:
        await client.async_set_brightness(42)
        assert fake_mestro.requests[-1] == {
            "id": fake_mestro.requests[-1]["id"],
            "op": "settings",
            "action": "set",
            "key": "display.brightness",
            "value": 42,
        }
    finally:
        await client.async_stop()


async def test_state_pushes_reach_the_callback(
    session, fake_mestro: FakeMestro
) -> None:
    seen: list[MestroState] = []
    client = make_client(session, fake_mestro, on_state=seen.append)
    await client.async_connect()
    try:
        fake_mestro.state["view"]["standby"] = True
        fake_mestro.state["view"]["vu_index"] = 2
        await fake_mestro.push_state()
        await eventually(lambda: len(seen) >= 2)
        assert seen[-1].standby is True
        assert seen[-1].vu_index == 2
        assert client.state == seen[-1]
    finally:
        await client.async_stop()


async def test_state_tolerates_missing_optional_fields() -> None:
    state = MestroState.from_wire({"view": {"standby": True}})
    assert state.standby is True
    assert state.vu_skins == ()
    assert state.display_supported is False


async def test_request_without_a_connection_fails(
    session, fake_mestro: FakeMestro
) -> None:
    client = make_client(session, fake_mestro)
    with pytest.raises(MestroConnectionError):
        await client.async_ctl("home")


async def test_reconnects_after_the_player_drops_the_socket(
    session, fake_mestro: FakeMestro
) -> None:
    lost: list[None] = []
    client = make_client(
        session, fake_mestro, on_disconnected=lambda: lost.append(None)
    )
    await client.async_connect()
    client.async_start()
    try:
        await fake_mestro.drop_connections()
        await eventually(lambda: bool(lost))
        await eventually(lambda: client.connected and fake_mestro.ws_attempts == 2)
        await client.async_ctl("home")
        assert fake_mestro.ctl_commands == ["home"]
    finally:
        await client.async_stop()


async def test_reconnect_backs_off_while_the_player_is_down(
    session, fake_mestro: FakeMestro
) -> None:
    delays: list[float] = []

    async def record(delay: float) -> None:
        delays.append(delay)
        await asyncio.sleep(0.001)

    client = MestroClient(
        session,
        fake_mestro.host,
        fake_mestro.port,
        token_for(PIN),
        backoff=RECONNECT_BACKOFF,
        sleep=record,
    )
    await client.async_connect()
    client.async_start()
    try:
        fake_mestro.accept_ws = False
        await fake_mestro.drop_connections()
        await eventually(lambda: len(delays) >= 7)
        assert delays[:7] == [1, 2, 5, 10, 30, 30, 30]
        assert tuple(delays[:5]) == RECONNECT_BACKOFF

        fake_mestro.accept_ws = True
        await eventually(lambda: client.connected)
        # a clean reconnect restarts the ladder
        seen = len(delays)
        await fake_mestro.drop_connections()
        await eventually(lambda: len(delays) > seen)
        assert delays[seen] == 1
    finally:
        await client.async_stop()


async def test_reconnect_with_a_rejected_token_reports_auth_failure(
    session, fake_mestro: FakeMestro
) -> None:
    failed: list[None] = []
    client = make_client(
        session, fake_mestro, on_auth_failed=lambda: failed.append(None)
    )
    await client.async_connect()
    client.async_start()
    try:
        fake_mestro.rotate_pin("NEWPIN23")
        await fake_mestro.drop_connections()
        await eventually(lambda: bool(failed))
        assert not client.connected
        ws_attempts = fake_mestro.ws_attempts
        await asyncio.sleep(0.05)
        # the loop stopped: no hammering with a dead token
        assert fake_mestro.ws_attempts == ws_attempts
    finally:
        await client.async_stop()


async def test_stop_closes_the_socket_and_the_loop(
    session, fake_mestro: FakeMestro
) -> None:
    client = make_client(session, fake_mestro)
    await client.async_connect()
    client.async_start()
    await client.async_stop()
    assert not client.connected
    attempts = fake_mestro.ws_attempts
    await asyncio.sleep(0.05)
    assert fake_mestro.ws_attempts == attempts
