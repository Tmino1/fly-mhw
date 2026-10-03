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
from env.tools import ToolCall, ToolSet

from .tool_segmenter import KeyEvent, MouseMotion, label_moves, segment_inputs

# How long after a call ends its lmtIDs are still attributed to it — the
# Lua action log lands with some latency, and an attack's animation
# outlives the button press.
LMT_TAIL_SECONDS = 0.6


def level_lookup(toolset: ToolSet) -> dict[str, dict[int, str]]:
    """move -> {observed lmt_id: charge level}, from the moveset graph's
    measured `lmt_ids`. Only moves that have been measured live appear."""
    out: dict[str, dict[int, str]] = {}
    for node in toolset.graph.nodes.values():
        if not node.lmt_ids or not node.move:
            continue
        out.setdefault(node.move, {}).update(
            {int(lmt): lv for lv, lmt in node.lmt_ids.items()}
        )
    return out


def level_from_lmt(move: str, observed: list[int], lookup: dict[str, dict[int, str]]) -> Optional[str]:
    """The charge level the GAME actually produced, read from the
    animation ids seen during a call.

    This is strictly better than inferring the level from how long the
    key was held, for two reasons found live (2026-10-02):
      - the charge thresholds are wall-clock approximations of
        frame-based game logic, and the narrow bands (lv2 spans only
        ~0.6s) misfire silently when the frame rate dips — a hold meant
        as lv2 lands as lv1 and nothing notices;
      - the Great Sword AUTO-RELEASES a full charge if held past lv3, so
        the key-up timestamp can come after the swing already happened,
        making the measured hold duration meaningless.
    Returns None when no observed id is a known level for this move, so
    the caller can fall back to the duration guess rather than inventing
    a level.
    """
    by_id = lookup.get(move)
    if not by_id:
        return None
    # Last match wins: a charge passes through lower levels on the way up,
    # and the level that matters is the one it was at when it fired.
    found = [by_id[i] for i in observed if i in by_id]
    return found[-1] if found else None


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

    lookup = level_lookup(toolset)
    tools, moves, depths = Counter(), Counter(), Counter()
    unresolved = overlaps = 0
    level_corrections = 0
    with (episode_dir / "tool_calls.jsonl").open("w") as f:
        for i, label in enumerate(labels):
            rec = label.to_dict()
            rec["index"] = i
            before = [r for r in lmt if r["t_arrival"] <= label.t_start]
            window = [r for r in lmt if label.t_start < r["t_arrival"] <= label.t_end + LMT_TAIL_SECONDS]
            during = [r.get("lmt_id") for r in window]
            rec["lmt"] = {
                "before": before[-1].get("lmt_id") if before else None,
                "during": during,
            }

            # Prefer the level the game actually reached over the one the
            # hold duration implies. See level_from_lmt() for why the
            # duration is the weaker signal.
            if label.call is not None and "level" in label.call.arg_dict:
                observed = level_from_lmt(label.call.name, [i for i in during if i is not None], lookup)
                guessed = label.call.arg_dict["level"]
                if observed is None:
                    rec["level_source"] = "duration"
                else:
                    rec["level_source"] = "lmt"
                    if observed != guessed:
                        level_corrections += 1
                        rec["level_from_duration"] = guessed
                        args = dict(label.call.arg_dict)
                        args["level"] = observed
                        label.call = toolset.validate_call(ToolCall.make(label.call.name, **args))
                        rec["call"] = label.call.to_dict()

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
        # How many charge levels the observed animation ids disagreed with
        # the hold-duration guess about. Worth watching: a high count
        # means the duration thresholds in the weapon config have drifted
        # from the game (frame rate, or a patch), even though the labels
        # themselves are now correct regardless.
        "level_corrections": level_corrections,
    }
