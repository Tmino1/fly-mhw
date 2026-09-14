# Architecture

Full design rationale, prior art, and the phased roadmap live in the
planning document this project was scoped from:
`~/.claude/plans/do-you-know-about-valiant-wreath.md` (not part of this
repo — copy the relevant sections in here as they firm up, so the repo is
self-contained once Phase 0/1 settle).

This file tracks the concrete technical decisions made so far, which are
this machine/environment's answers to the roadmap's open questions.

## Design Principles

Copied in from the roadmap now that Phase 0/1 have settled enough to make
this repo self-contained (plus one new principle Phase 1 added):

1. **Connectome topology and synaptic sign are permanently fixed.** Only
   per-edge gains, per-neuron bias/time-constant/gain, sensory encoders,
   and the motor readout are ever trained.
2. **Monster and weapon are data, not architecture.** No hardcoded monster
   name, weapon-specific input binding, or reward term anywhere in code —
   both are config files loaded at runtime.
3. **Design for one live game instance, not a simulator farm.** Every
   training-loop decision assumes real-time-only pacing and a noisy/lagged
   reward signal.
4. **The reward signal and the observation are separate, and privileged
   game state must never leak from one into the other.** `MHWEnv.step()`
   / `.reset()` return the raw screen image as the observation and
   nothing else; anything read from `state_reader.lua` (monster HP,
   player HP, quest state, ...) that feeds the reward calculation lives
   under `info["reward_debug"]` — logging/debugging only, never to be fed
   into the agent's observation or wired into a sensory-input channel in
   Phase 2. The whole point of the connectome-brain approach is learning
   from pixels + a reward scalar, the same way the Doom demo worked
   (frame in, dopamine pulse in, nothing else) — handing the brain the
   monster's exact HP directly would defeat that.

## Phase 0 decisions

| Concern | Decision | Why |
|---|---|---|
| Screen capture | `grim` (wlroots screenshot CLI), shelled out per frame | This machine runs Hyprland/Wayland, not X11 — `mss` and other X11-based capture libraries don't work here. `grim` is already installed and works on any wlroots compositor. Per-process-spawn overhead is a known limitation — see `capture.py`'s docstring. |
| Structured state IPC | Lua script writes a JSON snapshot file; Python polls it | Simplest thing likely to work without confirming LuaEngine's full API surface first. Websocket support is hinted at in LuaEngine's own docs and would be a strictly better upgrade — revisit once the real API is inspected (see `docs/modding_setup.md`). |
| Input injection | Virtual Xbox 360 gamepad via `/dev/uinput` (`python-evdev`) | Wine/Proton's controller input goes through SDL with an evdev fallback, so a uinput-created virtual pad is picked up the same way a physical controller would be. `/dev/uinput` already has an ACL entry for this user on this machine — no extra setup needed. |
| Python environment | Plain `venv` + `pip`, not the Nix-managed system Python | System `python3` here is a Nix per-user profile interpreter with no `pip`; `python3 -m venv` does produce a working `pip` inside the venv. |

## Phase 1 decisions

| Concern | Decision | Why |
|---|---|---|
| Great Sword action-space granularity | 9 discrete actions: `idle`, `move_forward/backward`, `strafe_left/right`, `attack_1`, `attack_2`, `dodge`, `sheathe_unsheathe` — see `configs/weapons/greatsword.yaml` | A minimal first pass, not a finished moveset — charge levels, tackle, camera, and triggers are deliberately excluded. Every action is tagged `verified: true/false`; only `dodge` (the `BTN_SOUTH` tap confirmed in Phase 0) is verified so far. |
| Great Jagras reward weights | HP-fraction-delta based: `monster_hp_lost_weight=1.0`, `player_hp_lost_weight=-1.0`, `step_penalty=-0.001`, terminal bonuses/penalties for defeat/cart — see `configs/monsters/great_jagras.yaml` | Only whole-monster HP is available (no per-part data), so this is the simplest reward that's actually possible. Normalized as a fraction of max HP so weights are HP-scale-independent. |
| Episode-boundary detection | Built only from confirmed signals: `quest.id`'s `-1` ↔ real-id transition, the monster list emptying, `player.health_current <= 0` | `quest.state`'s real enum values have never been observed (only the idle value `0` has) — boundary logic intentionally never branches on it, to avoid hardcoding a guess. It's logged into every step's `info` dict for later analysis instead. |
| `gymnasium` dependency | Not added yet — `MHWEnv` mirrors Gymnasium's `reset()`/`step()` tuple shapes as a plain class | Matches the project's stated dependency discipline (heavier deps land in Phase 2 with the brain work). Converting to a real `gymnasium.Env` subclass later is mechanical. `PyYAML` is the one new Phase 1 dependency actually needed, for `configs/*.yaml`. |

## Not yet decided (later phases)

- Full connectome scale vs. a scoped-down subset (Phase 2 — needs measured
  parameter count / forward-pass latency first).
- A proper win/fail/abandon distinction for episode endings, once
  `quest.state`'s real values are observed from a live run (see
  `configs/monsters/great_jagras.yaml`'s `episode_boundaries.notes`).
