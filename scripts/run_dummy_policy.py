#!/usr/bin/env python3
"""
Phase 1 acceptance test: run an idle-then-random dummy policy through
MHWEnv against a live quest, logging every step for review. Actions are
tool calls (configs/weapons/*_tools.yaml): idle = wait(short); random =
uniform over the calls the moveset graph currently allows (the action
mask), or over the whole flat tool space with --ignore-mask (then most
graph moves come back as logged invalid no-ops).

Recommended order (see the plan / docs/architecture.md):
  1. scripts/verify_tools.py first, to confirm the tool inputs live.
  2. This script with --policy idle through one full quest — confirms
     reset()'s start detection fires and logs real quest.state
     transitions without ever sending a meaningful attack.
  3. This script with --policy random through at least one more full
     quest — the actual "nothing crashes, episode boundaries detected
     correctly" acceptance check.

Usage:
    python scripts/run_dummy_policy.py \
        --tools configs/weapons/greatsword_tools.yaml \
        --monster configs/monsters/great_jagras.yaml \
        --state-path "$HOME/.local/share/Steam/steamapps/common/Monster Hunter World/fly_mhw_state.json" \
        --policy idle --max-steps 500

Accept the quest in-game once this prints that it's waiting for one —
reset() only detects the quest-start transition, it doesn't accept
quests itself.
"""

from __future__ import annotations

import argparse
import json
import random
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from env.mhw_env import MHWEnv  # noqa: E402


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--tools", default="configs/weapons/greatsword_tools.yaml")
    parser.add_argument("--monster", required=True)
    parser.add_argument("--state-path", required=True)
    parser.add_argument("--policy", choices=["idle", "random"], default="idle")
    parser.add_argument("--ignore-mask", action="store_true",
                         help="random policy samples the full flat tool space, not just currently-valid calls")
    parser.add_argument("--max-steps", type=int, default=500)
    parser.add_argument("--step-period", type=float, default=0.2)
    parser.add_argument("--capture-geometry", default=None)
    parser.add_argument("--log-path", default=None)
    parser.add_argument("--seed", type=int, default=0)
    parser.add_argument("--reset-timeout", type=float, default=120.0)
    args = parser.parse_args()

    random.seed(args.seed)

    log_file = None
    if args.log_path:
        Path(args.log_path).parent.mkdir(parents=True, exist_ok=True)
        log_file = open(args.log_path, "w")

    env = MHWEnv(
        tools_config_path=args.tools,
        monster_config_path=args.monster,
        state_path=args.state_path,
        capture_geometry=args.capture_geometry,
        step_period_seconds=args.step_period,
        reset_timeout_seconds=args.reset_timeout,
    )

    idle = env.toolset.call("wait", duration="short")

    def policy(info):
        if args.policy == "idle":
            return idle
        if args.ignore_mask:
            return random.randrange(env.toolset.n)
        return random.choice([i for i, ok in enumerate(info["action_mask"]) if ok])

    print(f"Waiting up to {args.reset_timeout:.0f}s for a quest to start — accept one now in-game...")
    try:
        _, info = env.reset()
    except RuntimeError as exc:
        raise SystemExit(str(exc))

    print(f"Quest started (quest_id={info['quest_id']}). Running {args.policy} policy for up to {args.max_steps} steps...")
    # NOTE: everything printed/logged below from info["reward_debug"] is
    # for a human to read while debugging this run — it must never be fed
    # back into a policy/observation. See mhw_env.py's _build_info.

    quest_states_seen = set()
    quest_states_seen.add(info["reward_debug"]["quest_state_raw"])
    monster_ids_seen = set()
    total_reward = 0.0
    termination_reason = ""
    step_count = 0

    try:
        for step_count in range(1, args.max_steps + 1):
            action = policy(info)
            _, reward, terminated, truncated, info = env.step(action)
            total_reward += reward
            quest_states_seen.add(info["reward_debug"]["quest_state_raw"])

            line = {"step": step_count, "reward": reward, "terminated": terminated, "truncated": truncated,
                    **{k: v for k, v in info.items() if k != "action_mask"}}
            rd = info["reward_debug"]
            call = f"{info['tool_name']}({', '.join(f'{k}={v}' for k, v in info['tool_args'].items())})"
            combo = info["moveset"]
            print(f"[{step_count:4d}] {call:<50s}{' INVALID' if info['invalid_call'] else ''} "
                  f"combo={combo['from']}->{combo['to'] or '-'} depth={combo['depth']} "
                  f"reward={reward:+.4f} "
                  f"monster_hp={rd['monster_hp_fraction']} player_hp={rd['player_hp_fraction']} "
                  f"quest_state={rd['quest_state_raw']} "
                  f"target_id={rd['monster_id']} (of {rd['monster_count']} live)")
            monster_ids_seen.add(rd["monster_id"])
            if log_file:
                log_file.write(json.dumps(line) + "\n")

            if terminated or truncated:
                termination_reason = info["termination_reason"]
                break
    except KeyboardInterrupt:
        termination_reason = "keyboard_interrupt"
    finally:
        env.close()
        if log_file:
            log_file.close()

    print("\n=== Summary ===")
    print(f"steps run:          {step_count}")
    print(f"total reward:       {total_reward:.4f}")
    print(f"termination reason: {termination_reason or '(max-steps arg reached without terminated/truncated)'}")
    print(f"distinct quest_state_raw values observed: {sorted(quest_states_seen, key=str)}")
    print(f"monster ids selected as target:           {sorted(monster_ids_seen, key=str)}")
    print("  -> if that's a single stable id, put it in the monster config's")
    print("     identification.expected_ids to pin target selection exactly.")
    if args.log_path:
        print(f"full log written to: {args.log_path}")


if __name__ == "__main__":
    main()
