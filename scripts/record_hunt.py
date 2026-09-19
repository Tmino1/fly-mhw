#!/usr/bin/env python3
"""
Phase 3: record human-played hunts as demo data for eventual imitation
learning. You play (keyboard + mouse); this only observes — captures
frames, reduces your currently-held input to a named action via a
calibrated bindings config, reads live game state, and writes both to
demos/storage/<episode_id>/.

Prerequisite: configs/keyboard_bindings.yaml must already exist — run
scripts/calibrate_keyboard_bindings.py first if it doesn't.

Runs as a SESSION, not a single hunt: launch this once, then just play
hunt after hunt normally — each quest-accept-to-quest-end is recorded as
its own episode automatically, no relaunching or timing coordination
needed between hunts. Ctrl+C ends the session cleanly (prints an
aggregate summary); it also ends on its own if nothing starts within
--reset-timeout of waiting (default 10 minutes — generous, since between
hunts you might restock/travel/chat, not just immediately requeue).

Usage:
    python scripts/record_hunt.py \
        --weapon configs/weapons/greatsword.yaml \
        --monster configs/monsters/great_jagras.yaml \
        --keyboard-bindings configs/keyboard_bindings.yaml \
        --state-path "$HOME/.local/share/Steam/steamapps/common/Monster Hunter World/fly_mhw_state.json" \
        [--max-episodes N]

Accept a quest in-game once this prints that it's waiting for one — it'll
go back to waiting automatically after each hunt ends.
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
    parser.add_argument("--reset-timeout", type=float, default=600.0,
                         help="how long to wait for the next quest to start before ending "
                              "the session (default 10 min — generous for time between hunts, "
                              "not just one hunt's worth of patience)")
    parser.add_argument("--max-episodes", type=int, default=None,
                         help="stop after recording this many hunts (default: unlimited, Ctrl+C to stop)")
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
        reset_timeout_seconds=args.reset_timeout,
    )

    print(f"Recording session started. Waiting up to {args.reset_timeout:.0f}s for each "
          "quest to start — accept one now in-game. Ctrl+C to end the session.\n")

    episode_summaries = []
    try:
        while args.max_episodes is None or len(episode_summaries) < args.max_episodes:
            summary = recorder.record_episode()
            episode_summaries.append(summary)

            print(f"\n=== Episode {len(episode_summaries)}: {summary.episode_id} ===")
            print(f"frames recorded:    {summary.frame_count}")
            print(f"duration:           {summary.duration_seconds:.1f}s")
            print(f"termination reason: {summary.termination_reason}")
            print(f"quest_id:           {summary.quest_id}")
            print(f"distinct quest_state_raw values observed: {summary.distinct_quest_states}")
            print(f"action histogram:   {summary.action_counts}")
            if summary.action_counts and set(summary.action_counts) <= {"idle"}:
                print("  -> WARNING: only 'idle' was ever recorded — the keyboard/mouse "
                      "reducer likely isn't picking up real input. Check --devices and "
                      "re-run scripts/calibrate_keyboard_bindings.py.")
            print(f"Stored under: {Path(args.output_dir) / summary.episode_id}")
            print("\nWaiting for the next quest... (accept in-game, or Ctrl+C to stop here)")
    except RuntimeError as exc:
        # wait_for_quest_start() timing out — treated as "no more hunts
        # coming", not a crash. If this is the first episode, that's a
        # real problem worth surfacing loudly; if hunts were already
        # recorded, it's just the natural end of a session.
        if not episode_summaries:
            raise SystemExit(str(exc))  # reducer.close() still runs, via the finally below
        print(f"\n(Session ended: {exc})")
    except KeyboardInterrupt:
        print("\n(Session stopped by Ctrl+C.)")
    finally:
        reducer.close()

    print("\n=== Session summary ===")
    print(f"episodes recorded: {len(episode_summaries)}")
    if episode_summaries:
        total_frames = sum(s.frame_count for s in episode_summaries)
        total_duration = sum(s.duration_seconds for s in episode_summaries)
        all_quest_states = sorted({qs for s in episode_summaries for qs in s.distinct_quest_states}, key=str)
        combined_actions: dict[str, int] = {}
        for s in episode_summaries:
            for name, count in s.action_counts.items():
                combined_actions[name] = combined_actions.get(name, 0) + count
        print(f"total frames:       {total_frames}")
        print(f"total duration:     {total_duration:.1f}s")
        print(f"distinct quest_state_raw values observed across the session: {all_quest_states}")
        print(f"combined action histogram: {combined_actions}")
        print(f"episode ids: {[s.episode_id for s in episode_summaries]}")


if __name__ == "__main__":
    main()
