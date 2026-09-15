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

**Phase 0 (tooling spike) — done.** Both verification scripts pass against
the live game:

- ✅ `state_reader.lua` writes a fresh, correct player/monster/quest
  snapshot every second (`docs/modding_setup.md` has the one real bug
  found and fixed along the way — a broken repeat-write timer).
- ✅ The virtual gamepad reaches the game — confirmed via MHW's menu
  "confirm" action landing on repeated South-button taps
  (`docs/risks.md` has the timing bug that caused the first failed
  attempt, and the OS-level udev diagnostics that ruled out
  detection/anti-cheat as the cause).
- ⚠️ `grim`-based capture works but is slow (~2.4 fps) until cropped to
  just the game window — see
  [`docs/performance_tuning.md`](docs/performance_tuning.md). Not a Phase
  0 blocker, but worth doing before Phase 1's environment loop needs
  real throughput.

**Phase 1 (MHWEnv + configs) — action mapping verified live, quest loop
not yet run.** `MHWEnv`, `action_space.py`, `reward.py`, and the Great
Sword / Great Jagras configs exist and pass offline tests (config
loading, a 7-case reward-logic fixture suite, `MHWEnv` construction) —
see `docs/architecture.md`'s Phase 1 decisions table.

- ✅ `attack_1`/`attack_2`/`dodge` confirmed live against the game's own
  HUD legends and actual move execution. Caught and fixed a real bug
  along the way: evdev's `BTN_NORTH`/`BTN_WEST` compass aliases are
  numerically **swapped** from their intuitive meaning (`BTN_NORTH` ==
  `BTN_X`, `BTN_WEST` == `BTN_Y`) — see `configs/weapons/greatsword.yaml`'s
  header comment before writing any new weapon config.
  `sheathe_unsheathe` was attempted twice and dropped (not load-bearing).
  Movement's sign convention is still unverified — one more pass of
  `scripts/verify_action_mapping.py --only move_forward,move_backward,strafe_left,strafe_right`
  will settle it.
- `quest.state`'s real values are still unknown — episode-boundary logic
  deliberately doesn't depend on them (see `docs/risks.md`), but
  `scripts/run_dummy_policy.py --policy idle` through a real quest will
  reveal them.

Recommended order from here: confirm movement signs → `run_dummy_policy.py
--policy idle` (one full quest) → `run_dummy_policy.py --policy random`
(the actual crash-resistance acceptance test).

## This machine's environment (recorded 2026-09-14)

- MHW install: `~/.local/share/Steam/steamapps/common/Monster Hunter World`
  (Proton, appid 582010)
- Stracker's Loader: **installed** (`loader.dll`, `dinput8.dll`,
  `loader-config.json`, 3 plugins already in `nativePC/plugins/`)
- LuaEngine: **installed** (`nativePC/plugins/LuaEngine.dll`,
  `Lua/Engine.lua` + `Lua/modules/`) — see `docs/modding_setup.md`.
- Desktop: Hyprland (wlroots, Wayland) — `mss`/X11-style capture will not
  work here; using `grim` instead (see `env/game_interface/capture.py`).
- `/dev/uinput` already has an ACL entry granting the current user rw access
  — no extra permission setup needed for the virtual-gamepad input backend.

## Setup

This machine is NixOS — use the flake, it's the recommended path here:

```sh
nix develop
```

Gives you Python with `evdev`/`Pillow` (built properly against the running
kernel, unlike the `pip`-in-a-venv route — see `docs/risks.md`), plus
`grim`/`slurp`/`wf-recorder`/`gamescope` on `PATH`.

For a non-Nix machine (e.g. if training ends up running elsewhere), the
portable fallback is still:

```sh
python3 -m venv .venv
. .venv/bin/activate
pip install -r requirements.txt
```

## Phase 0 verification

`lua_scripts/state_reader.lua` is already installed to the game's `Lua/`
folder. Launch MHW, open the in-game chat (Insert by default — works in
single-player too, LuaEngine repurposes the local text box as a command
console) and run `reload state_reader`. Then, from a `nix develop` shell:

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
