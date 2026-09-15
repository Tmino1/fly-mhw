"""
Offline fixture suite for env/reward.py — no running game needed.

    python tests/test_reward.py

Worth keeping green: this has already caught two real bugs before they
reached the live game — an exact-zero HP check that would never have
fired against real float reads, and monsters[0] selection picking a
random small monster instead of the hunt target.
"""

import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

from env.reward import RewardModel  # noqa: E402
from env.game_interface.lua_bridge import GameState  # noqa: E402

r = RewardModel.from_config(REPO / "configs/monsters/great_jagras.yaml")


def gs(player=None, monsters=None, quest=None):
    return GameState(
        raw={"player": player, "monsters": monsters, "quest": quest},
        read_at=0.0,
        file_age_seconds=0.1,
    )


def check(label, outcome, expect):
    ok = all(getattr(outcome, k) == v for k, v in expect.items())
    status = "OK" if ok else "FAIL"
    print(f"[{status}] {label}: reward={outcome.reward:.4f} terminated={outcome.terminated} "
          f"truncated={outcome.truncated} reason={outcome.reason!r}")
    if not ok:
        print(f"        expected: {expect}")


# 1. Normal step: monster takes some damage, player takes a little
prev = gs(
    player={"health_current": 150, "health_max": 150},
    monsters=[{"id": 1, "health_current": 500, "health_max": 500}],
    quest={"id": 42, "state": 0},
)
curr = gs(
    player={"health_current": 140, "health_max": 150},
    monsters=[{"id": 1, "health_current": 450, "health_max": 500}],
    quest={"id": 42, "state": 0},
)
out = r.step(prev, curr, 0)
check("normal step (monster -50/500, player -10/150)", out,
      {"terminated": False, "truncated": False, "reason": ""})
expected_reward = -0.001 + 1.0 * (50/500) + (-1.0) * (10/150)
print(f"        expected reward ~= {expected_reward:.4f}")

# 2. Player faints (cart)
curr2 = gs(
    player={"health_current": 0, "health_max": 150},
    monsters=[{"id": 1, "health_current": 450, "health_max": 500}],
    quest={"id": 42, "state": 0},
)
out2 = r.step(prev, curr2, 1)
check("player cart", out2, {"terminated": True, "truncated": False, "reason": "player_cart"})

# 3. Monster defeated: HP was near-zero (not exactly 0.0 — realistic float
# read) last step, monster list now empty, quest was real
prev3 = gs(
    player={"health_current": 140, "health_max": 150},
    monsters=[{"id": 1, "health_current": 0.3, "health_max": 500}],  # 0.06% of max, well under the 1% threshold
    quest={"id": 42, "state": 0},
)
curr3 = gs(
    player={"health_current": 140, "health_max": 150},
    monsters=[],
    quest={"id": 42, "state": 0},
)
out3 = r.step(prev3, curr3, 2)
check("monster defeated", out3, {"terminated": True, "truncated": False, "reason": "monster_defeated"})

# 4. Quest ends but monster wasn't near 0 HP (abandoned/captured/unknown)
prev4 = gs(
    player={"health_current": 140, "health_max": 150},
    monsters=[{"id": 1, "health_current": 300, "health_max": 500}],
    quest={"id": 42, "state": 0},
)
curr4 = gs(
    player={"health_current": 140, "health_max": 150},
    monsters=[],
    quest={"id": -1, "state": 0},
)
out4 = r.step(prev4, curr4, 3)
check("quest ended unknown", out4, {"terminated": True, "truncated": False, "reason": "quest_ended_unknown"})

# 5. Max steps truncation (no other terminal condition)
out5 = r.step(prev, curr, r.max_episode_steps - 1)
check("max steps truncation", out5, {"terminated": False, "truncated": True, "reason": "max_steps"})

# 6. Error-shaped sections don't crash
prev6 = gs(player={"error": "pcall failed"}, monsters={"error": "pcall failed"}, quest={"id": 42, "state": 0})
curr6 = gs(player={"health_current": 140, "health_max": 150},
           monsters=[{"id": 1, "health_current": 450, "health_max": 500}],
           quest={"id": 42, "state": 0})
out6 = r.step(prev6, curr6, 4)
check("error-shaped prev sections (no crash)", out6, {"terminated": False, "truncated": False, "reason": ""})

# 7. No prev_state at all (first step after reset)
out7 = r.step(None, curr, 0)
check("no prev_state (first step)", out7, {"terminated": False, "truncated": False, "reason": ""})


# 8. Monster selection with a realistic crowded list — modeled on the real
# 11-entity state captured live from a training area (small monsters at
# max HP 100/300), plus a large quest target.
crowded = [
    {"id": 57, "health_current": 100.0, "health_max": 100.0},
    {"id": 58, "health_current": 300.0, "health_max": 300.0},
    {"id": 59, "health_current": 300.0, "health_max": 300.0},
    {"id": 42, "health_current": 4000.0, "health_max": 5000.0},  # the actual target
    {"id": 58, "health_current": 300.0, "health_max": 300.0},
]
picked = r.select_monster(gs(monsters=crowded))
ok = picked and picked["id"] == 42
print(f"[{'OK' if ok else 'FAIL'}] crowded list picks the large monster: id={picked and picked['id']} "
      f"(expected 42, NOT 57 which is monsters[0])")

# 9. select_monster tolerates an all-small list rather than returning None
#    (min_health_max fallback) — returning None reads as "quest ended".
small_only = [{"id": 57, "health_current": 100.0, "health_max": 100.0}]
picked_small = r.select_monster(gs(monsters=small_only))
print(f"[{'OK' if picked_small is not None else 'FAIL'}] all-small list still returns a monster "
      f"(fallback, avoids false 'quest ended'): {picked_small and picked_small['id']}")

# 10. monster_id / monster_count surfaced for logging
prev10 = gs(player={"health_current": 150, "health_max": 150}, monsters=crowded, quest={"id": 42, "state": 0})
curr10 = gs(player={"health_current": 150, "health_max": 150}, monsters=crowded, quest={"id": 42, "state": 0})
out10 = r.step(prev10, curr10, 0)
ok10 = out10.monster_id == 42 and out10.monster_count == 5
print(f"[{'OK' if ok10 else 'FAIL'}] StepOutcome exposes monster_id={out10.monster_id} "
      f"monster_count={out10.monster_count} (expected 42 / 5)")

print("\nAll checks ran without raising.")
