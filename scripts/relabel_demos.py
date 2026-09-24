#!/usr/bin/env python3
"""
Re-run demo labelling over already-recorded v2 episodes, from their raw
event logs (input_events.jsonl, lmt_events.jsonl) — no replaying needed.
Use it after changing the moveset graph (e.g. promoting a `candidates:`
edge the audit confirmed), retuning timings/segmenter thresholds, or
recalibrating the mouse.

Usage:
    python scripts/relabel_demos.py                       # every episode under demos/storage
    python scripts/relabel_demos.py demos/storage/<episode_id> [...]
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from demos.keyboard_bindings import KeyboardBindings  # noqa: E402
from demos.labeling import file_sha256, label_episode  # noqa: E402
from env.tools import ToolSet  # noqa: E402


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("episodes", nargs="*", help="episode dirs (default: all v2 episodes under --storage)")
    parser.add_argument("--storage", default="demos/storage")
    parser.add_argument("--tools", default="configs/weapons/greatsword_tools.yaml")
    parser.add_argument("--keyboard-bindings", default="configs/keyboard_bindings.yaml")
    args = parser.parse_args()

    toolset = ToolSet.from_config(args.tools)
    bindings = KeyboardBindings.from_config(args.keyboard_bindings)
    dirs = [Path(p) for p in args.episodes] or sorted(p.parent for p in Path(args.storage).glob("*/episode_meta.json"))

    for ep in dirs:
        meta_path = ep / "episode_meta.json"
        meta = json.loads(meta_path.read_text()) if meta_path.exists() else {}
        if meta.get("schema") != "fly-mhw/demo_episode/v2":
            print(f"skip {ep}: not a v2 episode (v1 episodes have no raw event log to relabel)")
            continue
        labels = label_episode(ep, toolset, bindings.mouse, meta.get("started_at"), meta.get("ended_at"))
        meta.update({
            "labels": labels,
            "tools_config": args.tools,
            "tools_config_sha256": file_sha256(args.tools),
            "moveset_config": str(toolset.moveset_path),
            "moveset_config_sha256": file_sha256(toolset.moveset_path),
            "keyboard_bindings": args.keyboard_bindings,
            "keyboard_bindings_sha256": file_sha256(args.keyboard_bindings),
        })
        meta_path.write_text(json.dumps(meta, indent=2))
        print(f"{ep.name}: {labels['tool_calls']} calls, unresolved {labels['unresolved']}, "
              f"depths {labels['depth_counts']}")


if __name__ == "__main__":
    main()
