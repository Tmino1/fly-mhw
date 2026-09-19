"""
Records a human-played hunt as synchronized (frame, action, state) demo
data for eventual imitation learning (Phase 3 of the roadmap). Unlike
env/mhw_env.py, this never touches a VirtualGamepad — you play, this only
observes: captures a frame, reduces your currently-held keyboard/mouse
input to one action name (demos/keyboard_bindings.py), reads live game
state, and writes both to disk.

Reuses env/reward.py's RewardModel directly for episode-boundary
detection and reward-debug fields (it's fully standalone — no MHWEnv/
gamepad dependency) and env/game_interface/lua_bridge.py's quest_id()
for quest-start detection (the same function env/mhw_env.py imports) —
neither is reimplemented here. See docs/architecture.md's Design
Principles: the reward computed is stored for offline analysis only,
never shown live or used to steer recording.

One correctness-critical ordering: each tick captures the frame BEFORE
sampling the action, preserving the same causal (observation_t ->
action_t) pairing MHWEnv.step() implies (the action recorded is "what you
did in response to this frame," not "what you did during its capture").
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

from env.game_interface.capture import GrimCaptureError, capture_frame
from env.game_interface.lua_bridge import GameState, LuaBridge, StateReadError, quest_id
from env.reward import RewardModel

from .keyboard_bindings import KeyboardActionReducer


@dataclass
class EpisodeSummary:
    episode_id: str
    frame_count: int
    duration_seconds: float
    termination_reason: str
    quest_id: Any
    distinct_quest_states: list = field(default_factory=list)
    action_counts: dict[str, int] = field(default_factory=dict)


class DemoRecorder:
    def __init__(
        self,
        reward_model: RewardModel,
        lua_bridge: LuaBridge,
        reducer: KeyboardActionReducer,
        capture_geometry: Optional[str],
        step_period_seconds: float,
        output_dir: str | Path,
        reset_timeout_seconds: float = 60.0,
    ):
        self.reward_model = reward_model
        self.lua_bridge = lua_bridge
        self.reducer = reducer
        self.capture_geometry = capture_geometry
        self.reset_timeout_seconds = reset_timeout_seconds
        self.step_period_seconds = step_period_seconds
        self.output_dir = Path(output_dir)

    def _wait_until(self, predicate, timeout_seconds: float, phase_name: str) -> GameState:
        # Identical pattern to env/mhw_env.py's MHWEnv._wait_until — kept
        # as a second small copy rather than a shared import, since
        # MHWEnv's version isn't currently a free function/staticmethod
        # (it's bound to self for its own gamepad/step-count state, which
        # this class doesn't have). Both call the same underlying
        # quest_id() helper, so behavior stays identical even though the
        # loop itself is duplicated.
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
                    f"DemoRecorder timed out in phase {phase_name!r} after "
                    f"{timeout_seconds}s waiting on lua_bridge state"
                )
            time.sleep(0.5)

    def wait_for_quest_start(self, timeout_seconds: float = 60.0) -> GameState:
        """Mirrors MHWEnv.reset()'s Phase A/B exactly: drain a stale
        in-progress quest back to idle first (in case one was already
        running from a previous session), then block until a human
        accepts a fresh quest (quest id transitions from -1/None to a
        real id)."""
        deadline = time.monotonic() + timeout_seconds

        try:
            initial = self.lua_bridge.read()
        except StateReadError:
            initial = None
        if quest_id(initial) is not None and quest_id(initial) != -1:
            remaining = max(0.0, deadline - time.monotonic())
            self._wait_until(lambda s: quest_id(s) == -1, remaining, "waiting_for_idle")

        remaining = max(0.0, deadline - time.monotonic())
        return self._wait_until(
            lambda s: quest_id(s) not in (None, -1), remaining, "waiting_for_quest_start"
        )

    def record_episode(self, episode_id: Optional[str] = None) -> EpisodeSummary:
        start_state = self.wait_for_quest_start(timeout_seconds=self.reset_timeout_seconds)
        qid = quest_id(start_state)
        episode_id = episode_id or f"{time.strftime('%Y%m%dT%H%M%S')}_{qid}"
        episode_dir = self.output_dir / episode_id
        episode_dir.mkdir(parents=True, exist_ok=True)

        started_at = time.time()
        prev_state: Optional[GameState] = start_state
        step = 0
        termination_reason = ""
        distinct_quest_states: list = []
        action_counts: dict[str, int] = {}

        jsonl_path = episode_dir / "episode.jsonl"
        with jsonl_path.open("w") as jsonl_f:
            while True:
                try:
                    capture = capture_frame(geometry=self.capture_geometry)
                except GrimCaptureError as exc:
                    termination_reason = f"capture_error: {exc}"
                    break

                sample = self.reducer.sample()

                try:
                    curr_state = self.lua_bridge.read()
                except StateReadError as exc:
                    termination_reason = f"state_read_error: {exc}"
                    break

                outcome = self.reward_model.step(prev_state, curr_state, step)

                frame_path = episode_dir / f"frame_{step:06d}.png"
                capture.image.save(frame_path)

                if outcome.quest_state_raw not in distinct_quest_states:
                    distinct_quest_states.append(outcome.quest_state_raw)
                action_counts[sample.action_name] = action_counts.get(sample.action_name, 0) + 1

                jsonl_f.write(json.dumps({
                    "step": step,
                    "wall_clock": time.time(),
                    "capture_latency_seconds": capture.latency_seconds,
                    "action_name": sample.action_name,
                    "held_input_names": sample.held_key_names,
                    "action_collision": sample.candidate_actions if sample.collided else None,
                    "state_age_seconds": curr_state.file_age_seconds,
                    "quest_id": quest_id(curr_state),
                    "episode_step": step,
                    "termination_reason": outcome.reason,
                    # Same reward_debug shape MHWEnv._build_info() uses —
                    # offline-analysis-only, never fed back into anything
                    # that steers recording. See module docstring.
                    "reward_debug": {
                        "reward": outcome.reward,
                        "quest_state_raw": outcome.quest_state_raw,
                        "monster_hp_fraction": outcome.monster_hp_fraction,
                        "player_hp_fraction": outcome.player_hp_fraction,
                        "monster_id": outcome.monster_id,
                        "monster_count": outcome.monster_count,
                    },
                }) + "\n")
                jsonl_f.flush()

                prev_state = curr_state
                step += 1

                if outcome.terminated or outcome.truncated:
                    termination_reason = outcome.reason
                    break

                time.sleep(self.step_period_seconds)

        ended_at = time.time()
        summary = EpisodeSummary(
            episode_id=episode_id,
            frame_count=step,
            duration_seconds=ended_at - started_at,
            termination_reason=termination_reason,
            quest_id=qid,
            distinct_quest_states=distinct_quest_states,
            action_counts=action_counts,
        )

        meta = {
            "schema": "fly-mhw/demo_episode/v1",
            "episode_id": episode_id,
            "started_at": started_at,
            "ended_at": ended_at,
            "frame_count": summary.frame_count,
            "termination_reason": summary.termination_reason,
            "quest_id": summary.quest_id,
            "distinct_quest_states_seen": summary.distinct_quest_states,
            "action_counts": summary.action_counts,
            "step_period_seconds": self.step_period_seconds,
            "capture_geometry": self.capture_geometry,
        }
        (episode_dir / "episode_meta.json").write_text(json.dumps(meta, indent=2))

        return summary
