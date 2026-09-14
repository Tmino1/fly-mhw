# fly-mhw

Teaching a fruit-fly brain connectome to hunt in Monster Hunter World.

The "brain" is a recurrent network instantiated from the real synaptic wiring
of a fruit fly's brain (topology and synapse sign fixed, only a small set of
gains/biases/encoders/readout trained — same approach as
[flyvis](https://github.com/TuragaLab/flyvis) and
[haltere](https://github.com/skulitom/haltere)). See
[`docs/architecture.md`](docs/architecture.md) for the full design and the
project roadmap it's based on.

**Target pair:** Great Jagras, with the Great Sword. Monster/weapon are
config, not code — see the roadmap's Design Principles before adding either.

## Status

Phase 0 (tooling spike) — in progress.

- ✅ `grim`-based capture and the `evdev` virtual gamepad both work
  end-to-end on this machine (see `docs/risks.md` for measured numbers —
  capture is currently slow, ~2.4 fps, until cropped to just the game
  window).
- ❌ LuaEngine is not installed — blocks state-read verification. See
  [`docs/modding_setup.md`](docs/modding_setup.md).
- ⬜ Input injection has been verified to construct/close a virtual pad, but
  **not yet verified against the actual running game** — run
  `scripts/verify_input_injection.py` with MHW focused to check.

## This machine's environment (recorded 2026-09-14)

- MHW install: `~/.local/share/Steam/steamapps/common/Monster Hunter World`
  (Proton, appid 582010)
- Stracker's Loader: **installed** (`loader.dll`, `dinput8.dll`,
  `loader-config.json`, 3 plugins already in `nativePC/plugins/`)
- LuaEngine: **not installed** — no matching files found anywhere on the
  filesystem. Needs to be downloaded and dropped in; see
  `docs/modding_setup.md`.
- Desktop: Hyprland (wlroots, Wayland) — `mss`/X11-style capture will not
  work here; using `grim` instead (see `env/game_interface/capture.py`).
- `/dev/uinput` already has an ACL entry granting the current user rw access
  — no extra permission setup needed for the virtual-gamepad input backend.

## Setup

```sh
python3 -m venv .venv
. .venv/bin/activate
pip install -r requirements.txt
```

## Phase 0 verification

Once LuaEngine is installed and `lua_scripts/state_reader.lua` is dropped
into the game's `Lua/` folder and loaded (`reload state_reader` in the
in-game chat):

```sh
python scripts/verify_state_read.py
```

To check the virtual-gamepad input path (make sure MHW is focused and you're
somewhere safe to see a controller press land, e.g. the main menu):

```sh
python scripts/verify_input_injection.py
```

Both are described in [`docs/modding_setup.md`](docs/modding_setup.md) and
are the Phase 0 "done when" criteria from the roadmap.
