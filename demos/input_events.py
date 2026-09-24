"""
Live, timestamped keyboard/mouse events for demo recording — replaces
v1's `device.active_keys()` polling every 0.2s, which dropped short taps
and hold durations and never saw the mouse at all.

A background thread select()s over the passive (never .grab()'d) evdev
devices from demos/keyboard_bindings.find_input_devices() and queues:
  - KeyEvent(t, role, down) for every bound key/button down/up
    (key-repeat events are ignored),
  - MouseMotion(t, dx, dy) for relative mouse motion (camera).
t is the kernel's own event timestamp (CLOCK_REALTIME, same clock as
time.time()), so it's exact regardless of how late this thread reads it.

It also tails lua_scripts/state_reader.lua's action-change log (one JSON
line per player lmtID change, only written while the recorder's flag
file exists), stamping each line with time.time() on arrival — used
offline only, to audit labels (scripts/audit_tool_labels.py), never fed
into a label or an observation.
"""

from __future__ import annotations

import json
import select
import threading
import time
from pathlib import Path
from typing import Any, Optional, Sequence

from .keyboard_bindings import KeyboardBindings
from .tool_segmenter import KeyEvent, MouseMotion


class InputEventStream:
    def __init__(self, devices: Sequence[Any], bindings: KeyboardBindings,
                 action_log_path: Optional[str | Path] = None, poll_seconds: float = 0.02):
        from evdev import ecodes

        self._ecodes = ecodes
        self.devices = list(devices)
        self.code_to_role = bindings.code_to_role()
        self.action_log_path = Path(action_log_path) if action_log_path else None
        self.poll_seconds = poll_seconds
        self._lock = threading.Lock()
        self._keys: list[KeyEvent] = []
        self._mouse: list[MouseMotion] = []
        self._lmt: list[dict[str, Any]] = []
        self._log_pos = 0
        self._log_buf = ""
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None

    def start(self) -> "InputEventStream":
        if self.action_log_path and self.action_log_path.exists():
            self._log_pos = self.action_log_path.stat().st_size  # only new lines
        self._thread = threading.Thread(target=self._run, name="input-events", daemon=True)
        self._thread.start()
        return self

    def close(self) -> None:
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=1.0)
        for dev in self.devices:
            dev.close()

    def __enter__(self):
        return self.start()

    def __exit__(self, *exc):
        self.close()

    def take(self) -> tuple[list[KeyEvent], list[MouseMotion], list[dict[str, Any]]]:
        """Everything queued since the last take()."""
        with self._lock:
            out = (self._keys, self._mouse, self._lmt)
            self._keys, self._mouse, self._lmt = [], [], []
        return out

    def _run(self) -> None:
        e = self._ecodes
        while not self._stop.is_set():
            ready, _, _ = select.select(self.devices, [], [], self.poll_seconds)
            keys, mouse = [], []
            for dev in ready:
                try:
                    events = list(dev.read())
                except (BlockingIOError, OSError):
                    continue
                for ev in events:
                    if ev.type == e.EV_KEY and ev.value in (0, 1) and ev.code in self.code_to_role:
                        keys.append(KeyEvent(ev.timestamp(), self.code_to_role[ev.code], ev.value == 1))
                    elif ev.type == e.EV_REL and ev.code in (e.REL_X, e.REL_Y):
                        dx = ev.value if ev.code == e.REL_X else 0
                        dy = ev.value if ev.code == e.REL_Y else 0
                        mouse.append(MouseMotion(ev.timestamp(), dx, dy))
            lmt = self._read_action_log()
            if keys or mouse or lmt:
                with self._lock:
                    self._keys.extend(keys)
                    self._mouse.extend(mouse)
                    self._lmt.extend(lmt)

    def _read_action_log(self) -> list[dict[str, Any]]:
        path = self.action_log_path
        if path is None or not path.exists():
            return []
        size = path.stat().st_size
        if size < self._log_pos:  # truncated/recreated
            self._log_pos, self._log_buf = 0, ""
        if size == self._log_pos:
            return []
        with path.open("r") as f:
            f.seek(self._log_pos)
            chunk = f.read()
            self._log_pos = f.tell()
        now = time.time()
        lines = (self._log_buf + chunk).split("\n")
        self._log_buf = lines.pop()  # a partial last line waits for the rest
        out = []
        for line in lines:
            if not line.strip():
                continue
            try:
                rec = json.loads(line)
            except json.JSONDecodeError:
                continue
            rec["t_arrival"] = now
            out.append(rec)
        return out
