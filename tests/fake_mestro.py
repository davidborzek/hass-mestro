"""A fake mestro: the documented web protocol, served by aiohttp.

This is the contract the integration is built against — pairing via
`POST /pair`, the cookie-gated `/ws`, `hello`, `ctl`, `settings` and
the `state` push frames.
"""

from __future__ import annotations

import copy
import hashlib
import json
from typing import Any

from aiohttp import WSMsgType, web
from aiohttp.test_utils import TestServer

PIN = "ABCD2345"
PLAYER_ID = "aabbccddeeff"
PLAYER_NAME = "wohnzimmer"
VU_SKINS = ["round", "bars", "needle"]

MAX_PIN_FAILURES = 5


def token_for(pin: str) -> str:
    """The cookie value mestro derives from the PIN."""
    return hashlib.sha256(f"mestro-pair:{pin}".encode()).hexdigest()


def default_state() -> dict[str, Any]:
    """A state frame's `data` as mestro pushes it."""
    return {
        "connected": True,
        "ma_url": "http://ma.local:8095",
        "player": {"name": PLAYER_NAME, "powered": True, "playing": False},
        "view": {
            "now_playing": False,
            "queue": False,
            "vu": False,
            "standby": False,
            "vu_index": 1,
            "vu_count": len(VU_SKINS),
            "vu_skins": list(VU_SKINS),
        },
        "display": {"supported": True, "brightness": 80},
    }


class FakeMestro:
    """The server side of one player."""

    def __init__(self, pin: str = PIN) -> None:
        """Start from a paired-able player with a default state."""
        self.pin = pin
        self.state = default_state()
        self.hello: dict[str, Any] = {
            "app": "0.1.0 (abc1234)",
            "version": "0.1.0 (abc1234)",
            "protocol": 1,
            "id": PLAYER_ID,
            "name": PLAYER_NAME,
            "capabilities": {"ota": False, "display": True},
        }
        self.requests: list[dict[str, Any]] = []
        self.pair_failures = 0
        self.pair_attempts = 0
        self.ws_attempts = 0
        self.accept_ws = True
        self.reject_settings = False
        self.sockets: list[web.WebSocketResponse] = []
        self.server: TestServer | None = None

    @property
    def host(self) -> str:
        """The address the server listens on."""
        return "127.0.0.1"

    @property
    def port(self) -> int:
        """The ephemeral port."""
        assert self.server is not None
        return self.server.port

    @property
    def ctl_commands(self) -> list[str]:
        """Every `ctl` command received so far, in order."""
        return [r["cmd"] for r in self.requests if r.get("op") == "ctl"]

    def app(self) -> web.Application:
        """The routes of the real endpoint the integration touches."""
        app = web.Application()
        app.router.add_post("/pair", self._pair)
        app.router.add_get("/ws", self._ws)
        return app

    async def _pair(self, request: web.Request) -> web.Response:
        self.pair_attempts += 1
        if self.pair_failures >= MAX_PIN_FAILURES:
            return web.Response(status=429, text="too many attempts, try later")
        body = await request.json()
        if body.get("pin") != self.pin:
            self.pair_failures += 1
            status = 429 if self.pair_failures >= MAX_PIN_FAILURES else 401
            return web.Response(status=status, text="wrong pin")
        self.pair_failures = 0
        response = web.Response(status=204)
        response.set_cookie(
            "mestro_pair",
            token_for(self.pin),
            path="/",
            max_age=31536000,
            httponly=True,
            samesite="Strict",
        )
        return response

    async def _ws(self, request: web.Request) -> web.StreamResponse:
        self.ws_attempts += 1
        if request.cookies.get("mestro_pair") != token_for(self.pin):
            return web.Response(status=401, text="pairing required")
        if not self.accept_ws:
            return web.Response(status=503, text="rebooting")
        ws = web.WebSocketResponse()
        await ws.prepare(request)
        self.sockets.append(ws)
        await ws.send_json({"event": "state", "data": copy.deepcopy(self.state)})
        async for message in ws:
            if message.type is WSMsgType.TEXT:
                await ws.send_json(self._answer(json.loads(message.data)))
        return ws

    def _answer(self, request: dict[str, Any]) -> dict[str, Any]:
        self.requests.append(request)
        request_id = request["id"]
        op = request.get("op")
        if op == "hello":
            return {"id": request_id, "ok": True, "data": self.hello}
        if op == "ctl":
            return self._ctl(request_id, str(request.get("cmd", "")))
        if op == "settings" and request.get("action") == "set":
            if self.reject_settings:
                return {"id": request_id, "ok": False, "error": "refused"}
            return {"id": request_id, "ok": True}
        return {"id": request_id, "ok": False, "error": "unknown op"}

    def _ctl(self, request_id: int, cmd: str) -> dict[str, Any]:
        if cmd.startswith("vu-select:"):
            index = cmd.removeprefix("vu-select:")
            if not index.isdigit() or int(index) >= len(self.state["view"]["vu_skins"]):
                return {"id": request_id, "ok": False, "error": "unknown command"}
            return {"id": request_id, "ok": True, "data": {"cmd": cmd}}
        known = {
            "standby-on",
            "standby-off",
            "standby-toggle",
            "home",
            "now-playing",
            "queue",
            "vu",
            "play-pause",
            "next-track",
            "prev-track",
            "volume-up",
            "volume-down",
        }
        if cmd not in known:
            return {"id": request_id, "ok": False, "error": "unknown command"}
        return {"id": request_id, "ok": True, "data": {"cmd": cmd}}

    async def push_state(self) -> None:
        """Push the current `self.state` to every open socket."""
        for ws in list(self.sockets):
            if not ws.closed:
                await ws.send_json(
                    {"event": "state", "data": copy.deepcopy(self.state)}
                )

    async def drop_connections(self) -> None:
        """Close every socket, like a rebooting player."""
        for ws in list(self.sockets):
            await ws.close()
        self.sockets.clear()

    def rotate_pin(self, pin: str) -> None:
        """A new PIN file: old cookies stop working."""
        self.pin = pin

    async def start(self) -> None:
        """Listen on an ephemeral loopback port."""
        self.server = TestServer(self.app(), host=self.host)
        await self.server.start_server()

    async def stop(self) -> None:
        """Close sockets and the listener."""
        await self.drop_connections()
        if self.server is not None:
            await self.server.close()
            self.server = None
