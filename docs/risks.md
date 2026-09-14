# Risk register

Living document — update as items get resolved or new ones surface. Carried
over from the initial roadmap, plus what Phase 0 has already turned up.

## Open

- **`weapon_type`/`weapon_id` don't have a known id→name mapping yet.**
  Confirmed readable (current test character: `weapon_type=5`,
  `weapon_id=234`) but unconfirmed whether `5` is actually the Great
  Sword. Needed before writing `configs/weapons/greatsword.yaml` in
  Phase 1.
- **Per-part monster HP / break flags are not in the bundled API.** Only
  whole-monster `health_current`/`health_max` is exposed by
  `Engine_monster.lua`. Monster configs (Phase 1) that want part-break
  rewards will need either a raw memory offset (cross-reference a
  community Cheat Engine table) or a module this project hasn't read yet.
- **`grim`-based capture is too slow as-is.** Measured 2026-09-14:
  ~2.4 fps sustained (10-frame average), and a 116-second outlier on the
  very first call in-process (possibly a one-time portal/compositor
  warm-up — not yet explained, worth re-checking). At 5360×1440 it's also
  capturing the *entire* multi-monitor layout, not just the MHW window —
  `capture_frame(geometry=...)` supports restricting to a region but this
  hasn't been tried yet. Cheapest immediate fix: MHW's own config confirms
  it renders to a single display (`Display2`/2560×1440, not the full
  virtual desktop) — cropping capture to just that output should recover
  most of the throughput before anything else is tried. Full writeup,
  plus in-game settings and a `gamescope` option for further gains, in
  `docs/performance_tuning.md`. If cropping alone isn't enough, the
  PipeWire-screencast-portal path mentioned in `docs/architecture.md` is
  the fallback.
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
