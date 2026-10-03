#!/usr/bin/env python3
"""
Drive every move in the moveset graph against the live game and record
which animation each one actually produced — the automated replacement
for verify_tools.py's "did that sequence play? [y/n]" prompt.

Why this can be automated at all: the game tells us. state_reader.lua
logs every change of the player's animation id (lmtID) per tick, and
those ids turned out to be precise enough to distinguish charge levels
(the Charged Slash's lv1/lv2/lv3 are consecutive ids 49304/49305/49306).
So instead of a human watching twelve moves go by, each move's identity
and length are read straight out of the game.

That matters beyond saving the prompts: `lmt_ids` in the moveset YAML is
what lets demo labelling READ a charge level instead of inferring it from
how long a key was held — which is frame-rate dependent, and outright
wrong when the Great Sword auto-releases a full charge. Only four moves
have measured ids today, so every other move still falls back to that
guess. This fills the rest in.

Run with MHW focused, weapon drawn, somewhere safe, and HANDS OFF the
keyboard — MHW ignores injected input entirely while unfocused, and
typing steals focus, so the probe checks focus before every move and
aborts rather than recording garbage. Whiff, don't hit anything: hitstop
stretches animation timings.

Usage:
    python scripts/probe_moveset.py
    python scripts/probe_moveset.py --only charged_slash,wide_slash
    python scripts/probe_moveset.py --levels           # also sweep charge levels
    python scripts/probe_moveset.py --write-yaml out.yaml

Nothing here edits the moveset graph. It prints a report and, with
--write-yaml, a patch you apply by hand after reading it — an id is only
worth trusting once a human has sanity-checked it, a lesson from
recording the Side Blow's id as the Strong Charged Slash's.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from env.game_interface.action_log import ActionLog  # noqa: E402
from env.game_interface.keyboard_mouse_injector import open_backend  # noqa: E402
from env.move_probe import (  # noqa: E402
    DRAWN_IDLE_IDS, IDLE_IDS, SHEATHED_IDLE_IDS, ProbeResult, attribute, conflicts,
)
from env.moveset_graph import MovesetTracker  # noqa: E402
from env.tool_executor import ToolExecutor  # noqa: E402
from env.tools import ToolSet  # noqa: E402

_DEFAULT_STATE = (
    Path.home() / ".local/share/Steam/steamapps/common/Monster Hunter World/fly_mhw_state.json"
)


def mhw_focused() -> bool:
    try:
        out = subprocess.run(["hyprctl", "-j", "activewindow"], capture_output=True,
                             text=True, timeout=3).stdout
        return "MONSTER HUNTER" in (json.loads(out).get("title") or "")
    except Exception:
        return False


def focus_mhw() -> bool:
    try:
        out = subprocess.run(["hyprctl", "-j", "clients"], capture_output=True,
                             text=True, timeout=3).stdout
        for c in json.loads(out):
            if c.get("class") == "steam_app_582010" and "MONSTER HUNTER" in (c.get("title") or ""):
                subprocess.run(["hyprctl", "dispatch", "focuswindow", f"address:{c['address']}"],
                               capture_output=True, timeout=3)
                time.sleep(1.5)
                return mhw_focused()
    except Exception:
        pass
    return False


def _ready_to_probe(log: ActionLog, ex: ToolExecutor, ts: ToolSet, current_id, settle: float) -> bool:
    """Get the character to a state an attack probe can actually start
    from: animation finished, weapon drawn, and no combo still live.

    All three are separate conditions, and treating "animation finished"
    as sufficient produced two different wrong results live:
      - after probing `sheathe` the character is idle but SHEATHED, so
        the next probe's first input is a draw attack rather than the
        move under test;
      - the combo window outlives the animation, so an input sent at
        animation-end can still resolve against the PREVIOUS move's root
        and perform something else. rising_slash probed as two different
        ids depending on what ran before it.
    """
    if not log.wait_for_idle(IDLE_IDS, current_id, timeout=settle + 8.0):
        return False

    if current_id() in SHEATHED_IDLE_IDS:
        # Draw by attacking, then let that attack finish.
        ex.pad.press(ex.buttons["Y"])
        time.sleep(ex.timings["tap_seconds"])
        ex.pad.release(ex.buttons["Y"])
        if not log.wait_for_idle(DRAWN_IDLE_IDS, current_id, timeout=12.0):
            return False

    # Outlast the longest combo window in the graph so the game is at
    # neutral, not merely standing still.
    longest = max((n.duration_s + n.combo_window_s) for n in ts.graph.nodes.values())
    time.sleep(min(longest, 4.0))
    return current_id() in DRAWN_IDLE_IDS


def probe_move(ex: ToolExecutor, ts: ToolSet, log: ActionLog, move: str,
               level: str, direction: str, settle: float, current_id) -> ProbeResult:
    path = ts.graph.shortest_path_to(move)
    if path is None:
        return ProbeResult(move, [], None, fired=False, note="no declared path from neutral")

    # Wait for the character to ACTUALLY be idle before claiming the
    # tracker is at neutral. A fixed sleep isn't equivalent: if the
    # previous move's combo root is still live, these inputs resolve from
    # the wrong root and perform a different move than the one being
    # probed (seen live — the Side Blow was recorded with the Charged
    # Slash's id that way). `settle` is only the fallback cap.
    if not _ready_to_probe(log, ex, ts, current_id, settle):
        # Do NOT just skip this move. If the character is stuck in some
        # state, every later probe inherits it and silently measures the
        # wrong thing — observed live: a run that continued past one such
        # failure reported guard as 49263 and kick as 49299, when guard
        # had already been measured twice as 49159. Results shifted by a
        # whole move. Unknown state poisons the rest of the run, so stop.
        return ProbeResult(move, [], None, fired=False, note="NOT-IDLE-ABORT")

    ex.tracker = MovesetTracker(ts.graph, time.monotonic())
    log.clear()

    t_commit = None
    for opt in path:
        spec = ts.spec(opt.move)
        args = {}
        for name in spec.arg_names:
            if name == "level":
                args[name] = opt.fixed_args.get("level", level)
            elif name == "direction":
                # A move that needs a direction held (the SCS/TCS need
                # forward, or the game gives a Side Blow) is masked
                # without one, so honour what the edge requires.
                req = opt.requires_direction
                args[name] = req[0] if req else direction
            else:
                args[name] = spec.arg_values(name)[0]
        r = ex.run(ts.call(opt.move, **args))
        if r.invalid:
            return ProbeResult(move, [o.move for o in path], None, fired=False,
                               note=f"masked at runtime on {opt.move!r}")
        t_commit = r.t_start

    # Wait for the move to finish, not merely for the log to go quiet:
    # ids are logged on CHANGE, so a 3s swing is silent throughout and
    # wait_for_quiet would return mid-animation with no end timestamp to
    # measure the duration from.
    log.wait_for_idle(IDLE_IDS, current_id, timeout=12.0)
    events = [(e.t, e.lmt_id) for e in log.events if e.lmt_id is not None]
    lmt_id, duration, sequence = attribute(events, t_commit or 0.0)
    return ProbeResult(move, [o.move for o in path], lmt_id, sequence, duration,
                       fired=lmt_id is not None,
                       note="" if lmt_id is not None else "no animation logged")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--tools", default="configs/weapons/greatsword_tools.yaml")
    ap.add_argument("--state-path", default=str(_DEFAULT_STATE),
                    help="only used to locate the game directory the Lua log is written into")
    ap.add_argument("--only", default=None, help="comma-separated move names")
    ap.add_argument("--levels", action="store_true",
                    help="probe every charge level of each level-bearing move, not just --level")
    ap.add_argument("--level", default="lv1")
    ap.add_argument("--direction", default="none")
    ap.add_argument("--settle", type=float, default=3.5,
                    help="idle time before each probe, so it can't measure the previous move")
    ap.add_argument("--write-yaml", default=None)
    args = ap.parse_args()

    ts = ToolSet.from_config(args.tools)
    game_dir = Path(args.state_path).parent
    state_file = Path(args.state_path)

    def current_id():
        """The player's current animation id, from the 1 Hz snapshot.
        Coarse, but it only has to answer "idle or not"."""
        return json.loads(state_file.read_text())["player"]["action"]["lmt_id"]

    names = args.only.split(",") if args.only else sorted(
        {n.move for n in ts.graph.nodes.values()
         if n.move and n.reachable and ts.has_tool(n.move)}
    )
    jobs: list[tuple[str, str]] = []
    for name in names:
        spec = ts.spec(name)
        if args.levels and "level" in spec.arg_names:
            jobs += [(name, lv) for lv in spec.arg_values("level")]
        else:
            jobs.append((name, args.level))

    if not focus_mhw():
        raise SystemExit("Monster Hunter World isn't focused — it ignores injected input when it "
                         "isn't, so this would record nothing but empty rows.")
    print(f"probing {len(jobs)} move/level combination(s). HANDS OFF THE KEYBOARD.\n", flush=True)

    results: list[ProbeResult] = []
    with ActionLog(game_dir) as log, open_backend(ts) as pad:
        ex = ToolExecutor(pad, ts, MovesetTracker(ts.graph, time.monotonic()))
        for move, level in jobs:
            if not mhw_focused():
                print("\n!! MHW lost focus — stopping. Results so far are still valid.", flush=True)
                break
            r = probe_move(ex, ts, log, move, level, args.direction, args.settle, current_id)
            if r.note == "NOT-IDLE-ABORT":
                print(f"\n!! {move}: character never returned to idle — aborting the run.",
                      flush=True)
                print("   Anything measured after an unknown state is untrustworthy.",
                      flush=True)
                results.append(r)
                break
            label = f"{move}({level})" if len(jobs) > len(names) else move
            dur = f"{r.full_animation_s:5.2f}s" if r.full_animation_s else "    ?"
            print(f"  {label:34s} lmt={str(r.lmt_id):>8}  {dur}  "
                  f"{'via ' + ' -> '.join(r.path) if len(r.path) > 1 else ''}"
                  f"{'  [' + r.note + ']' if r.note else ''}", flush=True)
            results.append(r)

    print("\n=== summary ===")
    # A result that disagrees with an id already in the moveset YAML is a
    # loud signal: either this run was polluted, or the recorded id was
    # wrong. Both have happened, so never silently prefer the new value.
    known = {}
    for node in ts.graph.nodes.values():
        for lv, lmt in (node.lmt_ids or {}).items():
            if node.move:
                known.setdefault(node.move, set()).add(int(lmt))
    contradictions = [r for r in results
                      if r.lmt_id is not None and r.move in known
                      and r.lmt_id not in known[r.move]]
    if contradictions:
        print("\nCONTRADICTS the ids already in the moveset YAML — do not copy these in")
        print("without working out which run was wrong:")
        for r in contradictions:
            print(f"  {r.move:28s} probed {r.lmt_id}, recorded {sorted(known[r.move])}")

    fired = [r for r in results if r.fired]
    print(f"{len(fired)}/{len(results)} produced an animation")
    missing = [r for r in results if not r.fired]
    if missing:
        print("\ndid NOT fire (graph edge wrong, timing off, or genuinely unreachable):")
        for r in missing:
            print(f"  {r.move:30s} {r.note}")
    dupes = conflicts(fired)
    if dupes:
        print("\nSAME animation id claimed by several moves — either the graph has a duplicate")
        print("(this is how the phantom 'Overhead Smash' was caught) or a probe silently failed:")
        for lmt, moves in sorted(dupes.items()):
            print(f"  {lmt}: {', '.join(moves)}")

    if args.write_yaml:
        Path(args.write_yaml).write_text(json.dumps([r.to_dict() for r in results], indent=2))
        print(f"\nwrote {args.write_yaml} — review before copying ids into the moveset YAML")


if __name__ == "__main__":
    main()
