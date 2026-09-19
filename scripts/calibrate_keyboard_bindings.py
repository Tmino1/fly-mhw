#!/usr/bin/env python3
"""
Interactive: watches your real keyboard/mouse and asks you to press the
key/button you actually use in-game for each action, then writes
configs/keyboard_bindings.yaml. Never guesses a binding — MHW's own
config.ini has no keyboard-binding section, so there's nothing to read
these from.

Opens the matched device(s) WITHOUT calling .grab() — a passive listener,
confirmed live not to interfere with normal typing/game input (the kernel
fans events out to every open reader).

Usage:
    python scripts/calibrate_keyboard_bindings.py --weapon configs/weapons/greatsword.yaml
    python scripts/calibrate_keyboard_bindings.py --weapon configs/weapons/greatsword.yaml \
        --devices keychron,mouse --output configs/keyboard_bindings.yaml

If --devices doesn't match anything, every available device's path/name
is printed so you can pick better patterns (same fallback list_windows.py
uses for Hyprland windows).
"""

from __future__ import annotations

import argparse
import select
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from evdev import ecodes  # noqa: E402

from demos.keyboard_bindings import code_name, find_input_devices  # noqa: E402
from env.action_space import ActionSpace  # noqa: E402


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


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--weapon", required=True, help="path to configs/weapons/*.yaml")
    parser.add_argument("--devices", default="keychron,mouse",
                         help="comma-separated case-insensitive substrings to match device names")
    parser.add_argument("--output", default="configs/keyboard_bindings.yaml")
    args = parser.parse_args()

    space = ActionSpace.from_config(args.weapon)
    action_names = [n for n in space.names() if n != "idle"]

    devices = find_input_devices(args.devices.split(","))
    print(f"Listening on: {[d.name for d in devices]}\n")
    print("idle is never calibrated — it's the automatic fallback when nothing else is held.\n")

    bindings: list[tuple[str, str]] = []
    try:
        for action_name in action_names:
            while True:
                code = wait_for_keypress(
                    devices, f"--- Press your key/button for {action_name!r} now ---"
                )
                key_name = code_name(code)
                answer = input(
                    f"    captured {key_name} for {action_name!r} — "
                    "[Enter] to accept, or press a different key to overwrite, "
                    "or type 'r' to retry: "
                ).strip().lower()
                if answer == "r":
                    continue
                bindings.append((action_name, key_name))
                print()
                break
    finally:
        for dev in devices:
            dev.close()

    out_path = Path(args.output)
    lines = [
        f'schema: "fly-mhw/keyboard_bindings/v1"',
        f'weapon_config: "{args.weapon}"',
        f'device_name_patterns: {args.devices.split(",")!r}',
        f'calibrated_at: "{datetime.now(timezone.utc).isoformat()}"',
        'notes: >',
        '  idle is intentionally not bound -- it is the reducer\'s fallback when',
        '  no other listed action\'s key/button is currently held. See',
        '  demos/keyboard_bindings.py\'s ACTION_PRIORITY for the multi-key',
        '  reduction rule used when more than one bound key is held at once.',
        'bindings:',
    ]
    for action_name, key_name in bindings:
        lines.append(f'  - action: {action_name}')
        lines.append(f'    key: {key_name}')
    out_path.write_text("\n".join(lines) + "\n")

    print(f"Wrote {out_path} with {len(bindings)} binding(s).")
    print("Sanity-check it loads:")
    print(f"  python -c \"from demos.keyboard_bindings import KeyboardBindings; "
          f"print(KeyboardBindings.from_config('{out_path}').actions())\"")


if __name__ == "__main__":
    main()
