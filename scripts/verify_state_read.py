#!/usr/bin/env python3
"""
Phase 0 check: poll lua_scripts/state_reader.lua's output and print it.

Usage:
    python scripts/verify_state_read.py [path/to/fly_mhw_state.json]

If no path is given, tries the MHW install directory first (the most
likely place a relative-path write from the game process lands), then
falls back to $HOME.
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Optional

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from env.game_interface.lua_bridge import LuaBridge, StateReadError  # noqa: E402

DEFAULT_CANDIDATES = [
    Path.home()
    / ".local/share/Steam/steamapps/common/Monster Hunter World/fly_mhw_state.json",
    Path.home() / "fly_mhw_state.json",
]


def find_state_file(explicit: Optional[str]) -> Path:
    if explicit:
        return Path(explicit)
    for candidate in DEFAULT_CANDIDATES:
        if candidate.exists():
            return candidate
    raise SystemExit(
        "Couldn't find fly_mhw_state.json in any default location:\n"
        + "\n".join(f"  {c}" for c in DEFAULT_CANDIDATES)
        + "\n\nPass the path explicitly, or check docs/modding_setup.md — "
        "has state_reader.lua actually been `reload`ed in-game yet?"
    )


def main():
    explicit = sys.argv[1] if len(sys.argv) > 1 else None
    state_path = find_state_file(explicit)
    print(f"Polling {state_path} ...")

    bridge = LuaBridge(state_path)
    try:
        for state in bridge.poll(interval_seconds=1.0):
            print(f"[{state.read_at:.1f}] age={state.file_age_seconds:.2f}s  {state.raw}")
    except StateReadError as exc:
        raise SystemExit(f"state read failed repeatedly: {exc}")
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
