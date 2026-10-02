# mhw-rl

# Very early stage prototype

Teaching an agent to hunt in Monster Hunter World from pixels: imitation
learning on recorded human hunts, then RL fine-tuning against the live game.

The project started as "teach a fruit-fly brain connectome to hunt"
(hence the old repo name, `fly-mhw`). The connectome was dropped on
2026-09-24 in favor of an ordinary learned policy, and the project has
since moved to a **tool-based action space**: move-named tool calls
(`charged_slash`, `dodge`, ...) validated against a **moveset graph**
that tracks the Great Sword's actual combo state, replacing both the
original 8 flat discrete actions and raw key-state polling. See
[`docs/architecture.md`](docs/architecture.md) for the full design and
decision history, and [`docs/ideas.md`](docs/ideas.md) for ideas still
under discussion.

**Target pair:** Great Jagras, with the Great Sword. Monster/weapon are
config, not code — see `docs/architecture.md`'s Design Principles before
adding either.

## Status

**Phase 0 (tooling spike) — done, verified live.** State reads
(`state_reader.lua`), virtual-gamepad input, and screen capture (now
JPEG, 10x the throughput of the original PNG path) all confirmed working
against the real game. See `docs/architecture.md`'s Phase 0 table.

**Phase 1 (original 8-action `MHWEnv`) — done, verified live, now
superseded.** The original flat action space (`idle`, movement,
`attack_1/2`, `dodge`) was fully built and live-verified, catching two
real bugs along the way (swapped evdev compass aliases, a target-
selection bug with multiple monster entities). It's superseded by the
tool-based action space below, but the legacy files
(`env/action_space.py`, `configs/weapons/greatsword.yaml`,
`scripts/verify_action_mapping.py`) stay for the old v1 demos.

**Tool-based action space + moveset graph — built, offline-tested,
NOT verified live yet.** This is the current direction (see
`docs/architecture.md`'s "Tool-based action space + moveset graph"
section): move-named tool calls, a Great Sword moveset graph transcribed
from the Iceborne flowchart that re-roots at the latest move,
timestamped-event demo recording, and an lmtID-based label audit.
**Next up:** `verify-tools`, rebinding keys, `audit-tool-labels` over
real hunts — none of this has touched the live game yet.

**Side infra, done and live-confirmed:**
- A working **Great Jagras arena quest** (`scripts/patch_arena_quest.py`,
  patched from a real Nexus mod file) — removes the "chase the monster
  around the open world" friction from recording sessions.
- A **quest-start trigger** (`mod_plugins/QuestStartTrigger` C# plugin +
  `scripts/trigger_quest_start.py`) — starts a hunt without walking to
  the Handler, confirmed live 2026-09-19. Needed for RL fine-tuning's
  much higher episode-restart rate.

**Not built yet:** Phase 3's imitation-learning training script
(`training/bootstrap_imitation.py`) was deleted along with the
connectome brain and hasn't been rebuilt against the new tool-based
action space. The planned IL→RL fine-tuning technique is
[Q2RL](https://q2rl.rai-inst.com/) — an early prototype
(`brain/q_network.py`, done; `training/q_estimation.py`, still tied to
the deleted connectome brain) is parked pending that IL rebuild.

Offline regression suite (no game, no evdev needed except where noted —
runs on macOS too):

```sh
for t in tests/test_*.py; do python "$t" || echo "FAILED: $t"; done
```

## This machine's environment (recorded 2026-09-14)

- MHW install: `~/.local/share/Steam/steamapps/common/Monster Hunter World`
  (Proton, appid 582010)
- Stracker's Loader: **installed** (`loader.dll`, `dinput8.dll`,
  `loader-config.json`, 3 plugins already in `nativePC/plugins/`)
- LuaEngine: **installed** (`nativePC/plugins/LuaEngine.dll`,
  `Lua/Engine.lua` + `Lua/modules/`) — see `docs/modding_setup.md`.
- SharpPluginLoader: **installed** (`mod_plugins/QuestStartTrigger`'s C#
  plugin builds against it) — see `docs/modding_setup.md`.
- Desktop: Hyprland (wlroots, Wayland) — `mss`/X11-style capture will not
  work here; using `grim` instead (see `env/game_interface/capture.py`).
- `/dev/uinput` already has an ACL entry granting the current user rw access
  — no extra permission setup needed for the virtual-gamepad input backend.

## Setup

This machine is NixOS — use the flake, it's the recommended path here:

```sh
nix develop
```

Gives you Python with `evdev`/`Pillow`/`pycryptodome` (built properly
against the running kernel, unlike the `pip`-in-a-venv route — see
`docs/risks.md`), plus `grim`/`slurp`/`wf-recorder`/`gamescope` on `PATH`.

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
  --monster configs/monsters/great_jagras.yaml \
  --state-path "$HOME/.local/share/Steam/steamapps/common/Monster Hunter World/fly_mhw_state.json" \
  --policy idle
nix run .#calibrate-keyboard-bindings   # keys -> input roles, plus a mouse camera sweep
nix run .#record-hunt   # zero args needed — defaults to the pilot pair + this machine's MHW install
nix run .#verify-tools  # live check of each move via its shortest combo path (--chain a,b,c)
nix run .#audit-tool-labels   # demo labels + moveset graph vs the game's own lmtIDs
nix run .#relabel-demos       # re-label recorded episodes after a graph/timing change
nix run .#patch-arena-quest -- --source ... --output ... --quest-id 90099  # see docs/architecture.md
```

`scripts/trigger_quest_start.py` doesn't have a `nix run` app yet — invoke
it directly inside `nix develop`: `python scripts/trigger_quest_start.py
--quest-id 90099`.

`nix develop` is still there for anything not wrapped as an app yet (e.g.
the `tests/` suite, or ad-hoc `python -c "..."` checks).

Both `verify-state-read` and `verify-input-injection` are described in
[`docs/modding_setup.md`](docs/modding_setup.md) and are the Phase 0
"done when" criteria from the roadmap.
