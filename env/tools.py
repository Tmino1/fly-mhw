"""
The tool-based action space: configs/weapons/*_tools.yaml -> a ToolSet of
named tools (moves) with small discrete args. Supersedes the legacy
8-action env/action_space.py.

A tool call is what the policy picks and what demos are labelled with —
e.g. ToolCall("strong_charged_slash", direction="forward", level="lv2").
Graph tools are moves in the weapon's moveset graph (env/moveset_graph.py):
which input produces them depends on the combo root, and whether they're
possible at all right now is ToolSet.mask(). Timed tools (move/wait/
camera) are always available and never touch the combo root.

Two views of the same space, so Phase 2 can pick its readout without
re-recording anything:
  - flat:     n / index_of(call) / call_at(i) — one discrete index per
              (tool, args) combination
  - factored: tool_names() / arg_choices(tool) — a tool head plus one
              head per arg

No weapon knowledge lives here (Design Principle 2), and no evdev import:
gamepad codes stay symbolic names until env/tool_executor.py resolves them.
"""

from __future__ import annotations

import itertools
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional

from .config_loader import load_config
from .evdev_codes import warn_if_compass_alias
from .moveset_graph import MovesetGraph, MovesetTracker

TOOLS_SCHEMA = "fly-mhw/weapon_tools/v1"
TOOL_KINDS = ("graph", "timed")

_REPO_ROOT = Path(__file__).resolve().parent.parent


@dataclass(frozen=True)
class ToolCall:
    name: str
    args: tuple[tuple[str, Any], ...] = ()

    @classmethod
    def make(cls, name: str, **args: Any) -> "ToolCall":
        return cls(name, tuple(sorted(args.items())))

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "ToolCall":
        return cls.make(data["tool"], **(data.get("args") or {}))

    @property
    def arg_dict(self) -> dict[str, Any]:
        return dict(self.args)

    def to_dict(self) -> dict[str, Any]:
        return {"tool": self.name, "args": self.arg_dict}

    def __str__(self) -> str:
        inner = ", ".join(f"{k}={v}" for k, v in self.args)
        return f"{self.name}({inner})"


@dataclass(frozen=True)
class ToolSpec:
    name: str
    kind: str
    args: tuple[tuple[str, tuple[Any, ...]], ...]  # (arg name, allowed values), config order
    notes: str = ""

    @property
    def arg_names(self) -> list[str]:
        return [a for a, _ in self.args]


def _resolve_repo_path(path: str | Path) -> Path:
    p = Path(path)
    if p.is_absolute() or p.exists():
        return p
    return _REPO_ROOT / p


