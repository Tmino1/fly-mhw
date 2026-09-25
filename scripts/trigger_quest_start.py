#!/usr/bin/env python3
"""
Side-project, not part of the ML pipeline: writes the flag file
mod_plugins/QuestStartTrigger/Plugin.cs watches for, to start a quest
without walking to the Handler. See that plugin's docstring and
docs/architecture.md's "Starting a quest programmatically" entry.

Deliberately standalone rather than wired into scripts/record_hunt.py yet
— this calls a native game function whose questMgr argument is an
unconfirmed inference (see the plugin's own docstring); this script exists
so the very first live test can happen in isolation, at a moment of your
choosing (in camp, between hunts, nothing at risk), before it's ever
folded into the actual recording loop.

Usage:
    python scripts/trigger_quest_start.py --quest-id 90099
"""

from __future__ import annotations

import argparse
from pathlib import Path

# Must match Plugin.cs's FlagFileName — that plugin resolves this relative
# filename against the game's own working directory, same as
# state_reader.lua's fly_mhw_state.json output, so this lives right next
# to --state-path, not wherever this script runs from.
_FLAG_FILENAME = "fly_mhw_start_quest.flag"

_DEFAULT_STATE_PATH = str(
    Path.home() / ".local/share/Steam/steamapps/common/Monster Hunter World/fly_mhw_state.json"
)


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--quest-id", type=int, required=True)
    parser.add_argument("--state-path", default=_DEFAULT_STATE_PATH,
                         help="used only to locate the game's working directory — the flag file "
                              "is written next to it, matching fly_mhw_state.json's own location")
    args = parser.parse_args()

    flag_path = Path(args.state_path).parent / _FLAG_FILENAME
    flag_path.write_text(str(args.quest_id))
    print(f"Wrote {flag_path} (quest_id={args.quest_id}). "
          f"QuestStartTrigger polls every frame — watch in-game now.")


if __name__ == "__main__":
    main()
