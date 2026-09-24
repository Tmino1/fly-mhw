"""
Turns a human's timestamped keyboard/mouse events into tool-call labels,
in two stages:

  1. segment_inputs(): raw role events -> timed input segments. The same
     input primitives the gamepad side sends (y_tap, y_hold(level,
     finish), b, yb, rt_hold, rt_y, a, x) plus the timed tools (move,
     wait, camera). Hold durations, simultaneous presses, diagonals and
     mouse motion all survive — the things v1's 0.2s key-state polling
     threw away.
  2. label_moves(): each attack segment goes through the MovesetTracker
     at its own timestamp, which decides which MOVE it was from the
     current combo root (B after a Strong Charged Slash is
     strong_wide_slash, not wide_slash). Timed segments don't touch the
     root; they just record the combo state they happened in.

Known approximations (logged, not hidden):
  - The agent runs tools one at a time, but a human pans the camera
    while moving/attacking. Those camera calls are kept and flagged
    `overlaps`.
  - WASD held *during* an attack isn't a separate move call — it's the
    attack's aim (direction at the press). Held during a charge ("Y (can
    move)"), it's dropped.
  - Mouse pitch (REL_Y) isn't a tool; only yaw is labelled.

Pure Python: no evdev, no files. Tunables come from the tools config's
`segmenter`/`timings` blocks and the bindings' `mouse` block.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Iterable, Optional

from env.moveset_graph import MovesetTracker
from env.tools import ToolCall, ToolSet

MOVE_ROLES = ("move_forward", "move_back", "move_left", "move_right")


@dataclass(frozen=True)
class KeyEvent:
    t: float
    role: str
    down: bool


@dataclass(frozen=True)
class MouseMotion:
    t: float
    dx: int
    dy: int


@dataclass
class InputSegment:
    t_start: float
    t_end: float
    t_commit: float
    input: str  # an input primitive, a timed tool name, or "unrepresentable"
    finish: Optional[str] = None
    args: dict[str, Any] = field(default_factory=dict)
    overlaps: bool = False
    source: list[str] = field(default_factory=list)

    @property
    def is_attack(self) -> bool:
        return self.input not in ("move", "wait", "camera", "unrepresentable")


@dataclass
class ToolCallLabel:
    t_start: float
    t_end: float
    call: Optional[ToolCall]
    input: str
    finish: Optional[str]
    graph: dict[str, Any]
    overlaps: bool
    unresolved: bool
    source: list[str]

    def to_dict(self) -> dict[str, Any]:
        return {
            "t_start": self.t_start,
            "t_end": self.t_end,
            "call": self.call.to_dict() if self.call else None,
            "input": self.input,
            "finish": self.finish,
            "graph": self.graph,
            "overlaps": self.overlaps,
            "unresolved": self.unresolved,
            "source": self.source,
        }


@dataclass
class _Press:
    role: str
    t_down: float
    t_up: float
    used: bool = False


def _presses(events: Iterable[KeyEvent], t_end: float) -> list[_Press]:
    """Pair downs with ups per role. Key-repeat downs are ignored; a key
    still held at the end is closed at t_end."""
    open_: dict[str, float] = {}
    out: list[_Press] = []
    for ev in sorted(events, key=lambda e: e.t):
        if ev.down:
            open_.setdefault(ev.role, ev.t)
        elif ev.role in open_:
            out.append(_Press(ev.role, open_.pop(ev.role), ev.t))
    for role, t in open_.items():
        out.append(_Press(role, t, max(t, t_end)))
    return sorted(out, key=lambda p: p.t_down)


def direction_from_held(held: set[str]) -> str:
    fwd = ("move_forward" in held) - ("move_back" in held)
    side = ("move_right" in held) - ("move_left" in held)
    vertical = {1: "forward", -1: "back", 0: ""}[fwd]
    horizontal = {1: "right", -1: "left", 0: ""}[side]
    if vertical and horizontal:
        return f"{vertical}_{horizontal}"
    return vertical or horizontal or "none"


def _held_at(presses: list[_Press], t: float) -> set[str]:
    return {p.role for p in presses if p.role in MOVE_ROLES and p.t_down <= t < p.t_up}


def _nearest(buckets: dict[str, float], value: float) -> str:
    return min(buckets, key=lambda k: abs(buckets[k] - value))


def level_for_hold(seconds: float, level_seconds: dict[str, float]) -> str:
    reached = [lv for lv, s in sorted(level_seconds.items(), key=lambda kv: kv[1]) if seconds >= s]
    return reached[-1] if reached else min(level_seconds, key=level_seconds.get)


def _overlap(a0: float, a1: float, b0: float, b1: float) -> bool:
    return a0 < b1 and b0 < a1


def segment_inputs(
    key_events: Iterable[KeyEvent],
    mouse_events: Iterable[MouseMotion],
    toolset: ToolSet,
    mouse_cfg: dict[str, Any],
    t_begin: Optional[float] = None,
    t_end: Optional[float] = None,
) -> list[InputSegment]:
    key_events = list(key_events)
    mouse_events = sorted(mouse_events, key=lambda m: m.t)
    all_t = [e.t for e in key_events] + [m.t for m in mouse_events]
    if not all_t and (t_begin is None or t_end is None):
        return []
    t_begin = min(all_t) if t_begin is None else t_begin
    t_end = max(all_t) if t_end is None else t_end

    cfg = toolset.segmenter
    tim = toolset.timings
    presses = _presses(key_events, t_end)
    by_role = {r: [p for p in presses if p.role == r] for r in {p.role for p in presses}}
    ys, bs, rts = by_role.get("y", []), by_role.get("b", []), by_role.get("rt", [])
    yb_window = cfg["yb_simultaneous_seconds"]
    segs: list[InputSegment] = []

    def attack(t0, t1, commit, inp, finish=None, src=(), **args):
        segs.append(InputSegment(t0, t1, commit, inp, finish, args, source=list(src)))

    def rt_at(t):
        return next((r for r in rts if r.t_down <= t < r.t_up), None)

    # Y+B pressed (near-)simultaneously -> yb
    for y in ys:
        b = next((b for b in bs if not b.used and abs(b.t_down - y.t_down) <= yb_window), None)
        if b is not None:
            y.used = b.used = True
            t0 = min(y.t_down, b.t_down)
            attack(t0, max(y.t_up, b.t_up), t0, "yb", src=["y+b"],
                   direction=direction_from_held(_held_at(presses, t0)))

    for y in ys:
        if y.used:
            continue
        y.used = True
        direction = direction_from_held(_held_at(presses, y.t_down))
        rt = rt_at(y.t_down)
        if rt is not None:
            rt.used = True
            if y.t_down - rt.t_down >= cfg["guard_min_seconds"]:
                held = y.t_down - rt.t_down
                attack(rt.t_down, y.t_down, y.t_down, "rt_hold", src=["rt"],
                       duration=_nearest(tim["duration_seconds"], held))
            attack(y.t_down, y.t_up, y.t_down, "rt_y", src=["rt+y"], direction=direction)
            continue
        tackle_b = next((b for b in bs if not b.used and y.t_down + yb_window < b.t_down < y.t_up), None)
        if tackle_b is not None:
            tackle_b.used = True
            level = level_for_hold(tackle_b.t_down - y.t_down, tim["level_seconds"])
            attack(y.t_down, y.t_up, tackle_b.t_down, "y_hold", "tackle", src=["y-hold", "b"],
                   direction=direction, level=level)
            continue
        held = y.t_up - y.t_down
        if held < cfg["tap_max_seconds"]:
            attack(y.t_down, y.t_up, y.t_down, "y_tap", src=["y"], direction=direction)
        else:
            attack(y.t_down, y.t_up, y.t_up, "y_hold", "release", src=["y-hold"],
                   direction=direction, level=level_for_hold(held, tim["level_seconds"]))

    for b in bs:
        if not b.used:
            b.used = True
            attack(b.t_down, b.t_up, b.t_down, "b", src=["b"],
                   direction=direction_from_held(_held_at(presses, b.t_down)))
    for p in by_role.get("a", []):
        attack(p.t_down, p.t_up, p.t_down, "a", src=["a"],
               direction=direction_from_held(_held_at(presses, p.t_down)))
    for p in by_role.get("x", []):
        attack(p.t_down, p.t_up, p.t_down, "x", src=["x"])
    for rt in rts:
        if not rt.used:
            attack(rt.t_down, rt.t_up, rt.t_up, "rt_hold", src=["rt"],
                   duration=_nearest(tim["duration_seconds"], rt.t_up - rt.t_down))
    for role in ("rb", "lt"):
        for p in by_role.get(role, []):
            segs.append(InputSegment(p.t_down, p.t_up, p.t_down, "unrepresentable", source=[role]))

    busy = sorted((s.t_start, s.t_end) for s in segs)

    # Movement: WASD-held stretches outside attacks, split wherever the
    # held direction changes, chunked to at most a `long` move each.
    move_presses = [p for p in presses if p.role in MOVE_ROLES]
    cuts = sorted({t_begin, t_end} | {p.t_down for p in move_presses} | {p.t_up for p in move_presses}
                  | {b0 for b0, _ in busy} | {b1 for _, b1 in busy})
    long_s = tim["duration_seconds"]["long"]
    for c0, c1 in zip(cuts, cuts[1:]):
        if c1 - c0 < cfg["min_move_seconds"] or any(_overlap(c0, c1, b0, b1) for b0, b1 in busy):
            continue
        direction = direction_from_held(_held_at(presses, c0))
        if direction == "none":
            continue
        t = c0
        while c1 - t >= cfg["min_move_seconds"]:
            piece = min(long_s, c1 - t)
            segs.append(InputSegment(t, t + piece, t, "move", args={
                "direction": direction, "duration": _nearest(tim["duration_seconds"], piece)},
                source=["wasd"]))
            t += piece

    # Camera: summed mouse yaw per fixed window. Flagged `overlaps` when it
    # coincides with a move/attack (the agent can't do both at once).
    window = cfg["camera_window_seconds"]
    small, large = mouse_cfg.get("counts_small"), mouse_cfg.get("counts_large")
    if mouse_events and small:
        sums: dict[int, int] = {}
        for m in mouse_events:
            k = int((m.t - t_begin) // window)
            sums[k] = sums.get(k, 0) + m.dx
        non_camera = [(s.t_start, s.t_end) for s in segs]
        for k in sorted(sums):
            dx = sums[k]
            if abs(dx) < small:
                continue
            w0 = t_begin + k * window
            segs.append(InputSegment(
                w0, w0 + window, w0, "camera",
                args={"yaw": "left" if dx < 0 else "right",
                      "amount": "large" if large and abs(dx) >= large else "small"},
                overlaps=any(_overlap(w0, w0 + window, a, b) for a, b in non_camera),
                source=[f"mouse dx={dx}"]))

    # Waits: gaps with no attack, move or camera call.
    occupied = sorted((s.t_start, s.t_end) for s in segs)
    gaps, cursor = [], t_begin
    for s0, s1 in occupied:
        if s0 > cursor:
            gaps.append((cursor, s0))
        cursor = max(cursor, s1)
    if t_end > cursor:
        gaps.append((cursor, t_end))
    for g0, g1 in gaps:
        t = g0
        while g1 - t >= cfg["min_wait_seconds"]:
            piece = min(long_s, g1 - t)
            segs.append(InputSegment(t, t + piece, t, "wait", args={
                "duration": _nearest(tim["duration_seconds"], piece)}, source=["idle"]))
            t += piece

    return sorted(segs, key=lambda s: (s.t_start, s.t_commit))


def label_moves(segments: list[InputSegment], toolset: ToolSet, tracker: MovesetTracker) -> list[ToolCallLabel]:
    labels: list[ToolCallLabel] = []
    for seg in segments:
        if seg.input == "unrepresentable":
            labels.append(ToolCallLabel(seg.t_start, seg.t_end, None, seg.input, None,
                                        tracker.snapshot(seg.t_start), seg.overlaps, True, seg.source))
            continue
        if not seg.is_attack:
            call = toolset.validate_call(ToolCall.make(seg.input, **seg.args))
            labels.append(ToolCallLabel(seg.t_start, seg.t_end, call, seg.input, None,
                                        tracker.snapshot(seg.t_start), seg.overlaps, False, seg.source))
            continue

        option = tracker.resolve_input(seg.input, seg.finish, seg.t_start)
        transition = tracker.advance(option, seg.t_start, seg.t_commit)
        call = None
        if option is not None:
            spec = toolset.spec(option.move)
            args = {}
            for name in spec.arg_names:
                if name == "level":
                    args[name] = seg.args.get("level") if seg.input == "y_hold" else option.fixed_args.get("level", "lv1")
                else:
                    args[name] = seg.args[name]
            call = toolset.validate_call(ToolCall.make(option.move, **args))
        labels.append(ToolCallLabel(seg.t_start, seg.t_end, call, seg.input, seg.finish,
                                    transition.to_dict(), seg.overlaps, option is None, seg.source))
    return labels
