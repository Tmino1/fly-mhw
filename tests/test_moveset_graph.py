"""
Offline fixture suite for env/moveset_graph.py against the real
configs/weapons/greatsword_moveset.yaml — no game, no evdev needed.

    python tests/test_moveset_graph.py

These pin the graph's *semantics* (root resolution, re-rooting, expiry,
charge `via` nodes, continues_as). Whether the transcribed edges match
the live game is a separate question — that's what
scripts/audit_tool_labels.py answers from recorded demos.
"""

import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

from env.moveset_graph import MovesetGraph, MovesetTracker  # noqa: E402

failures = 0


def check(label, ok, detail=""):
    global failures
    print(f"[{'OK' if ok else 'FAIL'}] {label}" + (f" — {detail}" if detail else ""))
    if not ok:
        failures += 1


g = MovesetGraph.from_config(REPO / "configs/weapons/greatsword_moveset.yaml")

# 1. Config validates and exposes the expected reachable moves
moves = g.moves()
check("config loads", True)
check("reachable moves exclude env-dependent ones",
      "sprint" not in moves and "midair_charged_slash" not in moves, str(moves))
check("dodge/sheathe are moves", "dodge" in moves and "sheathe" in moves)

# 2. The charge chain: neutral -> Charged -> Strong Charged -> True Charged
tr = MovesetTracker(g)
t1 = tr.advance_input("y_hold", "release", 0.0, 0.8)
t2 = tr.advance_input("y_hold", "release", 1.0, 2.0)
t3 = tr.advance_input("y_hold", "release", 2.5, 3.5)
check("charge chain moves", [t1.move, t2.move, t3.move] ==
      ["charged_slash", "strong_charged_slash", "true_charged_slash"],
      str([t1.move, t2.move, t3.move]))
check("charge chain via nodes", [t1.via, t2.via, t3.via] == ["charge_1", "charge_2", "charge_3"])
check("charge chain depth 1->3", [t1.depth, t2.depth, t3.depth] == [1, 2, 3])
check("charge chain path", t3.path_from_root ==
      ["neutral", "charged_slash", "strong_charged_slash", "true_charged_slash"], str(t3.path_from_root))
check("charge chain never re-roots", not any(t.rerooted for t in (t1, t2, t3)))

# 3. True Charged Slash returns to neutral (a fresh chain, not a re-root)
t4 = tr.advance_input("b", None, 4.0)
check("after TCS, B = wide_slash from neutral", t4.move == "wide_slash" and t4.root_before == "neutral"
      and not t4.rerooted and not t4.expired and t4.depth == 1, str(t4.to_dict()))

# 4. The same input is a different move depending on the root
tr = MovesetTracker(g)
check("B from neutral", tr.advance_input("b", None, 0.0).move == "wide_slash")
tr = MovesetTracker(g)
tr.advance_input("y_hold", "release", 0.0, 0.5)
tr.advance_input("y_hold", "release", 1.0, 1.5)
check("B after SCS", tr.advance_input("b", None, 2.0).move == "strong_wide_slash")
tr = MovesetTracker(g)
tr.advance_input("b", None, 0.0)          # wide_slash
tr.advance_input("b", None, 1.0)          # tackle_b
jws = tr.advance_input("b", None, 2.0)
check("B after tackle_b", jws.move == "jumping_wide_slash" and jws.depth == 3, str(jws.to_dict()))

# 5. A Y tap after a Charged Slash is Side Blow 1 — the root claims y_tap,
#    so it never falls through to neutral's Overhead Smash
tr = MovesetTracker(g)
tr.advance_input("y_hold", "release", 0.0, 0.5)
sb = tr.advance_input("y_tap", None, 1.0)
check("Y tap after Charged Slash", sb.move == "side_blow_1" and not sb.rerooted)

# 6. An unclaimed input re-roots through neutral (B after Charged Slash)
tr = MovesetTracker(g)
tr.advance_input("y_hold", "release", 0.0, 0.5)
rr = tr.advance_input("b", None, 1.0)
check("unclaimed input re-roots", rr.move == "wide_slash" and rr.rerooted and rr.depth == 1
      and rr.path_from_root == ["neutral", "wide_slash"], str(rr.to_dict()))

