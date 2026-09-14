#!/usr/bin/env python3
"""
Phase 0 check: send a small, safe, canned input to confirm the virtual
gamepad reaches MHW.

Run this with the game focused, ideally sitting somewhere harmless (main
menu / gathering hub) so a stray button press can't do anything costly.
Watch the screen during the countdown to see if the press visibly lands.
"""

from __future__ import annotations

import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from env.game_interface.input_injector import open_gamepad  # noqa: E402
from evdev import ecodes as e  # noqa: E402


def main():
    print("Make sure Monster Hunter World is focused and visible.")
    print("Sending a single South-button (A/Cross) tap in 3 seconds...")
    for n in (3, 2, 1):
        print(n)
        time.sleep(1)

    with open_gamepad() as pad:
        pad.tap(e.BTN_SOUTH, hold_seconds=0.15)

    print("Sent. Did you see it land in-game? If not, check the Steam Input")
    print("note in docs/modding_setup.md.")


if __name__ == "__main__":
    main()
