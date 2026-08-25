"""Shared, transport-backed workflow submission for manual and director runs.

The browser route and the server-side director use this service so automatic
demonstrations cannot bypass snapshot freezing, Gateway package verification,
or run/chain checks.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from amos_platform.agents.a2a.workflow_run_store import get_workflow_run_store
from amos_platform.agents.a2a.workflow_view import build_submission_snapshot
from amos_platform.data.scenario_repository import get_scenario


@dataclass
class WorkflowSubmissionError(RuntimeError):
    status_code: int
    message: str
    details: dict[str, Any] | None = None

    def __str__(self) -> str:
        return self.message


def normalize_upstream_error(result: dict[str, Any], fallback: str) -> tuple[int, str]:
    """Normalize an A2A transport error without exposing connection internals."""
    try:
        status = int(result.get("code") or 502)
    except (TypeError, ValueError):
        status = 502
    if status < 400 or status > 599:
        status = 502
    detail = result.get("detail")
    if isinstance(detail, dict):
        message = str(detail.get("message") or detail.get("code") or fallback)
    else:
        message = str(detail or fallback)
    markers = (
        "urlopen error", "connection refused", "timed out",
        "name or service not known", "temporary failure in name resolution",
    )
    if status in {502, 503, 504} and any(marker in message.casefold() for marker in markers):
        message = "分析服务不可达，请检查 Gateway 状态与连接配置"
    return status, message


def submit_current_workflow(
    data: dict[str, Any],
    *,
    engine: Any,
    bridge: Any,
    scenario_support: dict[str, Any],
    run_manifest_store: Any | None = None,
) -> dict[str, Any]:
    """Freeze the active causal state and submit it through the real backend."""
    if data.get("sim_context") is False:
        raise WorkflowSubmissionError(400, "sim_context=false is not supported by the AMOS simulation boundary")

    active_scenario_id = str(engine.clock.get("scenario_id") or "")
    scenario_id = str(data.get("scenario_id") or active_scenario_id or "maritime-convoy-air-defense")
    if active_scenario_id and scenario_id != active_scenario_id:
        raise WorkflowSubmissionError(409, "submitted scenario_id does not match the active simulation")
    scenario = get_scenario(scenario_id)
    if not scenario:
        raise WorkflowSubmissionError(404, f"scenario not found: {scenario_id}")
    if not engine.clock.get("run_id"):
        raise WorkflowSubmissionError(409, "reset or start the simulation before submitting a snapshot")

    try:
        with engine._lock:
            frozen_run_id = str(engine.clock.get("run_id") or "")
            frozen_scenario_id = str(engine.clock.get("scenario_id") or "")
            if frozen_scenario_id != scenario_id:
                raise WorkflowSubmissionError(409, "active simulation changed before snapshot capture")
            mission_payload = bridge.build_workflow_payload(
                scenario,
                scenario_support,
                engine,
                options=data.get("options") or {},
            )
            mission_input = (
                mission_payload.get("mission_input")
                if isinstance(mission_payload.get("mission_input"), dict)
                else {}
            )
            authorization_events = [
                event
                for event in engine.events
                if isinstance(event, dict)
                and event.get("type") == "authorized_fire_command"
                and event.get("command_source") == "operator"
            ]
            if authorization_events:
                latest_authorization = authorization_events[-1]
                stage_transfer = mission_input.setdefault("stage_transfer", {})
                supplemental = stage_transfer.setdefault("supplemental_inputs", {})
                supplemental["operator_authorization"] = {
                    "status": "approved",
                    "source": "operator",
                    "asset_id": latest_authorization.get("asset_id"),
                    "target_track_id": latest_authorization.get("target_track_id"),
                    "weapon_id": latest_authorization.get("weapon_id"),
                }
            engine.exchange.set_submission_context(
                frozen_run_id,
                mission_input.get("stage_transfer") or {},
            )
            backend_payload = bridge.build_backend_submission(
                mission_payload,
                engine=engine,
                overrides=data,
            )
            if str(backend_payload.get("run_id") or frozen_run_id) != frozen_run_id:
                raise WorkflowSubmissionError(409, "snapshot run_id does not match the active simulation")
            exchange_snapshot = engine.exchange.build_snapshot(engine.get_agent_visible_state())
            exchange_events = engine.exchange.events_after(0)
    except WorkflowSubmissionError:
        raise
    except ValueError as exc:
        raise WorkflowSubmissionError(400, str(exc)) from exc

    result = bridge.submit_workflow(backend_payload)
    workflow_id = str(result.get("workflow_id") or "")
    package_verified = False
    if bridge.mode == "gateway" and result.get("package_id"):
        package, checksum_verified = bridge.get_submission_package(
            str(result["package_id"]),
            str(result.get("package_checksum") or ""),
        )
        package_verified = bool(
            checksum_verified
            and str(package.get("run_id") or "") == str(backend_payload.get("run_id") or "")
            and str(package.get("chain_id") or "") == str(backend_payload.get("chain_id") or "")
        )
        if package_verified:
            candidate_snapshot = package.get("snapshot")
            candidate_events = package.get("events")
            if isinstance(candidate_snapshot, dict) and isinstance(candidate_events, list):
                exchange_snapshot = candidate_snapshot
                exchange_events = candidate_events

    submission = build_submission_snapshot(
        mission_payload,
        scenario_id=scenario_id,
        accepted=bool(workflow_id and not result.get("error")),
        transport=bridge.mode,
        backend_request=backend_payload,
        exchange_snapshot=exchange_snapshot,
        exchange_events=exchange_events,
    )
    submission["package"] = ({
        "package_id": result.get("package_id"),
        "checksum": result.get("package_checksum"),
        "event_cursor": result.get("event_cursor"),
        "verified": package_verified,
    } if bridge.mode == "gateway" else None)
    result["amos_submission"] = submission
    if workflow_id:
        get_workflow_run_store().put(workflow_id, submission)
    if run_manifest_store is not None:
        run_manifest_store.record_submission(
            str(submission.get("run_id") or frozen_run_id),
            workflow_id=workflow_id or None,
            submission=submission,
        )

    if result.get("error"):
        status, message = normalize_upstream_error(result, "分析服务拒绝了当前输入快照")
        raise WorkflowSubmissionError(status, message, {"amos_submission": submission, "backend": result})
    if not workflow_id:
        raise WorkflowSubmissionError(
            502,
            "analysis backend did not return workflow_id",
            {"amos_submission": submission, "backend": result},
        )
    return result


__all__ = ["WorkflowSubmissionError", "normalize_upstream_error", "submit_current_workflow"]
