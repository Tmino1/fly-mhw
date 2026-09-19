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
| Great Sword action-space granularity | 8 discrete actions: `idle`, `move_forward/backward`, `strafe_left/right`, `attack_1`, `attack_2`, `dodge` — see `configs/weapons/greatsword.yaml` | A minimal first pass, not a finished moveset — charge levels, tackle, camera, and triggers are deliberately excluded. `sheathe_unsheathe` was attempted and dropped (two live guesses came up empty; not load-bearing for Phase 1). `attack_1`/`attack_2`/`dodge` are confirmed live against the game's own HUD legends and actual move execution — only movement's sign convention is still unverified. Verifying live also caught a real bug: evdev's `BTN_NORTH`/`BTN_WEST` compass aliases are numerically swapped from their intuitive meaning (`BTN_NORTH` == `BTN_X`, `BTN_WEST` == `BTN_Y`) — always use the letter alias, never the compass one, in any weapon config. |
| Great Jagras reward weights | HP-fraction-delta based: `monster_hp_lost_weight=1.0`, `player_hp_lost_weight=-1.0`, `step_penalty=-0.001`, terminal bonuses/penalties for defeat/cart — see `configs/monsters/great_jagras.yaml` | Only whole-monster HP is available (no per-part data), so this is the simplest reward that's actually possible. Normalized as a fraction of max HP so weights are HP-scale-independent. |
| Episode-boundary detection | Built only from confirmed signals: `quest.id`'s `-1` ↔ real-id transition, the monster list emptying, `player.health_current <= 0` | `quest.state`'s real enum values have never been observed (only the idle value `0` has) — boundary logic intentionally never branches on it, to avoid hardcoding a guess. It's logged into every step's `info` dict for later analysis instead. |
| `gymnasium` dependency | Not added yet — `MHWEnv` mirrors Gymnasium's `reset()`/`step()` tuple shapes as a plain class | Matches the project's stated dependency discipline (heavier deps land in Phase 2 with the brain work). Converting to a real `gymnasium.Env` subclass later is mechanical. `PyYAML` is the one new Phase 1 dependency actually needed, for `configs/*.yaml`. |

## Phase 3 decisions

| Concern | Decision | Why |
|---|---|---|
| Recording your real (keyboard + mouse) input | Calibrate, never guess: `scripts/calibrate_keyboard_bindings.py` watches real key/button-down events while you press your actual in-game keys, writes `configs/keyboard_bindings.yaml` | MHW's `config.ini` has no keyboard-binding section, so real bindings are unknowable from config alone. A passive (non-`.grab()`'d) `evdev.InputDevice` open reads keyboard *and* mouse device events without interfering with normal use — confirmed live. |
| Multiple keys held at once → one action per tick | `demos/keyboard_bindings.py`'s `ACTION_PRIORITY`: `attack_1, attack_2, dodge` win over the four movement actions | Real play routinely holds movement while attacking (e.g. a forward lunge); the discrete action space has no compound action for that, so a precedence rule is needed. Collisions are logged, not silently dropped — a diagnostic for the open "how much data is enough" question. |
| Demo storage format | Per-frame PNG (`Pillow`, already a dependency) + a JSONL sidecar per episode — no video-encoding dependency | Zero new Python deps for a first pass; `wf-recorder` (already in `flake.nix`'s devShell) stays available as an escape hatch if storage/IO ever becomes the bottleneck. |
| Episode-boundary reuse | `demos/recorder.py` imports `env/reward.py`'s `RewardModel` directly (fully standalone, no `MHWEnv`/gamepad dependency) and the promoted `quest_id()` helper (moved from `mhw_env.py`'s private `_quest_id` into `env/game_interface/lua_bridge.py`) | Confirmed both were already decoupled enough to reuse as-is — avoided reimplementing episode-boundary logic a second time. |
| Recording session shape | `scripts/record_hunt.py` runs as a long-lived session, not one hunt per invocation — after each episode ends it automatically goes back to waiting for the next quest-accept, looping until Ctrl+C or `--max-episodes` | Launching the recorder fresh and timing it against each individual quest-accept proved impractical live — coordinating "start the recorder, then accept within N seconds" for every single hunt doesn't scale to recording dozens of them. `--reset-timeout` (10 min default) covers the between-hunts gap (restocking, traveling), not just one hunt's worth of patience. |

## Not yet decided (later phases)

- Full connectome scale vs. a scoped-down subset (Phase 2 — needs measured
  parameter count / forward-pass latency first).
- A proper win/fail/abandon distinction for episode endings, once
  `quest.state`'s real values are observed from a live run (see
  `configs/monsters/great_jagras.yaml`'s `episode_boundaries.notes`).
- **Imitation-learning dataset size for Great Sword vs. Great Jagras**
  (Phase 3). No fixed target — reasoning from chat, 2026-09-18: the action
  space is small and discrete (8 actions) and the connectome brain's
  trainable surface is modest by design (per-edge/per-neuron scalars, not
  a free-form weight matrix), both of which argue for less data than
  typical game-IL work; pixel-only observation (Design Principle 4 — no
  structured state leaks into the input) argues for more, since the
  visual encoder needs real camera-angle/position coverage. Rough
  estimate: "full" (held-out accuracy plateaus) likely lands around
  60–120 hunts (~150k–400k frame/action transitions), but the actual
  target should be found empirically — record in batches (~15-30 hunt
  starter, then +15 increments), track held-out action-prediction
  accuracy per batch, stop when it plateaus. Diversity across monster
  states (enraged vs. not, recoveries from mistakes, not just clean runs)
  matters more than raw hunt count.
- **IL→RL transition mechanics** (Phase 4). The roadmap already calls for
  behavior-cloning bootstrap then online RL fine-tuning — two things
  still need deciding once Phase 3 produces a checkpoint to fine-tune:
  (1) **algorithm choice** — a replay-buffer-based/off-policy method
  (DQN-family, given the small discrete action space, or an off-policy
  actor-critic) likely beats a fully on-policy method like PPO here,
  since with only one real-time game instance every expensive live
  transition should be reusable across multiple gradient updates rather
  than spent on a single rollout; (2) **guarding against policy
  collapse** — naive RL fine-tuning of a decent IL policy risks a few bad
  early updates wrecking it before RL improves anything. Standard
  mitigation: keep a BC-regularization term (KL penalty toward the IL
  policy, or a blended BC+RL loss) early in fine-tuning rather than
  switching to pure RL loss immediately — the offline-to-online RL
  literature (AWAC, IQL, similar) uses this pattern for the same reason.
