# Modding tooling setup (Phase 0)

## Current state on this machine (checked 2026-09-14)

MHW install: `~/.local/share/Steam/steamapps/common/Monster Hunter World`
(Proton/appid 582010).

| Component | Status | Evidence |
|---|---|---|
| Stracker's Loader | ✅ Installed | `loader.dll`, `dinput8.dll`, `loader-config.json` present at game root; `enablePluginLoader: true` |
| Plugin loader | ✅ Working | `nativePC/plugins/` has `CutsceneSkip.dll`, `MonsterLoader.dll`, `QuestLoader.dll` |
| LuaEngine | ✅ **Installed** (2026-09-14) | `nativePC/plugins/LuaEngine.dll` (+ `LuaEngineUI.dll`, `LuaEngineAudio.dll`); `Lua/Engine.lua` + `Lua/modules/Engine_*.lua` present at game root |

Installed by downloading the **Main file** from the
[Nexus Mods LuaEngine page](https://www.nexusmods.com/monsterhunterworld/mods/6934)
and unzipping it into the MHW game folder (same folder as
`MonsterHunterWorld.exe`). That's the whole required install — the "Optional
Files" section on Nexus is sample scripts, not additional engine components,
and turned out to already be bundled in the Main file anyway: a
`可选脚本` ("optional scripts") folder appeared alongside `Lua/`, containing
`LuaScript/luas.lua` and `数据窗口/dataview.lua` (a full ImGui data-viewer
example script — see below).

In-game chat commands (confirmed present, from `Lua/Engine.lua`):
- `luac: <command>` — run a Lua expression immediately, for poking around
- `reload <script name>` — (re)load a script from `Lua/`

## The real API (read from source, not guessed)

`state_reader.lua` was originally written blind, guessing at field names
and a made-up `OnUpdate(delta_time)` hook. Once LuaEngine was actually
installed, its real API was readable directly from
`Lua/Engine.lua` and `Lua/modules/Engine_{player,monster,quest,world}.lua`
— `state_reader.lua` has since been rewritten against the real thing.
Key facts, for the next person extending it:

- **Script entry points are**: `on_init()` (once, at load — cache
  `engine.Player:new()` / `engine.Quest:new()` etc. here), `on_time()`
  (a per-tick hook — no delta-time argument; use LuaEngine's own
  `AddChronoscope`/`CheckChronoscope`/`CheckPresenceChronoscope`
  cooldown-timer helpers to debounce/throttle, not a hand-rolled timer),
  `on_imgui()` (ImGui drawing, if wanted), `on_monster_create()` /
  `on_monster_destroy()`, `on_switch_scenes()`. Confirmed by reading the
  bundled example, `可选脚本/数据窗口/dataview.lua`.
- `engine.Player:new()` returns a **live metatable-backed proxy** —
  reading e.g. `.Position.position.x` triggers a fresh memory read every
  time, so it's safe (and is the intended pattern) to cache the object
  once in `on_init()` and just keep reading fields off it. Real shape:
  `.Position.position.{x,y,z}`, `.Characteristic.health.{health_base,
  health_current,health_max}`, `.Characteristic.stamina.{stamina_current,
  stamina_max,stamina_eat}`, `.Weapon.{type,id,position,hit}`,
  `.Armor.{head,chest,arm,waist,leg}`, `.Action.{lmtID,fsm,useItem}`.
- `GetAllMonster()` returns a table keyed by monster **address** (not an
  id) with `.Id` as a field on the value — iterate with `pairs`, pass the
  address key into `engine.Monster:new(address)` per monster (monsters are
  **not** cached across ticks, unlike the player, since the set of live
  monsters changes). Real shape: `.Characteristic.{health_current,
  health_max}` (flat, unlike the player's nested version),
  `.Position.position.{x,y,z}`, `.Action.{lmtID,fsm}`,
  `.Frame.{frame,frameEnd,frameSpeed,frameSpeedMultiplies}`.
- **Per-part HP / break flags are not exposed anywhere in the bundled
  modules** — only whole-monster `health_current`/`health_max`. Still an
  open question for Phase 1's reward shaping (per-monster configs that
  want part-break rewards): either it's not exposed at all and needs a
  raw memory offset (cross-reference a community Cheat Engine table), or
  it's available through a module this project hasn't needed to read yet.
- `engine.Quest:new()` gives `.Id`, `.State`, `.Time` — `.State` is the
  natural episode-boundary signal for Phase 1 (quest start/clear/fail),
  worth checking what values it actually takes once a hunt is run.
  `engine.World:new()` gives `.MapId`, `.Time`, `.Position.wayPosition`.
- `io.open` is expected to work (unverified in a live run, but
  `Lua/Engine.lua` itself uses LuaFileSystem — `lfs.dir`, `lfs.attributes`
  — and `GetFileMD5`/`dofile` for its own module loading, which all but
  requires a full, unsandboxed `io`/`os` environment). No evidence of a
  scriptable websocket API in anything read so far — file-polling IPC
  stays the plan; not chasing the websocket idea further without a
  concrete reason to.

## Runtime, confirmed working (2026-09-14)

`reload state_reader` + `python scripts/verify_state_read.py` now prints a
fresh snapshot every second, continuously. Along the way, one real bug
was found and fixed:

- **v1 wrote the file exactly once and then stopped.** It used LuaEngine's
  `Chronoscope` cooldown helpers (`AddChronoscope`/`CheckChronoscope`/
  `CheckPresenceChronoscope`) for the repeat-write timer, copying the
  pattern from the bundled `dataview.lua` example — but `on_time()` wasn't
  wrapped in `pcall`, so when one of those calls threw on the second tick
  (their exact behavior was inferred from a single usage example, never
  confirmed), the uncaught error silently killed all further `on_time()`
  calls for the script. **v2** replaced this with plain `os.time()`
  interval-gating (confirmed working — it's what produced the correct
  `written_at` timestamp even in the broken v1) and wrapped the whole
  `on_time()` body in `pcall`, surfacing any error via `Console_Error()` so
  it's visible in-game instead of silently stopping.
- Relative-path writes from the game process land directly in the MHW
  install directory under Proton — `fly_mhw_state.json` appears right next
  to `MonsterHunterWorld.exe`. Confirmed, no longer an open question.
- A real snapshot outside any quest looks like: `quest.id = -1`,
  `quest.state = 0`, `monsters = []` (all as expected with nothing
  active), and real player data (`health_current`/`health_max` = 150/150,
  `stamina_current`/`stamina_max` = 100/100, `weapon_type = 5`,
  `weapon_id = 234`). **Open item for Phase 1:** need a `weapon_type`
  id→name mapping (is `5` actually the Great Sword? unconfirmed) before
  writing `configs/weapons/greatsword.yaml`.

This is meant to be a living doc — keep updating it as Phase 1 finds more.

## Player action id + action-change log (2026-09-24) — UNVERIFIED LIVE

Added for the tool-based action space (`docs/architecture.md`). **Re-copy
`lua_scripts/state_reader.lua` into the game's `Lua/` folder and run
`reload state_reader`** — the game reads its own copy, not the repo's.

- Every snapshot now carries `player.action = {lmt_id, fsm}`, read from
  `Player.Action.{lmtID,fsm}` (field names from `Engine_player.lua`, per
  "The real API" above — never read live yet). It's in its own `pcall`, so
  if the shape is wrong you'll see `player.action = {"error": ...}` in
  `verify-state-read` while the rest of the snapshot keeps working.
- While `fly_mhw_action_log.flag` exists (next to `fly_mhw_state.json`),
  every **change** of `lmtID` is appended to `fly_mhw_actions.jsonl` as
  `{"lmt_id", "fsm", "t", "tick"}`. `scripts/record_hunt.py` creates the
  flag for the length of a recording session and removes it after — same
  pattern as the quest-end-skip flag, so untracked play writes nothing.
  The flag's existence is checked once a second (with the snapshot write),
  the lmtID itself every tick.
- Checked only against stubbed LuaEngine globals under Lua 5.4 (the log
  writes only on change, only while the flag exists). First live check:
  `verify-state-read` shows `player.action` changing when you attack.

## SharpPluginLoader (2026-09-19) — for the post-hunt timer skip

Installed to get a real, working "skip the return-to-camp timer" —
`docs/risks.md` has the full story of why our own `BTN_SOUTH`-based
attempt never worked (it's a keyboard-driven UI, not a gamepad one).
[Fexty12573/SharpPluginLoader](https://github.com/Fexty12573/SharpPluginLoader)
(v1.0.0, officially supports Linux/Proton as of 0.0.7.2) plus the
[Standalone Quest End Timer Skip](https://www.nexusmods.com/monsterhunterworld/mods/7538)
plugin (the standalone one specifically — **not** the full "Yomi Utils"
cheat menu bundled on the same page, which also has damage/stamina/item
cheats that could silently corrupt recorded demo data if left on by
accident).

Install steps actually taken on this machine:
1. `nativePC/plugins/CSharp/Loader/` + `ucrtbase.dll` extracted from the
   SPL Linux release into the MHW install dir (same tree Stracker's
   Loader already uses).
2. `nativePC/plugins/CSharp/StandaloneQuestEndSkip/standalonequestendskip.dll`
   — the actual plugin, downloaded from Nexus (manual — needs a browser).
3. **.NET 8 Desktop Runtime + `d3dcompiler_47`, installed into the Wine
   prefix directly** (not the Linux side — this machine's `dotnet` check
   on PATH is irrelevant, SPL runs inside the Windows/Wine process).
   `protontricks` couldn't auto-detect this machine's Proton (a
   Nix-packaged `proton-cachyos` build, not one Steam's own tooling
   discovers) — worked around by calling `winetricks` directly against
   the known prefix, wrapped in `steam-run` (NixOS needs this for any
   generic dynamically-linked Linux binary, which is what nixpkgs' wine
   build is) with `NIXPKGS_ALLOW_UNFREE=1 --impure`:
   ```sh
   export WINEPREFIX="$HOME/.local/share/Steam/steamapps/compatdata/582010/pfx"
   export WINE=".../proton-cachyos/bin/files/bin/wine"
   export WINESERVER=".../proton-cachyos/bin/files/bin/wineserver"
   export PATH=".../proton-cachyos/bin/files/bin:$PATH"
   NIXPKGS_ALLOW_UNFREE=1 nix shell --impure nixpkgs#steam-run nixpkgs#winetricks \
     -c steam-run winetricks --unattended dotnetdesktop8 d3dcompiler_47
   ```
   Confirmed live: `drive_c/Program Files/dotnet/shared/.../8.0.12` and
   `drive_c/windows/{system32,syswow64}/d3dcompiler_47.dll` both present
   afterward.
4. Steam launch option, since Stracker's Loader is already in use too:
   `WINEDLLOVERRIDES="ucrtbase,dinput8=n,b" %command%`
5. In-game: `F9` opens the SharpPluginLoader menu. The timer-skip plugin
   is hotkey-triggered (confirmed via its own docs — not automatic), which
   conveniently means it does nothing during normal play unless something
   actually presses that hotkey. Exact default hotkey: **TODO, check the
   F9 menu once loaded**.

## Input injection note (Steam Input)

The input-injection backend (`env/game_interface/input_injector.py`) creates
a virtual Xbox 360 gamepad via `/dev/uinput`. Steam Input can intercept and
remap controller input before it reaches a game — if injected presses don't
land in MHW, check the game's Steam Input configuration (Controller settings
→ this game) and either disable Steam Input for MHW or set it to a
passthrough/"Gamepad" template so the virtual pad's raw input reaches Wine's
SDL layer unmodified.
