--[[
  state_reader.lua — Phase 0 state-read spike for fly-mhw.

  Written against the REAL LuaEngine API — read directly out of the
  installed Lua/Engine.lua and Lua/modules/Engine_{player,monster,quest}.lua
  (see docs/modding_setup.md for how those were found), not guessed. API
  shape is confirmed from source; this script's actual behavior in-game is
  still unverified — run it and check with scripts/verify_state_read.py.

  Install: drop this file in the game's `Lua/` folder, then in the
  in-game chat:
      reload state_reader

  What it does: once a second (via LuaEngine's own Chronoscope
  cooldown/timer helper — see on_time() below), dumps a JSON snapshot of
  the player, all known monsters, and the current quest to a file, so the
  Python side (env/game_interface/lua_bridge.py) can poll it.
]]

local OUTPUT_PATH = "fly_mhw_state.json"
local WRITE_INTERVAL_SECONDS = 1
local CHRONOSCOPE_TAG = "fly_mhw_state_write"

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

function on_init()
  Data_Player = engine.Player:new()
  Data_Quest = engine.Quest:new()
end

-- LuaEngine's own cooldown/timer helper (used the same way in the bundled
-- dataview.lua example for debouncing a keypress) — avoids guessing at
-- on_time()'s call frequency or reimplementing timing with os.clock().
function on_time()
  if CheckChronoscope(CHRONOSCOPE_TAG) or not CheckPresenceChronoscope(CHRONOSCOPE_TAG) then
    AddChronoscope(WRITE_INTERVAL_SECONDS, CHRONOSCOPE_TAG)
    write_snapshot()
  end
end
