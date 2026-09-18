#!/usr/bin/env python3
"""
Diagnostic: list every mapped Hyprland window's class/title/geometry.

Use this with MHW running to find its real `class`/`title` and fix
env/game_interface/capture.py's _MHW_CLASS_PATTERNS/_MHW_TITLE_PATTERNS
if find_window_geometry() isn't matching it (they were written without
the game running — best guesses, flagged unverified in that file).
"""

from __future__ import annotations

import json
import subprocess
import sys


def main():
    try:
        proc = subprocess.run(
            ["hyprctl", "clients", "-j"], capture_output=True, check=True, text=True
        )
    except FileNotFoundError:
        raise SystemExit("hyprctl not found — this script is Hyprland-specific.")
    except subprocess.CalledProcessError as exc:
        raise SystemExit(f"hyprctl exited {exc.returncode}: {exc.stderr}")

    clients = json.loads(proc.stdout)
    if not clients:
        print("No windows reported.")
        return

    for c in clients:
        if not c.get("mapped"):
            continue
        x, y = c["at"]
        w, h = c["size"]
        print(f"class={c['class']!r:30} title={c['title']!r:50} geometry={x},{y} {w}x{h}")


if __name__ == "__main__":
    sys.exit(main())
