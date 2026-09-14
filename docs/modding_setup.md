# Modding tooling setup (Phase 0)

## Current state on this machine (checked 2026-09-14)

MHW install: `~/.local/share/Steam/steamapps/common/Monster Hunter World`
(Proton/appid 582010).

| Component | Status | Evidence |
|---|---|---|
| Stracker's Loader | ✅ Installed | `loader.dll`, `dinput8.dll`, `loader-config.json` present at game root; `enablePluginLoader: true` |
| Plugin loader | ✅ Working | `nativePC/plugins/` already has `CutsceneSkip.dll`, `MonsterLoader.dll`, `QuestLoader.dll` |
| LuaEngine | ❌ **Not installed** | No matching files found anywhere on the filesystem (searched the whole game dir and a broader filesystem sweep) |

So: the loader that LuaEngine plugs into is ready, but LuaEngine itself still
needs to be added. (If you set this up on a different machine previously,
it didn't carry over here — this needs to happen on whichever machine will
actually run the training loop.)

## Installing LuaEngine

1. Download a release from
   [HalcyonAlcedo/LuaEngine releases](https://github.com/HalcyonAlcedo/LuaEngine/releases).
2. Extract it into the MHW game folder (same folder as
   `MonsterHunterWorld.exe`) — it installs alongside the existing Stracker's
   Loader setup. This should create a `Lua/` directory at the game root;
   that's where all `.lua` scripts live.
3. Launch the game. In the in-game chat, two commands become available:
   - `luac: <command>` — run a Lua expression immediately, for poking around
   - `reload <script name>` — (re)load a script from `Lua/`
4. Drop `lua_scripts/state_reader.lua` (from this repo) into the game's
   `Lua/` folder, then run `reload state_reader` in chat. It should start
   writing a state snapshot file (see the script's header comment for the
   path) roughly once a second.
5. Confirm from this repo:
   ```sh
   python scripts/verify_state_read.py
   ```
   It should start printing monster/player state read from that file.

## Open questions to resolve once LuaEngine is actually running

`state_reader.lua` is written against the documented pieces of LuaEngine's
API (`GetAllMonster()`, `engine.Player:new()`, `CheckKeyIsPressed`) and a
plain `io.open`-based file IPC, but none of this has been exercised against
a real running instance yet. Specifically unverified:

- Whether `GetAllMonster()`'s returned monster handles expose HP directly,
  or whether HP requires an extra raw memory read at some offset from the
  entity address (the README calls out "basic memory reading and writing
  functionality" as a separate feature from the data-parsing `engine` API —
  may need to cross-reference a community Cheat Engine table for the actual
  HP offset).
- Whether per-part HP / break flags are exposed at all via the documented
  API, or need the same raw-memory-offset treatment.
- Whether `io` is unrestricted in this Lua environment (the script assumes
  yes — LuaEngine's README mentions built-in file downloading and websocket
  support, which implies a fairly unsandboxed environment, but this hasn't
  been confirmed).
- **If websockets turn out to be scriptable from Lua**, that's a strictly
  better IPC path than file polling (lower latency, push instead of poll) —
  worth revisiting `env/game_interface/lua_bridge.py` once the API surface
  in `src/lua_register.h` (from the LuaEngine source) has actually been
  read.

Update this file's table and the open-questions list once these are
resolved — this is meant to be a living doc, not a one-time note.

## Input injection note (Steam Input)

The input-injection backend (`env/game_interface/input_injector.py`) creates
a virtual Xbox 360 gamepad via `/dev/uinput`. Steam Input can intercept and
remap controller input before it reaches a game — if injected presses don't
land in MHW, check the game's Steam Input configuration (Controller settings
→ this game) and either disable Steam Input for MHW or set it to a
passthrough/"Gamepad" template so the virtual pad's raw input reaches Wine's
SDL layer unmodified.
