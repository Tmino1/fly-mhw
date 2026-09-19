# fly-mhw

# Very early stage prototype

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

- ✅ **All 8 actions verified live.** Attacks/dodge confirmed against the
  game's own HUD legends and actual move execution; movement confirmed by
  measuring position deltas (forward/backward and left/right both exactly
  antiparallel, forward ⊥ left) plus a visual landmark check.
- ✅ Two real bugs caught by verifying instead of trusting guesses:
  evdev's `BTN_NORTH`/`BTN_WEST` compass aliases are numerically
  **swapped** from their intuitive meaning (`BTN_NORTH` == `BTN_X`,
  `BTN_WEST` == `BTN_Y`) — read `configs/weapons/greatsword.yaml`'s header
  before writing any new weapon config; and `monsters[0]` target selection
  was disproved by a live dump showing 11 simultaneous monster entities.
  `sheathe_unsheathe` was attempted twice and dropped (not load-bearing).
- ⬜ `quest.state`'s real values are still unknown — episode-boundary logic
  deliberately doesn't depend on them (see `docs/risks.md`), but
  `scripts/run_dummy_policy.py --policy idle` through a real quest will
  reveal them, along with Great Jagras's real monster id (needed to pin
  target selection exactly).

Recommended order from here: `run_dummy_policy.py --policy idle` (one full
quest) → `run_dummy_policy.py --policy random` (the crash-resistance
acceptance test). Offline regression suite: `python tests/test_reward.py`.

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

## Getting set up again (each session)

1. Launch MHW via Steam.
2. In-game, open the chat box (**Insert** by default — works in
   single-player too, LuaEngine repurposes the local text box as a command
   console) and run `reload state_reader`. (If you ever edit
   `lua_scripts/state_reader.lua`, re-copy it into the game's `Lua/`
   folder first — it's not symlinked, the game reads its own copy.)
3. Confirm state is flowing: `nix run .#verify-state-read` — should
   stream a fresh player/monster/quest snapshot every second. `Ctrl-C` to
   stop.
4. (Optional) Confirm input reaches the game: `nix run
   .#verify-input-injection` — MHW focused, somewhere harmless. See
   `docs/modding_setup.md`'s Steam Input note if it doesn't land.

## `nix run` apps

Every script in `scripts/` has a matching app — no `nix develop` shell
needed for these, each one builds/caches on first use:

```sh
nix run .#verify-state-read
nix run .#verify-input-injection
nix run .#list-windows              # dump Hyprland window class/title/geometry — see docs/performance_tuning.md
nix run .#run-dummy-policy -- \
  --weapon configs/weapons/greatsword.yaml \
  --monster configs/monsters/great_jagras.yaml \
  --state-path "$HOME/.local/share/Steam/steamapps/common/Monster Hunter World/fly_mhw_state.json" \
  --policy idle
nix run .#calibrate-keyboard-bindings -- --weapon configs/weapons/greatsword.yaml
nix run .#record-hunt   # zero args needed — defaults to the pilot pair + this machine's MHW install
```

`nix develop` is still there for anything not wrapped as an app yet (e.g.
`python tests/test_reward.py`, or ad-hoc `python -c "..."` checks).

Both `verify-state-read` and `verify-input-injection` are described in
[`docs/modding_setup.md`](docs/modding_setup.md) and are the Phase 0
"done when" criteria from the roadmap.
