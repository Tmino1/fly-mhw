# Risk register

Living document — update as items get resolved or new ones surface. Carried
over from the initial roadmap, plus what Phase 0 has already turned up.

## Open

- **Target selection is heuristic, not exact.** `GetAllMonster()` returns
  every live monster entity, so `configs/monsters/great_jagras.yaml` uses
  `highest_max_health` + a `min_health_max: 1000` floor to separate the
  quest target from small fry. This would pick the wrong creature if a
  second large monster invades the hunt. The exact fix is filling in
  `identification.expected_ids` once Great Jagras's real id is read out of
  `scripts/run_dummy_policy.py`'s logs (it prints the ids it selected).
- **`quest.state`'s real enum values are still only partially observed.**
  `0` = idle (no active quest) was already known. **New 2026-09-19:** `2`
  = in-progress — confirmed live from a real state-file read mid-hunt
  (`quest.id=1151`, `quest.time=43.1`, `monsters` populated with real
  entities including one at `health_max=20637.5`, clearly the actual
  quest target vs. the small wildlife). **Also new, same session:** `6` —
  observed on the *same* quest id (`1151`) later in the same hunt
  (`quest.time=94.6`, still climbing, so not yet ended) — the id staying
  constant while state changed from `2`→`6` suggests these encode hunt
  *phases* (e.g. engaged-in-combat vs. some other in-progress state,
  perhaps monster-captured/fleeing/a cutscene) rather than a simple
  binary. **Third new value:** `3` — observed after a real ~410s hunt
  completed (still `quest.id=1151`, not yet `-1`), i.e. this looks like
  the post-hunt state (rewards/carving screen, before returning to camp)
  — `quest.id` doesn't reset to `-1` until you actually return to the
  hub, confirmed live: `DemoRecorder`'s "wait for idle" phase correctly
  kept waiting through this. **Fourth new value, same session:** `4` —
  observed on the first fully-successful recorded episode
  (`demos/storage/20260918T212155_1151`), whose `distinct_quest_states`
  came out as `[1, 2, 3, 4]` across one complete hunt lifecycle
  (accept→combat→post-hunt-wait→?). `4` is a strong candidate for the
  actual "quest cleared" terminal signal, appearing right before the
  return to `quest.id=-1` — not yet confirmed which exact transition
  produces it though (need to check the JSONL's per-step
  `reward_debug.quest_state_raw` sequence, not just the deduped set).
  Whatever value(s) mean failed/abandoned specifically are still
  unobserved. Phase 1's episode-boundary logic (`env/reward.py`)
  deliberately doesn't depend on this regardless — it's logged into every
  step's `info` dict / the demo recorder's JSONL instead. Next real hunt
  recorded end-to-end (start through clear/cart) should fill in the rest;
  update this entry and consider a v2 monster-config schema with a proper
  win/fail/abandon distinction once the full set is known.
- **Per-part monster HP / break flags are not in the bundled API.** Only
  whole-monster `health_current`/`health_max` is exposed by
  `Engine_monster.lua`. Monster configs (Phase 1) that want part-break
  rewards will need either a raw memory offset (cross-reference a
  community Cheat Engine table) or a module this project hasn't read yet.
- **5 fps cropped may still not be enough for Phase 4's real-time control
  loop.** Cropping fixed the "capturing far more than needed" problem
  (see Resolved below), but 5 fps is still a coarse control rate for
  live combat — worth revisiting with `gamescope`'s fixed-window
  approach or a PipeWire-screencast-portal capture (both mentioned in
  `docs/performance_tuning.md`/`docs/architecture.md`) if it proves
  limiting once Phase 4 actually needs to react to fast monster tells in
  real time. Not a blocker for Phase 1-3 work.
- **Licensing.** Connectome dataset, flyvis, and haltere licenses all need
  individual verification before any code is vendored or a derived model is
  used/shared. Not yet checked — do this before Phase 2 goes deep.
- **Scope: single-player/offline only.** Automating solo hunts is broadly
  tolerated in the modding community; automating anything touching
  online/multiplayer is a materially different ToS/anti-cheat risk and
  stays explicitly out of scope for this project.
