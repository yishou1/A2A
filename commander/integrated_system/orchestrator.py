from __future__ import annotations

import os
import threading
import time
from concurrent.futures import Future, ThreadPoolExecutor
from copy import deepcopy
from pathlib import Path
from typing import Any, Optional

from integrated_system.blackboard import (
    append_trace,
    apply_adjustment,
    create_blackboard,
    latest_result,
    record_result,
)
from integrated_system.branch_adapter import run_branch_capability
from integrated_system.capability_map import CAPABILITY_SEQUENCE, capability_config, preferred_agent
from integrated_system.reporting import build_mission_report
from integrated_system.simulation_executor import simulate_execution
from workflow_state_store import WorkflowStateStore, new_workflow_id, utc_now_iso


class IntegratedDemoOrchestrator:
    def __init__(self, *, state_dir: Optional[str] = None, max_workflows: int = 4):
        default_state_dir = os.path.join(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
            ".a2a_state",
            "integrated_system",
        )
        self.state_store = WorkflowStateStore(state_dir or default_state_dir)
        self.max_workflows = max(1, int(max_workflows))
        self._executor = ThreadPoolExecutor(
            max_workers=self.max_workflows,
            thread_name_prefix="integrated-demo",
        )
        self._lock = threading.RLock()
        self._jobs: dict[str, dict[str, Any]] = {}
        self._controls: dict[str, dict[str, Any]] = {}
        self._closed = False

    def submit_mission(self, mission_request: dict) -> dict:
        workflow_id = new_workflow_id("integrated")
        blackboard = create_blackboard(mission_request, workflow_id)
        job = {
            "workflow_id": workflow_id,
            "status": "queued",
            "submitted_at": utc_now_iso(),
            "started_at": None,
            "finished_at": None,
            "current_capability": None,
            "last_error": None,
            "mission_input": deepcopy(mission_request),
            "blackboard": blackboard,
        }
        control = {
            "paused": False,
            "abort": False,
            "pending_adjustments": [],
        }
        with self._lock:
            if self._closed:
                raise RuntimeError("integrated demo orchestrator is already shut down")
            self._jobs[workflow_id] = job
            self._controls[workflow_id] = control
            self._persist_job(job)
            future = self._executor.submit(self._run_workflow, workflow_id)
            job["future"] = future
        return self.get_mission(workflow_id, include_blackboard=False)

    def list_missions(self) -> list[dict]:
        with self._lock:
            jobs_by_id = {
                workflow_id: self._snapshot(job, include_blackboard=False)
                for workflow_id, job in self._jobs.items()
            }
        for persisted in self._load_persisted_snapshots():
            jobs_by_id.setdefault(persisted["workflow_id"], persisted)
        return sorted(
            jobs_by_id.values(),
            key=lambda item: item.get("submitted_at") or "",
            reverse=True,
        )

    def get_mission(self, workflow_id: str, *, include_blackboard: bool = True) -> dict:
        with self._lock:
            job = self._jobs.get(workflow_id)
            if job is not None:
                return self._snapshot(job, include_blackboard=include_blackboard)
        if self.state_store.exists(workflow_id):
            state = self.state_store.load(workflow_id)
            if not include_blackboard:
                state.pop("blackboard", None)
            return state
        raise KeyError(f"Mission not found: {workflow_id}")

    def get_mission_report(self, workflow_id: str) -> dict:
        with self._lock:
            job = self._jobs.get(workflow_id)
            if job is not None:
                return build_mission_report(self._snapshot(job, include_blackboard=True))
        if self.state_store.exists(workflow_id):
            return build_mission_report(self.state_store.load(workflow_id))
        raise KeyError(f"Mission not found: {workflow_id}")

    def _load_persisted_snapshots(self) -> list[dict]:
        snapshots: list[dict] = []
        state_dir = Path(self.state_store.base_dir)
        for state_path in sorted(state_dir.glob("*.json"), reverse=True):
            try:
                state = self.state_store.load(state_path.stem)
            except Exception:
                continue
            state.pop("blackboard", None)
            snapshots.append(state)
        return snapshots

    def pause_mission(self, workflow_id: str) -> dict:
        return self._set_control(workflow_id, paused=True)

    def resume_mission(self, workflow_id: str) -> dict:
        return self._set_control(workflow_id, paused=False)

    def abort_mission(self, workflow_id: str) -> dict:
        with self._lock:
            control = self._controls.get(workflow_id)
            if control is None:
                raise KeyError(f"Mission not found: {workflow_id}")
            control["abort"] = True
        return self.get_mission(workflow_id, include_blackboard=False)

    def adjust_mission(self, workflow_id: str, adjustment: dict) -> dict:
        with self._lock:
            control = self._controls.get(workflow_id)
            job = self._jobs.get(workflow_id)
            if control is None or job is None:
                raise KeyError(f"Mission not found: {workflow_id}")
            control.setdefault("pending_adjustments", []).append(deepcopy(adjustment))
            append_trace(job["blackboard"], "operator_adjustment_received", adjustment=deepcopy(adjustment))
            self._persist_job(job)
        return self.get_mission(workflow_id, include_blackboard=False)

    def shutdown(self, wait: bool = True) -> None:
        with self._lock:
            self._closed = True
        self._executor.shutdown(wait=wait)

    def _set_control(self, workflow_id: str, *, paused: bool) -> dict:
        with self._lock:
            control = self._controls.get(workflow_id)
            job = self._jobs.get(workflow_id)
            if control is None or job is None:
                raise KeyError(f"Mission not found: {workflow_id}")
            control["paused"] = paused
            append_trace(job["blackboard"], "mission_paused" if paused else "mission_resumed")
            self._persist_job(job)
        return self.get_mission(workflow_id, include_blackboard=False)

    def _snapshot(self, job: dict, *, include_blackboard: bool) -> dict:
        metadata = job["mission_input"].get("metadata", {})
        snapshot = {
            "workflow_id": job["workflow_id"],
            "status": job["status"],
            "submitted_at": job["submitted_at"],
            "started_at": job["started_at"],
            "finished_at": job["finished_at"],
            "current_capability": job["current_capability"],
            "last_error": job["last_error"],
            "state_path": str(self.state_store.state_path(job["workflow_id"])),
            "objective": job["mission_input"].get("objective"),
            "scenario_name": job["mission_input"].get("scenario_name"),
            "display_name": metadata.get("display_name"),
            "template_id": metadata.get("template_id"),
        }
        if include_blackboard:
            snapshot["blackboard"] = deepcopy(job["blackboard"])
        return snapshot

    def _persist_job(self, job: dict) -> None:
        self.state_store.save(job["workflow_id"], self._snapshot(job, include_blackboard=True))

    def _run_workflow(self, workflow_id: str) -> None:
        with self._lock:
            job = self._jobs[workflow_id]
            job["status"] = "running"
            job["started_at"] = utc_now_iso()
            self._persist_job(job)

        try:
            self._execute_workflow(job)
            if job["status"] == "running":
                job["status"] = "completed"
                job["finished_at"] = utc_now_iso()
                self._persist_job(job)
        except Exception as exc:
            with self._lock:
                job["status"] = "failed"
                job["finished_at"] = utc_now_iso()
                job["last_error"] = str(exc)
                append_trace(job["blackboard"], "mission_failed", error=str(exc))
                self._persist_job(job)
            raise

    def _execute_workflow(self, job: dict) -> None:
        blackboard = job["blackboard"]
        mission_input = job["mission_input"]
        success_threshold = float(mission_input.get("success_threshold", 0.6))
        max_replans = int(mission_input.get("max_replans", 1))

        append_trace(blackboard, "mission_started", objective=mission_input.get("objective"))
        cycle = 0
        while True:
            cycle += 1
            append_trace(blackboard, "workflow_cycle_started", cycle=cycle)
            for capability in CAPABILITY_SEQUENCE:
                self._wait_if_paused(job)
                self._abort_if_needed(job)
                self._apply_pending_adjustments(job)
                self._run_capability(job, capability)

            evaluation = latest_result(blackboard, "effect_evaluation")
            score = float(evaluation.get("result", {}).get("overall_score", 0.0))
            if score >= success_threshold:
                append_trace(blackboard, "mission_success_threshold_met", score=score, threshold=success_threshold)
                return
            if blackboard["summary"].get("replan_count", 0) >= max_replans:
                append_trace(blackboard, "mission_replan_budget_exhausted", score=score, threshold=success_threshold)
                return
            blackboard["summary"]["replan_count"] += 1
            append_trace(blackboard, "mission_replan_requested", score=score, threshold=success_threshold)
            self._run_capability(job, "decision_planning", is_replan=True)
            self._run_capability(job, "compliance_authorization")
            self._run_capability(job, "execution_control")
            self._run_capability(job, "effect_evaluation")
            reevaluation = latest_result(blackboard, "effect_evaluation")
            reevaluated_score = float(reevaluation.get("result", {}).get("overall_score", 0.0))
            if reevaluated_score >= success_threshold:
                append_trace(
                    blackboard,
                    "mission_success_threshold_met_after_replan",
                    score=reevaluated_score,
                    threshold=success_threshold,
                )
            return

    def _wait_if_paused(self, job: dict) -> None:
        workflow_id = job["workflow_id"]
        while True:
            with self._lock:
                control = self._controls[workflow_id]
                paused = control.get("paused", False)
                aborted = control.get("abort", False)
            if aborted:
                self._abort_if_needed(job)
            if not paused:
                return
            with self._lock:
                if job["status"] != "paused":
                    job["status"] = "paused"
                    append_trace(job["blackboard"], "mission_waiting_for_resume")
                    self._persist_job(job)
            time.sleep(0.1)
        return

    def _abort_if_needed(self, job: dict) -> None:
        workflow_id = job["workflow_id"]
        with self._lock:
            control = self._controls[workflow_id]
            if not control.get("abort"):
                if job["status"] == "paused":
                    job["status"] = "running"
                    self._persist_job(job)
                return
            job["status"] = "aborted"
            job["finished_at"] = utc_now_iso()
            append_trace(job["blackboard"], "mission_aborted")
            self._persist_job(job)
        raise RuntimeError("Mission aborted by operator")

    def _apply_pending_adjustments(self, job: dict) -> None:
        workflow_id = job["workflow_id"]
        with self._lock:
            control = self._controls[workflow_id]
            pending = list(control.get("pending_adjustments", []))
            control["pending_adjustments"] = []
        if not pending:
            return
        for adjustment in pending:
            apply_adjustment(job["blackboard"], adjustment)
            if adjustment.get("success_threshold") is not None:
                job["mission_input"]["success_threshold"] = float(adjustment["success_threshold"])
            if adjustment.get("additional_constraint"):
                constraints = job["mission_input"].setdefault("constraints", {})
                extra = constraints.setdefault("operator_constraints", [])
                extra.append(adjustment["additional_constraint"])
            if adjustment.get("planning_focus"):
                job["mission_input"].setdefault("metadata", {})["planning_focus"] = adjustment["planning_focus"]
        with self._lock:
            self._persist_job(job)

    def _run_capability(self, job: dict, capability: str, *, is_replan: bool = False) -> None:
        with self._lock:
            job["status"] = "running"
            job["current_capability"] = capability
            append_trace(job["blackboard"], "capability_started", capability=capability, replan=is_replan)
            self._persist_job(job)

        delay_ms = int(job["mission_input"].get("demo_delay_ms", 0) or 0)
        if delay_ms > 0:
            time.sleep(delay_ms / 1000.0)

        envelope = None
        try:
            envelope = run_branch_capability(job["blackboard"], capability)
        except Exception as exc:
            append_trace(
                job["blackboard"],
                "branch_capability_fallback",
                capability=capability,
                error=str(exc),
            )

        if envelope is None:
            if capability == "execution_control":
                envelope = simulate_execution(job["blackboard"])
            else:
                envelope = self._simulate_capability(job["blackboard"], capability, is_replan=is_replan)

        with self._lock:
            record_result(job["blackboard"], capability, envelope)
            job["current_capability"] = None
            self._persist_job(job)

    def _simulate_capability(self, blackboard: dict, capability: str, *, is_replan: bool = False) -> dict:
        config = capability_config(capability)
        agent_name = preferred_agent(capability)
        mission_input = blackboard["mission_input"]
        contacts = mission_input.get("contacts", [])
        platforms = mission_input.get("friendly_platforms", [])
        operator_override = blackboard.get("operator", {}).get("approval_override")

        if capability == "cognition":
            top_contact = contacts[0] if contacts else {"contact_id": "contact-unknown", "intent": "unknown"}
            result = {
                "status": "completed",
                "capability": capability,
                "agent": agent_name,
                "result": {
                    "situation_summary": mission_input.get("intelligence_text")
                    or f"Objective {mission_input.get('objective')} has {len(contacts)} tracked contacts.",
                    "key_entities": [item.get("contact_id") for item in contacts[:3]],
                    "suspected_intent": top_contact.get("intent") or "probing",
                },
                "confidence": 0.8,
                "evidence": ["Mission text and contact list were normalized into a shared situation summary."],
                "warnings": [],
                "next_suggestion": "continue",
            }
            return result

        if capability == "tracking":
            tracks = []
            for index, contact in enumerate(contacts, start=1):
                tracks.append(
                    {
                        "track_id": f"track-{index:03d}",
                        "contact_id": contact.get("contact_id"),
                        "location": contact.get("location"),
                        "track_quality": round(0.6 + min(float(contact.get("threat_level", 0.5)) * 0.3, 0.25), 3),
                    }
                )
            return {
                "status": "completed",
                "capability": capability,
                "agent": agent_name,
                "result": {
                    "tracks": tracks,
                    "track_count": len(tracks),
                    "maintenance_required": any(item["track_quality"] < 0.7 for item in tracks),
                },
                "confidence": 0.78,
                "evidence": ["Contacts were converted into track objects for downstream threat ranking."],
                "warnings": [] if tracks else ["No contacts were available for tracking."],
                "next_suggestion": "continue",
            }

        if capability == "threat_assessment":
            ranked = []
            for contact in contacts:
                base_score = float(contact.get("threat_level", 0.5))
                if contact.get("intent") in {"attack", "strike", "hostile"}:
                    base_score += 0.2
                ranked.append(
                    {
                        "contact_id": contact.get("contact_id"),
                        "priority_score": round(min(base_score, 0.99), 3),
                        "intent": contact.get("intent") or "unknown",
                    }
                )
            ranked.sort(key=lambda item: item["priority_score"], reverse=True)
            return {
                "status": "completed",
                "capability": capability,
                "agent": agent_name,
                "result": {
                    "ranked_threats": ranked,
                    "top_priority": ranked[0]["contact_id"] if ranked else None,
                },
                "confidence": 0.76,
                "evidence": ["Threat scores were estimated from declared threat levels and intent labels."],
                "warnings": [],
                "next_suggestion": "continue",
            }

        if capability == "decision_planning":
            threat = latest_result(blackboard, "threat_assessment").get("result", {}).get("ranked_threats", [])
            top_contact = threat[0]["contact_id"] if threat else "contact-unknown"
            plan_name = "replanned_containment" if is_replan else "coordinated_precision_strike"
            resource_ids = [
                str(
                    platform.get("platform_id")
                    or platform.get("resource_id")
                    or platform.get("name")
                    or f"platform-{index}"
                )
                for index, platform in enumerate(platforms, start=1)
            ]
            candidate_plans = [
                {
                    "id": "PLAN-PRIORITY-MONITOR",
                    "name": "Priority monitoring and reassessment",
                    "status": "recommended",
                    "target_ids": [top_contact],
                    "assigned_resources": resource_ids[: max(1, min(2, len(resource_ids)))],
                    "actions": [
                        "focus available resources on highest-priority targets",
                        "increase observation cadence for selected targets",
                        "reassess risk ranking after the next review window",
                    ],
                    "expected_effects": [
                        "improves confidence on the highest-risk items",
                        "keeps the plan in decision-support mode",
                    ],
                    "score": 86.0 if not is_replan else 82.0,
                    "rationale": "Fallback planner aligned the highest ranked threat with available platforms.",
                    "assumptions": ["highest-risk targets should receive first attention"],
                    "risk_notes": [],
                },
                {
                    "id": "PLAN-BROAD-SURVEILLANCE",
                    "name": "Broad surveillance coverage",
                    "status": "candidate",
                    "target_ids": [item.get("contact_id") for item in threat[:3] if item.get("contact_id")],
                    "assigned_resources": resource_ids,
                    "actions": [
                        "spread available resources across all scheduled targets",
                        "maintain broad-area monitoring continuity",
                        "defer prioritization changes until updated risk evidence arrives",
                    ],
                    "expected_effects": [
                        "maximizes target coverage",
                        "reduces chance of losing lower-priority targets",
                    ],
                    "score": 78.0,
                    "rationale": "Fallback planner favors coverage when all platforms remain available.",
                    "assumptions": ["coverage is preferred over concentrated monitoring"],
                    "risk_notes": [],
                },
                {
                    "id": "PLAN-RESOURCE-SPARING",
                    "name": "Resource-sparing watch",
                    "status": "candidate",
                    "target_ids": [top_contact],
                    "assigned_resources": resource_ids[:1],
                    "actions": [
                        "monitor only the top-priority target with minimum viable resources",
                        "hold remaining resources for follow-up tasking",
                        "escalate to broader coverage if risk increases",
                    ],
                    "expected_effects": [
                        "preserves resource availability",
                        "accepts reduced coverage for lower-priority targets",
                    ],
                    "score": 71.0,
                    "rationale": "Fallback planner keeps spare capacity for later reassignment.",
                    "assumptions": ["resource availability is valuable for follow-up tasking"],
                    "risk_notes": [],
                },
            ]
            return {
                "status": "completed",
                "capability": capability,
                "agent": agent_name,
                "result": {
                    "candidate_plans": candidate_plans,
                    "recommended_plan_id": "PLAN-PRIORITY-MONITOR",
                    "recommended_plan": {
                        "plan_name": plan_name,
                        "target_contact_id": top_contact,
                        "platform_count": len(platforms),
                        "steps": [
                            "stabilize_track",
                            "prioritize_target",
                            "authorize_action",
                            "simulate_execution",
                        ],
                    },
                    "alternatives": [
                        "observe_and_delay",
                        "multi_platform_containment",
                    ],
                },
                "confidence": 0.74 if is_replan else 0.79,
                "evidence": ["Threat ranking and platform readiness were combined into a recommended plan."],
                "warnings": [] if platforms else ["No friendly platforms were supplied; plan is highly abstract."],
                "next_suggestion": "continue",
            }

        if capability == "compliance_authorization":
            require_operator_approval = bool(mission_input.get("require_operator_approval"))
            blocked = require_operator_approval and operator_override is not True
            authorized = not blocked
            warnings = []
            suggestion = "continue"
            if blocked:
                warnings.append("Operator approval is required before execution can continue.")
                suggestion = "operator_review"
            return {
                "status": "completed",
                "capability": capability,
                "agent": agent_name,
                "result": {
                    "authorized": authorized,
                    "rule_checks": [
                        {"rule": "operator_approval", "passed": not blocked},
                        {"rule": "simulation_mode_only", "passed": mission_input.get("simulation_mode") == "safe"},
                    ],
                },
                "confidence": 0.91,
                "evidence": ["Mission approval requirements and simulation boundary were checked."],
                "warnings": warnings,
                "next_suggestion": suggestion,
            }

        if capability == "effect_evaluation":
            execution = latest_result(blackboard, "execution_control").get("result", {})
            simulated_score = float(execution.get("simulated_score", 0.0))
            completion = float(execution.get("completion_ratio", 0.0))
            replan_count = int(blackboard.get("summary", {}).get("replan_count", 0))
            score = round(min(0.97, simulated_score + (0.05 if replan_count > 0 else 0.0)), 3)
            return {
                "status": "completed",
                "capability": capability,
                "agent": agent_name,
                "result": {
                    "overall_score": score,
                    "completion_ratio": completion,
                    "replan_count": replan_count,
                    "assessment": "mission_effective" if score >= 0.6 else "mission_requires_replan",
                },
                "confidence": 0.82,
                "evidence": ["Simulated execution completion and risk reduction were used to estimate mission effect."],
                "warnings": [],
                "next_suggestion": "continue" if score >= 0.6 else "replan",
            }

        return {
            "status": "completed",
            "capability": capability,
            "agent": agent_name,
            "result": {"label": config["label"], "stub": config["stub"]},
            "confidence": 0.6,
            "evidence": ["Capability returned a generic simulated response."],
            "warnings": ["This capability is currently simulated."],
            "next_suggestion": "continue",
        }
