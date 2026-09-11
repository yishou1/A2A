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
        self._scenario: dict[str, Any] | None = None
        self._checkpoint_index = -1
        self._reached: list[dict[str, Any]] = []
        self._actions: list[dict[str, Any]] = []
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
        elapsed = float(engine.clock.get("elapsed_sec", 0) or 0)
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
        return True

    def _reach_checkpoint(self, checkpoint: dict[str, Any]) -> dict[str, Any]:
        engine = self.runtime.get_engine()
        self._checkpoint_index += 1
        engine.clock["director_checkpoint_id"] = checkpoint.get("checkpoint_id")
        self._apply_fault_requests(checkpoint)
        analysis_status = "not_requested"
        callback_result: dict[str, Any] | None = None
        if checkpoint.get("submit_analysis"):
            analysis_status = "submission_unavailable"
            if self.checkpoint_callback is not None:
                try:
                    callback_result = self.checkpoint_callback({
                        "scenario_id": self._state["scenario_id"],
                        "run_id": self._state["run_id"],
                        "branch": self._state["branch"],
                        "seed": self._state["seed"],
                        "checkpoint_id": checkpoint.get("checkpoint_id"),
                    }) or {}
                    if callback_result.get("workflow_id"):
                        analysis_status = "submitted"
                    elif callback_result.get("error"):
                        analysis_status = "submission_failed"
                    else:
                        analysis_status = "submission_unverified"
                except Exception as exc:  # callback failure is recorded, never converted into success
                    analysis_status = "submission_failed"
                    callback_result = {"error": f"{type(exc).__name__}: {exc}"}
        reached = {
            "checkpoint_id": checkpoint.get("checkpoint_id"),
            "title": checkpoint.get("title"),
            "reached_at_sec": round(float(engine.clock.get("elapsed_sec", 0) or 0), 3),
            "analysis_status": analysis_status,
            # Authorization belongs to a concrete reached checkpoint.  Do not
            # start an operator gate merely because the timeline entered the
            # ENGAGE phase before the evidence/analysis checkpoint was ready.
            "requires_operator_action": bool(checkpoint.get("requires_operator_action")),
        }
        if callback_result:
            reached["submission"] = deepcopy(callback_result)
        self._reached.append(reached)
        self._state["current_checkpoint"] = reached
        self._state["awaiting_analysis"] = analysis_status in {
            "submitted", "running",
        }
        self._state["director_status"] = (
            "awaiting_analysis" if self._state["awaiting_analysis"] else "checkpoint_reached"
        )
        engine.clock["director_status"] = self._state["director_status"]
        engine.clock["director_analysis_status"] = analysis_status
        self._record("checkpoint_reached", checkpoint_id=reached["checkpoint_id"], analysis_status=analysis_status)
        return reached

    def _current_phase(self) -> str | None:
        engine = self.runtime.get_engine()
        elapsed = float(engine.clock.get("elapsed_sec", 0) or 0)
        branch = str(self._state.get("branch") or "")
        reached = [
            str(item.get("phase") or "").upper()
            for item in (self._scenario or {}).get("timeline") or []
            if self._available_on_branch(item, branch)
            and float(item.get("at_sec", 0) or 0) <= elapsed
        ]
        return reached[-1] if reached else None

    def _has_authorized_engagement(self, *, since_sec: float = 0.0) -> bool:
        return any(
            isinstance(event, dict)
            and event.get("type") == "authorized_fire_command"
            and event.get("command_source") == "operator"
            and float(event.get("sim_time", 0) or 0) >= float(since_sec)
            for event in self.runtime.get_engine().events
        )

    def _authorization_gate_required(self) -> bool:
        return self._authorization_stage() in {"warning", "fire"}

    def _authorization_stage(self) -> str | None:
        policy = (self._scenario or {}).get("engagement_policy") or {}
        checkpoint = self._state.get("current_checkpoint")
        if (
            not policy.get("requires_explicit_authorization")
            or self._current_phase() != "ENGAGE"
            or not isinstance(checkpoint, dict)
            or not checkpoint.get("requires_operator_action")
            or self._has_authorized_engagement(
                since_sec=float(checkpoint.get("reached_at_sec", 0) or 0),
            )
        ):
            return None
        if not policy.get("requires_prior_warning"):
            return "fire"
        engine = self.runtime.get_engine()
        warnings = list(engine._engagement_warnings.values())
        if not warnings:
            return "warning"
        warning = warnings[-1]
        not_before = float(warning.get("fire_not_before_sec", 0) or 0)
        if float(engine.clock.get("elapsed_sec", 0) or 0) < not_before:
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
        """Lock playback to real time while an operator decision is pending."""
        self.runtime.get_engine().lock_speed_for_confirmation("awaiting_authorization")

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
        engine._director_motion_limit_sec = self._analysis_motion_limit()
        self._record(
            "analysis_motion_continues",
            limit_sec=engine._director_motion_limit_sec,
        )

    def _clear_analysis_motion_limit(self) -> None:
        self.runtime.get_engine()._director_motion_limit_sec = None

    def _poll_current_analysis(self, *, resume_on_success: bool) -> str:
        checkpoint = self._state.get("current_checkpoint")
        if not isinstance(checkpoint, dict):
            return "none"
        submission = checkpoint.get("submission")
        workflow_id = submission.get("workflow_id") if isinstance(submission, dict) else None
        if not workflow_id or self.workflow_state_callback is None:
            return "manual"
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
            checkpoint["analysis_status"] = "backend_unreachable"
            checkpoint["analysis_error"] = f"{type(exc).__name__}: {exc}"
            self._state["last_error"] = checkpoint["analysis_error"]
            self._state["awaiting_analysis"] = True
            self._set_status("awaiting_analysis", analysis_status="backend_unreachable")
            return "pending"

        workflow_status = str(view.get("status") or "unknown").lower()
        checkpoint["workflow_status"] = workflow_status
        if not view.get("terminal"):
            checkpoint["analysis_status"] = "running" if workflow_status == "running" else "submitted"
            self._state["awaiting_analysis"] = True
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
            self._state["last_error"] = checkpoint["analysis_error"]
            self._state["awaiting_analysis"] = False
            self._clear_analysis_motion_limit()
            self.runtime.get_engine().pause()
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
        self._state["awaiting_analysis"] = False
        self._state["last_error"] = None
        self._clear_analysis_motion_limit()
        # Release the analysis crawl lock (if held) so the demo speed is
        # restored; the follow-launch confirmation lock, when separately
        # pending, keeps holding 1x on its own until the operator answers.
        self.runtime.get_engine().unlock_speed_for_confirmation(
            "analysis_motion", restore=True
        )
        self._set_status("auto_running" if resume_on_success else "paused", analysis_status="completed")
        self._record(
            "checkpoint_analysis_completed",
            checkpoint_id=checkpoint.get("checkpoint_id"),
            workflow_id=workflow_id,
        )
        if resume_on_success:
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
                self._set_status("error")
                self._state["last_error"] = f"checkpoint conditions not satisfied: {checkpoint.get('checkpoint_id')}"
                self.runtime.record_director_state(self.state())
                raise DirectorError(self._state["last_error"])
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

    def _auto_monitor(self) -> None:
        try:
            while not self._auto_stop.wait(0.05):
                engine = self.runtime.get_engine()
                current = self._state.get("current_checkpoint")
                if isinstance(current, dict) and current.get("analysis_status") in {
                    "submitted", "running", "backend_unreachable",
                }:
                    # Analysis still in flight and the story clock has reached
                    # the analysis motion limit: instead of freezing the left
                    # panel in place, drop the hard clamp so the scene keeps
                    # moving while the agents work. With the follow-launch
                    # confirmation dialog pending the demo speed (e.g. 32x)
                    # continues; otherwise playback holds 1x until the
                    # analysis completes (or fails) restores the demo speed.
                    limit = getattr(engine, "_director_motion_limit_sec", None)
                    if (
                        limit is not None
                        and engine.clock.get("running")
                        and float(engine.clock.get("elapsed_sec", 0) or 0)
                        >= float(limit) - 1e-6
                    ):
                        self._clear_analysis_motion_limit()
                        if engine.follow_confirmation_pending():
                            # While the follow-launch confirmation dialog is
                            # open, keep the operator demo speed (e.g. 32x)
                            # during the analysis instead of crawling at 1x.
                            self._record("analysis_motion_continue", limit_sec=float(limit))
                        else:
                            engine.lock_speed_for_confirmation("analysis_motion")
                            self._record("analysis_motion_slowdown", limit_sec=float(limit))
                    result = self._poll_current_analysis(resume_on_success=True)
                    if result in {"pending", "failed"}:
                        if result == "failed":
                            return
                        continue

                authorization_stage = self._authorization_stage()
                if authorization_stage in {"warning", "fire"}:
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
                            if (
                                not engine.clock.get("running")
                                and engine.clock.get("lifecycle") != "completed"
                            ):
                                engine.resume()
                            self._record(
                                "authorization_required",
                                phase="ENGAGE",
                                authorization_stage=authorization_stage,
                            )
                            self.runtime.record_director_state(self.state())
                    continue
                if self._state.get("awaiting_authorization"):
                    with self._lock:
                        completed_stage = self._state.get("authorization_stage")
                        self._state["awaiting_authorization"] = False
                        self._state["authorization_stage"] = (
                            "warning_wait" if authorization_stage == "warning_wait" else None
                        )
                        self._state["authorization_not_before_sec"] = (
                            list(engine._engagement_warnings.values())[-1].get("fire_not_before_sec")
                            if authorization_stage == "warning_wait" and engine._engagement_warnings
                            else None
                        )
                        self._set_status("auto_running")
                        self._leave_authorization_wait()
                        self._record(
                            "authorization_completed",
                            phase="ENGAGE",
                            authorization_stage=completed_stage,
                        )
                        engine.resume()
                        self.runtime.record_director_state(self.state())

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
                    if not continue_during_analysis:
                        engine.pause()
                    with self._lock:
                        self._reach_checkpoint(checkpoint)
                        if continue_during_analysis and self._state.get("awaiting_analysis"):
                            self._arm_analysis_motion_limit()
                        elif continue_during_analysis:
                            engine.pause()
                        self.runtime.record_director_state(self.state())
                    continue
                if not engine.clock.get("running"):
                    return
        finally:
            with self._lock:
                self._auto_thread = None

    def _stop_auto_thread(self, *, pause: bool) -> None:
        self._auto_stop.set()
        thread = self._auto_thread
        if thread and thread is not threading.current_thread():
            thread.join(timeout=1.0)
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
        if action == "start":
            engine.start() if engine.clock.get("lifecycle") == "ready" else engine.resume()
            with self._lock:
                self._set_status("running")
                self._state["awaiting_analysis"] = False
        elif action == "pause":
            engine.pause()
            with self._lock:
                self._set_status("paused")
        elif action == "step_tick":
            try:
                value = float(step_sec)
            except (TypeError, ValueError) as exc:
                raise DirectorError("step_sec must be a number") from exc
            engine.step(value)
            with self._lock:
                checkpoint = self._next_checkpoint()
                if checkpoint and self._checkpoint_satisfied(checkpoint):
                    self._reach_checkpoint(checkpoint)
                else:
                    self._set_status("paused")
        elif action == "advance_checkpoint":
            with self._lock:
                current = self._state.get("current_checkpoint")
                if isinstance(current, dict) and current.get("analysis_status") in {
                    "submitted", "running", "backend_unreachable",
                }:
                    analysis_result = self._poll_current_analysis(resume_on_success=False)
                    if analysis_result == "pending":
                        raise DirectorError("checkpoint analysis is not completed")
                    if analysis_result == "failed":
                        raise DirectorError(str(self._state.get("last_error") or "checkpoint analysis failed"))
                result = self._advance_checkpoint()
                self._record(action)
                result = self.state()
                result["last_action"] = action
                self.runtime.record_director_state(result)
                return result
        elif action == "refresh_analysis":
            with self._lock:
                analysis_result = self._poll_current_analysis(
                    resume_on_success=False
                )
                result = self.state()
                result["analysis_poll_result"] = analysis_result
                result["last_action"] = action
                self._record(action, analysis_poll_result=analysis_result)
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
                self._auto_thread = threading.Thread(target=self._auto_monitor, daemon=True, name="amos-director")
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

    def state(self) -> dict[str, Any]:
        """Return state without unreached checkpoint conditions or future script."""
        with self._lock:
            engine = self.runtime.get_engine()
            payload = deepcopy(self._state)
            payload["status"] = payload["director_status"]
            payload["elapsed_sec"] = round(float(engine.clock.get("elapsed_sec", 0) or 0), 3)
            payload["simulation_lifecycle"] = engine.clock.get("lifecycle")
            payload["reached_checkpoints"] = deepcopy(self._reached)
            payload["action_log"] = deepcopy(self._actions)
            payload["auto_running"] = bool(self._auto_thread and self._auto_thread.is_alive())
            return payload


__all__ = ["DirectorError", "DirectorService"]
