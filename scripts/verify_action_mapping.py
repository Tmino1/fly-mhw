#!/usr/bin/env python3
"""
Phase 1 check: human-in-the-loop verification of a weapon config's
button/axis mapping against the live game — mirrors
verify_input_injection.py's style (hold the pad open, give real time for
each press to register, ask a human to confirm what happened).

Run with MHW focused, somewhere safe (main menu / gathering hub) so a
wrong guess can't do anything costly.

Usage:
    python scripts/verify_action_mapping.py --weapon configs/weapons/greatsword.yaml
    python scripts/verify_action_mapping.py --weapon configs/weapons/greatsword.yaml \
        --only attack_1,attack_2,dodge

This does NOT edit the YAML for you — corrections are made by hand
afterward (same manual-but-cheap spirit as Phase 0's diagnostics), by
changing the `code:`/`verified:` fields for whichever actions come back
wrong.
"""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from env.action_space import ActionSpace  # noqa: E402
from env.game_interface.input_injector import open_gamepad  # noqa: E402


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--weapon", required=True, help="path to configs/weapons/*.yaml")
    parser.add_argument("--only", default=None, help="comma-separated action names to test (default: all)")
    parser.add_argument("--pause-before", type=float, default=3.0)
    parser.add_argument("--pause-after", type=float, default=2.0)
    args = parser.parse_args()

    space = ActionSpace.from_config(args.weapon)

    names = args.only.split(",") if args.only else space.names()
    for name in names:
        if name not in space.names():
            raise SystemExit(f"no such action {name!r} in {args.weapon} — known: {space.names()}")

    print(f"Loaded {space.name}'s action space: {len(names)}/{space.n} action(s) to test.")
    print("Make sure Monster Hunter World is focused and visible.\n")

    results = {"confirmed": [], "wrong": [], "skipped": []}

    with open_gamepad() as pad:
        for name in names:
            action_def = space.actions[space.index_of(name)]
            print(f"--- {name} (currently verified={action_def.verified}) ---")
            if action_def.notes:
                print(f"    notes: {action_def.notes}")
            print(f"    sending in {args.pause_before:.0f}s...")
            time.sleep(args.pause_before)

            space.apply(pad, name)

            time.sleep(args.pause_after)
            answer = input(f"    did that do '{name}'? [y/n/skip]: ").strip().lower()
            if answer.startswith("y"):
                results["confirmed"].append(name)
            elif answer.startswith("n"):
                results["wrong"].append(name)
            else:
                results["skipped"].append(name)
            print()

    print("=== Summary ===")
    print(f"confirmed correct: {results['confirmed'] or '(none)'}")
    print(f"confirmed WRONG:   {results['wrong'] or '(none)'}")
    print(f"skipped:           {results['skipped'] or '(none)'}")
    if results["wrong"]:
        print(f"\nHand-edit {args.weapon} for the wrong action(s) above, then re-run to confirm.")


if __name__ == "__main__":
    main()
