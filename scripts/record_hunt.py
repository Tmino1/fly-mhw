#!/usr/bin/env python3
"""
Phase 3: record one human-played hunt as demo data for eventual imitation
learning. You play (keyboard + mouse); this only observes — captures
frames, reduces your currently-held input to a named action via a
calibrated bindings config, reads live game state, and writes both to
demos/storage/<episode_id>/.

Prerequisite: configs/keyboard_bindings.yaml must already exist — run
scripts/calibrate_keyboard_bindings.py first if it doesn't.

Usage:
    python scripts/record_hunt.py \
        --weapon configs/weapons/greatsword.yaml \
        --monster configs/monsters/great_jagras.yaml \
        --keyboard-bindings configs/keyboard_bindings.yaml \
        --state-path "$HOME/.local/share/Steam/steamapps/common/Monster Hunter World/fly_mhw_state.json"

Accept the quest in-game once this prints that it's waiting for one.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from demos.keyboard_bindings import KeyboardActionReducer, KeyboardBindings, find_input_devices  # noqa: E402
from demos.recorder import DemoRecorder  # noqa: E402
from env.action_space import ActionSpace  # noqa: E402
from env.game_interface.capture import find_window_geometry  # noqa: E402
from env.game_interface.lua_bridge import LuaBridge  # noqa: E402
from env.reward import RewardModel  # noqa: E402


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--weapon", required=True)
    parser.add_argument("--monster", required=True)
    parser.add_argument("--keyboard-bindings", required=True)
    parser.add_argument("--state-path", required=True)
    parser.add_argument("--output-dir", default="demos/storage")
    parser.add_argument("--step-period", type=float, default=0.2)
    parser.add_argument("--reset-timeout", type=float, default=120.0)
    parser.add_argument("--capture-geometry", default=None,
                         help="auto-detected via find_window_geometry() if omitted")
    parser.add_argument("--devices", default=None,
                         help="comma-separated device-name patterns; defaults to the bindings config's own device_name_patterns")
    args = parser.parse_args()

    action_space = ActionSpace.from_config(args.weapon)  # also prints any unverified-action warning
    reward_model = RewardModel.from_config(args.monster)
    bindings = KeyboardBindings.from_config(args.keyboard_bindings)

    if bindings.weapon_config != args.weapon:
        print(f"WARNING: {args.keyboard_bindings} was calibrated against "
              f"{bindings.weapon_config!r}, not {args.weapon!r} — bindings may not "
              "match this weapon's action set. Continuing anyway.")

    missing = [n for n in action_space.names() if n != "idle" and n not in bindings.actions()]
    if missing:
        raise SystemExit(
            f"{args.keyboard_bindings} is missing bindings for: {missing} — "
            f"re-run scripts/calibrate_keyboard_bindings.py --weapon {args.weapon}"
        )

    capture_geometry = args.capture_geometry
    if capture_geometry is None:
        capture_geometry = find_window_geometry()
        if capture_geometry is None:
            print("WARNING: could not auto-detect the MHW window (find_window_geometry() "
                  "returned None) — falling back to full-desktop capture. Run "
                  "scripts/list_windows.py to check the game is running and its class "
                  "matches env/game_interface/capture.py's _MHW_CLASS_PATTERNS.")

    device_patterns = (
        args.devices.split(",") if args.devices
        else bindings.device_name_patterns or ["keychron", "logitech"]
    )
    try:
        devices = find_input_devices(device_patterns)
    except PermissionError:
        raise SystemExit(
            "Permission denied opening the keyboard/mouse device — did you re-login "
            "after the nixos-rebuild switch that added the 'input' group? A rebuild "
            "alone isn't enough; group membership needs a fresh session. See "
            "docs/risks.md."
        )

    reducer = KeyboardActionReducer(bindings, devices)
    recorder = DemoRecorder(
        reward_model=reward_model,
        lua_bridge=LuaBridge(Path(args.state_path)),
        reducer=reducer,
        capture_geometry=capture_geometry,
        step_period_seconds=args.step_period,
        output_dir=args.output_dir,
    )

    print(f"Waiting up to {args.reset_timeout:.0f}s for a quest to start — accept one now in-game...")
    try:
        summary = recorder.record_episode()
    except RuntimeError as exc:
        raise SystemExit(str(exc))
    finally:
        reducer.close()

    print("\n=== Summary ===")
    print(f"episode_id:         {summary.episode_id}")
    print(f"frames recorded:    {summary.frame_count}")
    print(f"duration:           {summary.duration_seconds:.1f}s")
    print(f"termination reason: {summary.termination_reason}")
    print(f"quest_id:           {summary.quest_id}")
    print(f"distinct quest_state_raw values observed: {summary.distinct_quest_states}")
    print(f"action histogram:   {summary.action_counts}")
    if summary.action_counts and set(summary.action_counts) <= {"idle"}:
        print("  -> WARNING: only 'idle' was ever recorded — the keyboard/mouse reducer "
              "likely isn't picking up real input. Check --devices and re-run "
              "scripts/calibrate_keyboard_bindings.py.")
    print(f"\nStored under: {Path(args.output_dir) / summary.episode_id}")


if __name__ == "__main__":
    main()
