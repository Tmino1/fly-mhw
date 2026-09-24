"""
Gymnasium-shaped (but not gymnasium-dependent — see docs/architecture.md)
environment wiring env/game_interface/{capture,lua_bridge,input_injector}
+ the tool-based action space (tools.py, moveset_graph.py,
tool_executor.py) + reward.py behind reset()/step()/close(). Observation
is the raw PIL.Image — no preprocessing pipeline yet, per the roadmap.

Each step runs one tool call to completion (a charge hold, a move for
its duration, ...), so steps are variable-length — a semi-MDP.
info["tool_duration_seconds"] says how long each one took. Actions can be:
  - a ToolCall (e.g. toolset.call("wide_slash", direction="forward")),
  - a {"tool": ..., "args": {...}} dict,
  - a flat index into toolset (toolset.n actions),
  - a RelativeAction: an index into the current combo root's options
    (info["moveset"]["available"]) plus the call's args.
A move no option reaches from the current root is masked: it's a logged
no-op (info["invalid_call"]) that still costs a step.
info["action_mask"] is the flat mask for the NEXT step.

The combo state (info["moveset"]) is driven only by the agent's own
calls, and — like everything else in info — is not part of the
observation. Whether the brain ever receives it is an open Phase 2
decision (docs/architecture.md, Design Principle 4).

reset() does NOT accept the quest itself — a human accepts it; reset()
only detects the -1 -> real-id transition (bounded by a timeout).
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

from PIL.Image import Image

from .config_loader import load_config  # re-exported for convenience/tests
from .game_interface.capture import CaptureResult, GrimCaptureError, capture_frame
from .game_interface.input_injector import VirtualGamepad
from .game_interface.lua_bridge import GameState, LuaBridge, StateReadError, quest_id as _quest_id
from .moveset_graph import MovesetTracker
from .reward import RewardModel
from .tool_executor import ExecResult, ToolExecutor
from .tools import ToolCall, ToolSet


@dataclass(frozen=True)
class RelativeAction:
    """Pick the index-th option of the current combo root (the order of
    info["moveset"]["available"]), with the call's own args. Any arg the
    option pins (e.g. a Tackle after a dodge is always lv1) is filled in
    automatically."""

    index: int
    args: dict[str, Any] = field(default_factory=dict)


class MHWEnv:
    def __init__(
        self,
        tools_config_path: str | Path,
        monster_config_path: str | Path,
        state_path: str | Path,
        capture_geometry: Optional[str] = None,
        step_period_seconds: float = 0.2,
        reset_timeout_seconds: float = 60.0,
        gamepad: Optional[VirtualGamepad] = None,
    ):
        self.toolset = ToolSet.from_config(tools_config_path)
        self.tracker = MovesetTracker(self.toolset.graph, time.monotonic())
        self.reward_model = RewardModel.from_config(monster_config_path)
        self.lua_bridge = LuaBridge(Path(state_path))
        self.capture_geometry = capture_geometry
        # Settle time after each tool finishes, before the frame is
        # captured. In v1 this 0.2s was the whole step; tools now carry
        # their own durations, so it's likely worth shrinking once tools
        # are verified live.
        self.step_period_seconds = step_period_seconds
        self.reset_timeout_seconds = reset_timeout_seconds

        self._owns_gamepad = gamepad is None
        self.gamepad = gamepad or VirtualGamepad()
        self.executor = ToolExecutor(self.gamepad, self.toolset, self.tracker)

        self._prev_state: Optional[GameState] = None
        self._step_count = 0
        self._last_image: Optional[Image] = None

    def _wait_until(self, predicate, timeout_seconds: float, phase_name: str) -> GameState:
        deadline = time.monotonic() + timeout_seconds
        while True:
            try:
                state = self.lua_bridge.read()
                if predicate(state):
                    return state
            except StateReadError:
                pass
            if time.monotonic() >= deadline:
                raise RuntimeError(
                    f"MHWEnv.reset() timed out in phase {phase_name!r} after "
                    f"{timeout_seconds}s waiting on lua_bridge state"
                )
            time.sleep(0.5)

    def reset(self) -> tuple[Image, dict[str, Any]]:
        deadline = time.monotonic() + self.reset_timeout_seconds

        # Phase A: if a stale in-progress quest is already active from a
        # previous run, wait for it to clear back to idle first, so we
        # don't mistake an already-running hunt for a fresh start.
        try:
            initial = self.lua_bridge.read()
        except StateReadError:
            initial = None
        if _quest_id(initial) is not None and _quest_id(initial) != -1:
            remaining = max(0.0, deadline - time.monotonic())
            self._wait_until(lambda s: _quest_id(s) == -1, remaining, "waiting_for_idle")

        # Phase B: wait for a human to accept a quest (-1 -> real id).
        remaining = max(0.0, deadline - time.monotonic())
        state = self._wait_until(lambda s: _quest_id(s) not in (None, -1), remaining, "waiting_for_quest_start")

        self.executor.rest()
        self.tracker.reset(time.monotonic())
        self._prev_state = state
        self._step_count = 0

        capture = capture_frame(geometry=self.capture_geometry)
        self._last_image = capture.image

        info = self._build_info(capture, state, None, quest_state_raw=None, monster_hp_fraction=None,
                                player_hp_fraction=None, termination_reason="")
        return capture.image, info

    def _resolve_action(self, action: "ToolCall | dict | int | RelativeAction"):
        """-> (ToolCall, pinned option or None)."""
        if not isinstance(action, RelativeAction):
            return self.toolset.coerce(action), None
        options = self.tracker.options(time.monotonic())
        option = options[action.index]
        spec = self.toolset.spec(option.move)
        args = {**{k: v for k, v in action.args.items() if k in spec.arg_names}, **option.fixed_args}
        return self.toolset.call(option.move, **args), option

    def step(self, action: "ToolCall | dict | int | RelativeAction") -> tuple[Image, float, bool, bool, dict[str, Any]]:
        call, option = self._resolve_action(action)
        result = self.executor.run(call, option)
        time.sleep(self.step_period_seconds)

        try:
            capture = capture_frame(geometry=self.capture_geometry)
            self._last_image = capture.image
        except GrimCaptureError:
            info = self._build_info(None, self._prev_state, result, quest_state_raw=None, monster_hp_fraction=None,
                                    player_hp_fraction=None, termination_reason="capture_error")
            return self._last_image, 0.0, False, True, info

        try:
            curr_state = self.lua_bridge.read()
        except StateReadError:
            info = self._build_info(capture, self._prev_state, result, quest_state_raw=None, monster_hp_fraction=None,
                                    player_hp_fraction=None, termination_reason="state_read_error")
            return capture.image, 0.0, False, True, info

        outcome = self.reward_model.step(self._prev_state, curr_state, self._step_count)
        self._prev_state = curr_state
        self._step_count += 1

        info = self._build_info(
            capture, curr_state, result, quest_state_raw=outcome.quest_state_raw,
            monster_hp_fraction=outcome.monster_hp_fraction, player_hp_fraction=outcome.player_hp_fraction,
            termination_reason=outcome.reason,
            monster_id=outcome.monster_id, monster_count=outcome.monster_count,
        )
        return capture.image, outcome.reward, outcome.terminated, outcome.truncated, info

    def _build_info(
        self,
        capture: Optional[CaptureResult],
        state: Optional[GameState],
        result: Optional[ExecResult],
        *,
        quest_state_raw: Any,
        monster_hp_fraction: Optional[float],
        player_hp_fraction: Optional[float],
        termination_reason: str,
        monster_id: Any = None,
        monster_count: int = 0,
    ) -> dict[str, Any]:
        now = time.monotonic()
        player = state.raw.get("player") if state else None
        if result is None:
            moveset = self.tracker.snapshot(now)
        elif result.transition is not None:
            moveset = result.transition.to_dict()
        else:
            moveset = self.tracker.snapshot(result.t_start)
        return {
            "capture_latency_seconds": capture.latency_seconds if capture else None,
            "state_age_seconds": state.file_age_seconds if state else None,
            "quest_id": _quest_id(state),
            "tool_name": result.call.name if result else None,
            "tool_args": result.call.arg_dict if result else None,
            "tool_duration_seconds": result.duration_s if result else None,
            "invalid_call": result.invalid if result else False,
            # What this step's call did to the combo state (for a timed
            # tool or an invalid call: the state it ran in, unchanged).
            "moveset": moveset,
            # For choosing the NEXT action: the flat mask, and the current
            # root's options (the order RelativeAction indexes).
            "action_mask": self.toolset.mask(self.tracker, now),
            "moveset_next": self.tracker.snapshot(now),
            "episode_step": self._step_count,
            "termination_reason": termination_reason,
            # Everything under reward_debug is privileged game state that
            # went into COMPUTING the reward scalar — logging/debugging
            # only. The observation returned alongside this info dict is
            # the raw screen image and nothing else; reward_debug's
            # contents must never be fed into the agent's observation or
            # wired into a sensory-input channel (Phase 2) — the monster's
            # HP (and any other reward-only signal) is meant to be learned
            # from pixels + the reward scalar, never read directly. See
            # docs/architecture.md's Design Principles.
            "reward_debug": {
                "quest_state_raw": quest_state_raw,
                "monster_hp_fraction": monster_hp_fraction,
                "player_hp_fraction": player_hp_fraction,
                # Which entity the reward is actually tracking, out of how
                # many live ones. Read monster_id out of a real hunt's logs
                # to pin the target in the monster config's expected_ids.
                "monster_id": monster_id,
                "monster_count": monster_count,
                # The game's own animation id — for auditing the moveset
                # graph offline, same rule as the rest of reward_debug.
                "player_action": player.get("action") if isinstance(player, dict) else None,
            },
        }

    def close(self) -> None:
        if self._owns_gamepad:
            self.gamepad.close()
