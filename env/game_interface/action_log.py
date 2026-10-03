"""
Reading state_reader.lua's per-tick player animation log.

The 1 Hz state snapshot (fly_mhw_state.json) cannot resolve a Great Sword
swing: measured live, a Charged Slash runs 2.56s and a Strong Charged
Slash 3.18s, with the chain-accept window opening ~1.1s in — sampling
that at 1 Hz produced pure noise and one outright wrong conclusion
(2026-10-02). The Lua side already logs every CHANGE of the player's
animation id as it happens, gated on a flag file; this is the Python side
of that.

Each line the Lua writes is {lmt_id, fsm, t, tick}. Its `t` is os.time(),
so only second-resolution — useless for animation timing — and `tick` is
the game's own counter with no fixed wall-clock mapping. So events are
stamped with a local monotonic arrival time instead, which is what the
timing measurements actually use. That arrival time includes the Lua
file-append plus this poll interval, so treat it as ~±20ms, fine for
animations measured in hundreds of milliseconds.

GOTCHA: the Lua only re-checks the flag once a second (it piggybacks on
the snapshot write), so there is a real delay between creating the flag
and logging starting. ENABLE_WAIT_SECONDS covers it — a shorter wait
silently produced empty logs.
"""

from __future__ import annotations

import json
import os
import threading
import time
from pathlib import Path
from typing import Any, Optional

FLAG_FILENAME = "fly_mhw_action_log.flag"      # must match state_reader.lua's ACTION_LOG_FLAG_PATH
LOG_FILENAME = "fly_mhw_actions.jsonl"         # must match its ACTION_LOG_PATH
ENABLE_WAIT_SECONDS = 5.0
POLL_SECONDS = 0.01


class ActionLogEvent:
    __slots__ = ("t", "lmt_id", "tick", "raw")

    def __init__(self, t: float, raw: dict[str, Any]):
        self.t = t                      # local monotonic arrival time
        self.lmt_id = raw.get("lmt_id")
        self.tick = raw.get("tick")
        self.raw = raw

    def __repr__(self) -> str:
        return f"ActionLogEvent(t={self.t:.3f}, lmt_id={self.lmt_id}, tick={self.tick})"


class ActionLog:
    """Tails the animation log for as long as the context is open.

    The flag is created on entry and removed on exit, so normal untracked
    play never leaves the log running — the same discipline the rest of
    this project's flag files follow.
    """

    def __init__(self, game_dir: str | Path, enable_wait_seconds: float = ENABLE_WAIT_SECONDS):
        self.game_dir = Path(game_dir)
        self.flag_path = self.game_dir / FLAG_FILENAME
        self.log_path = self.game_dir / LOG_FILENAME
        self.enable_wait_seconds = enable_wait_seconds
        self.events: list[ActionLogEvent] = []
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None

    # --- lifecycle --------------------------------------------------------

    def __enter__(self) -> "ActionLog":
        self.flag_path.write_text("")
        time.sleep(self.enable_wait_seconds)
        # Start reading from the CURRENT end: anything already in the file
        # is from an earlier session and would be attributed to this one.
        start = self.log_path.stat().st_size if self.log_path.exists() else 0
        self._thread = threading.Thread(target=self._tail, args=(start,), daemon=True)
        self._thread.start()
        return self

    def __exit__(self, *exc) -> None:
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=1.0)
        try:
            self.flag_path.unlink()
        except OSError:
            pass

    def _tail(self, pos: int) -> None:
        while not self._stop.is_set():
            try:
                if self.log_path.stat().st_size > pos:
                    with self.log_path.open() as f:
                        f.seek(pos)
                        chunk = f.read()
                        pos = f.tell()
                    now = time.monotonic()
                    for line in chunk.splitlines():
                        line = line.strip()
                        if not line:
                            continue
                        try:
                            self.events.append(ActionLogEvent(now, json.loads(line)))
                        except json.JSONDecodeError:
                            pass  # a partially-flushed line; it'll be re-read whole next poll
            except FileNotFoundError:
                pass
            time.sleep(POLL_SECONDS)

    # --- reading ----------------------------------------------------------

    def clear(self) -> None:
        self.events.clear()

    def since(self, t: float) -> list[ActionLogEvent]:
        return [e for e in self.events if e.t >= t]

    def wait_for_quiet(self, seconds: float, timeout: float = 10.0) -> None:
        """Block until no new animation has been logged for `seconds`.

        NOT the same as the move being over: ids are logged on CHANGE, so
        a long swing is silent for its whole duration and this returns
        mid-animation. Use wait_for_idle() to wait for a move to finish.
        """
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            last = self.events[-1].t if self.events else 0.0
            if time.monotonic() - last >= seconds:
                return
            time.sleep(POLL_SECONDS)

    def wait_for_idle(self, idle_ids, current_id, quiet_seconds: float = 0.4,
                      timeout: float = 12.0) -> bool:
        """Block until the character is back to an idle animation and has
        stayed there briefly. Returns False on timeout.

        `current_id` is a callable returning the player's CURRENT lmt_id
        (from the 1 Hz snapshot). It is required, not optional: this log
        only records CHANGES, so a character that is already idle and
        standing still emits nothing at all — waiting on the log alone
        waits forever. The log is still used, for quietness: no change
        for `quiet_seconds` means no animation is in progress.

        This is what makes repeated probing reliable. Sleeping a fixed
        "should be long enough" interval is not equivalent: a probe that
        starts while the previous move's combo root is still live sends
        its inputs from the wrong root and performs — and then records —
        a different move entirely. Observed directly: a run with a fixed
        3.5s settle attributed the Charged Slash's id to the Side Blow.
        """
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            last_change = self.events[-1].t if self.events else 0.0
            if time.monotonic() - last_change >= quiet_seconds:
                try:
                    if current_id() in idle_ids:
                        return True
                except Exception:
                    pass
            time.sleep(POLL_SECONDS)
        return False
