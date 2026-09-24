"""
Maps your real keyboard/mouse onto input ROLES (v2) — the same button
vocabulary the gamepad side uses (y, b, a, x, rt, left-stick directions),
so demos/tool_segmenter.py can group your timestamped key/button events
into the tool calls the agent uses. You play with keyboard + mouse, not a
controller; configs/keyboard_bindings.yaml (written by
scripts/calibrate_keyboard_bindings.py, never hand-guessed) says which
physical key plays each role.

v1 mapped keys straight to the 8 legacy action names and reduced
"whatever's held" to one action every 0.2s (KeyboardActionReducer +
ACTION_PRIORITY). That lost short taps, hold durations, simultaneous
presses and all mouse movement — v2 records events instead, see
docs/architecture.md's "Tool-based action space + moveset graph".

evdev is imported lazily so this module (and the offline tests) load on
a machine without evdev.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional, Sequence

from env.config_loader import load_config

KEYBOARD_BINDINGS_SCHEMA = "fly-mhw/keyboard_bindings/v2"

# Every role demos/tool_segmenter.py understands. rb (sprint) and lt
# (slinger) aren't v1 tools — they're bound only so demos that use them
# get flagged `unrepresentable` instead of silently mislabelled.
ROLES: tuple[str, ...] = (
    "move_forward", "move_back", "move_left", "move_right",
    "y", "b", "a", "x", "rt", "rb", "lt",
)


def code_name(code: int) -> str:
    """Symbolic name for an evdev EV_KEY code, for diagnostics only.
    ecodes.KEY/ecodes.BTN can map a code to a tuple of aliases (e.g. 272 ==
    ('BTN_LEFT', 'BTN_MOUSE')) — always pick the first, so this is always
    a plain string, never a tuple, in logs/JSONL output."""
    from evdev import ecodes

    name = ecodes.KEY.get(code) or ecodes.BTN.get(code) or str(code)
    return name[0] if isinstance(name, tuple) else name


def find_input_devices(name_patterns: Sequence[str]) -> list:
    """List every evdev input device whose .name case-insensitively
    contains one of name_patterns (e.g. "keychron", "logitech"). Opens each
    match via evdev.InputDevice(path) — deliberately NEVER calls .grab():
    a non-grabbing open is a passive listener, the kernel fans events out
    to every open reader, so this does not steal input from the desktop
    or the game. Confirmed live 2026-09-18.

    If nothing matches, prints every available device's path/name (same
    "show everything, let a human pick" fallback list_windows.py uses for
    Hyprland windows) and raises SystemExit rather than silently
    returning an empty list.
    """
    import evdev

    matched = []
    all_devices = [evdev.InputDevice(path) for path in evdev.list_devices()]
    for dev in all_devices:
        name_lower = dev.name.lower()
        if any(p.lower() in name_lower for p in name_patterns):
            matched.append(dev)
        else:
            dev.close()

    if not matched:
        print(f"No input device matched any of {list(name_patterns)!r}. Available devices:")
        for dev in all_devices:
            print(f"  {dev.path}: {dev.name!r}")
        raise SystemExit(
            "No matching input device found — pass different --devices patterns "
            "matching one of the names printed above."
        )
    return matched


@dataclass
class KeyboardBindings:
    tools_config: str
    roles: dict[str, Optional[str]]  # role -> evdev KEY_*/BTN_* name, or None if uncalibrated
    mouse: dict[str, Any] = field(default_factory=dict)
    device_name_patterns: tuple[str, ...] = ()

    @classmethod
    def from_config(cls, path: str | Path) -> "KeyboardBindings":
        data = load_config(path, KEYBOARD_BINDINGS_SCHEMA)
        roles = dict(data.get("roles") or {})
        unknown = set(roles) - set(ROLES)
        if unknown:
            raise ValueError(f"{path}: unknown role(s) {sorted(unknown)} — known: {list(ROLES)}")
        bound = [k for k in roles.values() if k]
        if len(bound) != len(set(bound)):
            raise ValueError(f"{path}: the same key is bound to more than one role")
        return cls(
            tools_config=data["tools_config"],
            roles={r: roles.get(r) for r in ROLES},
            mouse=dict(data.get("mouse") or {}),
            device_name_patterns=tuple(data.get("device_name_patterns", ())),
        )

    def bound_roles(self) -> list[str]:
        return [r for r, k in self.roles.items() if k]

    def unbound_roles(self) -> list[str]:
        return [r for r, k in self.roles.items() if not k]

    def code_to_role(self) -> dict[int, str]:
        from evdev import ecodes

        return {ecodes.ecodes[k]: r for r, k in self.roles.items() if k}
