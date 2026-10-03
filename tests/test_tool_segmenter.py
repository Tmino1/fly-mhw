"""
Offline fixture suite for demos/tool_segmenter.py — synthetic key/mouse
event timelines in, tool-call labels out. No game, no evdev.

    python tests/test_tool_segmenter.py

Charge-hold durations are DERIVED from the live config's level_seconds
rather than hardcoded, because those thresholds are still placeholders
awaiting live measurement of the red-flash timings — this suite should
test the segmenter's classification logic, not a particular guess at
the numbers. Other timings (tap_max 0.3s, yb window 0.08s, move short
0.4 / long 1.0) come from configs/weapons/greatsword_tools.yaml.
"""

import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

from demos.tool_segmenter import KeyEvent, MouseMotion, label_moves, segment_inputs  # noqa: E402
from env.moveset_graph import MovesetTracker  # noqa: E402
from env.tools import ToolSet  # noqa: E402

failures = 0


def check(label, ok, detail=""):
    global failures
    print(f"[{'OK' if ok else 'FAIL'}] {label}" + (f" — {detail}" if detail else ""))
    if not ok:
        failures += 1


ts = ToolSet.from_config(REPO / "configs/weapons/greatsword_tools.yaml")
MOUSE = {"counts_small": 60, "counts_large": 400}


def press(role, t0, t1):
    return [KeyEvent(t0, role, True), KeyEvent(t1, role, False)]


def run(keys, mouse=(), t_begin=0.0, t_end=None):
    t_end = t_end if t_end is not None else max([e.t for e in keys] + [m.t for m in mouse] + [0.0])
    segs = segment_inputs(keys, mouse, ts, MOUSE, t_begin, t_end)
    return label_moves(segs, ts, MovesetTracker(ts.graph, t_begin))


def calls(labels, kinds=None):
    return [str(l.call) if l.call else f"<{l.input}>" for l in labels
            if kinds is None or l.input in kinds]


ATTACKS = ("y_tap", "y_hold", "b", "yb", "rt_hold", "rt_y", "a", "x", "unrepresentable")

# 1. Tap vs hold levels, from neutral
check("Y tap -> uncharged charged_slash", calls(run(press("y", 0, 0.1)), ATTACKS)
      == ["charged_slash(direction=none, level=lv0)"], str(calls(run(press("y", 0, 0.1)), ATTACKS)))
# A hold comfortably inside each level's band: past that level's
# threshold, short of the next one's.
LVS = ts.timings["level_seconds"]
def mid(lv, nxt):
    return (LVS[lv] + LVS[nxt]) / 2 if nxt else LVS[lv] + 0.3
for hold, lv in [(mid("lv1", "lv2"), "lv1"), (mid("lv2", "lv3"), "lv2"), (mid("lv3", None), "lv3")]:
    got = calls(run(press("y", 0, hold)), ATTACKS)
    check(f"Y hold {hold}s -> charged_slash {lv}", got == [f"charged_slash(direction=none, level={lv})"], str(got))

# 2. The full charged chain: three holds -> charged / strong / true charged slash
# Spaced so each hold starts only after the previous one ends —
# derived durations are long enough that fixed start times would
# overlap and merge into one segment.
keys, t = [], 0.0
for _lv, _nxt in [("lv1", "lv2"), ("lv2", "lv3"), ("lv3", None)]:
    _h = mid(_lv, _nxt)
    keys += press("y", t, t + _h)
    t += _h + 0.5
labels = [l for l in run(keys) if l.input in ATTACKS]
check("charge chain", [l.call.name for l in labels] == ["charged_slash", "strong_charged_slash", "true_charged_slash"],
      str(calls(labels)))
check("charge chain depth", [l.graph["depth"] for l in labels] == [1, 2, 3])