# 7. An expired combo window re-roots to neutral
tr = MovesetTracker(g)
tr.advance_input("y_hold", "release", 0.0, 0.5)
late = tr.advance_input("y_tap", None, 60.0)
check("expired window", late.expired and late.root_before == "neutral" and late.move == "overhead_smash",
      str(late.to_dict()))

# 8. Tackle skips a tier: charge_1 tackle -> hold Y -> Strong Charged Slash
tr = MovesetTracker(g)
tk = tr.advance_input("y_hold", "tackle", 0.0, 0.5)
nxt = tr.advance_input("y_hold", "release", 1.0, 2.0)
check("tackle from charge 1", tk.move == "tackle" and tk.to == "tackle_1" and tk.via == "charge_1")
check("tackle skips a tier", nxt.move == "strong_charged_slash" and nxt.via == "charge_2")

# 9. continues_as: Strong Wide Slash follows up like Wide Slash
tr = MovesetTracker(g)
tr.advance_input("y_hold", "release", 0.0, 0.5)
tr.advance_input("y_hold", "release", 1.0, 1.5)
tr.advance_input("b", None, 2.0)  # strong_wide_slash
ca = tr.advance_input("b", None, 3.0)
check("continues_as wide_slash", ca.move == "tackle" and ca.to == "tackle_b" and not ca.rerooted,
      str(ca.to_dict()))

# 10. Dodge from any attack -> dodge_out, which starts a new chain
tr = MovesetTracker(g)
tr.advance_input("y_hold", "release", 0.0, 0.5)
dg = tr.advance_input("a", None, 1.0)
check("dodge from an attack", dg.move == "dodge" and dg.to == "dodge_out" and dg.depth == 0)
aft = tr.advance_input("y_tap", None, 1.5)
check("Y after dodge = tackle", aft.move == "tackle" and aft.to == "tackle_1")

# 11. Tackle during Charge 3 isn't on the chart -> unresolved, falls back to neutral
tr = MovesetTracker(g)
tr.advance_input("y_hold", "release", 0.0, 0.5)
tr.advance_input("y_hold", "release", 1.0, 1.5)
un = tr.advance_input("y_hold", "tackle", 2.0, 3.0)
check("unresolved input", un.unresolved and un.move is None and tr.current == "neutral")

# 12. Relative labels and availability change with the root
tr = MovesetTracker(g)
rel_neutral = tr.advance_input("b", None, 0.0).relative_label
tr = MovesetTracker(g)
tr.advance_input("y_hold", "release", 0.0, 0.5)
tr.advance_input("y_hold", "release", 1.0, 1.5)
rel_scs = tr.advance_input("b", None, 2.0).relative_label
check("relative label depends on root", rel_neutral != rel_scs, f"{rel_neutral} vs {rel_scs}")
tr = MovesetTracker(g)
check("neutral can't strong-wide-slash", "strong_wide_slash" not in tr.available_moves(0.0))
tr.advance_input("y_hold", "release", 0.0, 0.5)
tr.advance_input("y_hold", "release", 1.0, 1.5)
check("SCS can strong-wide-slash", "strong_wide_slash" in tr.available_moves(2.0))
check("SCS can't overhead-smash (y_tap is claimed)", "overhead_smash" not in tr.available_moves(2.0))

# 13. Candidate edges are ignored by the tracker
tr = MovesetTracker(g)
tr.advance_input("b", None, 0.0)  # wide_slash; wide_slash --y_hold--> charge_2 is only a candidate
cand = tr.advance_input("y_hold", "release", 0.5, 1.0)
check("candidate edge ignored", cand.move == "charged_slash" and cand.rerooted, str(cand.to_dict()))
check("candidates loaded", len(g.candidates) >= 8, str(len(g.candidates)))

# 14. Validation rejects a bad graph
try:
    MovesetGraph.from_dict({"name": "bad", "nodes": [{"id": "neutral", "kind": "root"}],
                            "edges": [{"from": "neutral", "input": "b", "to": "nowhere"}]})
    check("bad edge rejected", False)
except ValueError:
    check("bad edge rejected", True)

print(f"\n{failures} failure(s).")
sys.exit(1 if failures else 0)
