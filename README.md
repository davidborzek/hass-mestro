# hass-mestro

Home Assistant integration for [mestro](https://github.com/davidborzek/mestro),
the Music Assistant player for a small touch panel. It controls what mestro
itself does — standby, the displayed view, the VU skin, the backlight and any
`mestro ctl` command. **Playback stays with Music Assistant's own Home
Assistant integration.**

It talks to the player's embedded web endpoint over its WebSocket channel
(`iot_class: local_push`): state changes arrive as pushes, no polling.

## Requirements

- Home Assistant 2026.10 or newer
- a mestro player with the web endpoint enabled (`[remote] enabled = true`,
  the default) that reports protocol 1 in its `hello` (identity, `vu_skins`,
  `vu-select`)

## Install

**HACS (custom repository):** HACS → ⋮ → *Custom repositories* → add this
repository as category *Integration* → install *mestro* → restart Home
Assistant.

**Manual:** copy `custom_components/mestro/` into your Home Assistant
`config/custom_components/` directory and restart.

## Set up and pairing

Settings → Devices & services → *Add integration* → **mestro**. A player on
the same network is discovered via mDNS (`_mestro._tcp`); otherwise enter
`host` or `host:port` (the player image serves on port 80, a desktop run on
8787 — spell that one out).

The integration pairs like a phone: it asks for the player's **pairing PIN**
(8 characters, shown on the player's Connection page and in its pairing URL
`http://host/#pair=<PIN>`), trades it via `POST /pair` for the pairing token
and stores only that token. It sends the token as the `mestro_pair` cookie on
the WebSocket upgrade.

- Wrong PIN → the form says so and you can retry. After five wrong attempts
  the player blocks pairing for 30 seconds; the form tells you to wait.
- If the player stops accepting the stored token (its PIN file was reset),
  Home Assistant starts a **reauthentication**: enter the new PIN.
- If the player drops off the network, its entities turn *unavailable*; the
  integration reconnects with a backoff of 1, 2, 5, 10, then every 30 s.

## Entities

One device per player (identified by its MAC-based id).

| Entity | What it does |
|---|---|
| `remote.<name>` | on/off = standby off/on. Activities `Now playing`, `Queue`, `VU`, `Home`; `current_activity` follows the open view. `remote.send_command` passes any `ctl` command (`play-pause`, `next-track`, `vu-select:2`, …) and honors `num_repeats` and `delay_secs`. |
| `light.<name>_display` | backlight brightness (player 1..100 ↔ HA 0..255). Only created when the player has a backlight. There is no separate backlight power: on/off map to standby. |
| `select.<name>_vu_skin` | the player's VU skins in cycle order; selecting one opens the VU view on it. |

Example automation:

```yaml
action: remote.send_command
target:
  entity_id: remote.wohnzimmer
data:
  command: [volume-up]
  num_repeats: 3
  delay_secs: 0.2
```

## Development

nixpkgs has no `pytest-homeassistant-custom-component`, so the test
dependencies live in a uv virtualenv; the flake dev shell provides Python 3.14,
`uv` and `ruff` (same version as CI).

```sh
nix develop                                   # picks up .venv if present
uv venv                                       # once
uv pip install -r requirements_test.txt       # pins HA via the plugin

ruff check . && ruff format --check .
pytest
```

To keep the venv outside the checkout, create it elsewhere and export
`HASS_VENV=/path/to/venv` before `nix develop` — the shell puts it on `PATH`.
Install with `uv pip install --python "$HASS_VENV/bin/python" -r requirements_test.txt`.

The tests run against a fake mestro (`tests/fake_mestro.py`): an aiohttp
server speaking `/pair` and `/ws` — it is the contract for the protocol in
mestro's `docs/remote.md`. The real-socket tests need loopback only.

CI (`.github/workflows/ci.yml`) runs ruff, pytest, hassfest and the HACS
action.

## License

GPL-3.0-or-later, like mestro.
