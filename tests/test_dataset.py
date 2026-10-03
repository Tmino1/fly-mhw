"""
Offline fixture suite for demos/labeling.py + demos/dataset.py — builds a
synthetic v2 episode directory (frames.jsonl + input_events.jsonl +
lmt_events.jsonl, no real PNGs needed), labels it, and reads it back.

    python tests/test_dataset.py
"""

import json
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

from demos.dataset import iter_sequences, iter_training_pairs  # noqa: E402
from demos.labeling import label_episode, read_jsonl  # noqa: E402
from env.tools import ToolSet  # noqa: E402

failures = 0


def check(label, ok, detail=""):
    global failures
    print(f"[{'OK' if ok else 'FAIL'}] {label}" + (f" — {detail}" if detail else ""))
    if not ok:
        failures += 1


ts = ToolSet.from_config(REPO / "configs/weapons/greatsword_tools.yaml")
T0 = 1_000_000.0  # wall-clock-like timestamps


def key(t, role, down):
    return {"type": "key", "t": T0 + t, "role": role, "down": down}


def press(role, t0, t1):
    return [key(t0, role, True), key(t1, role, False)]


with tempfile.TemporaryDirectory() as tmp:
    ep = Path(tmp)
    # Frames every 0.2s for 8s
    with (ep / "frames.jsonl").open("w") as f:
        for i in range(41):
            f.write(json.dumps({"step": i, "t": T0 + 0.2 * i, "frame": f"frame_{i:06d}.png"}) + "\n")
    # Chain 1: charged -> strong charged (0.0-2.0). Long gap (window expires).
    # Chain 2: B from neutral (6.0), then a dodge (6.5) and Y-after-dodge tackle (6.8).
    events = (press("y", 0.05, 0.55) + press("y", 1.05, 2.0)
              + press("b", 6.0, 6.1) + press("a", 6.5, 6.55) + press("y", 6.8, 6.9))
    with (ep / "input_events.jsonl").open("w") as f:
        for e in sorted(events, key=lambda e: e["t"]):
            f.write(json.dumps(e) + "\n")
    with (ep / "lmt_events.jsonl").open("w") as f:
        for t, lmt in [(0.3, 101), (0.7, 102), (1.4, 201), (2.1, 202)]:
            f.write(json.dumps({"lmt_id": lmt, "fsm": 0, "t_arrival": T0 + t}) + "\n")

    summary = label_episode(ep, ts, {"counts_small": 60, "counts_large": 400})
    recs = read_jsonl(ep / "tool_calls.jsonl")
    moves = [r["call"]["tool"] for r in recs if r["graph"].get("move")]
    check("labelled moves", moves == ["charged_slash", "strong_charged_slash", "wide_slash", "dodge", "tackle"],
          str(moves))
    check("summary counts", summary["tool_counts"].get("charged_slash") == 1 and summary["unresolved"] == 0,
          str(summary))
    first = next(r for r in recs if r["graph"].get("move"))
    check("lmt ids attributed to the call window", first["lmt"]["during"][:2] == [101, 102], str(first["lmt"]))

    # Training pairs: the frame is the latest one at or before t_start
    pairs = list(iter_training_pairs(ep))
    ok = all(p.frame_t <= p.record["t_start"] and p.record["t_start"] - p.frame_t < 0.2 + 1e-9 for p in pairs)
    check("pairs use the last frame before the call", ok and len(pairs) == len([r for r in recs if r["call"]]))
    scs = next(p for p in pairs if p.call.name == "strong_charged_slash")
    check("graph_before is the pre-call combo state", scs.graph_before["root"] == "charged_slash"
          and scs.graph_before["path_from_root"] == ["neutral", "charged_slash"], str(scs.graph_before))
    check("relative label indexes graph_before's options",
          scs.graph_before["available"][scs.relative_label].endswith("strong_charged_slash via charge_2"),
          str(scs.graph_before["available"]))

    # Sequences: split at the expired window, and the dodge starts its own chain
    seqs = [[r["call"]["tool"] for r in chain] for chain in iter_sequences(ep)]
    check("sequences split into chains", seqs == [["charged_slash", "strong_charged_slash"], ["wide_slash"],
                                                  ["dodge", "tackle"]], str(seqs))



