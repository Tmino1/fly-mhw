"""
Gymnasium-shaped (but not gymnasium-dependent — see docs/architecture.md)
environment wiring env/game_interface/{capture,lua_bridge,input_injector}
+ action_space.py + reward.py behind reset()/step()/close(). Observation
is the raw PIL.Image — no preprocessing pipeline yet, per the roadmap.

reset() does NOT accept the quest itself — a human accepts it; reset()
only detects the -1 -> real-id transition (bounded by a timeout). Out of
scope for Phase 1.
"""

from __future__ import annotations

import time
from pathlib import Path
from typing import Any, Optional

from PIL.Image import Image

from .action_space import ActionSpace
from .config_loader import load_config  # re-exported for convenience/tests
from .game_interface.capture import CaptureResult, GrimCaptureError, capture_frame
from .game_interface.input_injector import VirtualGamepad
from .game_interface.lua_bridge import GameState, LuaBridge, StateReadError, quest_id as _quest_id
from .reward import RewardModel


class MHWEnv:
    def __init__(
        self,
        weapon_config_path: str | Path,
        monster_config_path: str | Path,
        state_path: str | Path,
        capture_geometry: Optional[str] = None,
        step_period_seconds: float = 0.2,
        reset_timeout_seconds: float = 60.0,
        gamepad: Optional[VirtualGamepad] = None,
    ):
        self.action_space = ActionSpace.from_config(weapon_config_path)
        self.reward_model = RewardModel.from_config(monster_config_path)
        self.lua_bridge = LuaBridge(Path(state_path))
        self.capture_geometry = capture_geometry
        self.step_period_seconds = step_period_seconds
        self.reset_timeout_seconds = reset_timeout_seconds

        self._owns_gamepad = gamepad is None
        self.gamepad = gamepad or VirtualGamepad()

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

        self.action_space.apply(self.gamepad, "idle")
        self._prev_state = state
        self._step_count = 0

        capture = capture_frame(geometry=self.capture_geometry)
        self._last_image = capture.image

        info = self._build_info(capture, state, quest_state_raw=None, monster_hp_fraction=None,
                                  player_hp_fraction=None, action_name="idle", action_verified=True,
                                  termination_reason="")
        return capture.image, info

    def step(self, action: "int | str") -> tuple[Image, float, bool, bool, dict[str, Any]]:
        action_def = self.action_space.apply(self.gamepad, action)
        time.sleep(self.step_period_seconds)

        try:
            capture = capture_frame(geometry=self.capture_geometry)
            self._last_image = capture.image
        except GrimCaptureError:
            info = self._build_info(
                None, self._prev_state, quest_state_raw=None, monster_hp_fraction=None,
                player_hp_fraction=None, action_name=action_def.name, action_verified=action_def.verified,
                termination_reason="capture_error",
            )
            return self._last_image, 0.0, False, True, info

        try:
            curr_state = self.lua_bridge.read()
        except StateReadError:
            info = self._build_info(
                capture, self._prev_state, quest_state_raw=None, monster_hp_fraction=None,
                player_hp_fraction=None, action_name=action_def.name, action_verified=action_def.verified,
                termination_reason="state_read_error",
            )
            return capture.image, 0.0, False, True, info

        outcome = self.reward_model.step(self._prev_state, curr_state, self._step_count)
        self._prev_state = curr_state
        self._step_count += 1

        info = self._build_info(
            capture, curr_state, quest_state_raw=outcome.quest_state_raw,
            monster_hp_fraction=outcome.monster_hp_fraction, player_hp_fraction=outcome.player_hp_fraction,
            action_name=action_def.name, action_verified=action_def.verified,
            termination_reason=outcome.reason,
            monster_id=outcome.monster_id, monster_count=outcome.monster_count,
        )
        return capture.image, outcome.reward, outcome.terminated, outcome.truncated, info

    def _build_info(
        self,
        capture: Optional[CaptureResult],
        state: Optional[GameState],
        *,
        quest_state_raw: Any,
        monster_hp_fraction: Optional[float],
        player_hp_fraction: Optional[float],
        action_name: str,
        action_verified: bool,
        termination_reason: str,
        monster_id: Any = None,
        monster_count: int = 0,
    ) -> dict[str, Any]:
        return {
            "capture_latency_seconds": capture.latency_seconds if capture else None,
            "state_age_seconds": state.file_age_seconds if state else None,
            "quest_id": _quest_id(state),
            "action_name": action_name,
            "action_verified": action_verified,
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
            },
        }

    def close(self) -> None:
        if self._owns_gamepad:
            self.gamepad.close()
