"""
A weapon's moveset as a directed graph (configs/weapons/*_moveset.yaml),
plus a tracker that re-roots it at the latest move.

The same button does different things depending on what came before it
(Great Sword B = Wide Slash from neutral, Strong Wide Slash after a
Strong Charged Slash, Jumping Wide Slash after a Tackle). The tracker
keeps the combo ROOT (the latest move) and resolves each input against
it, so both sides of the project share one set of rules:

  - demo recording (demos/tool_segmenter.py): human input -> which move
    that was, from this root  (advance_input)
  - the agent (env/tool_executor.py, env/mhw_env.py): requested move ->
    which input produces it from this root, and which moves are
    currently possible at all (options / move_options)

Resolution order for an input at root R (see the moveset YAML header):
R's own edges, then R's `continues_as` chain, then the global `any`
edges, then — only for inputs none of those claim — neutral's edges (a
"re-root": a fresh chain). An input R does claim never falls through to
neutral, so e.g. a Y tap after a Charged Slash is always Side Blow 1,
never Overhead Smash. The agent's action mask is exactly "the moves
some option resolves to" (user decision, 2026-09-24), so demo labels
and agent calls can never disagree about what's possible.

Only the caller's own inputs/calls drive the tracker — never game state
read from Lua. Pure Python (no evdev, no gamepad): fully testable offline.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

from .config_loader import load_config

MOVESET_SCHEMA = "fly-mhw/moveset_graph/v1"

ATTACK_INPUTS = ("y_tap", "y_hold", "b", "yb", "rt_hold", "rt_y", "a", "x")
CHARGE_FINISHES = ("release", "tackle")
NODE_KINDS = ("move", "root", "charge")


@dataclass(frozen=True)
class Node:
    id: str
    kind: str
    move: Optional[str]
    duration_s: float
    combo_window_s: float
    continues_as: Optional[str] = None
    returns_to: Optional[str] = None
    reachable: bool = True
    motion_values: Any = None
    notes: str = ""


@dataclass(frozen=True)
class Edge:
    id: str
    from_node: str  # "*" for the global `any` edges
    input: str
    to: str
    args: tuple[tuple[str, Any], ...] = ()
    source: str = ""
    verified: bool = False
    notes: str = ""

    @property
    def fixed_args(self) -> dict[str, Any]:
        return dict(self.args)


@dataclass(frozen=True)
class Option:
    """One thing that can be done from a root: an input (plus a charge
    finish, when it goes through a held-charge node) and where it lands."""

    input: str
    finish: Optional[str]
    edges: tuple[Edge, ...]  # one edge, or (root->charge, charge->move)
    to: str
    move: str
    via: Optional[str]
    rerooted: bool

    @property
    def fixed_args(self) -> dict[str, Any]:
        out: dict[str, Any] = {}
        for e in self.edges:
            out.update(e.fixed_args)
        return out

    @property
    def edge_ids(self) -> list[str]:
        return [e.id for e in self.edges]

    def describe(self) -> str:
        inp = f"{self.input}/{self.finish}" if self.finish else self.input
        via = f" via {self.via}" if self.via else ""
        rr = " (re-root)" if self.rerooted else ""
        return f"{inp} -> {self.to}{via}{rr}"

    def accepts_args(self, args: dict[str, Any]) -> bool:
        return all(args.get(k) == v for k, v in self.fixed_args.items())


def _edge_from_raw(raw: dict, default_id: str, from_node: Optional[str] = None) -> Edge:
    return Edge(
        id=raw.get("id", default_id),
        from_node=from_node if from_node is not None else raw["from"],
        input=raw["input"],
        to=raw["to"],
        args=tuple(sorted((raw.get("args") or {}).items())),
        source=raw.get("source", ""),
        verified=raw.get("verified", False),
        notes=raw.get("notes", ""),
    )


class MovesetGraph:
    def __init__(
        self,
        name: str,
        root: str,
        nodes: dict[str, Node],
        edges: list[Edge],
        any_edges: list[Edge],
        candidates: list[dict],
    ):
        self.name = name
        self.root = root
        self.nodes = nodes
        self.edges = edges
        self.any_edges = any_edges
        self.candidates = candidates
        self._own: dict[str, dict[str, Edge]] = {}
        for e in edges:
            per_node = self._own.setdefault(e.from_node, {})
            if e.input in per_node:
                raise ValueError(
                    f"{name}: node {e.from_node!r} has two edges for input {e.input!r} "
                    f"({per_node[e.input].id!r}, {e.id!r}) — an input must resolve to exactly one move"
                )
            per_node[e.input] = e
        self._validate()

    @classmethod
    def from_config(cls, path: str | Path) -> "MovesetGraph":
        return cls.from_dict(load_config(path, MOVESET_SCHEMA))

    @classmethod
    def from_dict(cls, data: dict) -> "MovesetGraph":
        defaults = data.get("defaults", {})
        nodes: dict[str, Node] = {}
        for raw in data["nodes"]:
            kind = raw.get("kind", "move")
            node = Node(
                id=raw["id"],
                kind=kind,
                move=raw.get("move"),
                duration_s=float(raw.get("duration_s", defaults.get("duration_s", 1.0))),
                combo_window_s=float(raw.get("combo_window_s", defaults.get("combo_window_s", 1.0))),
                continues_as=raw.get("continues_as"),
                returns_to=raw.get("returns_to"),
                reachable=raw.get("reachable", True),
                motion_values=raw.get("motion_values"),
                notes=raw.get("notes", ""),
            )
            if node.id in nodes:
                raise ValueError(f"duplicate node id {node.id!r}")
            nodes[node.id] = node

        edges = [
            _edge_from_raw(raw, f"{raw['from']}--{raw['input']}->{raw['to']}")
            for raw in data.get("edges", [])
        ]
        any_edges = [
            _edge_from_raw(raw, f"*--{raw['input']}->{raw['to']}", from_node="*")
            for raw in data.get("any", [])
        ]
        return cls(
            name=data["name"],
            root=data.get("root", "neutral"),
            nodes=nodes,
            edges=edges,
            any_edges=any_edges,
            candidates=list(data.get("candidates", [])),
        )

    def _validate(self) -> None:
        if self.root not in self.nodes:
            raise ValueError(f"{self.name}: root {self.root!r} is not a declared node")
        for n in self.nodes.values():
            if n.kind not in NODE_KINDS:
                raise ValueError(f"node {n.id!r}: unknown kind {n.kind!r}")
            if n.kind == "move" and not n.move:
                raise ValueError(f"node {n.id!r}: kind 'move' needs a `move` (tool) name")
            for ref in (n.continues_as, n.returns_to):
                if ref is not None and ref not in self.nodes:
                    raise ValueError(f"node {n.id!r} references unknown node {ref!r}")
        for e in self.edges + self.any_edges:
            if e.from_node != "*" and e.from_node not in self.nodes:
                raise ValueError(f"edge {e.id!r}: unknown from-node {e.from_node!r}")
            if e.to not in self.nodes:
                raise ValueError(f"edge {e.id!r}: unknown to-node {e.to!r}")
            from_kind = self.nodes[e.from_node].kind if e.from_node != "*" else "move"
            allowed = CHARGE_FINISHES if from_kind == "charge" else ATTACK_INPUTS
            if e.input not in allowed:
                raise ValueError(
                    f"edge {e.id!r}: input {e.input!r} not valid leaving a {from_kind!r} node "
                    f"(expected one of {allowed})"
                )
            if self.nodes[e.to].kind == "charge" and e.input != "y_hold":
                raise ValueError(f"edge {e.id!r}: only y_hold can enter a charge node")
            if e.input == "y_hold" and self.nodes[e.to].kind != "charge":
                raise ValueError(f"edge {e.id!r}: y_hold must lead into a charge node")
            if not self.nodes[e.to].reachable:
                raise ValueError(f"edge {e.id!r} leads to unreachable node {e.to!r}")
        for c in self.candidates:
            for key in ("from", "to", "continues_as"):
                if key in c and c[key] not in self.nodes:
                    raise ValueError(f"candidate {c!r}: unknown node {c[key]!r}")

    # --- structure queries ------------------------------------------------

    def moves(self, reachable_only: bool = True) -> list[str]:
        """Distinct move (tool) names, in declaration order."""
        seen: list[str] = []
        for n in self.nodes.values():
            if n.move and n.kind != "charge" and n.move not in seen and (n.reachable or not reachable_only):
                seen.append(n.move)
        return seen

    def claimed_edges(self, node_id: str) -> dict[str, Edge]:
        """input -> edge for every input node_id claims: its own edges,
        then its continues_as chain, then the global any-edges."""
        claimed: dict[str, Edge] = {}
        seen: set[str] = set()
        cur: Optional[str] = node_id
        while cur is not None and cur not in seen:
            seen.add(cur)
            for inp, e in self._own.get(cur, {}).items():
                claimed.setdefault(inp, e)
            cur = self.nodes[cur].continues_as
        for e in self.any_edges:
            claimed.setdefault(e.input, e)
        return claimed

    def _expand(self, edge: Edge, rerooted: bool) -> list[Option]:
        target = self.nodes[edge.to]
        if target.kind != "charge":
            return [Option(edge.input, None, (edge,), target.id, target.move or target.id, None, rerooted)]
        return [
            Option(edge.input, finish, (edge, fin_edge), fin_edge.to,
                   self.nodes[fin_edge.to].move or fin_edge.to, target.id, rerooted)
            for finish, fin_edge in self._own.get(target.id, {}).items()
        ]

    def options_at(self, root_id: str) -> list[Option]:
        """Everything doable from root_id, in a stable order: the inputs
        root_id claims first, then neutral's options for the inputs it
        doesn't (marked rerooted). This order defines relative labels."""
        claimed = self.claimed_edges(root_id)
        options: list[Option] = []
        for e in claimed.values():
            options.extend(self._expand(e, rerooted=False))
        if root_id != self.root:
            for inp, e in self.claimed_edges(self.root).items():
                if inp not in claimed:
                    options.extend(self._expand(e, rerooted=True))
        return options


