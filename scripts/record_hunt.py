#!/usr/bin/env python3
"""
Phase 3: record human-played hunts as demo data for eventual imitation
learning, in the v2 tool-call format. You play (keyboard + mouse); this
only observes — captures frames, records every key/mouse event with its
timestamp, reads live game state and the game's own animation ids, and
writes it all to demos/storage/<episode_id>/. At the end of each hunt the
events are labelled as move-named tool calls against the moveset graph
(see demos/recorder.py).

Prerequisites: configs/keyboard_bindings.yaml (v2, roles) must exist —
run scripts/calibrate_keyboard_bindings.py first if it doesn't — and the
in-game state_reader.lua must be the current one (it writes the lmtID
action log this uses — see docs/modding_setup.md).

Runs as a SESSION, not a single hunt: launch this once, then just play
hunt after hunt normally — each quest-accept-to-quest-end is recorded as
its own episode automatically, no relaunching or timing coordination
needed between hunts. Ctrl+C ends the session cleanly (prints an
aggregate summary); it also ends on its own if nothing starts within
--reset-timeout of waiting (default 10 minutes — generous, since between
hunts you might restock/travel/chat, not just immediately requeue).

Usage (all args default to this project's one pilot pair + this
machine's MHW install — see --help for overrides):
    nix run .#record-hunt
    python scripts/record_hunt.py [--max-episodes N]

Accept a quest in-game once this prints that it's waiting for one — it'll
go back to waiting automatically after each hunt ends.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from demos.input_events import InputEventStream  # noqa: E402
from demos.keyboard_bindings import KeyboardBindings, find_input_devices  # noqa: E402
from demos.recorder import DemoRecorder  # noqa: E402
from env.game_interface.capture import find_window_geometry  # noqa: E402
from env.game_interface.lua_bridge import LuaBridge  # noqa: E402
from env.reward import RewardModel  # noqa: E402
from env.tools import ToolSet  # noqa: E402

# Must match lua_scripts/state_reader.lua's SKIP_QUEST_END_FLAG_PATH.
# That script resolves this relative filename against the game's own
# working directory, same as its OUTPUT_PATH (fly_mhw_state.json) — so
# this lives right next to --state-path, not wherever this script runs
# from.
_SKIP_FLAG_FILENAME = "fly_mhw_skip_quest_end.flag"

# Same pattern: lua_scripts/state_reader.lua appends one JSON line per
# player lmtID change to _ACTION_LOG_FILENAME, but only while
# _ACTION_LOG_FLAG_FILENAME exists — created for the length of a
# recording session, removed after, so untracked play writes nothing.
_ACTION_LOG_FLAG_FILENAME = "fly_mhw_action_log.flag"
_ACTION_LOG_FILENAME = "fly_mhw_actions.jsonl"

# This project's one pilot pair (docs/architecture.md: "Target pair: Great
# Jagras, with the Great Sword") and this machine's one fixed MHW install
# — defaulted so `nix run .#record-hunt` works with zero arguments, since
# every session so far has used exactly these. Still fully overridable via
# CLI flags (e.g. for a second weapon/monster pair later, or a different
# machine's Steam library path).
_DEFAULT_TOOLS = "configs/weapons/greatsword_tools.yaml"
_DEFAULT_MONSTER = "configs/monsters/great_jagras.yaml"
_DEFAULT_KEYBOARD_BINDINGS = "configs/keyboard_bindings.yaml"
_DEFAULT_STATE_PATH = str(
    Path.home() / ".local/share/Steam/steamapps/common/Monster Hunter World/fly_mhw_state.json"
)


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--tools", default=_DEFAULT_TOOLS,
                         help="tools config (configs/weapons/*_tools.yaml); its moveset graph is loaded from it")
    parser.add_argument("--monster", default=_DEFAULT_MONSTER)
    parser.add_argument("--keyboard-bindings", default=_DEFAULT_KEYBOARD_BINDINGS)
    parser.add_argument("--state-path", default=_DEFAULT_STATE_PATH)
    parser.add_argument("--output-dir", default="demos/storage")
    parser.add_argument("--step-period", type=float, default=0.2)
    parser.add_argument("--reset-timeout", type=float, default=600.0,
                         help="how long to wait for the next quest to start before ending "
                              "the session (default 10 min — generous for time between hunts, "
                              "not just one hunt's worth of patience)")
    parser.add_argument("--max-episodes", type=int, default=None,
                         help="stop after recording this many hunts (default: unlimited, Ctrl+C to stop)")
    parser.add_argument("--max-state-age", type=float, default=20.0,
                         help="how stale (seconds) the game-state file can get before a read counts "
                              "as an error and ends the episode — see LuaBridge's docstring for why "
                              "this isn't tiny (loading-screen pauses, not just real crashes)")
    parser.add_argument("--capture-geometry", default=None,
                         help="auto-detected via find_window_geometry() if omitted")
    parser.add_argument("--devices", default=None,
                         help="comma-separated device-name patterns; defaults to the bindings config's own device_name_patterns")
    parser.add_argument("--no-skip-post-hunt-wait", action="store_true",
                         help="disable the automatic skip of the real post-hunt 'return to camp' "
                              "wait screen (quest.state=3). When enabled (the default), creates a "
                              "flag file next to --state-path that lua_scripts/state_reader.lua "
                              "watches for and acts on with a direct memory write, replicating "
                              "SharpPluginLoader's own 'Quest End Skip' example plugin's technique "
                              "— see demos/recorder.py's module docstring")
    args = parser.parse_args()

    toolset = ToolSet.from_config(args.tools)
    reward_model = RewardModel.from_config(args.monster)
    bindings = KeyboardBindings.from_config(args.keyboard_bindings)

    if bindings.tools_config != args.tools:
        print(f"WARNING: {args.keyboard_bindings} was calibrated against "
              f"{bindings.tools_config!r}, not {args.tools!r}. Continuing anyway.")
    if bindings.unbound_roles():
        print(f"NOTE: unbound input roles {bindings.unbound_roles()} — presses of those keys "
              "can't be recorded (rt: guard/kick/RT side blows; rb/lt: sprint/slinger flagging). "
              "Run scripts/calibrate_keyboard_bindings.py to bind them.")
    if not bindings.mouse.get("calibrated"):
        print("NOTE: mouse camera thresholds are uncalibrated placeholders — camera labels "
              "may be over/under-counted. Re-labelling later is cheap: scripts/relabel_demos.py.")

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

    state_dir = Path(args.state_path).parent
    action_log_flag = state_dir / _ACTION_LOG_FLAG_FILENAME
    action_log = state_dir / _ACTION_LOG_FILENAME
    action_log.unlink(missing_ok=True)  # start the session's lmtID log fresh
    action_log_flag.touch(exist_ok=True)
    events = InputEventStream(devices, bindings, action_log_path=action_log).start()

    skip_flag_path = (
        None if args.no_skip_post_hunt_wait
        else state_dir / _SKIP_FLAG_FILENAME
    )
    recorder = DemoRecorder(
        reward_model=reward_model,
        lua_bridge=LuaBridge(Path(args.state_path), max_age_seconds=args.max_state_age),
        events=events,
        toolset=toolset,
        tools_config_path=args.tools,
        bindings_path=args.keyboard_bindings,
        mouse_cfg=bindings.mouse,
        capture_geometry=capture_geometry,
        step_period_seconds=args.step_period,
        output_dir=args.output_dir,
        reset_timeout_seconds=args.reset_timeout,
        skip_flag_path=skip_flag_path,
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
            labels = summary.labels
            print(f"tool calls:         {labels.get('tool_calls')} "
                  f"(unresolved {labels.get('unresolved')}, camera overlaps {labels.get('overlaps')})")
            print(f"tool histogram:     {labels.get('tool_counts')}")
            print(f"combo depth:        {labels.get('depth_counts')}")
            print(f"lmtID changes seen: {labels.get('lmt_events')}")
            if labels.get("tool_counts") and set(labels["tool_counts"]) <= {"wait"}:
                print("  -> WARNING: only 'wait' was ever labelled — the input event stream "
                      "likely isn't picking up real input. Check --devices and "
                      "re-run scripts/calibrate_keyboard_bindings.py.")
            if not labels.get("lmt_events"):
                print("  -> NOTE: no lmtID changes were logged — is the in-game "
                      "state_reader.lua the current version? (docs/modding_setup.md)")
            print(f"Stored under: {Path(args.output_dir) / summary.episode_id}")
            print("\nWaiting for the next quest... (accept in-game, or Ctrl+C to stop here)")
    except RuntimeError as exc:
        # wait_for_quest_start() timing out — treated as "no more hunts
        # coming", not a crash. If this is the first episode, that's a
        # real problem worth surfacing loudly; if hunts were already
        # recorded, it's just the natural end of a session.
        if not episode_summaries:
            raise SystemExit(str(exc))  # events.close() still runs, via the finally below
        print(f"\n(Session ended: {exc})")
    except KeyboardInterrupt:
        print("\n(Session stopped by Ctrl+C.)")
    finally:
        events.close()
        action_log_flag.unlink(missing_ok=True)
        # Don't leave the flag file behind — a stale one would make
        # state_reader.lua keep auto-skipping during untracked play after
        # this session ends, which is exactly what it's not supposed to do.
        if skip_flag_path is not None:
            skip_flag_path.unlink(missing_ok=True)

    print("\n=== Session summary ===")
    print(f"episodes recorded: {len(episode_summaries)}")
    if episode_summaries:
        total_frames = sum(s.frame_count for s in episode_summaries)
        total_duration = sum(s.duration_seconds for s in episode_summaries)
        all_quest_states = sorted({qs for s in episode_summaries for qs in s.distinct_quest_states}, key=str)
        combined_tools: dict[str, int] = {}
        for s in episode_summaries:
            for name, count in s.labels.get("tool_counts", {}).items():
                combined_tools[name] = combined_tools.get(name, 0) + count
        print(f"total frames:       {total_frames}")
        print(f"total duration:     {total_duration:.1f}s")
        print(f"distinct quest_state_raw values observed across the session: {all_quest_states}")
        print(f"combined tool histogram: {combined_tools}")
        print(f"episode ids: {[s.episode_id for s in episode_summaries]}")


if __name__ == "__main__":
    main()
