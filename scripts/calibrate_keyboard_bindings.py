#!/usr/bin/env python3
"""
Interactive: watches your real keyboard/mouse and asks you to press the
key/button you actually use in-game for each input ROLE (v2: y, b, a, x,
rt, rb, lt and the four movement directions), then measures your mouse's
camera-pan counts, and writes configs/keyboard_bindings.yaml. Never
guesses a binding — MHW's own config.ini has no keyboard-binding section,
so there's nothing to read these from.

Opens the matched device(s) WITHOUT calling .grab() — a passive listener,
confirmed live not to interfere with normal typing/game input (the kernel
fans events out to every open reader).

Usage:
    python scripts/calibrate_keyboard_bindings.py
    python scripts/calibrate_keyboard_bindings.py --devices keychron,logitech \
        --output configs/keyboard_bindings.yaml --skip-mouse

If --devices doesn't match anything, every available device's path/name
is printed so you can pick better patterns (same fallback list_windows.py
uses for Hyprland windows).
"""

from __future__ import annotations

import argparse
import select
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from evdev import ecodes  # noqa: E402

from demos.keyboard_bindings import ROLES, code_name, find_input_devices  # noqa: E402
from env.tools import ToolSet  # noqa: E402

# What each role does in-game, so the prompt makes sense to a human.
# Gamepad letters are Xbox; the chart's Mouse & Keyboard defaults are
# only hints — press whatever YOU actually use.
ROLE_HINTS = {
    "move_forward": "move forward",
    "move_back": "move back",
    "move_left": "move left",
    "move_right": "move right",
    "y": "Attack 1 / Y — overhead / charge (chart default: LMB)",
    "b": "Attack 2 / B — wide slash / tackle (chart default: RMB)",
    "a": "Dodge / A (chart default: Space)",
    "x": "Sheathe / X (chart default: E)",
    "rt": "Guard / RT — guard, kick, RT side blows",
    "rb": "Sprint / RB — not a tool, bound only to flag it in demos",
    "lt": "Slinger / LT — not a tool, bound only to flag it in demos",
}
OPTIONAL_ROLES = {"rt", "rb", "lt"}


def drain_events(devices) -> None:
    """Discard any events already buffered on devices, non-blockingly.

    Real bug found live (2026-09-19): the listener is deliberately
    non-grabbing (see module docstring), so every terminal keystroke used
    to CONFIRM a binding — pressing Enter, or 'r' to retry — also lands as
    a raw event on the keyboard device we're watching. Left undrained,
    that leftover event was picked up by the *next* wait_for_keypress()
    call before the user could press their real key/button, silently
    capturing e.g. KEY_ENTER instead of an actual mouse click. Call this
    right before waiting for each new role.
    """
    while True:
        ready, _, _ = select.select(devices, [], [], 0)
        if not ready:
            return
        for dev in ready:
            for _ in dev.read():
                pass


def wait_for_keypress(devices, prompt: str) -> int:
    """Block until a key/button DOWN event arrives on any of devices,
    return its evdev code. Uses select() to multiplex the real file
    descriptors rather than polling — a transient press can't be missed
    between polls."""
    print(prompt)
    while True:
        ready, _, _ = select.select(devices, [], [])
        for dev in ready:
            for event in dev.read():
                if event.type == ecodes.EV_KEY and event.value == 1:  # down, not up/repeat
                    return event.code


