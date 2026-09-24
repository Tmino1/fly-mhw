"""
Episode-level labelling: a recorded episode's raw event logs
(input_events.jsonl, lmt_events.jsonl) -> tool_calls.jsonl.

Run by demos/recorder.py at the end of every episode, and re-runnable any
time later by scripts/relabel_demos.py — the raw events are the ground
truth that's kept, so improved segmenter rules, retuned timings or a
corrected moveset graph can be applied to old recordings without
re-playing anything.
"""

from __future__ import annotations

import hashlib
import json
from collections import Counter
from pathlib import Path
from typing import Any, Optional

from env.moveset_graph import MovesetTracker
from env.tools import ToolSet

from .tool_segmenter import KeyEvent, MouseMotion, label_moves, segment_inputs

# How long after a call ends its lmtIDs are still attributed to it — the
# Lua action log lands with some latency, and an attack's animation
# outlives the button press.
LMT_TAIL_SECONDS = 0.6


def file_sha256(path: str | Path) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


def load_events(episode_dir: Path) -> tuple[list[KeyEvent], list[MouseMotion]]:
    keys, mouse = [], []
    for rec in read_jsonl(episode_dir / "input_events.jsonl"):
        if rec["type"] == "key":
            keys.append(KeyEvent(rec["t"], rec["role"], rec["down"]))
        elif rec["type"] == "mouse":
            mouse.append(MouseMotion(rec["t"], rec["dx"], rec["dy"]))
    return keys, mouse


def label_episode(episode_dir: str | Path, toolset: ToolSet, mouse_cfg: dict[str, Any],
                  t_begin: Optional[float] = None, t_end: Optional[float] = None) -> dict[str, Any]:
    """(Re)write episode_dir/tool_calls.jsonl; return summary counts."""
    episode_dir = Path(episode_dir)
    keys, mouse = load_events(episode_dir)
    lmt = sorted(read_jsonl(episode_dir / "lmt_events.jsonl"), key=lambda r: r["t_arrival"])
    if t_begin is None or t_end is None:
        frames = read_jsonl(episode_dir / "frames.jsonl")
        if frames:
            t_begin = frames[0]["t"] if t_begin is None else t_begin
            t_end = frames[-1]["t"] if t_end is None else t_end

    segments = segment_inputs(keys, mouse, toolset, mouse_cfg, t_begin, t_end)
    tracker = MovesetTracker(toolset.graph, t_begin or 0.0)
    labels = label_moves(segments, toolset, tracker)

    tools, moves, depths = Counter(), Counter(), Counter()
    unresolved = overlaps = 0
    with (episode_dir / "tool_calls.jsonl").open("w") as f:
        for i, label in enumerate(labels):
            rec = label.to_dict()
            rec["index"] = i
            before = [r for r in lmt if r["t_arrival"] <= label.t_start]
            window = [r for r in lmt if label.t_start < r["t_arrival"] <= label.t_end + LMT_TAIL_SECONDS]
            rec["lmt"] = {
                "before": before[-1].get("lmt_id") if before else None,
                "during": [r.get("lmt_id") for r in window],
            }
            f.write(json.dumps(rec) + "\n")
            if label.call:
                tools[label.call.name] += 1
                if label.graph.get("move"):
                    depths[label.graph["depth"]] += 1
            unresolved += label.unresolved
            overlaps += label.overlaps
            if label.graph.get("move"):
                moves[label.graph["to"]] += 1

    return {
        "tool_calls": len(labels),
        "tool_counts": dict(tools),
        "move_node_counts": dict(moves),
        "depth_counts": {str(k): v for k, v in sorted(depths.items())},
        "unresolved": unresolved,
        "overlaps": overlaps,
        "lmt_events": len(lmt),
    }