# --- charge level is READ from the animation id, not the hold duration ---
# Both cases below are ones the duration heuristic gets wrong in real
# recordings: a frame-rate dip shifting a hold across a narrow band, and
# the Great Sword auto-releasing a full charge (so key-up lands after the
# swing and the measured hold is meaningless). See demos/labeling.py's
# level_from_lmt().
with tempfile.TemporaryDirectory() as tmp:
    ep = Path(tmp)
    with (ep / "frames.jsonl").open("w") as f:
        for i in range(61):
            f.write(json.dumps({"step": i, "t": T0 + 0.2 * i, "frame": f"frame_{i:06d}.png"}) + "\n")

    # A hold of 0.9s: by duration that's lv1 (band 0.3-1.5). The game says
    # 49305 — lv2. The label must follow the game.
    # Then, after a long enough gap that the combo window expires (so this
    # is a fresh charged_slash, not a chained SCS), a 5.0s hold: way past
    # lv3, so the GS auto-released long before key-up and the measured
    # hold duration is meaningless. The id still says lv3.
    events = press("y", 0.05, 0.95) + press("y", 5.0, 10.0)
    with (ep / "input_events.jsonl").open("w") as f:
        for e in sorted(events, key=lambda e: e["t"]):
            f.write(json.dumps(e) + "\n")
    with (ep / "lmt_events.jsonl").open("w") as f:
        for t, lmt in [(0.5, 49304), (0.9, 49305), (5.5, 49304), (7.0, 49306)]:
            f.write(json.dumps({"lmt_id": lmt, "fsm": 0, "t_arrival": T0 + t}) + "\n")

    summary = label_episode(ep, ts, {"counts_small": 60, "counts_large": 400})
    recs = [r for r in read_jsonl(ep / "tool_calls.jsonl") if r["graph"].get("move")]
    levels = [(r["call"]["tool"], r["call"]["args"].get("level"), r.get("level_source")) for r in recs]
    check("level taken from the observed lmt id",
          levels[0] == ("charged_slash", "lv2", "lmt"), str(levels))
    check("duration guess kept for comparison", recs[0].get("level_from_duration") == "lv1", str(recs[0]))
    check("auto-released full charge reads as lv3",
          any(lv == "lv3" and src == "lmt" for _, lv, src in levels), str(levels))
    check("corrections counted", summary["level_corrections"] >= 1, str(summary))

# No measured ids for this move -> fall back to the duration guess rather
# than inventing a level.
with tempfile.TemporaryDirectory() as tmp:
    ep = Path(tmp)
    with (ep / "frames.jsonl").open("w") as f:
        f.write(json.dumps({"step": 0, "t": T0, "frame": "frame_000000.png"}) + "\n")
    with (ep / "input_events.jsonl").open("w") as f:
        for e in sorted(press("y", 0.05, 0.95), key=lambda e: e["t"]):
            f.write(json.dumps(e) + "\n")
    with (ep / "lmt_events.jsonl").open("w") as f:
        f.write(json.dumps({"lmt_id": 999999, "fsm": 0, "t_arrival": T0 + 0.5}) + "\n")
    label_episode(ep, ts, {"counts_small": 60, "counts_large": 400})
    recs = [r for r in read_jsonl(ep / "tool_calls.jsonl") if r["graph"].get("move")]
    check("unknown ids fall back to duration",
          recs and recs[0].get("level_source") == "duration", str(recs[:1]))

print(f"\n{failures} failure(s).")
sys.exit(1 if failures else 0)
