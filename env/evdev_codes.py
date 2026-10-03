"""
Resolving evdev symbolic code names ("BTN_Y", "ABS_RZ", ...) to their
numeric values, shared by env/action_space.py (legacy v1) and
env/tool_executor.py (v2 tools).

evdev is imported lazily, inside resolve_code(), so modules that only
*carry* code names around (env/tools.py loading a tools config, the
offline test suite) stay importable on a machine without evdev — e.g. a
macOS laptop, where python-evdev doesn't build at all.
"""

from __future__ import annotations

# Confirmed live 2026-09-14 (see configs/weapons/greatsword.yaml and
# docs/risks.md): evdev's compass-direction button aliases are swapped
# from their intuitive meaning — BTN_NORTH is numerically BTN_X, and
# BTN_WEST is numerically BTN_Y. A weapon config almost certainly means
# the letter when it says "Y" or "X", so warn loudly if a compass alias
# shows up — it's very likely a bug, the same one attack_1 originally had.
COMPASS_ALIAS_WARNING = {
    "BTN_NORTH": "BTN_X",
    "BTN_WEST": "BTN_Y",
    "BTN_SOUTH": "BTN_A",  # not actually swapped, but flagged too since it's
    "BTN_EAST": "BTN_B",   # the same family of alias and easy to typo-confuse
}


def warn_if_compass_alias(name: str) -> bool:
    """Print the compass-alias warning for name if it is one. Pure (no
    evdev needed) so config loaders can call it at load time, before any
    code is resolved. Returns True if a warning was printed."""
    if name not in COMPASS_ALIAS_WARNING:
        return False
    print(
        f"[evdev_codes] WARNING: {name!r} is a compass alias, numerically "
        f"the same code as {COMPASS_ALIAS_WARNING[name]!r}. "
        f"If you meant the letter button, use {COMPASS_ALIAS_WARNING[name]!r} "
        "directly instead — BTN_NORTH/BTN_WEST in particular are NOT what "
        "their compass name suggests. See docs/risks.md."
    )
    return True


# Virtual axis identifiers used by the keyboard/mouse backend
# (env/game_interface/keyboard_mouse_injector.py), where a gamepad config
# would name ABS_X/ABS_RZ/... A keyboard has no absolute axes, so these
# stand in: VAXIS_MOVE_* becomes WASD key holds and VAXIS_CAM_* becomes
# relative mouse motion. Negative so they can never collide with a real
# evdev code, and resolved here — with no evdev import — so a tools config
# naming them still loads on a machine without evdev.
VIRTUAL_CODES = {
    "VAXIS_MOVE_X": -1,
    "VAXIS_MOVE_Y": -2,
    "VAXIS_CAM_X": -3,
    "VAXIS_CAM_Y": -4,
}


def resolve_code(name: str) -> int:
    if name in VIRTUAL_CODES:
        return VIRTUAL_CODES[name]

    from evdev import ecodes

    warn_if_compass_alias(name)
    try:
        return getattr(ecodes, name)
    except AttributeError as exc:
        raise ValueError(f"unknown evdev code {name!r}") from exc
