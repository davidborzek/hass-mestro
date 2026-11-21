"""Client for mestro's WebSocket JSON protocol (local push)."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable, Sequence
import contextlib
from dataclasses import dataclass, field
import json
import logging
from typing import Any

import aiohttp
from yarl import URL

from .const import SUPPORTED_PROTOCOL

_LOGGER = logging.getLogger(__name__)

PAIR_COOKIE = "mestro_pair"

# seconds to wait before reconnect attempt n; the last value repeats
RECONNECT_BACKOFF = (1, 2, 5, 10, 30)

REQUEST_TIMEOUT = 10
HEARTBEAT = 30


class MestroError(Exception):
    """Base class for every error this client raises."""


class MestroConnectionError(MestroError):
    """The player is unreachable or the connection broke."""


class MestroProtocolError(MestroConnectionError):
    """The peer answers, but is not a mestro speaking our protocol."""


class MestroAuthError(MestroError):
    """The PIN or pairing token was rejected (HTTP 401)."""


class MestroRateLimitError(MestroError):
    """`/pair` locked out after too many wrong PINs (HTTP 429)."""


class MestroRequestError(MestroError):
    """The player replied `ok: false`."""


@dataclass(frozen=True, slots=True)
class MestroHello:
    """Identity from the `hello` reply."""

    id: str
    name: str
    version: str
    protocol: int

    @classmethod
    def from_wire(cls, data: dict[str, Any]) -> MestroHello:
        """Build from the `hello` reply data."""
        return cls(
            id=str(data.get("id") or ""),
            name=str(data.get("name") or ""),
            version=str(data.get("version") or ""),
            protocol=int(data.get("protocol") or 0),
        )


@dataclass(frozen=True, slots=True)
class MestroState:
    """The parts of the pushed state frame the integration uses."""

    standby: bool = False
    now_playing: bool = False
    queue: bool = False
    vu: bool = False
    vu_index: int = 0
    vu_skins: tuple[str, ...] = field(default_factory=tuple)
    display_supported: bool = False
    brightness: int = 100

    @classmethod
    def from_wire(cls, data: dict[str, Any]) -> MestroState:
        """Build from a state frame's `data`; absent fields keep defaults."""
        view = data.get("view") or {}
        display = data.get("display") or {}
        return cls(
            standby=bool(view.get("standby", False)),
            now_playing=bool(view.get("now_playing", False)),
            queue=bool(view.get("queue", False)),
            vu=bool(view.get("vu", False)),
            vu_index=int(view.get("vu_index", 0)),
            vu_skins=tuple(str(skin) for skin in view.get("vu_skins") or ()),
            display_supported=bool(display.get("supported", False)),
            brightness=int(display.get("brightness", 100)),
        )


def _url(scheme: str, host: str, port: int, path: str) -> str:
    return str(URL.build(scheme=scheme, host=host, port=port, path=path))


async def async_pair(
    session: aiohttp.ClientSession, host: str, port: int, pin: str
) -> str:
    """Trade the PIN for the pairing token (the `mestro_pair` cookie value)."""
    try:
        async with asyncio.timeout(REQUEST_TIMEOUT):
            async with session.post(
                _url("http", host, port, "/pair"), json={"pin": pin}
            ) as response:
                if response.status == 401:
                    raise MestroAuthError("wrong PIN")
                if response.status == 429:
                    raise MestroRateLimitError("too many attempts")
                if response.status != 204:
                    raise MestroConnectionError(
                        f"unexpected pairing status {response.status}"
                    )
                cookie = response.cookies.get(PAIR_COOKIE)
                if cookie is None or not cookie.value:
                    raise MestroProtocolError("pairing reply carries no cookie")
                return cookie.value
    except (aiohttp.ClientError, TimeoutError) as err:
        raise MestroConnectionError(str(err) or "pairing request failed") from err


