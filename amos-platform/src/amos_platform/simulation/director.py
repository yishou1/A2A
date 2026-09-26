"""Server-side demonstration director for causal, reproducible scenario runs."""

from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
import threading
import time
from typing import Any, Callable

from amos_platform.data.scenario_repository import get_scenario


CheckpointCallback = Callable[[dict[str, Any]], dict[str, Any] | None]
WorkflowStateCallback = Callable[..., dict[str, Any]]


def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


class DirectorError(ValueError):
    """Raised for invalid director configuration or actions."""


class DirectorService:
    """Coordinate manual and checkpoint-driven runs without fabricating evidence."""

    VALID_MODES = {"integration", "demonstration"}
    VALID_ACTIONS = {
        "start",
        "pause",
        "step_tick",
        "advance_checkpoint",
        "refresh_analysis",
        "start_auto",
        "stop_auto",
    }
    FAILED_ANALYSIS_STATUSES = {
        "submission_failed",
        "submission_unavailable",
        "submission_unverified",
        "failed",
    }

    def __init__(
        self,
        runtime: Any,
        *,
        checkpoint_callback: CheckpointCallback | None = None,
        workflow_state_callback: WorkflowStateCallback | None = None,
    ):
        self.runtime = runtime
        self.checkpoint_callback = checkpoint_callback
        self.workflow_state_callback = workflow_state_callback
        self._lock = threading.RLock()
        self._auto_stop = threading.Event()
        self._auto_thread: threading.Thread | None = None
        self._analysis_speed_before: float | None = None
        self._last_analysis_poll_at = 0.0
        # A stopped worker may still be inside a slow backend callback after
        # join(timeout=1).  The generation prevents that old worker from
        # resuming against a newly configured run when the shared stop event is
        # cleared for the replacement worker.
        self._auto_generation = 0
        self._scenario: dict[str, Any] | None = None
        self._checkpoint_index = -1
        self._reached: list[dict[str, Any]] = []
        self._actions: list[dict[str, Any]] = []
        self._stale_sweep_at = 0.0
        self._auth_target_missing_since = 0.0
        self._state: dict[str, Any] = {
            "scenario_id": None,
            "run_id": None,
            "mode": "integration",
            "branch": None,
            "seed": None,
            "director_status": "unconfigured",
            "current_checkpoint": None,
            "awaiting_analysis": False,
            "awaiting_authorization": False,
            "authorization_stage": None,
            "authorization_not_before_sec": None,
            "requested_faults": [],
            "verified_faults": [],
            "last_error": None,
        }

    def _record(self, action: str, **details: Any) -> None:
        self._actions.append({"action": action, "at": _now_iso(), **details})
        self._actions = self._actions[-100:]

    @staticmethod
    def _normalize_seed(raw: Any) -> int:
        if isinstance(raw, bool) or (isinstance(raw, float) and not raw.is_integer()):
            raise DirectorError("seed must be an integer")
        try:
            seed = int(raw)
        except (TypeError, ValueError) as exc:
            raise DirectorError("seed must be an integer") from exc
        if seed < 0 or seed > 2**32 - 1:
            raise DirectorError("seed must be an integer from 0 to 4294967295")
        return seed

    def configure(self, *, scenario_id: str, mode: str = "integration",
                  branch: str | None = None, seed: Any = None) -> dict[str, Any]:
        """Prepare a new paused run from an explicit scenario and seed."""
        if mode == "demo":
            mode = "demonstration"
        scenario = get_scenario(scenario_id)
        if not scenario:
            raise DirectorError(f"scenario not found: {scenario_id}")
        if mode not in self.VALID_MODES or mode not in set(scenario.get("supported_modes") or []):
            raise DirectorError(f"unsupported director mode: {mode}")
        branches = {str(item.get("branch_id")): item for item in scenario.get("expected_branches") or []}
        branch = branch or str(scenario.get("default_branch") or "standard")
        if branch not in branches:
            raise DirectorError(f"unsupported scenario branch: {branch}")
        selected_seed = self._normalize_seed(scenario.get("default_seed") if seed is None else seed)

        self._stop_auto_thread(pause=False)
        engine = self.runtime.get_engine()
        engine.stop()
        run_context = self.runtime.begin_run(scenario_id, seed=selected_seed)
        engine.load_scenario(scenario, seed=selected_seed)
        engine.clock["scenario_id"] = scenario_id
        engine.clock["run_id"] = run_context.run_id
        engine.clock["director_mode"] = mode
        engine.clock["scenario_branch"] = branch
        engine.evaluate_media_captures()
        with self._lock:
            self._scenario = scenario
            self._analysis_speed_before = None
            self._last_analysis_poll_at = 0.0
            self._checkpoint_index = -1
            self._reached = []
            self._actions = []
            self._state.update({
                "scenario_id": scenario_id,
                "run_id": run_context.run_id,
                "mode": mode,
                "branch": branch,
                "seed": selected_seed,
                "director_status": "configured",
                "current_checkpoint": None,
                "awaiting_analysis": False,
                "awaiting_authorization": False,
                "authorization_stage": None,
                "authorization_not_before_sec": None,
                "requested_faults": [],
                "verified_faults": [],
                "last_error": None,
            })
            engine.clock["director_status"] = "configured"
            engine.clock["director_analysis_status"] = None
            engine.clock["authorization_stage"] = None
            engine.clock["authorization_not_before_sec"] = None
            self._record("configure", scenario_id=scenario_id, mode=mode, branch=branch, seed=selected_seed)
            result = self.state()
        self.runtime.record_director_state(result)
        return result

    def _ensure_configured(self) -> None:
        if self._scenario is not None:
            return
        scenario_id = str(self.runtime.context.scenario_id)
        scenario = get_scenario(scenario_id)
        if not scenario:
            raise DirectorError("director is not configured")
        self.configure(
            scenario_id=scenario_id,
            mode="integration",
            branch=scenario.get("default_branch"),
            seed=scenario.get("default_seed"),
        )

    def _ensure_checkpoint_can_advance(self) -> None:
        """Fail closed when the current checkpoint has no trusted analysis."""
        checkpoint = self._state.get("current_checkpoint")
        if not isinstance(checkpoint, dict):
            return
        analysis_status = str(checkpoint.get("analysis_status") or "").lower()
        if analysis_status == "submitting" and checkpoint.get("analysis_blocking", True):
            raise DirectorError("checkpoint analysis submission is not completed")
        if analysis_status not in self.FAILED_ANALYSIS_STATUSES:
            return
        detail = checkpoint.get("analysis_error")
        submission = checkpoint.get("submission")
        if not detail and isinstance(submission, dict):
            detail = submission.get("error")
        message = str(detail or "当前检查点分析失败")
        raise DirectorError(f"{message}；请重置场景后重试")

    def _next_checkpoint(self) -> dict[str, Any] | None:
        branch = str(self._state.get("branch") or "")
        checkpoints = [
            checkpoint
            for checkpoint in (self._scenario or {}).get("demo_checkpoints") or []
            if self._available_on_branch(checkpoint, branch)
        ]
        next_index = self._checkpoint_index + 1
        return checkpoints[next_index] if next_index < len(checkpoints) else None

    @staticmethod
    def _available_on_branch(item: dict[str, Any], branch: str) -> bool:
        branch_ids = {str(value) for value in item.get("branch_ids") or ["*"] if value}
        return "*" in branch_ids or branch in branch_ids

    def _checkpoint_satisfied(self, checkpoint: dict[str, Any]) -> bool:
        engine = self.runtime.get_engine()
        elapsed = float(engine.clock.get("scenario_elapsed_sec", engine.clock.get("elapsed_sec", 0)) or 0)
        if elapsed < float(checkpoint.get("min_elapsed_sec", 0) or 0):
            return False
        conditions = checkpoint.get("conditions") or {}
        tracks = list(engine.sensor_fusion.get_tracks().values())
        if len(tracks) < int(conditions.get("track_count_at_least", 0) or 0):
            return False
        stable_required = int(conditions.get("stable_track_count_at_least", 0) or 0)
        if stable_required:
            min_confidence = float(conditions.get("minimum_track_confidence", 0.6) or 0.6)
            min_samples = int(conditions.get("minimum_track_samples", 2) or 2)
            stable_tracks = 0
            for track in tracks:
                sample_times = {
                    float(ref.get("sim_time"))
                    for ref in track.get("source_refs") or []
                    if ref.get("sim_time") is not None
                }
                if float(track.get("confidence", 0) or 0) >= min_confidence and len(sample_times) >= min_samples:
                    stable_tracks += 1
            if stable_tracks < stable_required:
                return False
        released = set(engine.media_capture.captured_media_ids)
        if not set(str(value) for value in conditions.get("media_ids_released") or []).issubset(released):
            return False
        emitted = set(engine._story_emitted)
        if not set(str(value) for value in conditions.get("cue_ids_emitted") or []).issubset(emitted):
            return False
        event_types = {
            str(event.get("type") or "")
            for event in engine.events
            if isinstance(event, dict)
        }
        if not set(
            str(value) for value in conditions.get("event_types_emitted") or []
        ).issubset(event_types):
            return False
        hit_target_ids = {
            str(event.get("target_threat_id"))
            for event in engine.events
            if isinstance(event, dict)
            and event.get("type") == "weapon_hit"
            and event.get("target_threat_id")
        }
        if not set(
            str(value) for value in conditions.get("weapon_hit_target_ids") or []
        ).issubset(hit_target_ids):
            return False
        assessed_target_ids = {
            str(event.get("target_threat_id") or event.get("target_ref"))
            for event in engine.events
            if isinstance(event, dict)
            and event.get("type") == "damage_assessment_confirmed"
            and (event.get("target_threat_id") or event.get("target_ref"))
        }
        required_assessed_targets = {
            str(value)
            for key in ("damage_assessed_target_ids", "damage_assessment_target_ids")
            for value in conditions.get(key) or []
        }
        if not required_assessed_targets.issubset(assessed_target_ids):
            return False
        recovered_asset_ids: set[str] = set()
        for event in engine.events:
            if not isinstance(event, dict) or event.get("type") not in {
                "asset_recovered", "aircraft_recovered", "carrier_recovery_completed",
            }:
                continue
            recovered_asset_ids.update(
                str(value)
                for value in [event.get("asset_id"), *(event.get("asset_ids") or [])]
                if value
            )
        if not set(
            str(value) for value in conditions.get("recovered_asset_ids") or []
        ).issubset(recovered_asset_ids):
            return False
        return True

    def _reach_checkpoint(
        self, checkpoint: dict[str, Any], *, defer_submission: bool = False,
        on_snapshot_captured: Callable[[], None] | None = None,
    ) -> dict[str, Any]:
        engine = self.runtime.get_engine()
        with self._lock:
            previous = self._state.get("current_checkpoint")
            if (
                isinstance(previous, dict)
                and previous.get("analysis_blocking", True)
                and previous.get("analysis_status") != "completed"
                and (
                    previous.get("analysis_after_authorization")
                    or isinstance(previous.get("submission"), dict)
                    and previous["submission"].get("workflow_id")
                )
            ):
                raise DirectorError("checkpoint analysis is not completed")
            context = {
                "scenario_id": self._state["scenario_id"],
                "run_id": str(self._state.get("run_id") or ""),
                "branch": self._state["branch"],
                "seed": self._state["seed"],
                "checkpoint_id": checkpoint.get("checkpoint_id"),
            }
            generation = self._auto_generation
            if on_snapshot_captured is not None:
                context["on_snapshot_captured"] = self._guard_snapshot_callback(
                    on_snapshot_captured, context["run_id"], generation,
                )
            callback = self.checkpoint_callback
            self._checkpoint_index += 1
            engine.clock["director_checkpoint_id"] = checkpoint.get("checkpoint_id")
            self._apply_fault_requests(checkpoint)
            after_authorization = bool(checkpoint.get("submit_after_authorization"))
            submit = bool(checkpoint.get("submit_analysis")) and callback is not None and not after_authorization
            analysis_status = (
                "awaiting_operator" if after_authorization else
                "submitting" if submit else
                "submission_unavailable" if checkpoint.get("submit_analysis") else
                "not_requested"
            )
            # Publish the concrete checkpoint before releasing the lock. Both
            # synchronous and deferred submissions must write back to this
            # object, never to whatever run/checkpoint happens to be current
            # after a slow gateway round trip.
            reached = {
                "checkpoint_id": checkpoint.get("checkpoint_id"),
                "title": checkpoint.get("title"),
                "reached_at_sec": round(float(engine.clock.get("elapsed_sec", 0) or 0), 3),
                "analysis_status": analysis_status,
                "analysis_after_authorization": after_authorization,
                # Authorization belongs to a concrete reached checkpoint.  Do not
                # start an operator gate merely because the timeline entered the
                # ENGAGE phase before the evidence/analysis checkpoint was ready.
                "requires_operator_action": bool(checkpoint.get("requires_operator_action")),
                "operator_action_type": str(
                    checkpoint.get("operator_action_type") or "fire"
                ),
                "engagement_wave": (
                    int(checkpoint["engagement_wave"])
                    if checkpoint.get("engagement_wave") is not None
                    else None
                ),
            }
            reached["analysis_blocking"] = bool(
                checkpoint.get("block_until_analysis_complete", True)
            )
            self._reached.append(reached)
            self._state["current_checkpoint"] = reached
            self._state["awaiting_analysis"] = analysis_status in {
                "submitting", "submitted", "running",
            } and bool(reached["analysis_blocking"])
            self._state["director_status"] = (
                "awaiting_analysis" if self._state["awaiting_analysis"] else "checkpoint_reached"
            )
            engine.clock["director_status"] = self._state["director_status"]
            engine.clock["director_analysis_status"] = analysis_status
            self._record("checkpoint_reached", checkpoint_id=reached["checkpoint_id"], analysis_status=analysis_status)
        if submit:
            if defer_submission or (
                ((self._scenario or {}).get("demo_controls") or {}).get("elastic_timeline")
                and threading.current_thread() is self._auto_thread
            ):
                threading.Thread(
                    target=self._complete_checkpoint_submission,
                    args=(callback, context, reached, generation),
                    daemon=True, name="amos-checkpoint-submit",
                ).start()
            else:
                self._complete_checkpoint_submission(callback, context, reached, generation)
        return reached

    def _complete_checkpoint_submission(
        self, callback: CheckpointCallback, context: dict[str, Any],
        reached: dict[str, Any], generation: int,
    ) -> None:
        # Network I/O is deliberately outside the director lock.
        try:
            callback_result = callback(context) or {}
            if callback_result.get("workflow_id"):
                analysis_status = "submitted"
            elif callback_result.get("error"):
                analysis_status = "submission_failed"
            else:
                analysis_status = "submission_unverified"
        except Exception as exc:
            analysis_status = "submission_failed"
            callback_result = {"error": f"{type(exc).__name__}: {exc}"}
        with self._lock:
            engine = self.runtime.get_engine()
            if (
                str(self._state.get("run_id") or "") != context["run_id"]
                or str(engine.clock.get("run_id") or "") != context["run_id"]
                or not any(item is reached for item in self._reached)
            ):
                return
            # Keep a late submission discoverable for history polling, but
            # never change a replacement checkpoint or override stop/pause.
            if callback_result:
                reached["submission"] = deepcopy(callback_result)
            reached["analysis_status"] = analysis_status
            if (
                self._state.get("current_checkpoint") is not reached
                or generation != self._auto_generation
            ):
                return
            awaiting = analysis_status in {"submitted", "running"} and bool(
                reached.get("analysis_blocking")
            )
            self._state["awaiting_analysis"] = awaiting
            engine.clock["director_analysis_status"] = analysis_status
            if self._state.get("director_status") in {"checkpoint_reached", "awaiting_analysis"}:
                if analysis_status in self.FAILED_ANALYSIS_STATUSES and reached.get("analysis_blocking", True):
                    self._state["last_error"] = str((callback_result or {}).get("error") or "检查点分析提交失败")
                    self._clear_analysis_motion_limit()
                    engine.pause()
                    self._set_status("error", analysis_status=analysis_status)
                else:
                    self._set_status("awaiting_analysis" if awaiting else "checkpoint_reached")
            self._record("checkpoint_analysis_submitted", checkpoint_id=reached.get("checkpoint_id"), analysis_status=analysis_status)
            self.runtime.record_director_state(self.state())

    def _guard_snapshot_callback(
        self, callback: Callable[[], None], run_id: str, generation: int,
    ) -> Callable[[], None]:
        def resume_current_run() -> None:
            with self._lock:
                if (
                    generation == self._auto_generation
                    and str(self._state.get("run_id") or "") == run_id
                    and str(self.runtime.get_engine().clock.get("run_id") or "") == run_id
                    and self._state.get("director_status") not in {"paused", "stopped"}
                ):
                    callback()
        return resume_current_run

    def _submit_deferred_analysis(
        self, checkpoint: dict[str, Any], *,
        on_snapshot_captured: Callable[[], None] | None = None,
    ) -> bool:
        if not checkpoint.get("analysis_after_authorization"):
            return True
        if checkpoint.get("analysis_status") != "awaiting_operator":
            return checkpoint.get("analysis_status") not in self.FAILED_ANALYSIS_STATUSES
        if self.checkpoint_callback is None:
            checkpoint["analysis_status"] = "submission_unavailable"
            return False
        with self._lock:
            generation = self._auto_generation
            context = {key: self._state[key] for key in ("scenario_id", "run_id", "branch", "seed")}
            context["checkpoint_id"] = checkpoint.get("checkpoint_id")
            if on_snapshot_captured is not None:
                context["on_snapshot_captured"] = self._guard_snapshot_callback(
                    on_snapshot_captured, str(context["run_id"]), generation,
                )
            checkpoint["analysis_status"] = "submitting"
            self._state["awaiting_analysis"] = True
            self._set_status("awaiting_analysis", analysis_status="submitting")
        args = (self.checkpoint_callback, context, checkpoint, generation)
        if (
            ((self._scenario or {}).get("demo_controls") or {}).get("elastic_timeline")
            and threading.current_thread() is self._auto_thread
        ):
            threading.Thread(target=self._complete_checkpoint_submission, args=args, daemon=True,
                             name="amos-authorized-submit").start()
        else:
            self._complete_checkpoint_submission(*args)
        return checkpoint.get("analysis_status") in {"submitting", "submitted"}

    def _current_phase(self) -> str | None:
        engine = self.runtime.get_engine()
        elapsed = float(engine.clock.get("scenario_elapsed_sec", engine.clock.get("elapsed_sec", 0)) or 0)
        branch = str(self._state.get("branch") or "")
        reached = [
            str(item.get("phase") or "").upper()
            for item in (self._scenario or {}).get("timeline") or []
            if self._available_on_branch(item, branch)
            and float(item.get("at_sec", 0) or 0) <= elapsed
        ]
        return reached[-1] if reached else None

    def _has_authorized_engagement(self, *, since_sec: float = 0.0) -> bool:
        # ``reached_at_sec`` is rounded to three decimals while weapon events
        # round their sim_time to two, so an authorization issued at the exact
        # paused gate instant can compare one millisecond "early" and make the
        # director wait forever for an authorization it already has.  Compare
        # at the event's own precision.
        boundary = round(float(since_sec), 2)
        return any(
            isinstance(event, dict)
            and event.get("type") == "authorized_fire_command"
            and event.get("command_source") == "operator"
            and round(float(event.get("sim_time", 0) or 0), 2) >= boundary
            for event in self.runtime.get_engine().events
        )

    def _authorization_gate_required(self) -> bool:
        return self._authorization_stage() in {"warning", "fire"}

    def _authorization_candidate_exists(self) -> bool:
        """Return whether the reached wave has a current operator-visible target."""
        checkpoint = self._state.get("current_checkpoint") or {}
        expected_wave = checkpoint.get("engagement_wave") if isinstance(checkpoint, dict) else None
        for track in self.runtime.get_engine().get_operator_state().get("fused_tracks") or []:
            if track.get("engagement_eligible") is not True:
                continue
            action = track.get("engagement_action") or {}
            if expected_wave is None or int(action.get("wave", 0) or 0) == int(expected_wave):
                return True
        return False

    def _authorization_stage(self) -> str | None:
        policy = (self._scenario or {}).get("engagement_policy") or {}
        checkpoint = self._state.get("current_checkpoint")
        if (
            not policy.get("requires_explicit_authorization")
            or not isinstance(checkpoint, dict)
            or not checkpoint.get("requires_operator_action")
            or str(checkpoint.get("operator_action_type") or "fire") != "fire"
            or self._has_authorized_engagement(
                since_sec=float(checkpoint.get("reached_at_sec", 0) or 0),
            )
        ):
            return None
        analysis_complete = not checkpoint.get("analysis_blocking", True) or checkpoint.get("analysis_status") not in {
            "submitting", "submitted", "running", "backend_unreachable", "failed",
        }
        if checkpoint.get("analysis_after_authorization") and checkpoint.get("analysis_status") == "awaiting_operator":
            analysis_complete = True
        if not policy.get("requires_prior_warning"):
            return "fire" if analysis_complete else "warning_wait"
        engine = self.runtime.get_engine()
        warnings = list(engine._engagement_warnings.values())
        if not warnings:
            return "warning"
        warning = warnings[-1]
        not_before = float(warning.get("fire_not_before_sec", 0) or 0)
        if (
            float(engine.clock.get("elapsed_sec", 0) or 0) < not_before
            or not analysis_complete
        ):
            return "warning_wait"
        return "fire"

    def _set_status(self, status: str, *, analysis_status: str | None = None) -> None:
        self._state["director_status"] = status
        engine = self.runtime.get_engine()
        engine.clock["director_status"] = status
        # Publish the exact authorization stage with the live simulation clock.
        # The browser receives this stream more frequently than director polling,
        # so it must not have to guess whether the pending dialog is WARN or FIRE.
        engine.clock["authorization_stage"] = self._state.get("authorization_stage")
        engine.clock["authorization_not_before_sec"] = self._state.get(
            "authorization_not_before_sec"
        )
        if analysis_status is not None:
            engine.clock["director_analysis_status"] = analysis_status

    def _enter_authorization_wait(self) -> None:
        """Freeze story time while an operator decision is pending.

        Merely reducing playback to 1x lets the timeline cross into ASSESS and
        can make an unexecuted engagement look complete.  Preserve the chosen
        multiplier for later, but pause the simulation until an explicit
        command creates the corresponding event.
        """
        self.runtime.get_engine().lock_speed_for_confirmation("awaiting_authorization")
        self.runtime.get_engine().pause()

    def _leave_authorization_wait(self, *, restore: bool = True) -> None:
        """Release the decision lock and restore the last operator speed."""
        self.runtime.get_engine().unlock_speed_for_confirmation(
            "awaiting_authorization", restore=restore
        )

    def _analysis_progress_enabled(self, checkpoint: dict[str, Any]) -> bool:
        controls = (self._scenario or {}).get("demo_controls") or {}
        return bool(
            controls.get("advance_while_analyzing")
            and checkpoint.get("submit_analysis")
            and checkpoint.get("block_until_analysis_complete", True)
        )

    def _analysis_motion_limit(self) -> float | None:
        engine = self.runtime.get_engine()
        elapsed = float(engine.clock.get("elapsed_sec", 0) or 0)
        current_phase = self._current_phase()
        branch = str(self._state.get("branch") or "")
        boundaries = [
            float(item.get("at_sec", 0) or 0)
            for item in (self._scenario or {}).get("timeline") or []
            if self._available_on_branch(item, branch)
            and float(item.get("at_sec", 0) or 0) > elapsed + 1e-6
            and str(item.get("phase") or "").upper() != current_phase
        ]
        if boundaries:
            return max(elapsed, min(boundaries) - 0.001)
        duration = float(
            ((self._scenario or {}).get("demo_controls") or {}).get("duration_sec", 0)
            or 0
        )
        return duration if duration > elapsed else None

    def _arm_analysis_motion_limit(self) -> None:
        engine = self.runtime.get_engine()
        controls = (self._scenario or {}).get("demo_controls") or {}
        if controls.get("elastic_timeline"):
            if self._analysis_speed_before is None:
                self._analysis_speed_before = float(engine.clock.get("speed", 1) or 1)
            engine.set_director_story_hold(True)
            engine.set_speed(float(controls.get("analysis_speed", 16) or 16))
            self._record("analysis_motion_continues", speed=engine.clock["speed"], elastic_timeline=True)
            return
        engine._director_motion_limit_sec = self._analysis_motion_limit()
        self._record(
            "analysis_motion_continues",
            limit_sec=engine._director_motion_limit_sec,
        )

    def _clear_analysis_motion_limit(self) -> None:
        engine = self.runtime.get_engine()
        engine._director_motion_limit_sec = None
        if self._analysis_speed_before is not None:
            engine.set_director_story_hold(False)
            engine.set_speed(self._analysis_speed_before)
            self._analysis_speed_before = None

    def _poll_current_analysis(self, *, resume_on_success: bool) -> str:
        """Fetch the backend view unlocked, then mutate director state locked.

        The backend round trip (brief while live, full projection at the
        terminal poll) can take seconds.  Holding the director lock across it
        stalls ``/director/state`` and every other lock user for the whole
        gateway call, which the operator sees as the whole scenario freezing
        at every analysis checkpoint.  Only the bookkeeping in
        ``_apply_analysis_view`` is serialized, so ``state()`` still
        deep-copies a consistent checkpoint dict.
        """
        with self._lock:
            checkpoint = self._state.get("current_checkpoint")
            if not isinstance(checkpoint, dict):
                return "none"
            submission = checkpoint.get("submission")
            workflow_id = submission.get("workflow_id") if isinstance(submission, dict) else None
            if not workflow_id or self.workflow_state_callback is None:
                return "manual"
            expected_run_id = str(self._state.get("run_id") or "")
            generation = self._auto_generation
        try:
            try:
                # Light first: while the workflow is live, poll only the small
                # status payload so the completion signal is not queued behind
                # the full UI projection (work-list + trace round-trips).
                view = self.workflow_state_callback(str(workflow_id), light=True)
            except TypeError:
                # Older callback signature: full view on every poll.
                view = self.workflow_state_callback(str(workflow_id))
        except Exception as exc:
            with self._lock:
                if generation != self._auto_generation:
                    return "stale_generation"
                self._mark_analysis_unreachable(
                    checkpoint, exc, expected_run_id=expected_run_id,
                )
            return "pending"
        with self._lock:
            if str(self._state.get("run_id") or "") != expected_run_id:
                return "stale_run"
            if generation != self._auto_generation:
                return "stale_generation"
            return self._apply_analysis_view(
                view,
                checkpoint=checkpoint,
                resume_on_success=resume_on_success,
                expected_run_id=expected_run_id,
            )

    def _mark_analysis_unreachable(
        self, checkpoint: dict[str, Any], exc: Exception, *, expected_run_id: str,
    ) -> None:
        if (
            str(self._state.get("run_id") or "") != expected_run_id
            or str(self.runtime.get_engine().clock.get("run_id") or "") != expected_run_id
        ):
            return
        # The view belongs to the checkpoint captured before the fetch; the
        # monitor may already have moved on to a newer one.
        if self._state.get("current_checkpoint") is not checkpoint:
            return
        checkpoint["analysis_status"] = "backend_unreachable"
        checkpoint["analysis_error"] = f"{type(exc).__name__}: {exc}"
        self._state["last_error"] = checkpoint["analysis_error"]
        blocking = bool(checkpoint.get("analysis_blocking", True))
        self._state["awaiting_analysis"] = blocking
        if (
            blocking
            and not self._state.get("awaiting_authorization")
            and self._state.get("director_status") != "paused"
        ):
            self._set_status("awaiting_analysis", analysis_status="backend_unreachable")

    def _poll_stale_checkpoints(self, run_id: str) -> None:
        """补漏轮询：推进到下一检查点后，回头确认旧检查点的终态。

        后端工作流完成时若导演已经推进，旧检查点的分析状态会永远停在
        submitted/running——前端分析面板的第一项就会一直转圈。这里对历史
        条目做记账式更新，不影响当前运行的状态机。
        """
        if self.workflow_state_callback is None:
            return
        current = self._state.get("current_checkpoint")
        for reached in list(self._reached):
            if reached is current:
                continue
            if reached.get("analysis_status") not in {
                "submitted", "running", "backend_unreachable",
            }:
                continue
            submission = reached.get("submission")
            workflow_id = (
                submission.get("workflow_id") if isinstance(submission, dict) else None
            )
            if not workflow_id:
                continue
            try:
                try:
                    view = self.workflow_state_callback(str(workflow_id), light=True)
                except TypeError:
                    view = self.workflow_state_callback(str(workflow_id))
            except Exception:
                return  # 网关暂时不可达时留到下一轮
            with self._lock:
                if str(self._state.get("run_id") or "") != run_id:
                    return
                self._apply_analysis_view(
                    view,
                    checkpoint=reached,
                    resume_on_success=False,
                    expected_run_id=run_id,
                    bookkeeping_only=True,
                )
                return  # 每轮只补一个，避免占用轮询预算

    def _apply_analysis_view(
        self,
        view: dict[str, Any],
        *,
        checkpoint: dict[str, Any],
        resume_on_success: bool,
        expected_run_id: str,
        bookkeeping_only: bool = False,
    ) -> str:
        # Operate on the checkpoint dict captured before the backend fetch:
        # if the monitor reached a newer checkpoint while the view was in
        # flight, the stale view must never mutate it.
        # ``bookkeeping_only`` 只更新历史检查点自身的分析状态，绝不影响
        # 当前运行的状态机（暂停/恢复/全局错误都不触发）。
        blocking = bool(checkpoint.get("analysis_blocking", True))
        submission = checkpoint.get("submission")
        workflow_id = (
            submission.get("workflow_id") if isinstance(submission, dict) else None
        ) or str(view.get("workflow_id") or "")

        # A backend status request can outlive scenario reset/configuration.
        # Never let its terminal result mutate the replacement run.
        if (
            str(self._state.get("run_id") or "") != expected_run_id
            or str(self.runtime.get_engine().clock.get("run_id") or "") != expected_run_id
        ):
            return "stale_run"
        if not bookkeeping_only and self._state.get("current_checkpoint") is not checkpoint:
            # History is finalized separately by _poll_stale_checkpoints;
            # this response has no authority over the current state machine.
            return "stale_checkpoint"

        workflow_status = str(view.get("status") or "unknown").lower()
        checkpoint["workflow_status"] = workflow_status
        if not view.get("terminal"):
            checkpoint["analysis_status"] = "running" if workflow_status == "running" else "submitted"
            if bookkeeping_only:
                return "pending"
            # A nonblocking checkpoint keeps the demonstration flowing while
            # the agents work; only blocking ones surface awaiting_analysis.
            self._state["awaiting_analysis"] = blocking
            if (
                blocking
                and not self._state.get("awaiting_authorization")
                and self._state.get("director_status") != "paused"
            ):
                self._set_status("awaiting_analysis", analysis_status=checkpoint["analysis_status"])
            return "pending"

        counts = ((view.get("orchestration") or {}).get("counts") or {})
        projection_status = str((view.get("result") or {}).get("projection_status") or "")
        valid = bool(
            workflow_status == "completed"
            and int(counts.get("failed", 0) or 0) == 0
            and int(counts.get("total", 0) or 0) > 0
            and int(counts.get("completed", 0) or 0) == int(counts.get("total", 0) or 0)
            and (view.get("run") or {}).get("current") is not False
            and projection_status not in {"integrity_error", "stale_run"}
        )
        if not valid:
            checkpoint["analysis_status"] = "failed"
            checkpoint["analysis_error"] = (
                (view.get("recovery") or {}).get("reason")
                or f"workflow ended with status {workflow_status}"
            )
            if bookkeeping_only:
                self._record(
                    "checkpoint_analysis_failed",
                    checkpoint_id=checkpoint.get("checkpoint_id"),
                    workflow_id=workflow_id,
                    workflow_status=workflow_status,
                )
                return "failed"
            self._state["last_error"] = checkpoint["analysis_error"]
            self._state["awaiting_analysis"] = False
            self._state["awaiting_authorization"] = False
            self._state["authorization_stage"] = None
            self._clear_analysis_motion_limit()
            self.runtime.get_engine().pause()
            self._leave_authorization_wait()
            # Clear the analysis crawl lock as well: a paused engine must not
            # inherit a lingering 1x lock when the operator resumes later.
            self.runtime.get_engine().unlock_speed_for_confirmation(
                "analysis_motion", restore=True
            )
            self._set_status("error", analysis_status="failed")
            self._record(
                "checkpoint_analysis_failed",
                checkpoint_id=checkpoint.get("checkpoint_id"),
                workflow_id=workflow_id,
                workflow_status=workflow_status,
            )
            self.runtime.record_director_state(self.state())
            return "failed"

        checkpoint["analysis_status"] = "completed"
        checkpoint["analysis_completed_at"] = _now_iso()
        if bookkeeping_only:
            self._record(
                "checkpoint_analysis_completed",
                checkpoint_id=checkpoint.get("checkpoint_id"),
                workflow_id=workflow_id,
            )
            return "completed"
        self._state["awaiting_analysis"] = False
        self._state["last_error"] = None
        self._clear_analysis_motion_limit()
        # Release the analysis crawl lock (if held) so the demo speed is
        # restored; the follow-launch confirmation lock, when separately
        # pending, keeps holding 1x on its own until the operator answers.
        self.runtime.get_engine().unlock_speed_for_confirmation(
            "analysis_motion", restore=True
        )
        authorization_pending = (
            bool(self._state.get("awaiting_authorization"))
            or self._authorization_gate_required()
        )
        operator_paused = self._state.get("director_status") in {"paused", "stopped"}
        can_resume = resume_on_success and not authorization_pending and not operator_paused
        if authorization_pending:
            self.runtime.get_engine().pause()
            next_status = (
                "awaiting_authorization" if self._state.get("awaiting_authorization") else
                "checkpoint_reached"
            )
        else:
            next_status = "auto_running" if can_resume else "paused"
        self._set_status(next_status, analysis_status="completed")
        self._record(
            "checkpoint_analysis_completed",
            checkpoint_id=checkpoint.get("checkpoint_id"),
            workflow_id=workflow_id,
        )
        if can_resume:
            self.runtime.get_engine().resume()
        self.runtime.record_director_state(self.state())
        return "completed"

    def _apply_fault_requests(self, checkpoint: dict[str, Any]) -> None:
        """Apply simulation-side conditions without claiming backend recovery."""
        branch_types = {
            "low_compute": {"compute_constraint"},
            "communication_degraded": {"communication_degradation"},
            "agent_failure": {"agent_unavailable"},
            "low_score_replan": {"low_assessment_score"},
            "resource_unavailable": {"resource_unavailable"},
            "compliance_rejected": {"compliance_rejected"},
            "evidence_insufficient": {"evidence_insufficient"},
        }
        selected_types = branch_types.get(str(self._state.get("branch") or ""), set())
        if not selected_types:
            return
        engine = self.runtime.get_engine()
        already_requested = {
            str(item.get("fault_id")) for item in self._state.get("requested_faults") or []
        }
        for fault in (self._scenario or {}).get("fault_injections") or []:
            fault_id = str(fault.get("fault_id") or "")
            if (
                not fault_id
                or fault_id in already_requested
                or fault.get("type") not in selected_types
                or fault.get("at_checkpoint") != checkpoint.get("checkpoint_id")
            ):
                continue
            record = {
                "fault_id": fault_id,
                "type": fault.get("type"),
                "target": fault.get("target"),
                "requested_at_sec": round(float(engine.clock.get("elapsed_sec", 0) or 0), 3),
                "simulation_status": "injected",
                "backend_status": "unverified",
            }
            if fault.get("type") == "communication_degradation":
                asset = engine.assets.get(str(fault.get("target") or ""))
                if asset is not None:
                    health = asset.setdefault("health", {})
                    health["comms_strength"] = min(float(health.get("comms_strength", 100)), 24.0)
                    asset["_comms_fault_cap"] = 24.0
                else:
                    record["simulation_status"] = "target_unavailable"
            # Agent availability, compute selection and evaluation scores are
            # backend concerns. AMOS records the requested test condition only.
            engine.events.append({
                "type": "simulation_fault_requested",
                "fault_id": fault_id,
                "fault_type": fault.get("type"),
                "target": fault.get("target"),
                "backend_status": "unverified",
                "sim_time": record["requested_at_sec"],
                "timestamp": time.time(),
            })
            self._state["requested_faults"].append(record)
            self._record("fault_requested", fault_id=fault_id, backend_status="unverified")

    def _advance_checkpoint(self) -> dict[str, Any]:
        checkpoint = self._next_checkpoint()
        if checkpoint is None:
            self._set_status("completed")
            return self.state()
        engine = self.runtime.get_engine()
        engine.pause()
        duration = float(((self._scenario or {}).get("demo_controls") or {}).get("duration_sec", 0) or 0)
        max_elapsed = max(duration, float(checkpoint.get("min_elapsed_sec", 0) or 0) + 60.0)
        while not self._checkpoint_satisfied(checkpoint):
            elapsed = float(engine.clock.get("elapsed_sec", 0) or 0)
            if elapsed >= max_elapsed or engine.clock.get("lifecycle") == "completed":
                with self._lock:
                    self._set_status("error")
                    self._state["last_error"] = f"checkpoint conditions not satisfied: {checkpoint.get('checkpoint_id')}"
                    error_message = str(self._state["last_error"])
                    self.runtime.record_director_state(self.state())
                raise DirectorError(error_message)
            # Thirty simulated seconds keeps at least two samples per formal
            # acquisition window while making hour-scale checkpoint advances
            # practical. SimEngine splits the step at every private schedule
            # boundary, so captures and fault windows are never overshot.
            remaining_to_time = max(
                0.0,
                float(checkpoint.get("min_elapsed_sec", 0) or 0) - elapsed,
            )
            engine.step(min(30.0, remaining_to_time or 30.0))
        if checkpoint.get("pause", True):
            engine.pause()
        self._reach_checkpoint(checkpoint)
        return self.state()

    def _auto_monitor(self, generation: int | None = None, run_id: str | None = None) -> None:
        generation = self._auto_generation if generation is None else generation
        run_id = str(self._state.get("run_id") or "") if run_id is None else run_id
        try:
            while not self._auto_stop.wait(0.05):
                if generation != self._auto_generation or str(self._state.get("run_id") or "") != run_id:
                    return
                engine = self.runtime.get_engine()
                if str(engine.clock.get("run_id") or "") != run_id:
                    return
                current = self._state.get("current_checkpoint")
                analysis_pending = False
                if isinstance(current, dict) and current.get("analysis_status") in {
                    "submitting", "submitted", "running", "backend_unreachable",
                }:
                    blocking = bool(current.get("analysis_blocking", True))
                    # Hold the clock until the warning is issued. Its observation
                    # period can then overlap the engagement analysis.
                    if (
                        blocking and not ((self._scenario or {}).get("demo_controls") or {}).get("elastic_timeline")
                        and
                        current.get("requires_operator_action")
                        and not engine._engagement_warnings
                        and engine.clock.get("running")
                    ):
                        engine.pause()
                        self._clear_analysis_motion_limit()
                        self._record("analysis_motion_paused", checkpoint_id=current.get("checkpoint_id"))
                    # Other stages may animate within their current phase,
                    # but must stop at the next phase boundary until analysis
                    # completes. Continuing past that boundary makes later
                    # checkpoints arrive too late for their own time windows.
                    limit = getattr(engine, "_director_motion_limit_sec", None)
                    if (
                        blocking and not ((self._scenario or {}).get("demo_controls") or {}).get("elastic_timeline")
                        and
                        limit is not None
                        and engine.clock.get("running")
                        and float(engine.clock.get("elapsed_sec", 0) or 0)
                        >= float(limit) - 1e-6
                    ):
                        engine.pause()
                        self._clear_analysis_motion_limit()
                        self._record("analysis_motion_paused", limit_sec=float(limit))
                    wait_for_fire_authorization = bool(
                        current.get("requires_operator_action")
                        and str(current.get("operator_action_type") or "fire") == "fire"
                        and (
                            not engine._engagement_warnings
                            or self._state.get("awaiting_authorization")
                        )
                    )
                    now = time.monotonic()
                    should_poll = (
                        threading.current_thread() is not self._auto_thread
                        or now - self._last_analysis_poll_at >= 0.75
                    )
                    if current.get("analysis_status") == "submitting" or not should_poll:
                        result = "pending"
                    else:
                        self._last_analysis_poll_at = now
                        result = self._poll_current_analysis(
                            resume_on_success=not wait_for_fire_authorization
                        )
                    if generation != self._auto_generation or str(self._state.get("run_id") or "") != run_id:
                        return
                    if result.startswith("stale_"):
                        continue
                    if result == "failed":
                        return
                    analysis_pending = blocking and result == "pending"

                now = time.monotonic()
                if now - self._stale_sweep_at >= 2.0:
                    self._stale_sweep_at = now
                    self._poll_stale_checkpoints(run_id)
                if generation != self._auto_generation or str(self._state.get("run_id") or "") != run_id:
                    return

                authorization_stage = self._authorization_stage()
                if authorization_stage is None:
                    self._auth_target_missing_since = 0.0
                if authorization_stage in {"warning", "fire"}:
                    current_checkpoint = self._state.get("current_checkpoint")
                    if (
                        isinstance(current_checkpoint, dict)
                        and current_checkpoint.get("requires_operator_action")
                        and not self._authorization_candidate_exists()
                    ):
                        analysis_status = str(current_checkpoint.get("analysis_status") or "")
                        if analysis_status in {"submitting", "submitted", "running", "backend_unreachable"}:
                            # Missing eligibility is expected until the
                            # relevant workflow has returned and been projected.
                            # Keep the gate paused; wall-clock gateway latency
                            # is not evidence that the scenario has failed.
                            self._auth_target_missing_since = 0.0
                            engine.pause()
                            continue
                        if analysis_status in self.FAILED_ANALYSIS_STATUSES:
                            with self._lock:
                                submission = current_checkpoint.get("submission") or {}
                                self._state["last_error"] = str(
                                    current_checkpoint.get("analysis_error")
                                    or submission.get("error")
                                    or "当前检查点分析未成功完成，请重试分析或重置场景"
                                )
                                self._state["awaiting_authorization"] = False
                                self._state["authorization_stage"] = None
                                self._set_status("error", analysis_status=analysis_status)
                                engine.pause()
                                self.runtime.record_director_state(self.state())
                            return
                        # 开火命令与投影/轮询并发时，"无可用目标"可能只是
                        # 一次过渡态快照（例如事件刚落、资格尚未重算）。
                        # 只有缺失持续一小段时间才判死整个演示。
                        missing_for = now - self._auth_target_missing_since
                        if self._auth_target_missing_since == 0.0 or missing_for < 0.5:
                            if self._auth_target_missing_since == 0.0:
                                self._auth_target_missing_since = now
                            continue
                        with self._lock:
                            self._state["awaiting_authorization"] = False
                            self._state["authorization_stage"] = None
                            self._state["last_error"] = (
                                "后端分析已完成，但当前波次没有满足识别、证据与交战规则的目标；"
                                "请检查投影结果或进入重规划"
                            )
                            self._set_status("error", analysis_status="target_unavailable")
                            engine.pause()
                            self._record(
                                "authorization_target_unavailable",
                                checkpoint_id=(self._state.get("current_checkpoint") or {}).get("checkpoint_id"),
                            )
                            self.runtime.record_director_state(self.state())
                        return
                    self._auth_target_missing_since = 0.0
                    if (
                        not self._state.get("awaiting_authorization")
                        or self._state.get("authorization_stage") != authorization_stage
                    ):
                        with self._lock:
                            self._state["awaiting_authorization"] = True
                            self._state["authorization_stage"] = authorization_stage
                            warnings = list(engine._engagement_warnings.values())
                            self._state["authorization_not_before_sec"] = (
                                warnings[-1].get("fire_not_before_sec") if warnings else None
                            )
                            self._set_status("awaiting_authorization")
                            self._enter_authorization_wait()
                            self._record(
                                "authorization_required",
                                phase="ENGAGE",
                                authorization_stage=authorization_stage,
                            )
                            self.runtime.record_director_state(self.state())
                    continue
                if self._state.get("awaiting_authorization"):
                    checkpoint = self._state.get("current_checkpoint") or {}
                    reached_at = float(checkpoint.get("reached_at_sec", 0) or 0)
                    completed_stage = self._state.get("authorization_stage")
                    completed_by_event = (
                        completed_stage == "fire"
                        and self._has_authorized_engagement(since_sec=reached_at)
                    )
                    warning_issued = (
                        completed_stage == "warning"
                        and authorization_stage == "warning_wait"
                    )
                    # Fail closed: phase changes, completion of the simulation,
                    # or a missing policy must never be interpreted as operator
                    # approval.  Only the explicit command event advances the
                    # director out of the authorization wait.
                    if not completed_by_event and not warning_issued:
                        continue
                    with self._lock:
                        self._state["awaiting_authorization"] = False
                        self._state["authorization_stage"] = (
                            "warning_wait" if authorization_stage == "warning_wait" else None
                        )
                        self._state["authorization_not_before_sec"] = (
                            list(engine._engagement_warnings.values())[-1].get("fire_not_before_sec")
                            if authorization_stage == "warning_wait" and engine._engagement_warnings
                            else None
                        )
                        self._set_status("awaiting_analysis" if analysis_pending else "auto_running")
                        self._leave_authorization_wait()
                        if completed_by_event and checkpoint.get("analysis_after_authorization"):
                            self._arm_analysis_motion_limit()
                            self._record(
                                "authorization_completed",
                                phase="ENGAGE",
                                authorization_stage=completed_stage,
                            )
                            if not self._submit_deferred_analysis(
                                checkpoint, on_snapshot_captured=engine.resume,
                            ):
                                return
                            analysis_pending = True
                        elif analysis_pending:
                            self._arm_analysis_motion_limit()
                        if not completed_by_event or not checkpoint.get("analysis_after_authorization"):
                            self._record(
                                "authorization_completed",
                                phase="ENGAGE",
                                authorization_stage=completed_stage,
                            )
                        if not engine.clock.get("running") and checkpoint.get("analysis_status") != "submitting":
                            engine.resume()
                        self.runtime.record_director_state(self.state())

                current = self._state.get("current_checkpoint")
                if (
                    isinstance(current, dict)
                    and current.get("analysis_after_authorization")
                    and current.get("analysis_status") == "awaiting_operator"
                ):
                    # A direct API fire command may arrive outside the dialog.
                    # It still needs the post-fire backend workflow.
                    if self._has_authorized_engagement(
                        since_sec=float(current.get("reached_at_sec", 0) or 0)
                    ):
                        engine.pause()
                        self._arm_analysis_motion_limit()
                        if not self._submit_deferred_analysis(
                            current, on_snapshot_captured=engine.resume,
                        ):
                            return
                        analysis_pending = True
                        if not engine.clock.get("running") and current.get("analysis_status") != "submitting":
                            engine.resume()
                    else:
                        continue

                # The warning gate is independent of the backend workflow, but
                # reaching the next checkpoint still requires its completion.
                if analysis_pending:
                    continue

                checkpoint = self._next_checkpoint()
                if checkpoint is None:
                    if engine.clock.get("lifecycle") != "completed":
                        if not engine.clock.get("running"):
                            engine.resume()
                        continue
                    with self._lock:
                        self._set_status("completed")
                        self.runtime.record_director_state(self.state())
                    return
                if self._checkpoint_satisfied(checkpoint):
                    continue_during_analysis = self._analysis_progress_enabled(checkpoint)
                    pause_on_reach = bool(checkpoint.get("pause", True))
                    operator_gate = bool(
                        checkpoint.get("requires_operator_action")
                        and str(checkpoint.get("operator_action_type") or "fire") == "fire"
                    )
                    elastic = bool(((self._scenario or {}).get("demo_controls") or {}).get("elastic_timeline"))
                    # The submission callback can take wall-clock seconds.
                    # Freeze simulated time before it captures and submits the
                    # snapshot, then allow motion only under the phase limit.
                    if elastic or (pause_on_reach and not continue_during_analysis):
                        engine.pause(announce=operator_gate)
                    if elastic and continue_during_analysis and not operator_gate:
                        self._arm_analysis_motion_limit()
                    self._reach_checkpoint(
                        checkpoint,
                        defer_submission=bool(checkpoint.get("requires_operator_action")) and not checkpoint.get("submit_after_authorization"),
                        on_snapshot_captured=(
                            (lambda: engine.resume(announce=False))
                            if elastic and continue_during_analysis and not operator_gate
                            else None
                        ),
                    )
                    with self._lock:
                        if generation != self._auto_generation or str(self._state.get("run_id") or "") != run_id:
                            return
                        current = self._state.get("current_checkpoint") or {}
                        if current.get("analysis_status") in self.FAILED_ANALYSIS_STATUSES:
                            self._clear_analysis_motion_limit()
                            engine.pause()
                            self._set_status("error", analysis_status=current.get("analysis_status"))
                            self.runtime.record_director_state(self.state())
                            return
                        if not elastic:
                            if continue_during_analysis and self._state.get("awaiting_analysis"):
                                self._arm_analysis_motion_limit()
                            elif pause_on_reach:
                                engine.pause()
                        elif self._state.get("awaiting_analysis"):
                            if continue_during_analysis and not operator_gate:
                                if not engine.clock.get("running") and current.get("analysis_status") != "submitting":
                                    engine.resume(announce=False)
                        elif not pause_on_reach or (
                            continue_during_analysis and not operator_gate
                        ):
                            engine.resume(announce=False)
                        self.runtime.record_director_state(self.state())
                    continue
                if not engine.clock.get("running"):
                    return
        finally:
            with self._lock:
                if self._auto_thread is threading.current_thread():
                    self._auto_thread = None

    def _stop_auto_thread(self, *, pause: bool) -> None:
        # Serialize cancellation with result application, but never hold the
        # lock while joining a worker that may need it to finish.
        with self._lock:
            self._auto_generation += 1
            generation = self._auto_generation
            self._auto_stop.set()
            thread = self._auto_thread
        if thread and thread is not threading.current_thread():
            thread.join(timeout=1.0)
        with self._lock:
            if generation != self._auto_generation:
                return
            self._auto_thread = None
            if pause:
                self.runtime.get_engine().pause()
            self._leave_authorization_wait()

    def action(self, action: str, *, step_sec: Any = 1.0) -> dict[str, Any]:
        """Apply a director action and return the operator-safe director state."""
        if action not in self.VALID_ACTIONS:
            raise DirectorError(f"unsupported director action: {action}")
        self._ensure_configured()
        engine = self.runtime.get_engine()
        if action in {"start", "step_tick", "advance_checkpoint", "start_auto"}:
            self._ensure_checkpoint_can_advance()
        with self._lock:
            self._state["last_error"] = None
            current = self._state.get("current_checkpoint")
            if (
                action in {"step_tick", "advance_checkpoint", "refresh_analysis"}
                and isinstance(current, dict)
                and current.get("analysis_after_authorization")
                and current.get("analysis_status") == "awaiting_operator"
            ):
                authorized = self._has_authorized_engagement(
                    since_sec=float(current.get("reached_at_sec", 0) or 0)
                )
                if not authorized and action != "refresh_analysis":
                    raise DirectorError("operator fire authorization is required")
                if authorized:
                    engine.pause()
                    if not self._submit_deferred_analysis(current):
                        raise DirectorError(str(self._state.get("last_error") or "checkpoint submission failed"))
        if action == "start":
            engine.start() if engine.clock.get("lifecycle") == "ready" else engine.resume()
            with self._lock:
                self._set_status("running")
                self._state["awaiting_analysis"] = False
        elif action == "pause":
            with self._lock:
                engine.pause()
                self._set_status("paused")
        elif action == "step_tick":
            try:
                value = float(step_sec)
            except (TypeError, ValueError) as exc:
                raise DirectorError("step_sec must be a number") from exc
            current = self._state.get("current_checkpoint")
            if isinstance(current, dict) and current.get("analysis_blocking", True) and current.get("analysis_status") in {
                "submitted", "running", "backend_unreachable",
            }:
                if self._poll_current_analysis(resume_on_success=False) == "pending":
                    raise DirectorError("checkpoint analysis is not completed")
            engine.step(value)
            with self._lock:
                checkpoint = self._next_checkpoint()
                satisfied = checkpoint is not None and self._checkpoint_satisfied(checkpoint)
            if satisfied:
                # Self-locking; the gateway submission inside must not hold
                # the director lock.
                self._reach_checkpoint(checkpoint)
            else:
                with self._lock:
                    self._set_status("paused")
        elif action == "advance_checkpoint":
            with self._lock:
                current = self._state.get("current_checkpoint")
                needs_poll = isinstance(current, dict) and current.get("analysis_status") in {
                    "submitted", "running", "backend_unreachable",
                } and current.get("analysis_blocking", True)
            if needs_poll:
                # The backend poll runs unlocked so concurrent state reads
                # stay responsive; the mutations inside are lock-protected.
                analysis_result = self._poll_current_analysis(resume_on_success=False)
                if analysis_result.startswith("stale_"):
                    raise DirectorError("运行或检查点已变化，请刷新状态后重试")
                if analysis_result == "pending":
                    raise DirectorError("checkpoint analysis is not completed")
                if analysis_result == "failed":
                    raise DirectorError(str(self._state.get("last_error") or "checkpoint analysis failed"))
            # Steps the engine locally and reaches the checkpoint through the
            # self-locking _reach_checkpoint (gateway call outside the lock).
            self._advance_checkpoint()
            with self._lock:
                self._record(action)
            result = self.state()
            result["last_action"] = action
            self.runtime.record_director_state(result)
            return result
        elif action == "refresh_analysis":
            analysis_result = self._poll_current_analysis(
                resume_on_success=False
            )
            with self._lock:
                self._record(action, analysis_poll_result=analysis_result)
            result = self.state()
            result["analysis_poll_result"] = analysis_result
            result["last_action"] = action
            self.runtime.record_director_state(result)
            return result
        elif action == "start_auto":
            if self._auto_thread and self._auto_thread.is_alive():
                raise DirectorError("automatic demonstration is already running")
            self._auto_stop.clear()
            engine.start() if engine.clock.get("lifecycle") == "ready" else engine.resume()
            with self._lock:
                self._set_status("auto_running")
                self._state["awaiting_analysis"] = False
                self._auto_generation += 1
                generation = self._auto_generation
                run_id = str(self._state.get("run_id") or "")
                self._auto_thread = threading.Thread(
                    target=self._auto_monitor,
                    args=(generation, run_id),
                    daemon=True,
                    name="amos-director",
                )
                self._auto_thread.start()
        elif action == "stop_auto":
            self._stop_auto_thread(pause=True)
            with self._lock:
                self._set_status("paused")
        with self._lock:
            self._record(action)
            result = self.state()
            result["last_action"] = action
        self.runtime.record_director_state(result)
        return result

    @staticmethod
    def _checkpoint_summary(checkpoint: dict[str, Any]) -> dict[str, Any]:
        keys = (
            "checkpoint_id", "title", "reached_at_sec", "analysis_status",
            "analysis_error", "analysis_after_authorization", "analysis_blocking",
            "requires_operator_action", "operator_action_type", "engagement_wave",
            "workflow_status", "analysis_completed_at",
        )
        result = {key: deepcopy(checkpoint[key]) for key in keys if key in checkpoint}
        submission = checkpoint.get("submission")
        if isinstance(submission, dict):
            result["submission"] = {
                key: submission[key]
                for key in ("workflow_id", "error") if submission.get(key) is not None
            }
        return result

    def state(self, *, summary: bool = False) -> dict[str, Any]:
        """Return state without unreached checkpoint conditions or future script."""
        with self._lock:
            engine = self.runtime.get_engine()
            if summary:
                payload = {
                    key: deepcopy(value)
                    for key, value in self._state.items()
                    if key != "current_checkpoint"
                }
                current = self._state.get("current_checkpoint")
                payload["current_checkpoint"] = (
                    self._checkpoint_summary(current)
                    if isinstance(current, dict) else None
                )
            else:
                payload = deepcopy(self._state)
            payload["status"] = payload["director_status"]
            payload["elapsed_sec"] = round(float(engine.clock.get("elapsed_sec", 0) or 0), 3)
            payload["scenario_elapsed_sec"] = round(float(engine.clock.get("scenario_elapsed_sec", engine.clock.get("elapsed_sec", 0)) or 0), 3)
            payload["simulation_lifecycle"] = engine.clock.get("lifecycle")
            payload["reached_checkpoints"] = (
                [self._checkpoint_summary(item) for item in self._reached]
                if summary else deepcopy(self._reached)
            )
            payload["action_log"] = deepcopy(self._actions[-10:] if summary else self._actions)
            payload["auto_running"] = bool(self._auto_thread and self._auto_thread.is_alive())
            return payload


__all__ = ["DirectorError", "DirectorService"]
