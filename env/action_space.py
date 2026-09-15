"""
Turns a weapon config (configs/weapons/*.yaml) into a usable discrete
action interface over env/game_interface/input_injector.py's
VirtualGamepad. No weapon-specific knowledge lives here — swapping
weapons is swapping the YAML path, per docs/architecture.md's Design
Principles.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from evdev import ecodes

from .config_loader import load_config
from .game_interface.input_injector import VirtualGamepad

WEAPON_CONFIG_SCHEMA = "fly-mhw/weapon_config/v1"

# Confirmed live 2026-09-14 (see configs/weapons/greatsword.yaml and
# docs/risks.md): evdev's compass-direction button aliases are swapped
# from their intuitive meaning — BTN_NORTH is numerically BTN_X, and
# BTN_WEST is numerically BTN_Y. A weapon config almost certainly means
# the letter when it says "Y" or "X", so warn loudly if a compass alias
# shows up — it's very likely a bug, the same one attack_1 originally had.
_COMPASS_ALIAS_WARNING = {
    "BTN_NORTH": "BTN_X",
    "BTN_WEST": "BTN_Y",
    "BTN_SOUTH": "BTN_A",  # not actually swapped, but flagged too since it's
    "BTN_EAST": "BTN_B",   # the same family of alias and easy to typo-confuse
}


def _resolve_code(name: str) -> int:
    if name in _COMPASS_ALIAS_WARNING:
        print(
            f"[action_space] WARNING: {name!r} is a compass alias, numeric "
            f"value {hex(getattr(ecodes, name))} == {_COMPASS_ALIAS_WARNING[name]!r}. "
            f"If you meant the letter button, use {_COMPASS_ALIAS_WARNING[name]!r} "
            "directly instead — BTN_NORTH/BTN_WEST in particular are NOT what "
            "their compass name suggests. See docs/risks.md."
        )
    try:
        return getattr(ecodes, name)
    except AttributeError as exc:
        raise ValueError(f"unknown evdev code {name!r}") from exc


@dataclass(frozen=True)
class ButtonOp:
    code: int
    op: str = "tap"  # only "tap" is used in v1; "press"/"release" reserved
                      # for later charge-hold mechanics (e.g. Great Sword
                      # charge levels), not implemented yet
    hold_seconds: float = 0.1


@dataclass(frozen=True)
class ActionDef:
    name: str
    axes: dict[int, int]
    buttons: tuple[ButtonOp, ...]
    verified: bool
    notes: str = ""


@dataclass
class ActionSpace:
    name: str
    weapon_type_id: int
    controlled_axes: list[int]
    actions: list[ActionDef] = field(default_factory=list)

    @classmethod
    def from_config(cls, path: str | Path) -> "ActionSpace":
        data = load_config(path, WEAPON_CONFIG_SCHEMA)

        controlled_axes = [_resolve_code(a) for a in data.get("controlled_axes", [])]

        actions: list[ActionDef] = []
        for raw in data["actions"]:
            axes = {_resolve_code(k): v for k, v in raw.get("axes", {}).items()}
            buttons = tuple(
                ButtonOp(
                    code=_resolve_code(b["code"]),
                    op=b.get("op", "tap"),
                    hold_seconds=b.get("hold_seconds", 0.1),
                )
                for b in raw.get("buttons", [])
            )
            actions.append(
                ActionDef(
                    name=raw["name"],
                    axes=axes,
                    buttons=buttons,
                    verified=raw.get("verified", False),
                    notes=raw.get("notes", ""),
                )
            )

        space = cls(
            name=data["name"],
            weapon_type_id=data["weapon_type_id"],
            controlled_axes=controlled_axes,
            actions=actions,
        )

        unverified = space.unverified_actions()
        if unverified:
            print(
                f"[action_space] {data['name']}: {len(unverified)}/{len(actions)} "
                f"action(s) unverified against the live game: {', '.join(unverified)} "
                "— see scripts/verify_action_mapping.py"
            )

        return space

    @property
    def n(self) -> int:
        return len(self.actions)

    def names(self) -> list[str]:
        return [a.name for a in self.actions]

    def index_of(self, name: str) -> int:
        for i, a in enumerate(self.actions):
            if a.name == name:
                return i
        raise KeyError(f"no action named {name!r} in {self.name}'s action space")

    def unverified_actions(self) -> list[str]:
        return [a.name for a in self.actions if not a.verified]

    def _resolve(self, action: "int | str") -> ActionDef:
        if isinstance(action, str):
            return self.actions[self.index_of(action)]
        return self.actions[action]

    def apply(self, pad: VirtualGamepad, action: "int | str") -> ActionDef:
        action_def = self._resolve(action)
        for axis_code, value in action_def.axes.items():
            pad.set_axis(axis_code, value)
        for button in action_def.buttons:
            if button.op == "tap":
                pad.tap(button.code, hold_seconds=button.hold_seconds)
            elif button.op == "press":
                pad.press(button.code)
            elif button.op == "release":
                pad.release(button.code)
            else:
                raise ValueError(f"unknown button op {button.op!r} in action {action_def.name!r}")
        return action_def
