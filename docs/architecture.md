# Architecture

The project was scoped from a planning document outside this repo
(`~/.claude/plans/do-you-know-about-valiant-wreath.md`). That roadmap was
built around a fruit-fly connectome brain, which was **dropped on
2026-09-24** (see "Policy model + privileged IL" below). Its Phase 0/1/3/4
structure (tooling → environment → imitation learning → RL fine-tuning)
still holds; its Phase 2 (connectome brain) does not. This file is now
the source of truth.

This file tracks the concrete technical decisions made so far.
`docs/ideas.md` is the running log of ideas discussed but not yet
adopted or built (the MoE/expert design, the GNN head, ...).

## Design Principles

Numbering is kept stable because code and docs refer to principles by
number.

1. ~~Connectome topology and synaptic sign are permanently fixed.~~
   **Retired 2026-09-24** along with the connectome brain. The policy is
   an ordinary learned model, and every part of it is trainable.
2. **Monster and weapon are data, not architecture.** No hardcoded monster
   name, weapon-specific input binding, or reward term anywhere in code —
   both are config files loaded at runtime.
3. **Design for one live game instance, not a simulator farm.** Every
   training-loop decision assumes real-time-only pacing and a noisy/lagged
   reward signal.
4. **The policy's inputs are pixels plus its own efference copy — in
   every phase. Privileged game state can shape training during imitation
   learning, never the inputs, and only the reward during RL.**
   (Rewritten 2026-09-24; the old version allowed privileged state for
   logging/debugging only.)
   - **Inputs (IL and RL alike):** the raw screen image, plus the
     agent's own combo-graph state (root, path, available options). That
     state is computed only from the agent's own tool calls, never from
     game memory. The inputs must be identical in both phases: a policy
     that learned to lean on an input during IL breaks the moment that
     input disappears in RL.
   - **During IL:** anything `state_reader.lua` reads (monster HP and
     animation IDs, player HP and `lmtID`, weapon gauges, quest state,
     ...) may be used as an **auxiliary prediction target** or seen by
     a **privileged teacher** that is distilled into the pixels-only
     student, as well as for label checking. The policy may predict it,
     never see it.
   - **During RL:** privileged state feeds only the reward
     (`info["reward_debug"]`), as before. Whether auxiliary targets carry
     on during RL fine-tuning is not yet decided.

   Why keep pixels as the only game-facing input: an agent that plays
   off memory reads isn't playing the game a human plays, and it can't
   run without the mod. Using privileged state as a teacher/target during
   IL is how the pixel policy gets trained fast on scarce demo data
   without depending on it.

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
| `gymnasium` dependency | Not added yet — `MHWEnv` mirrors Gymnasium's `reset()`/`step()` tuple shapes as a plain class | Matches the project's stated dependency discipline (heavier deps land in Phase 2 with the model work). Converting to a real `gymnasium.Env` subclass later is mechanical. `PyYAML` is the one new Phase 1 dependency actually needed, for `configs/*.yaml`. |

## Phase 3 decisions

