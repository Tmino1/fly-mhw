#!/usr/bin/env python3
"""
Human-in-the-loop check of the tool-based action space against the live
game — the v2 counterpart of verify_action_mapping.py (same style: hold
the pad open, send, ask a human what happened).

Graph moves can only be checked in context (a Strong Wide Slash only
exists after a Strong Charged Slash), so for each move this runs the
SHORTEST combo path from neutral that reaches it, back-to-back through
the executor (combo windows are real — no pauses mid-chain), then asks
whether that exact sequence played. --chain runs a sequence you name.

Run with MHW focused, weapon drawn, somewhere safe (training area).
Focus matters literally: MHW ignores injected input entirely when its
window is not focused (confirmed live 2026-10-02 — it cost an hour of
false negatives). The device is whatever the tools config's
input_backend selects; on this machine that is keyboard/mouse, since
the virtual gamepad never reaches the game (see docs/risks.md).

Usage:
    python scripts/verify_tools.py                          # every tool
    python scripts/verify_tools.py --only wide_slash,kick
    python scripts/verify_tools.py --chain charged_slash,strong_charged_slash,true_charged_slash
    python scripts/verify_tools.py --level lv3 --direction forward

This does NOT edit any YAML — flip `verified:` (tools config `gamepad`
block, moveset `edges`) and retune `timings` by hand afterwards.
"""

from __future__ import annotations

import argparse
import sys
import time
from collections import deque
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from env.game_interface.keyboard_mouse_injector import open_backend  # noqa: E402
from env.moveset_graph import MovesetTracker, Option  # noqa: E402
from env.tool_executor import ToolExecutor  # noqa: E402
from env.tools import ToolSet  # noqa: E402


def shortest_path(toolset: ToolSet, move: str) -> list[Option] | None:
    """BFS over combo roots (declared edges only, no re-roots) from the
    graph root to the first node whose move is `move`."""
    graph = toolset.graph
    queue = deque([(graph.root, [])])
    seen = {graph.root}
    while queue:
        node, path = queue.popleft()
        for opt in graph.options_at(node):
            if opt.rerooted:
                continue
            if opt.move == move:
                return path + [opt]
            nxt = graph.nodes[opt.to]
            nxt_root = nxt.returns_to or nxt.id
            if nxt_root not in seen:
                seen.add(nxt_root)
                queue.append((nxt_root, path + [opt]))
    return None


def call_for(toolset: ToolSet, option: Option, direction: str, level: str, duration: str):
    spec = toolset.spec(option.move)
    defaults = {"direction": direction, "level": level, "duration": duration}
    args = {name: option.fixed_args.get(name, defaults[name]) for name in spec.arg_names}
    return toolset.call(option.move, **args)


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--tools", default="configs/weapons/greatsword_tools.yaml")
    parser.add_argument("--only", default=None, help="comma-separated tool names (default: all)")
    parser.add_argument("--chain", default=None, help="comma-separated moves to run back-to-back from neutral")
    parser.add_argument("--direction", default="none")
    parser.add_argument("--level", default="lv1")
    parser.add_argument("--duration", default="short")
    parser.add_argument("--pause-before", type=float, default=3.0)
    parser.add_argument("--pause-after", type=float, default=2.0,
                         help="also the gap between tests — long enough for the combo window to expire")
    args = parser.parse_args()

    toolset = ToolSet.from_config(args.tools)
    tests: list[tuple[str, list]] = []  # (label, [(call, option|None)])

    if args.chain:
        tracker = MovesetTracker(toolset.graph)
        steps, t = [], 0.0
        for move in args.chain.split(","):
            opts = [o for o in tracker.options(t) if o.move == move]
            if not opts:
                raise SystemExit(f"{move!r} isn't reachable from {tracker.root_at(t)[0]!r} in the moveset graph")
            steps.append((call_for(toolset, opts[0], args.direction, args.level, args.duration), opts[0]))
            tracker.advance(opts[0], t)
            t += 0.01
        tests.append((" -> ".join(args.chain.split(",")), steps))
    else:
        names = args.only.split(",") if args.only else toolset.tool_names()
        for name in names:
            spec = toolset.spec(name)
            if spec.kind == "timed":
                defaults = {"direction": args.direction if args.direction != "none" else "forward",
                            "duration": args.duration, "yaw": "left", "amount": "large"}
                tests.append((name, [(toolset.call(name, **{a: defaults[a] for a in spec.arg_names}), None)]))
                continue
            path = shortest_path(toolset, name)
            if path is None:
                print(f"(skip {name}: no declared path from neutral)")
                continue
            tests.append((" -> ".join(o.move for o in path),
                          [(call_for(toolset, o, args.direction, args.level, args.duration), o) for o in path]))

    print(f"{len(tests)} test(s). Make sure Monster Hunter World is focused, weapon drawn.\n")
    results = {"confirmed": [], "wrong": [], "skipped": []}
    with open_backend(toolset) as pad:
        for label, steps in tests:
            print(f"--- {label} ---")
            for call, opt in steps:
                how = f"  [{opt.describe()}]" if opt else ""
                print(f"    {call}{how}")
            print(f"    sending in {args.pause_before:.0f}s...")
            time.sleep(args.pause_before)

            executor = ToolExecutor(pad, toolset, MovesetTracker(toolset.graph, time.monotonic()))
            for call, opt in steps:
                r = executor.run(call, opt)
                if r.invalid:
                    print(f"    !! {call} was masked at runtime (combo window expired?)")

            time.sleep(args.pause_after)
            answer = input("    did exactly that sequence play? [y/n/skip]: ").strip().lower()
            key = "confirmed" if answer.startswith("y") else "wrong" if answer.startswith("n") else "skipped"
            results[key].append(label)
            print()

    print("=== Summary ===")
    for key, items in results.items():
        print(f"{key}:")
        for item in items or ["(none)"]:
            print(f"  {item}")
    if results["wrong"]:
        print("\nFor wrong ones: check the input timings (configs/weapons/greatsword_tools.yaml) and the "
              "edge itself (greatsword_moveset.yaml) — record a demo of it and run "
              "scripts/audit_tool_labels.py to see which lmtIDs the game actually played.")


if __name__ == "__main__":
    main()