@dataclass
class Transition:
    """What one input/call did to the combo state — the `graph` block
    recorded with every demo tool call and every MHWEnv step."""

    t: float
    root_before: str
    expired: bool
    to: Optional[str]
    move: Optional[str]
    via: Optional[str]
    edge_ids: list[str]
    rerooted: bool
    depth: int
    path_from_root: list[str]
    relative_label: Optional[int]
    available: list[str] = field(default_factory=list)
    unresolved: bool = False
    path_before: list[str] = field(default_factory=list)  # combo path before this input
    starts_chain: bool = False  # first move of a new combo chain (see advance())

    def to_dict(self) -> dict[str, Any]:
        return {
            "from": self.root_before,
            "expired": self.expired,
            "to": self.to,
            "move": self.move,
            "via": self.via,
            "edge_ids": self.edge_ids,
            "rerooted": self.rerooted,
            "depth": self.depth,
            "path_from_root": self.path_from_root,
            "relative_label": self.relative_label,
            "available": self.available,
            "unresolved": self.unresolved,
            "path_before": self.path_before,
            "starts_chain": self.starts_chain,
        }


class MovesetTracker:
    def __init__(self, graph: MovesetGraph, t0: float = 0.0):
        self.graph = graph
        self.reset(t0)

    def reset(self, t: float = 0.0) -> None:
        self.current = self.graph.root
        self.committed_at = t
        self.path: list[str] = [self.graph.root]

    # --- the re-rooted view ---------------------------------------------

    def root_at(self, t: float) -> tuple[str, bool]:
        """(effective root, expired?) at time t. A move with `returns_to`
        hands straight back (not an expiry); otherwise the root falls back
        to neutral once duration_s + combo_window_s have passed."""
        node = self.graph.nodes[self.current]
        if node.returns_to:
            return node.returns_to, False
        if node.id != self.graph.root and t > self.committed_at + node.duration_s + node.combo_window_s:
            return self.graph.root, True
        return self.current, False

    def options(self, t: float) -> list[Option]:
        return self.graph.options_at(self.root_at(t)[0])

    def move_options(self, move: str, args: dict[str, Any], t: float) -> list[Option]:
        return [o for o in self.options(t) if o.move == move and o.accepts_args(args)]

    def available_moves(self, t: float) -> set[str]:
        return {o.move for o in self.options(t)}

    def snapshot(self, t: float) -> dict[str, Any]:
        """The combo state at t without advancing — the `graph` block for
        timed tools (move/wait/camera), same keys as Transition.to_dict()."""
        root, expired = self.root_at(t)
        path = self.path if root == self.current else [root]
        return Transition(
            t=t, root_before=root, expired=expired, to=None, move=None, via=None, edge_ids=[],
            rerooted=False, depth=len(path) - 1, path_from_root=list(path), relative_label=None,
            available=[o.describe() for o in self.graph.options_at(root)], path_before=list(path),
        ).to_dict()

    # --- advancing --------------------------------------------------------

    def resolve_input(self, input: str, finish: Optional[str], t: float) -> Optional[Option]:
        for o in self.options(t):
            if o.input == input and (o.finish is None or o.finish == finish):
                return o
        return None

    def advance(self, option: Optional[Option], t_input: float, t_commit: Optional[float] = None) -> Transition:
        """Commit option (resolved at t_input; the move itself commits at
        t_commit — the Y release for a charge, else t_input). option=None
        records an unresolved input: the state is unknown afterwards, so
        the tracker falls back to neutral."""
        t_commit = t_input if t_commit is None else t_commit
        root, expired = self.root_at(t_input)
        options = self.graph.options_at(root)
        available = [o.describe() for o in options]
        base_path = self.path if root == self.current else [root]

        if option is None:
            self.current, self.committed_at, self.path = self.graph.root, t_commit, [self.graph.root]
            return Transition(t_input, root, expired, None, None, None, [], False,
                              0, [self.graph.root], None, available, unresolved=True,
                              path_before=list(base_path), starts_chain=True)

        relative = options.index(option) if option in options else None
        target = self.graph.nodes[option.to]
        if target.kind == "root":
            path = [target.id]
        elif option.rerooted:
            path = [self.graph.root, target.id]
        else:
            path = base_path + [target.id]

        self.current, self.committed_at, self.path = target.id, t_commit, path
        return Transition(
            t=t_input, root_before=root, expired=expired, to=target.id, move=option.move,
            via=option.via, edge_ids=option.edge_ids, rerooted=option.rerooted,
            depth=len(path) - 1, path_from_root=list(path), relative_label=relative,
            available=available, path_before=list(base_path),
            # A chain starts from neutral (fresh, expired or returned-to), on
            # a re-root, or when landing on a chain root like dodge_out —
            # but NOT on the move right after dodge_out (Y after dodge ->
            # Tackle continues the dodge's chain).
            starts_chain=option.rerooted or target.kind == "root" or base_path == [self.graph.root],
        )

    def advance_input(self, input: str, finish: Optional[str], t_input: float,
                      t_commit: Optional[float] = None) -> Transition:
        """Demo side: what move did this input make, from the current root?"""
        return self.advance(self.resolve_input(input, finish, t_input), t_input, t_commit)
