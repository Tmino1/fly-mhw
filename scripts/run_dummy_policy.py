#!/usr/bin/env python3
"""
Phase 1 acceptance test: run an idle-then-random dummy policy through
MHWEnv against a live quest, logging every step for review.

Recommended order (see the plan / docs/architecture.md):
  1. scripts/verify_action_mapping.py first, to correct the weapon config.
  2. This script with --policy idle through one full quest — confirms
     reset()'s start detection fires and logs real quest.state
     transitions without ever sending a meaningful attack.
  3. This script with --policy random through at least one more full
     quest — the actual "nothing crashes, episode boundaries detected
     correctly" acceptance check.

Usage:
    python scripts/run_dummy_policy.py \
        --weapon configs/weapons/greatsword.yaml \
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
    parser.add_argument("--weapon", required=True)
    parser.add_argument("--monster", required=True)
    parser.add_argument("--state-path", required=True)
    parser.add_argument("--policy", choices=["idle", "random"], default="idle")
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
        weapon_config_path=args.weapon,
        monster_config_path=args.monster,
        state_path=args.state_path,
        capture_geometry=args.capture_geometry,
        step_period_seconds=args.step_period,
        reset_timeout_seconds=args.reset_timeout,
    )

    def policy():
        if args.policy == "idle":
            return env.action_space.index_of("idle")
        return random.randrange(env.action_space.n)

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
    total_reward = 0.0
    termination_reason = ""
    step_count = 0

    try:
        for step_count in range(1, args.max_steps + 1):
            action = policy()
            _, reward, terminated, truncated, info = env.step(action)
            total_reward += reward
            quest_states_seen.add(info["reward_debug"]["quest_state_raw"])

            line = {"step": step_count, "reward": reward, "terminated": terminated, "truncated": truncated, **info}
            print(f"[{step_count:4d}] action={info['action_name']:<20s} reward={reward:+.4f} "
                  f"monster_hp={info['reward_debug']['monster_hp_fraction']} "
                  f"player_hp={info['reward_debug']['player_hp_fraction']} "
                  f"quest_state={info['reward_debug']['quest_state_raw']}")
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
    if args.log_path:
        print(f"full log written to: {args.log_path}")


if __name__ == "__main__":
    main()