def max_window_dx(devices, seconds: float, window: float) -> int:
    """Listen for `seconds`; return the largest |summed REL_X| over any
    `window`-long bucket — the same quantity demos/tool_segmenter.py
    thresholds on."""
    sums: dict[int, int] = {}
    t0 = time.time()
    while time.time() - t0 < seconds:
        ready, _, _ = select.select(devices, [], [], 0.05)
        for dev in ready:
            for ev in dev.read():
                if ev.type == ecodes.EV_REL and ev.code == ecodes.REL_X:
                    k = int((ev.timestamp() - t0) // window)
                    sums[k] = sums.get(k, 0) + ev.value
    return max((abs(v) for v in sums.values()), default=0)


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--tools", default="configs/weapons/greatsword_tools.yaml",
                         help="tools config — only used for its segmenter.camera_window_seconds")
    parser.add_argument("--devices", default="keychron,logitech",
                         help="comma-separated case-insensitive substrings to match device names "
                              "(default matches this machine's keyboard and mouse — confirmed live "
                              "2026-09-19: the real mouse reports as 'Logitech PRO X', which contains "
                              "neither 'keychron' nor 'mouse' in its name, so a naive 'mouse' pattern "
                              "would silently miss it entirely)")
    parser.add_argument("--output", default="configs/keyboard_bindings.yaml")
    parser.add_argument("--skip-mouse", action="store_true", help="skip the camera-pan measurement")
    args = parser.parse_args()

    window = ToolSet.from_config(args.tools).segmenter["camera_window_seconds"]
    devices = find_input_devices(args.devices.split(","))
    print(f"Listening on: {[d.name for d in devices]}\n")
    print("For each role, press the key/button you use in-game. Optional roles "
          f"({', '.join(sorted(OPTIONAL_ROLES))}) can be skipped by typing 's'.\n")

    roles: dict[str, str | None] = {}
    mouse = {"counts_small": 60, "counts_large": 400, "calibrated": False}
    try:
        for role in ROLES:
            while True:
                drain_events(devices)  # discard any stale event from the previous confirm/retry prompt
                if role in OPTIONAL_ROLES:
                    skip = input(f"--- {role}: {ROLE_HINTS[role]} — [Enter] to bind, 's' to skip: ").strip().lower()
                    if skip == "s":
                        roles[role] = None
                        print()
                        break
                    drain_events(devices)
                code = wait_for_keypress(devices, f"--- Press your key/button for {role!r} ({ROLE_HINTS[role]}) now ---")
                key_name = code_name(code)
                answer = input(
                    f"    captured {key_name} for {role!r} — [Enter] to accept, or type 'r' to retry: "
                ).strip().lower()
                if answer == "r":
                    continue
                roles[role] = key_name
                print()
                break

        if not args.skip_mouse:
            print("--- Mouse camera calibration (in-game, weapon drawn is fine) ---")
            input("    [Enter], then make SMALL camera nudges for 4 seconds... ")
            drain_events(devices)  # drop mouse motion buffered while you read the prompt
            small = max_window_dx(devices, 4.0, window)
            input(f"    peak {small} counts/{window}s. [Enter], then make BIG fast camera sweeps for 4 seconds... ")
            drain_events(devices)
            large = max_window_dx(devices, 4.0, window)
            print(f"    peak {large} counts/{window}s.")
            if small and large > small:
                mouse = {"counts_small": max(1, small // 2), "counts_large": (small + large) // 2,
                         "calibrated": True}
            else:
                print("    Couldn't separate small from large — keeping the placeholder thresholds.")
    finally:
        for dev in devices:
            dev.close()

    bound = [k for k in roles.values() if k]
    if len(bound) != len(set(bound)):
        raise SystemExit("The same key was captured for two roles — re-run and pick distinct keys.")

    out_path = Path(args.output)
    lines = [
        'schema: "fly-mhw/keyboard_bindings/v2"',
        f'tools_config: "{args.tools}"',
        f'device_name_patterns: {args.devices.split(",")!r}',
        f'calibrated_at: "{datetime.now(timezone.utc).isoformat()}"',
        'notes: >',
        '  Captured live by scripts/calibrate_keyboard_bindings.py. Roles are the',
        '  gamepad button vocabulary; demos/tool_segmenter.py turns timestamped',
        '  presses of these keys into tool calls. null = not bound.',
        'roles:',
    ]
    for role in ROLES:
        lines.append(f"  {role}: {roles.get(role) or 'null'}")
    lines.append("mouse:")
    for k, v in mouse.items():
        lines.append(f"  {k}: {str(v).lower() if isinstance(v, bool) else v}")
    out_path.write_text("\n".join(lines) + "\n")

    print(f"Wrote {out_path}.")
    print("Sanity-check it loads:")
    print(f"  python -c \"from demos.keyboard_bindings import KeyboardBindings; "
          f"print(KeyboardBindings.from_config('{out_path}').roles)\"")


if __name__ == "__main__":
    main()