# 3. LMB hold + RMB during the hold -> tackle at the level reached then
# RMB pressed once the hold has passed lv2 but not lv3 -> tackle at lv2.
tackle_at = mid("lv2", "lv3")
got = run(press("y", 0, tackle_at + 0.2) + press("b", tackle_at, tackle_at + 0.1))
check("hold + RMB -> tackle lv2", calls(got, ATTACKS) == ["tackle(direction=none, level=lv2)"], str(calls(got)))

# 4. Near-simultaneous LMB+RMB -> yb -> rising_slash
got = run(press("y", 0, 0.2) + press("b", 0.03, 0.2))
check("LMB+RMB -> rising_slash", calls(got, ATTACKS) == ["rising_slash(direction=none)"], str(calls(got)))

# 5. Diagonal aim: W+A held, Space -> dodge(forward_left)
got = run(press("move_forward", 0, 0.6) + press("move_left", 0, 0.6) + press("a", 0.3, 0.35))
check("W+A + Space -> dodge(forward_left)", calls(got, ATTACKS) == ["dodge(direction=forward_left)"], str(calls(got)))

# 6. A 50ms tap between two 0.2s frame ticks is still captured
got = run(press("b", 0.21, 0.26), t_end=0.6)
check("50ms tap captured", calls(got, ATTACKS) == ["wide_slash(direction=none)"], str(calls(got)))

# 7. WASD 0.8s alone -> move(forward, long)
got = run(press("move_forward", 0, 0.8))
check("W 0.8s -> move long", calls(got) == ["move(direction=forward, duration=long)"], str(calls(got)))

# 8. W held while attacking: aim for the attack, move outside it
got = run(press("move_forward", 0, 2.0) + press("b", 1.0, 1.1))
check("moving attack aims the attack", "wide_slash(direction=forward)" in calls(got), str(calls(got)))
check("move segments don't overlap the attack",
      all(not (l.t_start < 1.1 and 1.0 < l.t_end) for l in got if l.input == "move"), str(calls(got)))

# 9. Mouse sweep -> camera; flagged as overlapping when it coincides with a move
got = run(press("move_forward", 0, 1.0), mouse=[MouseMotion(0.05, -250, 0), MouseMotion(0.1, -250, 0)])
cams = [l for l in got if l.input == "camera"]
check("mouse dx -> camera(left, large)", [str(l.call) for l in cams] == ["camera(amount=large, yaw=left)"],
      str(calls(got)))
check("camera during move flagged overlaps", cams and cams[0].overlaps)
got = run([], mouse=[MouseMotion(0.05, 100, 0)], t_end=1.0)
cams = [l for l in got if l.input == "camera"]
check("camera while standing isn't an overlap", cams and not cams[0].overlaps and str(cams[0].call) ==
      "camera(amount=small, yaw=right)", str(calls(got)))
check("tiny mouse jitter ignored", not [l for l in run([], mouse=[MouseMotion(0.05, 5, 0)], t_end=1.0)
                                        if l.input == "camera"])

# 10. Idle gap -> wait
got = run(press("b", 0, 0.1) + press("b", 2.0, 2.1))
check("idle gap -> wait", any(l.input == "wait" for l in got), str(calls(got)))

# 11. Context decides the move: B after a Strong Charged Slash
got = run(press("y", 0, 0.5) + press("y", 1.0, 2.0) + press("b", 2.3, 2.4))
check("B after SCS -> strong_wide_slash", calls(got, ATTACKS)[-1] == "strong_wide_slash(direction=none)",
      str(calls(got, ATTACKS)))

# 12. Unbound-tool roles are flagged, not dropped
got = run(press("rb", 0, 0.5))
check("sprint flagged unrepresentable", [l.input for l in got if l.unresolved] == ["unrepresentable"])

# 13. Guard then kick: RT held, then Y
got = run(press("rt", 0, 1.0) + press("y", 0.6, 0.7))
check("RT hold + Y -> guard, kick", calls(got, ATTACKS) == ["guard(duration=short)", "kick(direction=none)"],
      str(calls(got, ATTACKS)))

print(f"\n{failures} failure(s).")
sys.exit(1 if failures else 0)
