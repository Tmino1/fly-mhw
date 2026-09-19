"""
Phase 0 IPC client for lua_scripts/state_reader.lua.

Polls a JSON snapshot file written periodically by the Lua script running
inside the game process. This is the simplest IPC likely to work without
first confirming LuaEngine's full API surface (see docs/modding_setup.md)
— if LuaEngine turns out to support scripting websockets directly, that's
a strictly better upgrade (push instead of poll, lower latency) and should
replace this.

STATUS: unverified against a real running LuaEngine instance — see
docs/modding_setup.md's open questions.
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterator, Optional


class StateReadError(RuntimeError):
    """The state file is missing, stale, or not valid JSON."""


@dataclass
class GameState:
    raw: dict[str, Any]
    read_at: float
    file_age_seconds: float


class LuaBridge:
    def __init__(self, state_path: Path, max_age_seconds: float = 5.0):
        """
        state_path: where state_reader.lua's OUTPUT_PATH actually resolves
            to on the Linux filesystem. This is NOT known in advance under
            Proton — find it empirically (see docs/modding_setup.md) and
            pass it in here; there's no reliable default to guess.
        max_age_seconds: how stale the file's mtime can be before reads are
            treated as an error (i.e. the Lua script has stopped writing —
            script unloaded, game closed, crash, etc.).
        """
        self.state_path = state_path
        self.max_age_seconds = max_age_seconds

    def read(self) -> GameState:
        if not self.state_path.exists():
            raise StateReadError(f"{self.state_path} does not exist yet")

        mtime = self.state_path.stat().st_mtime
        age = time.time() - mtime
        if age > self.max_age_seconds:
            raise StateReadError(
                f"{self.state_path} is {age:.1f}s old (max {self.max_age_seconds}s) "
                "— is state_reader.lua still loaded and running in-game?"
            )

        try:
            text = self.state_path.read_text()
            data = json.loads(text)
        except (OSError, json.JSONDecodeError) as exc:
            # A partial write (Python reading mid-write) is possible since
            # this is plain polling with no lock/atomic-rename — a
            # transient decode failure here is expected occasionally, not
            # necessarily a real problem. Callers should tolerate retrying.
            raise StateReadError(f"failed to read/parse {self.state_path}: {exc}") from exc

        return GameState(raw=data, read_at=time.time(), file_age_seconds=age)

    def poll(self, interval_seconds: float = 0.5) -> Iterator[GameState]:
        """Yield GameState forever, one read per interval. Swallows
        transient StateReadError from partial writes; re-raises once
        errors persist across several retries in a row."""
        consecutive_errors = 0
        while True:
            try:
                state = self.read()
                consecutive_errors = 0
                yield state
            except StateReadError:
                consecutive_errors += 1
                if consecutive_errors > 5:
                    raise
            time.sleep(interval_seconds)


def quest_id(state: Optional[GameState]) -> Optional[int]:
    """The current quest's id out of a GameState snapshot, or None if
    state is None or malformed. -1 means "no active quest" (idle) —
    confirmed live; any other value means a quest is in progress.
    Promoted out of env/mhw_env.py's private _quest_id (2026-09-18) so
    demos/recorder.py can share the exact same quest-start detection
    instead of reimplementing it — see docs/architecture.md."""
    if state is None:
        return None
    quest = state.raw.get("quest")
    if not isinstance(quest, dict):
        return None
    return quest.get("id")
