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
(`state_reader.lua`) and screen capture (JPEG, 10x the throughput of the
original PNG path) confirmed against the real game.

**Input injection — keyboard + mouse, fully verified live (2026-10-02).**
The original virtual *gamepad* does not reach MHW on this machine and the
attempt was abandoned after exhausting it — see `docs/risks.md` for the
evidence, including a Wine prefix that registers the pad as a genuine
XInput device while the game still ignores it. A uinput keyboard/mouse
works instead, reaching the game via libinput → Hyprland → XWayland. It
also matches how the human plays and how demos are recorded, removing a
latent demos-on-keyboard / agent-on-gamepad mismatch. Every binding is
confirmed live by watching the game's own animation ids.

**Tool-based action space + moveset graph — built AND verified live.**
Move-named tool calls over a Great Sword moveset graph. All 16 reachable
moves fire correctly, and the full CS → SCS → TCS chain works end to end.
Two moveset facts are encoded that the chart alone didn't give:
- a follow-up must come *after* the previous animation, never during
  (`ToolExecutor` waits it out), and
- the SCS/TCS require **forward held** — without it the game produces a
  Side Blow, so those moves are *masked* rather than sent wrong.

**Animation ids: the project's ground truth.** `state_reader.lua` logs
every change of the player's animation id (`lmtID`) per tick, and those
ids turn out to identify moves *and* charge levels exactly (consecutive
ids per level: `charged_slash` lv1/2/3 = 49304/5/6). 16 moveset nodes now
carry measured ids. Two consequences:
- **demo labelling reads the charge level instead of guessing it** from
  how long a key was held — a signal that is frame-rate dependent and
  outright meaningless when the Great Sword auto-releases a full charge;
- **move verification is automated** (`scripts/probe_moveset.py`),
  replacing a per-move "did that play? [y/n]" prompt.

**Demo recording — dry-run tested end to end.** One real hunt recorded
and labelled: 616 tool calls, combos to depth 4, 1 unresolved. The dry
run paid for itself by exposing two bugs before a long session, both
fixed: a crash labelling a move with no charge levels, and that crash
landing *before* the episode metadata was written, which left a played
hunt unrecoverable by the relabeler.

**Side infra, done and live-confirmed:**
- A working **Great Jagras arena quest** (`scripts/patch_arena_quest.py`,
  patched from a real Nexus mod file) — removes the "chase the monster
  around the open world" friction from recording sessions.
- A **quest-start trigger** (`mod_plugins/QuestStartTrigger` C# plugin +
  `scripts/trigger_quest_start.py`) — starts a hunt without walking to
  the Handler. Needed for RL fine-tuning's much higher restart rate.

### Not built yet (the critical path)

1. **Demo data.** Only the single dry-run hunt exists. This is wall-clock
   time someone has to spend playing.
2. **The imitation-learning trainer.** `training/bootstrap_imitation.py`
   was deleted with the connectome brain and has not been rebuilt for the
   tool-based action space. Nothing trains until it is.