| Concern | Decision | Why |
|---|---|---|
| Recording your real (keyboard + mouse) input | Calibrate, never guess: `scripts/calibrate_keyboard_bindings.py` watches real key/button-down events while you press your actual in-game keys, writes `configs/keyboard_bindings.yaml` | MHW's `config.ini` has no keyboard-binding section, so real bindings are unknowable from config alone. A passive (non-`.grab()`'d) `evdev.InputDevice` open reads keyboard *and* mouse device events without interfering with normal use — confirmed live. |
| Multiple keys held at once → one action per tick | **Superseded 2026-09-24** by the tool-based action space below (events, not held-key polling). Was: `demos/keyboard_bindings.py`'s `ACTION_PRIORITY`: `attack_1, attack_2, dodge` win over the four movement actions | Real play routinely holds movement while attacking (e.g. a forward lunge); the discrete action space has no compound action for that, so a precedence rule is needed. Collisions are logged, not silently dropped — a diagnostic for the open "how much data is enough" question. |
| Demo storage format | Per-frame PNG (`Pillow`, already a dependency) + a JSONL sidecar per episode — no video-encoding dependency | Zero new Python deps for a first pass; `wf-recorder` (already in `flake.nix`'s devShell) stays available as an escape hatch if storage/IO ever becomes the bottleneck. |
| Episode-boundary reuse | `demos/recorder.py` imports `env/reward.py`'s `RewardModel` directly (fully standalone, no `MHWEnv`/gamepad dependency) and the promoted `quest_id()` helper (moved from `mhw_env.py`'s private `_quest_id` into `env/game_interface/lua_bridge.py`) | Confirmed both were already decoupled enough to reuse as-is — avoided reimplementing episode-boundary logic a second time. |
| Recording session shape | `scripts/record_hunt.py` runs as a long-lived session, not one hunt per invocation — after each episode ends it automatically goes back to waiting for the next quest-accept, looping until Ctrl+C or `--max-episodes` | Launching the recorder fresh and timing it against each individual quest-accept proved impractical live — coordinating "start the recorder, then accept within N seconds" for every single hunt doesn't scale to recording dozens of them. `--reset-timeout` (10 min default) covers the between-hunts gap (restocking, traveling), not just one hunt's worth of patience. |
| Auto-skip the post-hunt "return to camp" wait | A direct memory write from `lua_scripts/state_reader.lua`, gated on a flag file `DemoRecorder` creates/removes (`fly_mhw_skip_quest_end.flag`, next to `fly_mhw_state.json`) — no input injection, no SharpPluginLoader dependency, at all | Two earlier approaches failed first: a `BTN_SOUTH` gamepad tap did nothing (confirmed live — turned out to be a keyboard/mouse-driven UI, not a gamepad one, from a screenshot showing a "Tab" key icon), and UI automation (SharpPluginLoader's F9 menu, click a button) would have needed a new absolute-position pointer-click mechanism and been fragile to layout changes. The actual fix replicates SharpPluginLoader's own open-source "Quest End Skip" example plugin's technique — `Quest.QuestEndTimer.SetToEnd()` is just `Timer.Time = Timer.MaxTime` (read directly from `github.com/Fexty12573/SharpPluginLoader`'s `Quest.cs`/`Timer.cs`, not guessed) — as a raw memory write at the same `sQuest` singleton our own Lua already resolves (confirmed: SPL's own source lists `CurrentQuestId`/`QuestState` at the exact same `+0x4C`/`+0x54` offsets `Engine_quest.lua` already uses). The flag file only exists while a recording session is actively requesting it, so normal untracked play never sees this at all (explicit requirement). |
| Capture format: JPEG, not PNG (supersedes the "Demo storage format" row above) | `env/game_interface/capture.py`'s `capture_frame()` now defaults to JPEG (quality 85); `demos/recorder.py` writes grim's raw bytes straight to disk instead of round-tripping through PIL (`frame_%06d.jpg` for new episodes, existing `.png` episodes still load fine) | Measured live at this project's actual capture geometry (2003,33 3324x1374), 2026-09-19: PNG capture alone averaged 280ms/frame (sustained 3.47 fps) — almost certainly the real reason the pipeline topped out below its documented 5fps target, since one capture could already exceed the 200ms step budget. JPEG: 33.8 fps sustained (10x), PIL decode 4x faster, files 2.8x smaller. |

## Tool-based action space + moveset graph (2026-09-24, branch `tool-action-space`)

Replaces the Phase 1 8-action space and the Phase 3 key-state reducer.
**Nothing here is verified live yet** — built offline, covered by
`tests/`, with live checks listed at the end of this section. The legacy
files (`env/action_space.py`, `configs/weapons/greatsword.yaml`,
`scripts/verify_action_mapping.py`) stay for v1 demos.

**Why:** the old representation misstated what happened in the game.
Polling held keys every 0.2s dropped short taps and hold durations (a
charged slash was labelled the same as a tap), collapsed simultaneous
presses to one "winner", never recorded the mouse (camera), and — most
importantly — ignored combo context: the same button is a different move
depending on what came before.

| Concern | Decision | Why |
|---|---|---|
| What the policy picks | **Move-named tool calls with small discrete args** (`env/tools.py`, `configs/weapons/greatsword_tools.yaml`): e.g. `strong_charged_slash(direction, level)`, `wide_slash(direction)`, plus always-available `dodge`, `sheathe`, `move`, `wait`, `camera`. Each call runs to completion (a semi-MDP). Flat index *and* factored (tool head + arg heads) views are both exposed. | User decision. Moves, not buttons, are the meaningful unit; discrete args keep the readout small. Which readout Phase 2 uses is left open. |
| Aim | Chosen by the policy from pixels: a `direction` arg on every attack/dodge, plus a `camera` tool. No target camera, no Lua-driven aiming. | User decision — keeps Design Principle 4. |
| Combo context | **Moveset graph, re-rooted at the latest move** (`env/moveset_graph.py`, `configs/weapons/greatsword_moveset.yaml`), transcribed from the MHW + Iceborne Great Sword flowchart (/u/Famas_1234, @DWiselight). The tracker resolves an input against the root's own edges → `continues_as` → global edges → neutral (a re-root). Charges are one call, with the Charge 1/2/3 node recorded as `via`. Non-attack tools never move the root; only the combo window expiring does. | User decisions. Lets the model learn the *paths* from neutral to deep moves (True Charged Slash) as sequences. |
| Invalid moves | **Masked**: a move is valid iff some input, sent from the current root, resolves to it (the root's own edges, or neutral's for inputs the root doesn't claim). A masked call is a logged no-op that still costs a step. | User decision. The mask is exactly the tracker's resolution rule, so demo labels and agent calls can never disagree about what's possible. |
| Graph state as a policy input | **An input (efference copy), decided 2026-09-24; not wired yet.** Every demo call and every `MHWEnv` step carries the combo state (root, path, depth, available options, relative label) in `info["moveset"]`. It's driven only by the agent's own calls — never Lua. `MHWEnv`'s observation is still the bare image; Phase 2 feeds the policy from `info["moveset"]`. | User decision — originally deferred, settled by the Principle 4 rewrite. |
| Demo labels | Timestamped evdev events (`demos/input_events.py`) → input primitives (`demos/tool_segmenter.py`) → moves via the tracker. Raw events are stored per episode, so `scripts/relabel_demos.py` can re-label old hunts after any graph/timing change. | User decision (input events + lmtID check). |
| Label checking | `state_reader.lua` logs every player `lmtID` change during recording sessions; `scripts/audit_tool_labels.py` builds an lmtID catalog from *trusted* labels and checks label consistency, unseen edges, candidate edges, undeclared transitions and combo gaps. lmtIDs never feed a label or an observation. | Needed to verify the graph against the game. Trusted-only matters: a synthetic test showed the catalog otherwise gets poisoned by the very labels under audit. |
| Unconfirmed edges | Declared: every chart edge traced cleanly, plus `side_blow_1 → hold Y → SCS` (user: tentative). Everything else unsure lives in `candidates:` — ignored by the tracker, given a SUPPORTED / contradicted verdict by the audit from **expert demonstrations**. The chart's orange "Wide Slash" circles mean "continue as if after Wide Slash" (user-confirmed). | User decision. |
| Overlapping input | Camera pans during a move/attack are kept and flagged `overlaps`; the executor stays sequential. WASD held during an attack is its aim, not a separate move. | User decision — measure how often it happens first. |
| Scope | Every chart move reachable with Y/B/A/RT. Sprint/slide/slope/aerial moves and slinger burst are graph nodes marked `reachable: false`; binding `rb`/`lt` flags demos that use them as `unrepresentable`. | User decision. |

**Live checks still to do:** re-copy `state_reader.lua` (see
`docs/modding_setup.md`); `nix run .#verify-tools` (confirm X/RT, tune
`level_seconds`, run `--chain` for the charged-slash path); recalibrate
bindings (bind `rt`/`rb`/`lt`, run the mouse sweep); record expert hunts
→ `nix run .#audit-tool-labels` → promote/drop candidates, set real
`combo_window_s`, flip edges to `verified`; `run-dummy-policy --policy
random` through one quest.

## Policy model + privileged IL (2026-09-24)

Nothing here is built yet — Phase 2 hadn't started — so no code changes;
the environment layer (`MHWEnv`, tools, moveset graph, recorder,
segmenter, label audit) carries over untouched.

| Concern | Decision | Why |
|---|---|---|
| The connectome brain | **Dropped.** The policy is an ordinary learned model (vision encoder → temporal model → readout), all of it trainable. Design Principle 1 is retired. | User decision, made alongside the move toward one base model across many weapons and monsters (`docs/ideas.md`: compositional weapon/monster experts, a GNN over the moveset graph). |
| What privileged state may do | **IL:** auxiliary prediction targets, a privileged teacher distilled into a pixels-only student, and label checking. **RL:** reward only. **Never:** a policy input. | User decision — Design Principle 4 as rewritten above. Demo data is scarce (one live instance, real time only); privileged targets and a teacher get far more out of each hunt without the deployed policy depending on the mod. |
| Graph state | A policy input in both phases (efference copy). | User decision. It's the agent's own action history, not game memory, so it passes the strict RL rule too. |
| Data and trainable surface | More trainable parameters than the connectome's per-edge/per-neuron scalars, so expect to need more demos, or a pretrained vision backbone to offset them. The batch-until-plateau recording plan below still applies. | Consequence of the above. |

## Not yet decided (later phases)

- **Policy architecture.** Being explored in `docs/ideas.md`: a shared
  base with per-weapon and per-monster experts composed per matchup,
  tactics vs. mechanics levels, a shared monster-tell reader with a
  family tier, and a GNN over the moveset graph as the weapon-side action
  head. The suggested first test is a 2×2 weapon × monster grid with one
  cell held out. Needs deciding before Phase 2 code starts; the "Open"
  items there list what's unresolved.
- **Whether to expand the Great Sword action space to cover clutch claw
  and slinger bursts.** Raised 2026-09-19: both are real parts of
  high-level Great Sword play (clutch claw wall-bang topples, slinger
  elemental-phial application) and relevant prep for tougher targets like
  Alatreon specifically. Still out of scope in the tool-based action
  space (2026-09-24): `slinger_burst` is a `reachable: false` node in the
  moveset graph, and clutch claw isn't in the graph at all. Until this
  is decided, **don't use clutch claw/slinger during recorded hunts** —
  bind the `lt` role (scripts/calibrate_keyboard_bindings.py) and slinger
  presses get flagged `unrepresentable` instead of silently dropped, but
  clutch claw has no role yet, so those frames would be mislabelled.

  **If/when this is decided, the natural mechanism is a staged expansion,
  not a dynamic one** (raised in chat, 2026-09-19): IL only ever learns
  what's in the demonstration data — there's no exploration process for a
  behavior-cloned policy to "discover" that new actions became available
  mid-training the way an RL curriculum might unlock content. So this
  isn't "the model gradually opens up its own action space" — it's
  collect a second demo batch with the expanded moveset, then continue
  training on the combined dataset with a larger action space. With the
  tool-based space that means new tools + new graph nodes/edges. The
  original plan here — keep the trained connectome core and warm-start
  just a grown readout — went with Design Principle 1 (2026-09-24). If
  the readout scores graph nodes (the GNN head in `docs/ideas.md`), new
  nodes need no new readout parameters at all; either way it's warm-start
  fine-tuning on the combined dataset, not retraining from scratch.
  Recommended sequencing: get one clean basic-moveset IL policy working
  end-to-end first, treat this expansion as a deliberate v2 stage after —
  not something to fold into the first training pass.
- **Readout form** (Phase 2): absolute tool calls, factored heads, or
  graph-relative (an index into the root's options, or scores over graph
  nodes). All are derivable from the recorded data without re-recording.
  (Whether the graph state is an *input* was settled 2026-09-24: yes —
  see Design Principle 4.)
- A proper win/fail/abandon distinction for episode endings, once
  `quest.state`'s real values are observed from a live run (see
  `configs/monsters/great_jagras.yaml`'s `episode_boundaries.notes`).
- **Starting a quest programmatically, without walking to the Handler.**
  Raised 2026-09-19 — user's call: deferred for now, "probably more
  important for RL when we get there" (RL fine-tuning needs far more
  episode restarts than IL data collection, so the per-restart friction
  matters more there). Found the real mechanism but didn't build it, given
  the risk: SharpPluginLoader's own source (`Quest.cs`, already installed
  on this machine) exposes the native function the game calls internally
  on quest accept — `AcceptQuest(questMgr, questId, bool)` at a known
  address (`AddressRepository.Get("Quest:AcceptQuest")`), which
  SharpPluginLoader already *hooks* (intercepts) but doesn't itself call
  directly anywhere in the file. A small custom SharpPluginLoader C#
  plugin could call it directly via the same `NativeFunction<...>`
  wrapper the file already uses for `GetQuestName` — buildable on Linux
  with the dotnet SDK, no Wine needed, same deployment path (drop the DLL
  in `nativePC/plugins/csharp/`) already used for Yomi Utils. **Not
  attempted**: unlike the quest-end-timer skip (a plain memory read/write,
  safe and well-scoped), this means *calling* a native function, and
  `questMgr`'s correct value is inferred, not confirmed — every other
  hooked function in the same file (`EnterQuest`, `LeaveQuest`,
  `AbandonQuest`, etc.) takes the same `nint questMgr` first parameter and
  none reference any singleton besides `sQuest`, so `questMgr ==
  Quest.SingletonInstance.Instance` (the same pointer this project's own
  `state_reader.lua` already resolves for the timer skip) is a reasonable
  inference — but if it's wrong, the likely failure mode is a game crash,
  not a quiet no-op. Whoever picks this up should test on a throwaway
  session, not mid-recording.
