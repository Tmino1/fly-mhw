--[[
  state_reader.lua — Phase 0 state-read spike for fly-mhw.

  Written against the REAL LuaEngine API — read directly out of the
  installed Lua/Engine.lua and Lua/modules/Engine_{player,monster,quest}.lua
  (see docs/modding_setup.md for how those were found), not guessed. API
  shape confirmed from source AND confirmed working in-game (v1, 2026-09-14
  — a snapshot with correct player HP/stamina/weapon data was written), but
  v1's Chronoscope-based repeat-write timer turned out to be broken (wrote
  exactly once, never again — see on_time() below for the v2 fix and the
  likely root cause).

  Install: drop this file in the game's `Lua/` folder, then in the
  in-game chat:
      reload state_reader

  What it does: once a second (os.time()-gated — see on_time() below),
  dumps a JSON snapshot of the player, all known monsters, and the
  current quest to a file, so the Python side
  (env/game_interface/lua_bridge.py) can poll it.
]]

local OUTPUT_PATH = "fly_mhw_state.json"
local WRITE_INTERVAL_SECONDS = 1

-- Minimal JSON encoder for the specific shapes this script produces
-- (nested tables of strings/numbers/booleans/arrays). Not general-purpose
-- — no evidence LuaEngine ships a JSON library, so this is hand-rolled.
local function json_encode(value)
  local t = type(value)
  if t == "nil" then
    return "null"
  elseif t == "boolean" then
    return tostring(value)
  elseif t == "number" then
    return tostring(value)
  elseif t == "string" then
    return string.format("%q", value)
  elseif t == "table" then
    local is_array = true
    local n = 0
    for k, _ in pairs(value) do
      n = n + 1
      if type(k) ~= "number" then
        is_array = false
      end
    end
    if is_array and n == #value then
      local parts = {}
      for i, v in ipairs(value) do
        parts[i] = json_encode(v)
      end
      return "[" .. table.concat(parts, ",") .. "]"
    else
      local parts = {}
      for k, v in pairs(value) do
        parts[#parts + 1] = string.format("%q", tostring(k)) .. ":" .. json_encode(v)
      end
      return "{" .. table.concat(parts, ",") .. "}"
    end
  else
    return "null"
  end
end

-- Cached once in on_init(), same pattern the bundled example script
-- (可选脚本/数据窗口/dataview.lua) uses — the object returned by
-- engine.Player:new() is a live proxy (metatable-backed), so re-reading
-- Data_Player.Position.position.x etc. on every write always gets fresh
-- memory reads; it's only the base pointer resolution that's cached.
local Data_Player
local Data_Quest

local function snapshot_player()
  local ok, result = pcall(function()
    return {
      position = {
        x = Data_Player.Position.position.x,
        y = Data_Player.Position.position.y,
        z = Data_Player.Position.position.z,
      },
      health_current = Data_Player.Characteristic.health.health_current,
      health_max = Data_Player.Characteristic.health.health_max,
      stamina_current = Data_Player.Characteristic.stamina.stamina_current,
      stamina_max = Data_Player.Characteristic.stamina.stamina_max,
      weapon_type = Data_Player.Weapon.type,
      weapon_id = Data_Player.Weapon.id,
    }
  end)
  if ok then
    return result
  else
    return { error = tostring(result) }
  end
end

-- Monsters are NOT cached across writes (mirrors dataview.lua) since the
-- set of live monsters changes — GetAllMonster() is re-queried each time.
local function snapshot_monsters()
  local ok, result = pcall(function()
    local monsters = GetAllMonster()
    local out = {}
    for address, info in pairs(monsters) do
      local m = engine.Monster:new(address)
      out[#out + 1] = {
        id = info.Id,
        health_current = m.Characteristic.health_current,
        health_max = m.Characteristic.health_max,
        position = {
          x = m.Position.position.x,
          y = m.Position.position.y,
          z = m.Position.position.z,
        },
      }
    end
    return out
  end)
  if ok then
    return result
  else
    return { error = tostring(result) }
  end
end

local function snapshot_quest()
  local ok, result = pcall(function()
    return {
      id = Data_Quest.Id,
      state = Data_Quest.State,
      time = Data_Quest.Time,
    }
  end)
  if ok then
    return result
  else
    return { error = tostring(result) }
  end
end

local function write_snapshot()
  local snapshot = {
    schema = "fly-mhw/state_reader/v1",
    written_at = os.time(),
    player = snapshot_player(),
    monsters = snapshot_monsters(),
    quest = snapshot_quest(),
  }

  local f = io.open(OUTPUT_PATH, "w")
  if f then
    f:write(json_encode(snapshot))
    f:close()
  end
  -- If io.open fails: LuaEngine bundles LuaFileSystem (used by Engine.lua
  -- itself for module loading) so this is expected to work, but if
  -- verify_state_read.py never sees a file appear, this is the first
  -- thing to check.
end

local last_write_time = 0

function on_init()
  Data_Player = engine.Player:new()
  Data_Quest = engine.Quest:new()
  last_write_time = os.time()
end

-- v1 used LuaEngine's Chronoscope cooldown helpers here (matching the
-- bundled dataview.lua example) — confirmed BROKEN in practice: the file
-- was written exactly once (on_init/the first on_time call) and never
-- again. Most likely explanation: on_time() itself wasn't wrapped in
-- pcall, so if CheckChronoscope/CheckPresenceChronoscope/AddChronoscope
-- threw on a later call (their real signature/behavior was inferred from
-- a single usage example, not confirmed), the uncaught error silently
-- killed all future on_time() calls for this script. Switched to
-- os.time() (confirmed working — it's what produced the correct
-- written_at timestamp in that one successful write) with the whole body
-- wrapped in pcall, and errors now get surfaced via Console_Error() so
-- they're visible in-game instead of silently stopping the hook.
function on_time()
  local ok, err = pcall(function()
    local now = os.time()
    if now - last_write_time >= WRITE_INTERVAL_SECONDS then
      last_write_time = now
      write_snapshot()
    end
  end)
  if not ok then
    Console_Error("fly-mhw state_reader on_time error: " .. tostring(err))
  end
end