class MestroClient:
    """One authenticated WebSocket to a player, with push and reconnect."""

    def __init__(
        self,
        session: aiohttp.ClientSession,
        host: str,
        port: int,
        token: str,
        *,
        on_state: Callable[[MestroState], None] | None = None,
        on_disconnected: Callable[[], None] | None = None,
        on_connected: Callable[[], None] | None = None,
        on_auth_failed: Callable[[], None] | None = None,
        backoff: Sequence[float] | None = None,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    ) -> None:
        """Store the target; nothing connects before `async_connect`."""
        self._backoff = backoff or RECONNECT_BACKOFF
        self._sleep = sleep
        self._session = session
        self._ws_url = _url("ws", host, port, "/ws")
        self.base_url = _url("http", host, port, "/")
        self._headers = {"Cookie": f"{PAIR_COOKIE}={token}"}
        self._on_state = on_state
        self._on_disconnected = on_disconnected
        self._on_connected = on_connected
        self._on_auth_failed = on_auth_failed

        self.hello: MestroHello | None = None
        self.state: MestroState | None = None

        self._ws: aiohttp.ClientWebSocketResponse | None = None
        self._reader: asyncio.Task[None] | None = None
        self._supervisor: asyncio.Task[None] | None = None
        self._pending: dict[int, asyncio.Future[dict[str, Any]]] = {}
        self._next_id = 0
        self._state_received = asyncio.Event()
        self._ready = False

    @property
    def connected(self) -> bool:
        """Whether the handshake finished and the WebSocket is still open."""
        return self._ready and self._ws is not None and not self._ws.closed

    def set_callbacks(
        self,
        *,
        on_state: Callable[[MestroState], None],
        on_disconnected: Callable[[], None],
        on_connected: Callable[[], None],
        on_auth_failed: Callable[[], None],
    ) -> None:
        """Replace the callbacks (the coordinator exists after the first connect)."""
        self._on_state = on_state
        self._on_disconnected = on_disconnected
        self._on_connected = on_connected
        self._on_auth_failed = on_auth_failed

    async def async_connect(self) -> MestroHello:
        """Open the socket once; raise on failure, never retry."""
        await self._open()
        assert self.hello is not None
        return self.hello

    def async_start(self) -> None:
        """Keep the open connection alive: reconnect with backoff on loss."""
        if self._supervisor is None:
            self._supervisor = asyncio.create_task(self._supervise())

    async def async_stop(self) -> None:
        """Stop reconnecting and close the socket."""
        supervisor, self._supervisor = self._supervisor, None
        if supervisor is not None:
            supervisor.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await supervisor
        await self._teardown()

    async def async_request(self, payload: dict[str, Any]) -> Any:
        """Send one request, return the reply's `data`."""
        ws = self._ws
        if ws is None or ws.closed:
            raise MestroConnectionError("not connected")
        self._next_id += 1
        request_id = self._next_id
        future: asyncio.Future[dict[str, Any]] = (
            asyncio.get_running_loop().create_future()
        )
        self._pending[request_id] = future
        try:
            async with asyncio.timeout(REQUEST_TIMEOUT):
                await ws.send_json({"id": request_id, **payload})
                reply = await future
        except TimeoutError as err:
            raise MestroConnectionError("request timed out") from err
        except (aiohttp.ClientError, ConnectionError) as err:
            raise MestroConnectionError(str(err) or "send failed") from err
        finally:
            self._pending.pop(request_id, None)
        if not reply.get("ok"):
            raise MestroRequestError(str(reply.get("error") or "request failed"))
        return reply.get("data")

    async def async_ctl(self, command: str) -> None:
        """Run a `ctl` command (the `mestro ctl` vocabulary)."""
        await self.async_request({"op": "ctl", "cmd": command})

    async def async_set_brightness(self, brightness: int) -> None:
        """Write `display.brightness` (1..100)."""
        await self.async_request(
            {
                "op": "settings",
                "action": "set",
                "key": "display.brightness",
                "value": brightness,
            }
        )

    async def _open(self) -> None:
        self._state_received.clear()
        try:
            async with asyncio.timeout(REQUEST_TIMEOUT):
                ws = await self._session.ws_connect(
                    self._ws_url, headers=self._headers, heartbeat=HEARTBEAT
                )
        except aiohttp.WSServerHandshakeError as err:
            if err.status == 401:
                raise MestroAuthError("pairing rejected") from err
            raise MestroConnectionError(f"handshake failed ({err.status})") from err
        except (aiohttp.ClientError, TimeoutError, OSError) as err:
            raise MestroConnectionError(str(err) or "connect failed") from err

        self._ws = ws
        self._reader = asyncio.create_task(self._read(ws))
        try:
            hello = MestroHello.from_wire(
                await self.async_request({"op": "hello"}) or {}
            )
            if not hello.id or hello.protocol != SUPPORTED_PROTOCOL:
                raise MestroProtocolError(
                    f"unsupported player (protocol {hello.protocol or 'unknown'})"
                )
            try:
                async with asyncio.timeout(REQUEST_TIMEOUT):
                    await self._state_received.wait()
            except TimeoutError as err:
                raise MestroConnectionError("no state frame received") from err
        except MestroRequestError as err:
            await self._teardown()
            raise MestroProtocolError(str(err)) from err
        except BaseException:
            await self._teardown()
            raise
        self.hello = hello
        self._ready = True
        if self._on_connected is not None:
            self._on_connected()

    async def _read(self, ws: aiohttp.ClientWebSocketResponse) -> None:
        try:
            async for message in ws:
                if message.type is aiohttp.WSMsgType.TEXT:
                    self._dispatch(message.data)
        except (aiohttp.ClientError, ConnectionError) as err:
            _LOGGER.debug("Read loop ended: %s", err)
        finally:
            for future in self._pending.values():
                if not future.done():
                    future.set_exception(MestroConnectionError("connection lost"))

    def _dispatch(self, text: str) -> None:
        try:
            frame = json.loads(text)
        except ValueError:
            _LOGGER.debug("Dropping a non-JSON frame")
            return
        if not isinstance(frame, dict):
            return
        request_id = frame.get("id")
        if request_id is not None:
            future = self._pending.get(request_id)
            if future is not None and not future.done():
                future.set_result(frame)
            return
        if frame.get("event") == "state" and isinstance(frame.get("data"), dict):
            self.state = MestroState.from_wire(frame["data"])
            self._state_received.set()
            if self._on_state is not None:
                self._on_state(self.state)

    async def _supervise(self) -> None:
        attempt = 0
        while True:
            reader = self._reader
            if reader is not None:
                await asyncio.wait([reader])
                await self._teardown()
                _LOGGER.info("Connection to %s lost", self._ws_url)
                if self._on_disconnected is not None:
                    self._on_disconnected()
            while True:
                delay = self._backoff[min(attempt, len(self._backoff) - 1)]
                attempt += 1
                await self._sleep(delay)
                try:
                    await self._open()
                except MestroAuthError:
                    _LOGGER.warning("Pairing for %s was rejected", self._ws_url)
                    if self._on_auth_failed is not None:
                        self._on_auth_failed()
                    return
                except MestroConnectionError as err:
                    _LOGGER.debug("Reconnect to %s failed: %s", self._ws_url, err)
                    continue
                attempt = 0
                break

    async def _teardown(self) -> None:
        self._ready = False
        ws, self._ws = self._ws, None
        reader, self._reader = self._reader, None
        if reader is not None:
            reader.cancel()
            await asyncio.wait([reader])
        if ws is not None and not ws.closed:
            await ws.close()