- **IL -> RL fine-tuning technique (Phase 4): Q2RL.** Raised 2026-09-19,
  still the plan as of 2026-09-25 even after dropping `ConnectomeBrain`.
  [Q2RL](https://q2rl.rai-inst.com/) (Dodeja et al., RSS 2026; RAI
  Institute/Brown/Northeastern; MIT-licensed code at
  github.com/rai-opensource/q2rl) extracts a Q-function for free from a
  trained BC policy's action log-probs/entropy (no extra training —
  assumes the BC policy approximates a Boltzmann distribution), then
  during RL fine-tuning gates between that frozen BC-derived Q and a
  trainable RL Q, taking whichever is higher at each step — directly
  targets "RL fine-tuning wrecks the imitation-learned policy," exactly
  the failure mode this project's own IL->RL transition will need to
  avoid. The math still fits: whatever replaces `ConnectomeBrain`'s
  `motor_readout` for the tool-based action space is still expected to
  output a softmax over discrete actions, which *is* a Boltzmann
  distribution — arguably a more direct fit than the continuous
  Gaussian-mixture robot policies Q2RL was built around.

  **Don't adopt the repo/dependencies** — it's JAX (this project is
  PyTorch) with a deep robotics-specific dependency chain (MuJoCo, D4RL,
  Adroit envs, robomimic, wandb, Docker/CUDA) built for continuous-control
  manipulation benchmarks, none of which apply here. The reusable part is
  the algorithmic idea (Q-estimation + Q-gating), not the codebase — an
  early offline-only prototype (`brain/q_network.py`,
  `training/q_estimation.py`, built against `ConnectomeBrain`'s softmax
  output) exists on a local branch and needs porting to whatever policy
  head the tool-based action space ends up with. Premature to finish now:
  Phase 4 (RL fine-tuning) hasn't started, and Phase 3's IL itself needs
  rebuilding for the new action space first. Revisit when Phase 4
  actually starts.