3. **RL fine-tuning**, planned via [Q2RL](https://q2rl.rai-inst.com/).
   `brain/q_network.py` is done; `training/q_estimation.py` is still tied
   to the deleted connectome brain and needs porting to whatever policy
   head the IL rebuild produces.

### Known gaps

- **`lt` (slinger) is unbound, and clutch claw has no role at all**, so
  using either during a recording gets absorbed into a neighbouring
  label rather than flagged — mild, invisible training-data pollution.
  For demos intended for the FIRST training pass, avoid both.
  (`rb`/sprint is bound as of 2026-10-03 and is flagged correctly.)
- **The clutch claw / slinger action-space expansion is DECIDED but not
  scheduled** (2026-10-03): the clutch claw is close to required on
  harder monsters, so an agent without it is capped at easy targets.
  It's a staged v2 — a second demo batch recorded with the new moves,
  then warm-start fine-tuning on the combined set. See
  `docs/architecture.md`.
- **Mouse camera thresholds are uncalibrated placeholders**, so camera
  labels may be over/under-counted. Cheap to fix: raw events are kept, so
  `scripts/relabel_demos.py` re-derives labels with no replay.
- **An uncharged tap and a lv1 charge share one animation** (49304), so
  that single distinction still falls back to hold duration. Same for
  `true_charged_slash` lv2, which has no trustworthy measurement yet.
- **Per-move timings are measured only for the Charged and Strong Charged
  Slash.** Other moves still use placeholder durations, so longer chains
  may mistime.

### Two gotchas that cost hours — read before any live work

- **MHW ignores injected input entirely when its window is not focused**,
  and typing to a terminal steals focus. Every live script checks focus
  and aborts rather than recording garbage.
- **The game reads its own copy of `state_reader.lua`** under `Lua/` — it
  is not symlinked. A stale copy there silently produced empty animation
  logs for an hour. Re-copy and `reload state_reader` after editing it.

## Tests

Offline regression suite — no game, no evdev, no network; ~2s:

```sh
for t in tests/test_*.py; do python "$t" || echo "FAILED: $t"; done
```

A pre-commit hook runs it and blocks the commit on failure. Git doesn't
ship hooks in a checkout, so enable it once per clone:

```sh
git config core.hooksPath .githooks
```

What it proves and doesn't: passing means nothing else broke. It does
NOT mean new code is correct — `scripts/` has no offline coverage, so a
change there can pass and still be wrong. Live verification against the
game stays the evidence for anything that drives it.

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
  — no extra permission setup needed for the virtual keyboard/mouse.
- This user is in the `input` group, needed to READ `/dev/input/eventN`
  when recording your own play.
- MHW runs under a Steam Linux Runtime (pressure-vessel) sandbox whose
  `/dev/input` is a static snapshot taken at launch. That is one reason
  the virtual gamepad never worked; the keyboard/mouse path avoids it
  entirely by going through the compositor.

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
4. (Optional) Confirm input reaches the game — MHW focused, somewhere
   harmless, weapon drawn:
   `python scripts/probe_moveset.py --only charged_slash,wide_slash`
   It should report `charged_slash lmt=49304` and `wide_slash lmt=49258`.
   (`nix run .#verify-input-injection` tests the *gamepad*, which does
   not work on this machine — see `docs/risks.md`.)

Also worth knowing before any live run:
   - **Hands off the keyboard.** MHW ignores injected input while
     unfocused, and typing anywhere steals focus. Scripts abort rather
     than record garbage, but they can only abort what they notice.
   - **Whiff, don't hit anything,** when measuring timings — hitstop
     stretches animations.

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
nix run .#verify-tools  # SUPERSEDED by scripts/probe_moveset.py (below) — it asks a human
                        # "did that play?" per move, which the animation log now answers
nix run .#audit-tool-labels   # demo labels + moveset graph vs the game's own lmtIDs
nix run .#relabel-demos       # re-label recorded episodes after a graph/timing change
nix run .#patch-arena-quest -- --source ... --output ... --quest-id 90099  # see docs/architecture.md
```

Two scripts have no `nix run` app yet — run them directly (inside
`nix develop`, or with `.venv/bin/python`):

```sh
# Start a hunt without walking to the Handler (needs the C# plugin built).
python scripts/trigger_quest_start.py --quest-id 90099

# Drive every move in the moveset and read back which animation each one
# actually produced. Replaces verify-tools' per-move y/n prompt. Game
# focused, weapon drawn, hands off; aborts on lost focus or bad state.
python scripts/probe_moveset.py                     # every move once
python scripts/probe_moveset.py --levels            # every charge level too
python scripts/probe_moveset.py --only charged_slash
```

It never edits the moveset YAML — it reports, and ids go in after a human
has looked. That rule exists because a wrong id was twice recorded from a
confident-looking run.

`nix develop` is still there for anything not wrapped as an app yet (e.g.
the `tests/` suite, or ad-hoc `python -c "..."` checks).

Both `verify-state-read` and `verify-input-injection` are described in
[`docs/modding_setup.md`](docs/modding_setup.md) and are the Phase 0
"done when" criteria from the roadmap.
