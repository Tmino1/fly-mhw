"""
Records a human-played hunt as synchronized (frame, action, state) demo
data for eventual imitation learning (Phase 3 of the roadmap). During the
actual hunt this never touches a VirtualGamepad — you play, this only
observes: captures a frame, reduces your currently-held keyboard/mouse
input to one action name (demos/keyboard_bindings.py), reads live game
state, and writes both to disk.

Optional exception, off the actual demo-data path: an auto-skip for the
real post-hunt "return to camp" wait (confirmed live to exist —
quest.state=3, quest.id unchanged, well after a kill). If a gamepad is
given, wait_for_quest_start()'s idle-drain phase sends ONE BTN_SOUTH tap
(the confirm button, verified back in Phase 0) the first time it sees
that state, instead of sitting through the wait. This never touches
anything recorded as demo data — it only runs between episodes, on a
gamepad the KeyboardActionReducer never sees, so it can't leak into an
action label. UNVERIFIED that BTN_SOUTH is actually what dismisses that
screen specifically (only that it's the general confirm button
elsewhere) — watch the first live use of this.

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

from evdev import ecodes

from env.game_interface.capture import GrimCaptureError, capture_frame
from env.game_interface.input_injector import VirtualGamepad
from env.game_interface.lua_bridge import GameState, LuaBridge, StateReadError, quest_id
from env.reward import RewardModel

from .keyboard_bindings import KeyboardActionReducer

# The confirmed-real (2026-09-19) post-hunt quest.state value seen while
# quest.id is still non-idle, well after a kill — the "return to camp"
# wait screen. See docs/risks.md's quest.state notes.
_POST_HUNT_WAIT_STATE = 3


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
        post_hunt_gamepad: Optional[VirtualGamepad] = None,
    ):
        self.reward_model = reward_model
        self.lua_bridge = lua_bridge
        self.reducer = reducer
        self.capture_geometry = capture_geometry
        self.reset_timeout_seconds = reset_timeout_seconds
        self.step_period_seconds = step_period_seconds
        self.output_dir = Path(output_dir)
        # See module docstring: only used to auto-skip the post-hunt
        # "return to camp" wait, never during actual recording, never
        # visible to KeyboardActionReducer.
        self.post_hunt_gamepad = post_hunt_gamepad
        self._sent_post_hunt_skip = False

    def _wait_until(self, predicate, timeout_seconds: float, phase_name: str, on_poll=None) -> GameState:
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
                if on_poll:
                    on_poll(state)
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

    def _maybe_skip_post_hunt_wait(self, state: Optional[GameState]) -> None:
        if self.post_hunt_gamepad is None or state is None:
            return
        quest = state.raw.get("quest")
        q_state = quest.get("state") if isinstance(quest, dict) else None
        if q_state == _POST_HUNT_WAIT_STATE:
            if not self._sent_post_hunt_skip:
                self.post_hunt_gamepad.tap(ecodes.BTN_SOUTH, hold_seconds=0.15)
                self._sent_post_hunt_skip = True
        else:
            # Reset so the NEXT hunt's post-hunt wait also gets one tap,
            # not just the first hunt of the session.
            self._sent_post_hunt_skip = False

    def wait_for_quest_start(self, timeout_seconds: float = 60.0) -> GameState:
        """Drain a stale in-progress quest back to idle first (in case one
        was already running — e.g. the recorder was (re)started mid-hunt),
        then block until a human accepts a fresh quest (quest id
        transitions from -1/None to a real id).

        Each phase gets its OWN full timeout_seconds budget, not a shared
        one. Confirmed live 2026-09-19 why that matters: with a single
        shared deadline (this class's original behavior, copied from
        MHWEnv.reset() — a bug there too, not fixed there since a single
        Phase-1 acceptance-test run is less likely to hit it), Phase A
        patiently draining an already-in-progress ~410s hunt ate most of a
        600s budget, leaving Phase B only ~169s to catch the next
        quest-accept — nowhere near enough patience for a real session
        with normal pauses between hunts.
        """
        try:
            initial = self.lua_bridge.read()
        except StateReadError:
            initial = None
        if quest_id(initial) is not None and quest_id(initial) != -1:
            self._wait_until(
                lambda s: quest_id(s) == -1, timeout_seconds, "waiting_for_idle",
                on_poll=self._maybe_skip_post_hunt_wait,
            )

        return self._wait_until(
            lambda s: quest_id(s) not in (None, -1), timeout_seconds, "waiting_for_quest_start"
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

                # Bug found live 2026-09-19: this call used to live only in
                # wait_for_quest_start()'s idle-drain phase, which only
                # runs AFTER the episode ends — but the episode can't end
                # until RewardModel sees the ENTIRE raw monster list go
                # empty (curr_monsters_empty = monsters == []), which only
                # happens once you're fully back at the hub and the whole
                # map's wildlife unloads too. That's the exact same wait
                # this was supposed to skip — circular, so it never fired.
                # Belongs in the active recording loop instead, where it
                # can actually run while quest.state==3 is still showing.
                self._maybe_skip_post_hunt_wait(curr_state)

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
