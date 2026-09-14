--[[
  state_reader.lua — Phase 0 state-read spike for fly-mhw.

  STATUS: written against LuaEngine's documented API surface
  (GetAllMonster(), engine.Player:new(), CheckKeyIsPressed) but has NOT been
  run against a real game instance yet, because LuaEngine isn't installed on
  the machine this was written on. See docs/modding_setup.md before trusting
  any of this — expect to have to fix field names once you can actually
  inspect what GetAllMonster()/engine.Player return.

  Install: drop this file in the game's `Lua/` folder (created when you
  install LuaEngine — see docs/modding_setup.md), then in the in-game chat:
      reload state_reader

  What it does: once per second, dumps a JSON snapshot of all known
  monsters and the player's state to a file, so the Python side
  (env/game_interface/lua_bridge.py) can poll it. No JSON library is
  assumed to be available, so this hand-rolls a minimal encoder for the
  flat/nested tables we actually produce below — don't reuse it for
  anything more general.

  OUTPUT PATH: relative to the game process's working directory, which
  under Proton may not be exactly where you expect. After `reload
  state_reader`, check both the MHW install folder and your home directory
  for `fly_mhw_state.json` and update OUTPUT_PATH below once you know where
  it actually lands.
]]

local OUTPUT_PATH = "fly_mhw_state.json"
local WRITE_INTERVAL_SECONDS = 1.0

local last_write_time = 0

-- Minimal JSON encoder for the specific shapes this script produces
-- (nested tables of strings/numbers/booleans/arrays). Not general-purpose.
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
    -- Treat as an array if it has a contiguous integer key sequence
    -- starting at 1; otherwise treat as an object.
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

-- Best-effort snapshot builders. Wrapped in pcall since none of this API
-- usage is confirmed yet — a failure here should show up as an "error"
-- field in the output rather than silently killing the write loop.
local function snapshot_monsters()
  local ok, result = pcall(function()
    local monsters = GetAllMonster()
    local out = {}
    if monsters then
      for _, m in ipairs(monsters) do
        -- Field names are a guess (entity address / id / subspecies id per
        -- the LuaEngine README summary) — confirm against the real return
        -- shape and fix once LuaEngine is actually installed.
        out[#out + 1] = {
          address = m.address or m.Address,
          id = m.id or m.Id or m.monsterId,
          sub_id = m.subId or m.SubId,
        }
      end
    end
    return out
  end)
  if ok then
    return result
  else
    return { error = tostring(result) }
  end
end

local function snapshot_player()
  local ok, result = pcall(function()
    local player = engine.Player:new()
    return {
      x = player.x or (player.pos and player.pos.x),
      y = player.y or (player.pos and player.pos.y),
      z = player.z or (player.pos and player.pos.z),
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
    schema = "fly-mhw/state_reader/v0",
    written_at = os.time(),
    monsters = snapshot_monsters(),
    player = snapshot_player(),
  }

  local f = io.open(OUTPUT_PATH, "w")
  if f then
    f:write(json_encode(snapshot))
    f:close()
  end
  -- If io.open fails, io isn't available/unsandboxed the way we assumed —
  -- that's the first thing to check in docs/modding_setup.md's open
  -- questions if verify_state_read.py never sees a file appear.
end

-- LuaEngine scripts are typically driven by an update/tick callback; the
-- exact hook name isn't confirmed (check LuaEngine's example scripts once
-- installed). OnUpdate here is a placeholder name — rename to match
-- whatever the real per-frame/per-tick entry point is called.
function OnUpdate(delta_time)
  last_write_time = last_write_time + (delta_time or 0)
  if last_write_time >= WRITE_INTERVAL_SECONDS then
    last_write_time = 0
    write_snapshot()
  end
end