class ToolSet:
    def __init__(self, data: dict[str, Any], graph: MovesetGraph, moveset_path: Optional[Path] = None):
        self.name: str = data["name"]
        self.moveset_path = moveset_path
        self.weapon_type_id: int = data["weapon_type_id"]
        self.gamepad: dict[str, Any] = data["gamepad"]
        self.directions: dict[str, list[int]] = data["directions"]
        self.timings: dict[str, Any] = data["timings"]
        self.segmenter: dict[str, Any] = data.get("segmenter", {})
        self.graph = graph

        arg_values: dict[str, list[Any]] = data["arg_values"]
        self.tools: list[ToolSpec] = []
        for raw in data["tools"]:
            kind = raw.get("kind", "graph")
            if kind not in TOOL_KINDS:
                raise ValueError(f"tool {raw['name']!r}: unknown kind {kind!r}")
            args = []
            for arg_name, value_set in (raw.get("args") or {}).items():
                if value_set not in arg_values:
                    raise ValueError(f"tool {raw['name']!r}: unknown arg_values set {value_set!r}")
                args.append((arg_name, tuple(arg_values[value_set])))
            self.tools.append(ToolSpec(raw["name"], kind, tuple(args), raw.get("notes", "")))
        self._by_name = {t.name: t for t in self.tools}
        if len(self._by_name) != len(self.tools):
            raise ValueError(f"{self.name}: duplicate tool names")

        self._validate()

        self._calls: list[ToolCall] = []
        for spec in self.tools:
            names = spec.arg_names
            for combo in itertools.product(*(vals for _, vals in spec.args)):
                self._calls.append(ToolCall.make(spec.name, **dict(zip(names, combo))))
        self._index = {c: i for i, c in enumerate(self._calls)}

    @classmethod
    def from_config(cls, path: str | Path, moveset_path: Optional[str | Path] = None) -> "ToolSet":
        data = load_config(path, TOOLS_SCHEMA)
        resolved = _resolve_repo_path(moveset_path or data["moveset"])
        return cls(data, MovesetGraph.from_config(resolved), resolved)

    def _validate(self) -> None:
        graph_moves = set(self.graph.moves())
        graph_tools = {t.name for t in self.tools if t.kind == "graph"}
        unknown = graph_tools - graph_moves
        if unknown:
            raise ValueError(f"{self.name}: graph tools with no reachable node in the moveset: {sorted(unknown)}")
        missing = graph_moves - graph_tools
        if missing:
            raise ValueError(f"{self.name}: reachable moveset moves with no tool: {sorted(missing)}")
        for t in self.tools:
            for arg_name, values in t.args:
                if arg_name == "direction":
                    for v in values:
                        if v not in self.directions:
                            raise ValueError(f"tool {t.name!r}: direction {v!r} missing from `directions`")
        for name in self.gamepad.get("buttons", {}).values():
            warn_if_compass_alias(name)

    # --- flat view ----------------------------------------------------------

    @property
    def n(self) -> int:
        return len(self._calls)

    def calls(self) -> list[ToolCall]:
        return list(self._calls)

    def index_of(self, call: ToolCall) -> int:
        try:
            return self._index[call]
        except KeyError:
            raise KeyError(f"{call} is not in {self.name}'s tool space") from None

    def call_at(self, index: int) -> ToolCall:
        return self._calls[index]

    # --- factored view ----------------------------------------------------

    def tool_names(self) -> list[str]:
        return [t.name for t in self.tools]

    def spec(self, name: str) -> ToolSpec:
        try:
            return self._by_name[name]
        except KeyError:
            raise KeyError(f"no tool named {name!r} in {self.name}") from None

    def arg_choices(self, name: str) -> dict[str, list[Any]]:
        return {a: list(v) for a, v in self.spec(name).args}

    # --- calls ----------------------------------------------------------------

    def validate_call(self, call: ToolCall) -> ToolCall:
        spec = self.spec(call.name)
        args = call.arg_dict
        if set(args) != set(spec.arg_names):
            raise ValueError(f"{call}: expected args {spec.arg_names}, got {sorted(args)}")
        for arg_name, values in spec.args:
            if args[arg_name] not in values:
                raise ValueError(f"{call}: {arg_name}={args[arg_name]!r} not one of {list(values)}")
        return call

    def call(self, name: str, **args: Any) -> ToolCall:
        return self.validate_call(ToolCall.make(name, **args))

    def coerce(self, action: "ToolCall | dict | int") -> ToolCall:
        """Accept a ToolCall, a {"tool", "args"} dict, or a flat index."""
        if isinstance(action, ToolCall):
            return self.validate_call(action)
        if isinstance(action, dict):
            return self.validate_call(ToolCall.from_dict(action))
        if isinstance(action, int):
            return self.call_at(action)
        raise TypeError(f"can't interpret {action!r} as a tool call")

    def is_graph_tool(self, name: str) -> bool:
        return self.spec(name).kind == "graph"

    def mask(self, tracker: MovesetTracker, t: float) -> list[bool]:
        """Flat-index action mask at time t: timed tools always, graph
        tools iff some option from the current root reaches that move
        with compatible args (see env/moveset_graph.py's docstring)."""
        options = tracker.options(t)
        out = []
        for c in self._calls:
            if not self.is_graph_tool(c.name):
                out.append(True)
                continue
            args = c.arg_dict
            out.append(any(o.move == c.name and o.accepts_args(args) for o in options))
        return out
