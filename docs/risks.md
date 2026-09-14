# Risk register

Living document — update as items get resolved or new ones surface. Carried
over from the initial roadmap, plus what Phase 0 has already turned up.

## Open

- **LuaEngine not installed.** Confirmed by filesystem search on
  2026-09-14. Blocks all structured-state work until installed. See
  `docs/modding_setup.md`.
- **LuaEngine's actual state coverage is unverified.** Unknown whether
  per-monster/per-part HP is available through the documented `engine` API
  or needs raw memory offsets (cross-referencing community Cheat Engine
  tables). Won't know until it's installed and `state_reader.lua` is
  actually run.
- **Input-injection detection / anti-cheat.** Untested whether MHW's client
  (or Steam) flags or blocks injected virtual-gamepad input. Test
  cautiously, in single-player, before relying on it.
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