- **Great Jagras arena quest — side project, not part of the ML pipeline,
  raised 2026-09-19, confirmed working live.** Motivation: recording
  sessions currently chase Great Jagras around the open world; a
  fixed-arena quest removes that.

  Extracting a real quest file from the game's own chunk archive was a
  dead end — needs Oodle's proprietary `oo2core_8_win64.dll`, unavailable
  anywhere on this machine or its Steam library. Worked around it: the
  user downloaded an existing Nexus quest mod ("Arch Tempered Great
  Jagras V2"), which ships a real, already-valid `.mib` — no chunk
  extraction needed. `scripts/patch_arena_quest.py` decrypts it (Blowfish
  ECB + a per-4-byte bswap wrapper, fixed key — format reverse-engineered
  directly from Aradi147/MHW-Quest's open-source Quest Editor, confirmed
  byte-for-byte correct: decrypted quest_id read back as exactly 90001,
  matching the source file's own name), patches quest ID / map / stars /
  rank / tempered flags, re-encrypts with a round-trip assertion before
  ever writing.

  **A loose `.mib` alone did nothing — the real mechanism needed a
  separate, already-installed-but-inert plugin.** Dropping
  `questData_90099.mib` into `nativePC/quest/` produced no visible change
  at all, even after a restart, even when reusing quest ID 1151 (the
  user's own frequently-recorded quest, since ID 1151 is real and
  definitely in the game's own catalog). Root-caused by reading
  `github.com/Strackeror/MHW-QuestLoader`'s actual source directly (its
  author's Nexus mod page claims this functionality "is now part of
  Stracker's Loader," which is *misleading but not exactly false* — see
  below): quest injection is a **separate native plugin**
  (`nativePC/plugins/QuestLoader.dll`) that hooks the game's own quest-list
  functions (`questNoList`, category/rank checks) via signature scanning
  at `DLL_PROCESS_ATTACH`, scanning `nativePC/quest/` for
  `questData_%d.mib` files with `id >= 90000` *at game launch only* (so a
  running session never picks up a new file — matches this project's
  "reload"-needed pattern from `state_reader.lua`/LuaEngine elsewhere).
  This plugin turned out to already be installed on this machine
  (`nativePC/plugins/QuestLoader.dll`, dated the same day as the rest of
  Stracker's Loader) — so it was never actually missing; the real blocker
  was **`loader-config.json`'s `"logfile": false`**, which had been
  silently suppressing Stracker's own diagnostic log the entire time.
  Setting `"logfile": true` + `"logLevel": "DEBUG"` and restarting
  produced `loader.log`, which proved decisively that the plugin *was*
  finding, registering, and actively querying the quest (`Registered
  quest at nativePC\quest\questData_90099.mib`, `Overriding questNoList`,
  repeated `GetQuestCategory`/`CheckQuestLoader` calls matching real
  browsing) — meaning the quest was real and live in the system the whole
  time, just not where it was expected (see next finding). Do NOT install
  the old standalone "MHW Quest Loader" Nexus mod (id 1453, 2019,
  base-game-only) alongside this — its own `dinput8.dll` directly
  conflicts with Stracker's Loader's (confirmed different files, would
  have silently broken `state_reader.lua`/LuaEngine if installed; backed
  up the working `dinput8.dll` before testing, never actually swapped
  it).

  **The "rank" byte this project had been patching is cosmetic for
  QuestLoader's own Master Rank determination — `stars` is what actually
  matters.** Read directly from `QuestLoader`'s hooked
  `is_master_rank_addr` function: `return QuestIds.at(id)->starcount >
  10;` — completely ignoring the quest file's own rank byte (offset 19).
  Every earlier attempt to fix visibility by changing "rank" (0/1/2) had
  zero effect on QuestLoader's categorization; lowering `stars` from the
  source quest's original 16 down to 1 (thinking "make it easy/Low Rank")
  is what actually broke visibility from the very first patch — 16 was
  already a working, correctly-MR-classified value the original mod
  author chose deliberately. Fixed by using `stars=16` again (`--rank` is
  kept for real-MHW-semantics consistency but is a no-op for this
  loader's own logic).

  **Arch Tempered's health-bar border survived clearing the per-monster
  Tempered checkbox** (offset `184 + 65*slot`) — caught live, since the
  monster still showed the AT border in-game after that fix. Arch
  Tempered turned out to be a **separate, quest-level** flag entirely,
  packed at offset 130 as `2*ATFlag + PSGear` (found by grepping Quest
  Editor's source for `ATFlag`) — clearing bit 1 there (keeping PSGear's
  bit) removed the AT border for real.

  **GMD name/description text is plain, unencrypted, and independently
  patchable** — `scripts/patch_arena_quest.py --gmd-source/--gmd-output
  --gmd-replace OLD=NEW` does an in-place find-and-replace (null-padded,
  since the replacement can't be longer than the original without also
  updating length/offset tables elsewhere in the file that weren't
  reverse-engineered this pass). Used to replace the source quest's
  leftover "The Golden Hair" / "Slay Supreme Jagras" flavor text with
  text describing what the quest actually is now.

  **Two more inherited-from-source bugs found after it became playable**,
  both live, 2026-09-19: the actual Great Jagras still hit far harder and
  looked oversized than a normal hunt (slot 0's `MHtP`/`MAtk` — a
  per-monster-type difficulty-tier *index*, not a raw percentage — were
  both `299`, and `MonsterSize` was `188`, i.e. 188%; none of these were
  ever touched by earlier passes, which only cleared the Tempered/AT
  flags), and small wildlife was still spawning even after emptying
  monster slots 1-6 — turned out to come from a completely separate,
  single, map-wide spawn block (`sMsobj`/`sMHP`/`sMAt`/`sMDe`, offset
  627) unrelated to the per-large-monster-slot fields. Zeroed the
  difficulty indices, set size to 100 (the only one of these that's a
  literal percentage, confirmed by the read code populating it into a
  plain textbox rather than a %-labeled dropdown), and zeroed the
  small-monster spawn block.

  A real 807-frame episode was recorded (`demos/storage/20260919T190805_90099`)
  against the un-normalized version, before this last fix landed — kept,
  still genuine (if statistically off, given the inflated monster
  stats) Great Jagras footage. Its `distinct_quest_states_seen` included
  `7`, a value never observed anywhere else in this project before (see
  `docs/risks.md`'s still-open `quest.state` enum tracking).

  Final working config: quest ID 90099, `Arena (Challenge)` map (id 202),
  `stars=16` (Master Rank via QuestLoader's own logic), both Tempered
  flags cleared, difficulty/size normalized, no extra large or small
  monsters, custom name/description. Confirmed visible and selectable
  live, 2026-09-19.
- **Imitation-learning dataset size for Great Sword vs. Great Jagras**
  (Phase 3). No fixed target — reasoning from chat, 2026-09-18 (written
  for the old 8-action space; the tool-based space is ~230 flat calls,
  which argues for more data or a factored/graph-relative readout; and
  written before the connectome was dropped, 2026-09-24): the action
  space is small and discrete (8 actions) and the connectome brain's
  trainable surface was modest by design, both of which argued for less
  data than typical game-IL work — the second no longer holds, so expect
  more, offset by a pretrained vision backbone and by privileged
  auxiliary targets / a teacher during IL (Design Principle 4). Pixel
  observation argues for more still, since the visual encoder needs real
  camera-angle/position coverage. Rough estimate from before the change
  (likely low now): "full" (held-out accuracy plateaus) likely lands around
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
