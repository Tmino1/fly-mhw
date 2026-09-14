#!/usr/bin/env python3
"""
Phase 0 check: send input to confirm the virtual gamepad reaches MHW.

v2: holds the device open and keeps tapping for a sustained period,
instead of tapping once and immediately closing. v1 created the pad,
waited 3s, tapped once, and closed — all in under 4 seconds total. If
Wine/SDL's hotplug detection takes any time to notice a newly-connected
controller, the device could vanish again before the game ever registered
it existed (confirmed independently at the OS level: udev tags it
ID_INPUT_JOYSTICK=1 correctly and a legacy js0 node appears while it's
open, so the device itself isn't the problem — timing was the suspect).

Run this with MHW focused. While it's running, check Options → Controls
→ (a Gamepad/Controller tab should appear once a controller is detected)
to see whether the game notices the pad at all, independent of whether
"A" does anything meaningful in your current context.
"""

from __future__ import annotations

import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from env.game_interface.input_injector import open_gamepad  # noqa: E402
from evdev import ecodes as e  # noqa: E402

HOLD_OPEN_SECONDS = 30
TAP_INTERVAL_SECONDS = 2


def main():
    print("Make sure Monster Hunter World is focused and visible.")
    print(f"Opening the virtual pad and holding it open for {HOLD_OPEN_SECONDS}s,")
    print(f"tapping South (A/Cross) every {TAP_INTERVAL_SECONDS}s.")
    print("Check Options → Controls in-game for a Gamepad/Controller tab")
    print("appearing — that confirms the game sees the device at all,")
    print("separately from whether A does anything in your current context.")
    print("Ctrl+C to stop early.\n")

    with open_gamepad() as pad:
        start = time.monotonic()
        tap_num = 0
        try:
            while time.monotonic() - start < HOLD_OPEN_SECONDS:
                tap_num += 1
                elapsed = time.monotonic() - start
                print(f"[{elapsed:5.1f}s] tap #{tap_num}")
                pad.tap(e.BTN_SOUTH, hold_seconds=0.15)
                time.sleep(TAP_INTERVAL_SECONDS)
        except KeyboardInterrupt:
            print("\nStopped early.")

    print("\nDone. Did a Gamepad/Controller tab ever appear in Options →")
    print("Controls, or did any tap visibly land? If neither, the pad likely")
    print("isn't reaching Wine's input layer at all — see docs/risks.md.")


if __name__ == "__main__":
    main()
