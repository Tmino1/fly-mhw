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
- **`quest.state`'s real enum values are still unobserved.** Only the
  idle value (`0`, no active quest) has ever been seen. Phase 1's episode-
  boundary logic (`env/reward.py`) deliberately doesn't depend on this —
  it's logged into every step's `info` dict instead. Run
  `scripts/run_dummy_policy.py --policy idle` through a real quest to find
  out what the real values are, then update this entry and consider a v2
  monster-config schema with a proper win/fail/abandon distinction.
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
