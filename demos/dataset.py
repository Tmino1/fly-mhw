"""
Reading v2 demo episodes (schema fly-mhw/demo_episode/v2) back as
training data.

iter_training_pairs(): one pair per labelled tool call — the latest frame
captured at or before the call started (the same observation_t ->
action_t causal ordering v1 used: the label is what you did in response
to that frame), the combo state before the call, the absolute call, and
its graph-relative label.

iter_sequences(): tool calls grouped into combo chains, split wherever the
tracker marked `starts_chain` (a fresh/expired/returned-to neutral, a
re-root, a dodge, or an unresolved input) — for learning the paths from
neutral to deep moves as sequences.

Whether the graph state is ever fed to the brain as an input is an open
Phase 2 decision (docs/architecture.md); here it's just carried along.
"""

from __future__ import annotations

import bisect
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterator, Optional

from env.tools import ToolCall

from .labeling import read_jsonl


@dataclass
class TrainingPair:
    frame_path: Path
    frame_t: float
    call: ToolCall
    graph_before: dict[str, Any]  # root, depth, path, available options — before the call
    relative_label: Optional[int]  # index into graph_before["available"]; None for timed tools
    overlaps: bool
    record: dict[str, Any]  # the full tool_calls.jsonl record


def _graph_before(graph: dict[str, Any]) -> dict[str, Any]:
    path = list(graph.get("path_before") or [])
    return {
        "root": graph.get("from"),
        "expired": graph.get("expired"),
        "path_from_root": path,
        "depth": max(len(path) - 1, 0),
        "available": graph.get("available", []),
    }


def iter_training_pairs(episode_dir: str | Path, skip_overlaps: bool = False) -> Iterator[TrainingPair]:
    episode_dir = Path(episode_dir)
    frames = read_jsonl(episode_dir / "frames.jsonl")
    frame_ts = [f["t"] for f in frames]
    for rec in read_jsonl(episode_dir / "tool_calls.jsonl"):
        if rec["call"] is None or (skip_overlaps and rec["overlaps"]):
            continue
        i = bisect.bisect_right(frame_ts, rec["t_start"]) - 1
        if i < 0:
            continue  # no frame captured before this call yet
        yield TrainingPair(
            frame_path=episode_dir / frames[i]["frame"],
            frame_t=frames[i]["t"],
            call=ToolCall.from_dict(rec["call"]),
            graph_before=_graph_before(rec["graph"]),
            relative_label=rec["graph"].get("relative_label"),
            overlaps=rec["overlaps"],
            record=rec,
        )


def iter_sequences(episode_dir: str | Path, include_timed: bool = False) -> Iterator[list[dict[str, Any]]]:
    """Yield lists of tool_calls.jsonl records, one list per combo chain.
    Timed tools (move/wait/camera) don't break a chain; with
    include_timed=False they're left out of the yielded lists."""
    chain: list[dict[str, Any]] = []
    for rec in read_jsonl(Path(episode_dir) / "tool_calls.jsonl"):
        graph = rec["graph"]
        is_move = bool(graph.get("move")) or graph.get("unresolved")
        if is_move and graph.get("starts_chain") and any(r["graph"].get("move") for r in chain):
            yield chain
            chain = []
        if is_move or include_timed:
            chain.append(rec)
    if any(r["graph"].get("move") for r in chain):
        yield chain