- **Realistic effort.** Multi-week-to-multi-month even optimistically —
  connectome-scale model work, unproven MHW reverse-engineering, and a
  real-time-only training loop are all individually substantial.
- **Reward signal noise/latency.** LuaEngine reads will lag true game state
  by some unmeasured amount — affects both demo-recording synchronization
  (Phase 3) and online reward usefulness (Phase 4).

## Resolved

- ~~`BTN_SOUTH` never actually skipped the post-hunt screen, even once
  correctly timed~~ — resolved 2026-09-19. After the timing fix below,
  still confirmed live that a manual, isolated `BTN_SOUTH` tap did
  nothing — a screenshot of the actual screen ("Select Return
  Destination", a `Tab`-key icon for navigation) showed it's a
  keyboard/mouse-driven UI, not a gamepad one, so no gamepad button was
  ever going to work regardless of timing. A community mod (Yomi Utils /
  Standalone Quest End Timer Skip, via SharpPluginLoader) does this via
  an F9-menu button click, not a bare hotkey either — real UI automation
  would have needed a new absolute-position pointer-click mechanism and
  been fragile to layout changes. Real fix: SharpPluginLoader is
  open-source — its own "Quest End Skip" example
  (`SharpPluginLoader.Core/{Quest,Timer}.cs`) shows the actual technique
  is a one-line memory write, `Quest.QuestEndTimer.SetToEnd()` =
  `Timer.Time = Timer.MaxTime`, at `sQuest_singleton + 0x13198` (Timer
  struct, `Time` at `+0x08`, `MaxTime` at `+0x0C`). Confirmed this is the
  exact same singleton `Engine_quest.lua` already resolves — SPL's source
  lists `CurrentQuestId`/`QuestState` at `+0x4C`/`+0x54`, matching our own
  Lua's offsets exactly. Replicated directly in
  `lua_scripts/state_reader.lua` (gated on a flag file `DemoRecorder`
  creates/removes, per the "only during recording" requirement) — no
  SharpPluginLoader dependency, no input injection, needed at all in the
  end. The SharpPluginLoader install itself (`docs/modding_setup.md`)
  wasn't wasted effort — reading its source is what revealed the correct
  technique — but isn't required for this feature to work going forward.
- ~~Post-hunt wait auto-skip never actually fired~~ — resolved 2026-09-19.
  Caught live: sitting on the real "return to camp" screen
  (`quest.state=3`) with no skip happening. Root cause: the skip call
  lived only in `wait_for_quest_start()`'s idle-drain phase, which only
  runs AFTER the episode ends — but `RewardModel` only ends the episode
  once the ENTIRE raw monster list goes empty (`monsters == []`), which
  doesn't happen until you're fully back at the hub and the whole map's
  wildlife unloads too. That's the exact same wait the skip was supposed
  to shortcut, so it was unreachable. Fixed by also calling it from
  inside `record_episode()`'s own per-step loop, where it can run while
  `quest.state==3` is still showing (kept in both places — the
  idle-drain-phase call is still useful for a `player_cart` ending, which
  terminates the episode immediately and reaches the post-quest UI only
  afterward).
- ~~`highest_max_health` target selection picked the wrong entity all
  hunt~~ — resolved 2026-09-19 (partially — see caveat). Caught live on
  a real completed hunt: `id=7` (`health_max=12540.0`) dropped to
  `health_current=0.0`, an exact confirmed kill matching "I killed it" —
  but the reward model had been tracking `id=0` (`health_max=18388.5`,
  never took damage) the entire episode, because it's an even-higher-HP
  entity than the real target (plausibly a large map prop/set-dressing
  object, not a combat target — `GetAllMonster()` apparently enumerates
  those too). This is why `monster_hp_fraction` sat at `1.0` for full
  episodes despite real hunting on screen. Fixed by setting
  `configs/monsters/great_jagras.yaml`'s `identification.expected_ids:
  [7]` (exact-id match already took precedence over the heuristic when
  configured — this was always the intended fix once a real id was
  known). **Caveat: one data point.** Unconfirmed whether Great Jagras
  keeps the same entity id across different hunt instances — watch the
  next few recorded episodes' `monster_hp_fraction`; if it tracks real
  combat, `7` is stable enough, if it pins at `1.0` again the id changed
  per-spawn and this needs a different approach (see the config's own
  notes). Importantly, this bug never affected the recorded frames/
  actions themselves (what IL actually needs) — only the reward-debug
  metadata, which Design Principle 4 already keeps separate from the
  observation/action data.
- ~~`wait_for_quest_start()`'s two phases shared one timeout budget~~ —
  resolved 2026-09-19. Caught live: after a real ~410s hunt (recorder
  restarted mid-hunt, so it spent that whole time in the "drain to idle"
  phase), the session then only had ~169s of its 600s budget left to
  catch the *next* quest-accept — nowhere near enough patience for a real
  gap between hunts — and the session ended prematurely. Fixed by giving
  each phase (`waiting_for_idle`, `waiting_for_quest_start`) its own full
  `timeout_seconds` budget instead of splitting one shared deadline. Note:
  `env/mhw_env.py`'s `reset()` has the same underlying pattern and wasn't
  fixed — a single Phase-1 acceptance-test run is much less likely to hit
  it, but worth remembering if it ever does.
- ~~The camp→hunting-ground loading screen killed recording episodes~~ —
  resolved 2026-09-19. `LuaBridge`'s `max_age_seconds` defaulted to `5.0`
  — a real recorded episode died after only 14.5s/7 frames on a
  `state_read_error` (file 5.9s old) right at the start of the hunt.
  Confirmed it wasn't a real crash: the state file was fresh again the
  moment loading finished, no `reload` needed. Bumped the default to
  `20.0` (a real crash/unload stays stale indefinitely regardless, so
  this doesn't weaken that detection) and added `record_hunt.py
  --max-state-age` to override per-run if 20s ever isn't enough.
- ~~`record_hunt.py --reset-timeout` was silently ignored~~ — resolved
  2026-09-19. `DemoRecorder.__init__` never had a `reset_timeout_seconds`
  parameter at all, so `record_episode()` always called
  `wait_for_quest_start()` with its hardcoded 60s default regardless of
  the CLI flag (which claimed a 120s default in its printed message).
  Caught live: a real recording attempt timed out in the
  `waiting_for_idle` phase (a quest was already active when the recorder
  started — its Phase A logic correctly refuses to start recording
  mid-hunt, but 60s wasn't enough budget) after almost exactly 60s, not
  120s. Fixed by threading `reset_timeout_seconds` through
  `DemoRecorder.__init__` → `record_episode()` → `wait_for_quest_start()`,
  and `record_hunt.py` now actually passes `args.reset_timeout` through.
- ~~`scripts/calibrate_keyboard_bindings.py` silently captured the wrong
  key~~ — resolved 2026-09-19. Root cause: the listener is deliberately
  non-grabbing, so the Enter/`r` keystroke used to *confirm* each binding
  also landed as a raw event on the very keyboard device being watched —
  left undrained, the *next* `wait_for_keypress()` call picked up that
  stale leftover event instead of the user's actual next key/button press
  (symptom: reported as "can't detect mouse input," since a mouse-bound
  action like `attack_1` would silently capture a stale `KEY_ENTER` from
  the previous confirmation instead of the real click). Confirmed via a
  standalone raw-event diagnostic that device-level delivery was fine
  before concluding it was a script-logic bug, not a permissions/device
  one. Fixed with a `drain_events()` call before each
  `wait_for_keypress()`. Note `demos/recorder.py`'s actual recording path
  was never affected — it queries live kernel key-state directly
  (`device.active_keys()`), not a drained event queue, so this bug was
  isolated to the calibration tool.
- ~~`/dev/input` permissions needed for demo recording~~ — resolved
  2026-09-19: `users.users.ad.extraGroups` + `"input"` added to
  `~/nix-conf/configuration.nix`, `sudo nixos-rebuild switch`, then a
  full reboot (a plain re-login wasn't tested — a reboot was used and
  confirmed sufficient). Confirmed live: `groups` now includes `input`,
  and `open("/dev/input/event9", "rb")` succeeds directly. Along the way,
  a real second bug was caught before it caused silently-missing data:
  the default `--devices keychron,mouse` pattern would never have
  matched this machine's actual mouse — it reports as `Logitech PRO X`,
  containing neither "keychron" nor "mouse". Any mouse-button action
  bindings (e.g. attacks) would have been silently invisible to the
  recorder. Fixed by changing the default to `keychron,logitech` in both
  `scripts/calibrate_keyboard_bindings.py` and `scripts/record_hunt.py`,
  confirmed via `find_input_devices()` now returning both the keyboard
  and `Logitech PRO X`.
- ~~MHW mispositioned itself on the wrong monitor, wrong size ("framing"
  looked broken), and mouse felt odd~~ — resolved 2026-09-18. Root cause:
  a known, still-open Hyprland/XWayland bug
  ([hyprwm/Hyprland#2350](https://github.com/hyprwm/Hyprland/issues/2350))
  where self-positioning by an XWayland client fails on a monitor at a
  non-zero offset — confirmed live via `hyprctl clients -j`: MHW's own
  `graphics_option.ini` asked for `PosX=1945` (on `DP-1`, the ultrawide,
  which starts at `x=1920`), but the window actually landed at `0,0` on
  `HDMI-A-1` instead. **Not fixable from the game's own settings** — the
  fix is a Hyprland window rule forcing placement from the compositor
  side, added to `~/nix-conf/caelestia-shell.nix` (outside this repo —
  that's this machine's NixOS/Home-Manager config):
  ```
  windowrule = [ "match:class steam_app_582010, monitor DP-1, fullscreen 1" ];
  ```
  (Hit one real syntax bug first: this Hyprland version dropped
  `windowrulev2` entirely in favor of a `match:PROP VALUE, EFFECT
  VALUE, ...` syntax where every effect needs an explicit value — a bare
  `fullscreen` with no value threw a parse error, `fullscreen 1` fixed
  it.) Confirmed live afterward via `nix run .#list-windows`: MHW's
  window is now at `1920,0 3440x1440`, exactly matching `DP-1`'s full
  geometry. The mouse oddness was very likely a symptom of the same
  wrong-monitor/wrong-size window rather than a separate bug — worth
  revisiting only if it's still noticeable now that positioning is fixed.
- ~~`grim`-based capture captured the whole desktop instead of just
  MHW~~ — resolved 2026-09-18, confirmed live with the game running:
  `find_window_geometry()`'s guessed pattern (`steam_app_582010`) matched
  MHW's real Hyprland window class exactly on the first try — window was
  at `0,0 1920x1080` at measurement time (differs from the
  `2560x1440`/`Display2` seen in `graphics_option.ini` earlier — display
  config had changed since; `find_window_geometry()` reads it live each
  call rather than assuming, so this doesn't matter). Measured **with the
  game actually running** (a more honest comparison than the original
  Phase 0 baseline, which was measured desktop-idle with the game
  closed): **0.97 fps uncropped → 5.00 fps cropped, a ~5x improvement.**
- ~~Screen-capture backend choice~~ — resolved 2026-09-14: `grim`, this
  machine is Hyprland/Wayland (see `docs/architecture.md`).
- ~~`/dev/uinput` permissions~~ — resolved 2026-09-14: already has an ACL
  entry for this user, no udev rule needed. Verified: `VirtualGamepad`
  opens `/dev/input/event27` and closes cleanly.
- ~~`evdev` won't build in the venv~~ — resolved 2026-09-14: plain `evdev`
  needs `/usr/include/linux/input.h` to compile its C extension, which this
  NixOS machine doesn't expose to a plain venv build. Switched
  `requirements.txt` to `evdev-binary` (prebuilt wheel, same `import
  evdev` API).
- ~~Stracker's Loader install~~ — resolved (was already installed and
  working before this project started).
- ~~LuaEngine install~~ — resolved 2026-09-14: installed via the Nexus
  Mods Main file. `nativePC/plugins/LuaEngine.dll` present, `Lua/Engine.lua`
  + `Lua/modules/Engine_*.lua` present.
- ~~LuaEngine API guesswork~~ — resolved 2026-09-14: real hook names
  (`on_init`/`on_time`/`on_imgui`/...) and real field paths for
  player/monster/quest/world all confirmed by reading the installed
  `Lua/Engine.lua` and `Lua/modules/Engine_*.lua` source directly, and
  `state_reader.lua` rewritten against them. See `docs/modding_setup.md`.
- ~~`state_reader.lua` hadn't been run against a live game~~ — resolved
  2026-09-14: `reload state_reader` + `verify_state_read.py` now prints a
  fresh, correct snapshot every second. Found and fixed a real bug along
  the way: v1's `Chronoscope`-based repeat-write timer wrote the file
  exactly once and then silently died (an uncaught error in an
  un-`pcall`'d `on_time()`, most likely) — v2 uses plain `os.time()`
  gating with the whole body wrapped in `pcall`. See
  `docs/modding_setup.md`.
- ~~Where relative-path writes land under Proton~~ — resolved 2026-09-14:
  directly in the MHW install directory, next to `MonsterHunterWorld.exe`.
- ~~Input-injection detection / anti-cheat / whether it reaches the game
  at all~~ — resolved 2026-09-14: confirmed working. Root cause of the
  first failed attempt was a timing bug, not detection/blocking — see
  `scripts/verify_input_injection.py`'s v2 (holds the pad open long
  enough for Wine/SDL hotplug detection instead of tapping once and
  immediately closing). Confirmed at the OS level too:
  `udevadm info` shows `ID_INPUT_JOYSTICK=1` and bus type `0003` (USB)
  matching a real Xbox 360 pad. No anti-cheat friction observed.
  Once landing correctly, `BTN_SOUTH` taps acted as the menu "confirm"
  button (expected — A/South is the standard confirm button in MHW's
  menus, so this is confirmation the mapping is *right*, not wrong).
- ~~`weapon_type`/`weapon_id` id→name mapping~~ — resolved 2026-09-14,
  confirmed empirically by swapping weapons in-game and reading
  `player.weapon_type`: `0` = Great Sword, `5` = Hunting Horn.
- ~~Great Sword attack_1/attack_2/dodge button mapping~~ — resolved
  2026-09-14, confirmed live against the game's own HUD legends and
  actual move execution. Found and fixed a real bug along the way:
  **`BTN_NORTH` and `BTN_WEST` are swapped from their intuitive compass
  meaning in evdev** — `BTN_NORTH` is numerically the same code as
  `BTN_X` (`0x133`), and `BTN_WEST` is numerically the same as `BTN_Y`
  (`0x134`). `attack_1` originally used `BTN_NORTH` intending Y and
  silently did nothing (it was sending X); fixed to `BTN_Y` directly.
  Also bumped `hold_seconds` from `0.05` to `0.15` for both attacks — the
  original 0.05 tap was too short to register at all, independent of the
  code bug. See `configs/weapons/greatsword.yaml`'s header comment —
  **always use the letter alias (`BTN_Y`/`BTN_X`/`BTN_A`/`BTN_B`), never
  the compass alias, in any future weapon config.**
- ~~Movement direction/strafe sign conventions~~ — resolved 2026-09-14 by
  measuring player-position deltas over a 2s hold per action and
  cross-checking visually against a fixed landmark. forward vs backward
  cos=-1.00, left vs right cos=-1.00, forward vs left cos=-0.01 — a clean
  orthogonal basis, and direction confirmed visually (forward approached
  the landmark; strafe_left shifted the world right on screen). All four
  now `verified: true`.
- ~~`first_monster` target selection~~ — **disproved** 2026-09-14 and
  replaced. A live state dump showed **11 simultaneous monster entities**
  (max HP 100 and 300) while `quest.id` was still `-1`, proving
  `GetAllMonster()` returns every live monster, not just a quest target.
  `monsters[0]` would have silently tracked a random small monster and
  made the entire reward signal meaningless. Now `highest_max_health`
  with a `min_health_max` floor — see the corresponding Open item about
  pinning `expected_ids` for an exact fix.
