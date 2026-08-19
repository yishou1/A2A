"""Normalize Commander Manager responses for the AMOS operator UI.

Frontend code deliberately does not understand Commander checkpoints, BPEL
work items, or the several historical artifact shapes.  This module exposes a
versioned, presentation-neutral view contract for workflow input, execution,
results, provenance, failures, and recovery.
"""

from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
from typing import Any

from amos_platform.data.operational_catalog import (
    SKILL_ALGORITHM_BINDINGS,
    operational_functions,
)


VIEW_SCHEMA_VERSION = "amos.workflow-view.v2"
TERMINAL_STATES = {"completed", "failed", "error", "cancelled", "aborted"}
DONE_ACTIVITY_STATES = {"completed", "done", "skipped"}
SUCCESS_STATES = {"completed", "done", "success", "succeeded"}
FAILED_STATES = {"failed", "error", "cancelled", "aborted"}
DETAIL_MAX_DEPTH = 5
DETAIL_MAX_LIST = 25
DETAIL_MAX_DICT = 50
DETAIL_MAX_STRING = 2000


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _parse_datetime(value: Any) -> datetime | None:
    if not value:
        return None
    text = str(value)
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        return datetime.fromisoformat(text)
    except ValueError:
        return None


def _duration_between_ms(started_at: Any, finished_at: Any) -> float | None:
    started = _parse_datetime(started_at)
    finished = _parse_datetime(finished_at)
    if not started or not finished:
        return None
    duration = (finished - started).total_seconds() * 1000
    return round(duration, 3) if duration > 0 else None


def build_submission_snapshot(
    payload: dict[str, Any],
    *,
    scenario_id: str,
    accepted: bool,
    transport: str = "commander",
    backend_request: dict[str, Any] | None = None,
    exchange_snapshot: dict[str, Any] | None = None,
    exchange_events: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Build a safe summary of the exact causal payload submitted."""
    attachments = list(payload.get("attachments") or [])
    mission: dict[str, Any] = {}
    if attachments:
        candidate = (attachments[0].get("meta") or {}).get("amos_mission")
        if isinstance(candidate, dict):
            mission = candidate
    if not mission and isinstance(payload.get("mission_input"), dict):
        mission = payload["mission_input"]

    stage_transfer = mission.get("stage_transfer") if isinstance(mission.get("stage_transfer"), dict) else {}
    stage_evidence = {
        str(item.get("media_id")): item
        for item in stage_transfer.get("evidence_items") or []
        if isinstance(item, dict) and item.get("media_id")
    }
    stage_media_ids = {
        str(value)
        for key in ("incremental_media_ids", "context_media_ids")
        for value in stage_transfer.get(key) or []
        if value
    }

    attachment_rows = []
    for item in attachments:
        meta = item.get("meta") if isinstance(item.get("meta"), dict) else {}
        checksum = item.get("checksum") if isinstance(item.get("checksum"), dict) else {}
        attachment_rows.append({
            "id": item.get("id"),
            "name": item.get("name") or item.get("id"),
            "kind": item.get("kind"),
            "mime_type": item.get("mime_type"),
            "sensor_id": meta.get("sensor_id"),
            "modality": meta.get("modality"),
            "captured_at_sim_time": meta.get("captured_at_sim_time"),
            "capture_id": meta.get("capture_id"),
            "product_type": meta.get("product_type"),
            "platform_id": meta.get("platform_id"),
            "observation_ids": list(meta.get("observation_ids") or []),
            "track_ids": list(meta.get("track_ids") or []),
            "checksum": {
                "algorithm": checksum.get("algorithm"),
                "value": checksum.get("value"),
            },
            "uri": item.get("uri"),
        })

    backend_request = backend_request or {}
    exchange_snapshot = exchange_snapshot or {}
    exchange_event_rows = list(exchange_events or exchange_snapshot.get("recent_events") or [])
    if transport == "gateway" and exchange_snapshot:
        media_refs_by_id: dict[str, dict[str, Any]] = {}
        for event in exchange_event_rows:
            if not isinstance(event, dict):
                continue
            for item in event.get("media_refs") or []:
                if isinstance(item, dict) and item.get("media_id"):
                    media_refs_by_id[str(item["media_id"])] = item
        for item in exchange_snapshot.get("media_refs") or []:
            if isinstance(item, dict) and item.get("media_id"):
                media_refs_by_id[str(item["media_id"])] = item
        media_refs = [
            item for media_id, item in media_refs_by_id.items()
            if not stage_transfer or media_id in stage_media_ids
        ]
        attachment_rows = [
            {
                "id": item.get("media_id"),
                "name": item.get("source_name") or item.get("media_id"),
                "kind": "media_ref",
                "mime_type": item.get("mime_type"),
                "sensor_id": (stage_evidence.get(str(item.get("media_id"))) or {}).get("sensor_id") or item.get("source_name"),
                "modality": None,
                "captured_at_sim_time": (stage_evidence.get(str(item.get("media_id"))) or {}).get("captured_at_sim_time"),
                "capture_id": (stage_evidence.get(str(item.get("media_id"))) or {}).get("capture_id"),
                "product_type": (stage_evidence.get(str(item.get("media_id"))) or {}).get("product_type"),
                "platform_id": (stage_evidence.get(str(item.get("media_id"))) or {}).get("platform_id"),
                "observation_ids": list((stage_evidence.get(str(item.get("media_id"))) or {}).get("observation_ids") or []),
                "track_ids": list((stage_evidence.get(str(item.get("media_id"))) or {}).get("track_ids") or []),
                "evidence_role": (stage_evidence.get(str(item.get("media_id"))) or {}).get("evidence_role"),
                "checksum": {"algorithm": "sha256", "value": item.get("checksum")},
                "uri": item.get("uri"),
            }
            for item in media_refs
            if isinstance(item, dict) and item.get("media_id")
        ]
    exchange_tracks = list(exchange_snapshot.get("tracks") or [])
    exchange_observations = list(exchange_snapshot.get("observations") or [])
    exchange_assets = list(exchange_snapshot.get("assets") or [])
    run_id = exchange_snapshot.get("run_id") or (mission.get("metadata") or {}).get("run_id")
    return {
        "captured_at": _utc_now_iso(),
        "accepted": bool(accepted),
        "scenario_id": scenario_id,
        "run_id": run_id,
        "chain_id": backend_request.get("chain_id"),
        "snapshot_sequence": exchange_snapshot.get("sequence") or (mission.get("metadata") or {}).get("snapshot_sequence"),
        "simulation_time_sec": (
            float(exchange_snapshot.get("sim_time_ms", 0)) / 1000.0
            if exchange_snapshot else mission.get("simulation_time_sec")
        ),
        "causal_cutoff_sec": (mission.get("metadata") or {}).get("causal_cutoff_sec"),
        "objective": mission.get("objective") or payload.get("task_goal"),
        "transport": transport,
        "contract": (
            "amos.simulation.snapshot.v1 + amos.simulation.event.v1"
            if transport == "gateway"
            else "commander-manager.attachments+amos_mission.v2"
        ),
        "workflow": payload.get("workflow"),
        "workflow_file": payload.get("workflow_file"),
        "counts": {
            "attachments": len(attachment_rows),
            "contacts": len(exchange_tracks) if exchange_snapshot else len(mission.get("contacts") or []),
            "observations": len(exchange_observations) if exchange_snapshot else len(mission.get("observations") or []),
            "perception_frames": len(mission.get("perception_frames") or []),
            "events": len(exchange_event_rows),
            "friendly_platforms": len(exchange_assets) if exchange_snapshot else len(mission.get("friendly_platforms") or []),
            "protected_assets": len(mission.get("protected_assets") or []),
            "evidence": len(mission.get("evidence") or []),
        },
        "environment": deepcopy(mission.get("environment") or {}),
        "contacts": ([
            {
                "contact_id": item.get("track_id"),
                "kind": item.get("domain_hint"),
                "geo": {"lat": item.get("lat"), "lon": item.get("lon")},
                "source_observation_ids": list(item.get("source_observation_ids") or []),
            }
            for item in exchange_tracks
            if isinstance(item, dict) and item.get("track_id")
        ] if exchange_snapshot else [
            {
                "contact_id": item.get("contact_id"),
                "kind": item.get("kind"),
                "geo": deepcopy(
                    item.get("geo")
                    or (item.get("metadata") or {}).get("geo")
                    or {}
                ),
                "source_observation_ids": list(
                    (item.get("metadata") or {}).get("source_observation_ids") or []
                ),
            }
            for item in mission.get("contacts") or []
            if isinstance(item, dict) and item.get("contact_id")
        ]),
        "attachments": attachment_rows,
        "stage_transfer": deepcopy(stage_transfer),
        # These fields describe the scenario's intended coverage.  They are
        # carried with the frozen submission so the UI can distinguish a plan
        # from evidence returned by Commander.
        "required_agents": deepcopy(
            mission.get("required_agents")
            or (mission.get("metadata") or {}).get("required_agents")
            or payload.get("required_agents")
            or []
        ),
        "functional_agents": deepcopy(
            mission.get("functional_agents")
            or (mission.get("metadata") or {}).get("functional_agents")
            or payload.get("functional_agents")
            or []
        ),
        "algorithm_coverage": deepcopy(
            mission.get("algorithm_coverage")
            or (mission.get("metadata") or {}).get("algorithm_coverage")
            or payload.get("algorithm_coverage")
            or []
        ),
        "function_point_coverage": deepcopy(
            mission.get("function_point_coverage")
            or (mission.get("metadata") or {}).get("function_point_coverage")
            or payload.get("function_point_coverage")
            or []
        ),
        "conditional_function_points": deepcopy(
            mission.get("conditional_function_points")
            or (mission.get("metadata") or {}).get("conditional_function_points")
            or payload.get("conditional_function_points")
            or []
        ),
    }


def _work_items(payload: Any) -> list[dict[str, Any]]:
    if isinstance(payload, dict):
        rows = payload.get("work_list") or payload.get("activities") or []
    else:
        rows = payload if isinstance(payload, list) else []
    return [deepcopy(row) for row in rows if isinstance(row, dict)]


def _trace_items(payload: Any) -> list[dict[str, Any]]:
    rows = payload.get("trace") if isinstance(payload, dict) else payload
    return [deepcopy(row) for row in (rows or []) if isinstance(row, dict)]


def _result_activity_map(status: dict[str, Any]) -> dict[str, dict[str, Any]]:
    result = status.get("result") if isinstance(status.get("result"), dict) else {}
    outputs = result.get("outputs") if isinstance(result.get("outputs"), dict) else {}
    rows = result.get("activity_results") or []
    mapped: dict[str, dict[str, Any]] = {}
    for row in rows:
        if not isinstance(row, dict):
            continue
        row = deepcopy(row)
        if status.get("mode") and "mode" not in row:
            row["mode"] = status.get("mode")
        if "output" not in row:
            output_ref = row.get("output_ref")
            if isinstance(output_ref, str) and output_ref.startswith("outputs."):
                output_key = output_ref.removeprefix("outputs.")
                if output_key in outputs:
                    row["output"] = {output_key: deepcopy(_unwrap_context_value(outputs[output_key]))}
        for key in (row.get("activity_id"), row.get("work_item")):
            if key:
                mapped[str(key)] = row
    return mapped


def _looks_like_local_agent(row: dict[str, Any]) -> bool:
    agent = str(row.get("agent") or row.get("target") or "")
    return bool(
        agent.startswith("Local_")
        or row.get("mode") == "local"
        or row.get("execution_mode") == "local_agent"
    )


def _execution_mode(row: dict[str, Any]) -> str:
    output = row.get("output") if isinstance(row.get("output"), dict) else {}
    meta = output.get("meta") if isinstance(output.get("meta"), dict) else {}
    result = output.get("result") if isinstance(output.get("result"), dict) else {}
    result_meta = result.get("meta") if isinstance(result.get("meta"), dict) else {}
    mode = (
        meta.get("execution_mode")
        or output.get("execution_mode")
        or result_meta.get("execution_mode")
        or result.get("execution_mode")
        or row.get("execution_mode")
    )
    if mode:
        return str(mode)
    if _looks_like_local_agent(row):
        return "local_agent"
    return "unspecified"


def _is_structural_activity(row: dict[str, Any]) -> bool:
    activity_type = str(row.get("type") or "").lower()
    activity_id = str(row.get("activity_id") or row.get("activatity_id") or "")
    work_item = str(row.get("work_item") or "")
    return (
        activity_type in {"sequence", "case", "otherwise"}
        or activity_id.lower().endswith("-sequence")
        or work_item.lower() in {"sequence", "rootsequence", "autorootsequence"}
    )


def _normalize_activities(status: dict[str, Any], work_payload: Any) -> list[dict[str, Any]]:
    result_rows = _result_activity_map(status)
    source_rows = _work_items(work_payload)
    if not source_rows:
        unique: dict[str, dict[str, Any]] = {}
        for row in result_rows.values():
            key = str(row.get("activity_id") or row.get("work_item") or len(unique))
            unique[key] = row
        source_rows = list(unique.values())

    normalized = []
    for index, work in enumerate(source_rows):
        result = result_rows.get(str(work.get("activity_id"))) or result_rows.get(str(work.get("work_item"))) or {}
        merged = {**work, **result}
        if _is_structural_activity(merged):
            continue
        if not merged.get("execution_mode") and status.get("mode") == "local" and merged.get("agent"):
            merged["execution_mode"] = "local_agent"
        metrics = merged.get("metrics") if isinstance(merged.get("metrics"), dict) else {}
        instance_id = merged.get("instance_id") or merged.get("agent_instance_id")
        started_at = merged.get("started_at") or metrics.get("started_at")
        finished_at = merged.get("finished_at") or metrics.get("finished_at")
        duration_ms = metrics.get("duration_ms")
        if _positive_duration_ms(duration_ms) is None:
            duration_ms = _duration_between_ms(started_at, finished_at)
        normalized.append({
            "index": len(normalized) + 1,
            "activity_id": merged.get("activity_id") or merged.get("activatity_id"),
            "work_item": merged.get("work_item"),
            "type": merged.get("type"),
            "role": merged.get("role"),
            "agent": merged.get("agent"),
            "instance_id": instance_id,
            "status": str(merged.get("status") or "pending").lower(),
            "required_skills": list(merged.get("required_skills") or []),
            "function_ids": _explicit_function_points(merged),
            "error": merged.get("error"),
            "duration_ms": duration_ms,
            "started_at": started_at,
            "finished_at": finished_at,
            "execution_mode": _execution_mode(merged),
            "depends_on": [str(item) for item in merged.get("depends_on") or [] if item],
            "retry_count": metrics.get("retry_count"),
            "last_heartbeat": merged.get("last_heartbeat") or metrics.get("last_heartbeat"),
            "is_stub": bool(merged.get("is_stub") or metrics.get("is_stub")),
            "is_mock": bool(merged.get("is_mock") or metrics.get("is_mock")),
        })
    return normalized


def _positive_duration_ms(value: Any) -> float | None:
    try:
        duration = float(value)
    except (TypeError, ValueError):
        return None
    if duration <= 0:
        return None
    return duration


def _apply_observed_activity_durations(
    activities: list[dict[str, Any]],
    algorithms: dict[str, Any],
) -> None:
    """Backfill activity timing from observed algorithm calls when Commander omits it."""
    durations_by_activity: dict[str, float] = {}
    for algorithm in algorithms.get("items") or []:
        duration = _positive_duration_ms(algorithm.get("duration_ms"))
        if duration is None:
            continue
        activity_refs = [
            str(ref).removeprefix("activity:")
            for ref in algorithm.get("evidence_refs") or []
            if str(ref).startswith("activity:")
        ]
        for activity_ref in activity_refs:
            durations_by_activity[activity_ref] = durations_by_activity.get(activity_ref, 0.0) + duration

    for activity in activities:
        if _positive_duration_ms(activity.get("duration_ms")) is not None:
            continue
        aliases = [
            str(value)
            for value in (activity.get("activity_id"), activity.get("work_item"))
            if value
        ]
        observed = sum(durations_by_activity.get(alias, 0.0) for alias in aliases)
        if observed > 0:
            activity["duration_ms"] = round(observed, 3)


def _scalar_facts(value: Any, *, depth: int = 0) -> list[dict[str, Any]]:
    """Extract a compact set of useful output facts without exposing raw blobs."""
    if depth > 4 or not isinstance(value, dict):
        return []
    preferred = (
        "track_count", "group_count", "protected_asset_count", "asset_impact_count",
        "overall_score", "completion_ratio", "assessment", "decision", "status",
        "risk_score", "priority_score", "score", "command_count", "frames_processed",
    )
    facts = []
    for key in preferred:
        item = value.get(key)
        if isinstance(item, (str, int, float, bool)) and item not in ("", None):
            facts.append({"key": key, "value": item})
    for key in (
        "tracks", "groups", "threats", "ranked_threats", "unified_threat_ranking",
        "asset_impacts", "scheduled_tasks", "resources", "sensor_assignments",
        "candidate_plans", "commands",
    ):
        item = value.get(key)
        if isinstance(item, list):
            facts.append({"key": f"{key}_count", "value": len(item)})
    if facts:
        return facts[:8]
    if len(value) == 1:
        nested = next(iter(value.values()))
        if isinstance(nested, dict):
            facts.extend(_scalar_facts(nested, depth=depth + 1))
            if facts:
                return facts[:8]
    for key in ("artifact", "result", "data", "output", "summary"):
        nested = value.get(key)
        if isinstance(nested, dict):
            facts.extend(_scalar_facts(nested, depth=depth + 1))
    return facts[:8]


def _safe_explicit_summary(value: Any) -> Any:
    if isinstance(value, (str, int, float, bool)):
        return deepcopy(value)
    if not isinstance(value, list):
        return None
    safe_items = []
    for item in value[:8]:
        if isinstance(item, (str, int, float, bool)):
            safe_items.append(deepcopy(item))
        elif isinstance(item, dict) and isinstance(item.get("key"), str) \
                and isinstance(item.get("value"), (str, int, float, bool)):
            safe_items.append({"key": item["key"], "value": deepcopy(item["value"])})
    return safe_items or None


def _context_key_for_variable(variable_name: Any) -> str:
    return {
        "MissionInput": "mission_input",
        "CognitionResult": "cognition_result",
        "TrackingResult": "tracking_result",
        "ThreatAssessmentResult": "threat_assessment_result",
        "TaskSchedulingResult": "task_scheduling_result",
        "PlanningInput": "planning_input",
        "DecisionPlanningResult": "decision_planning_result",
        "ComplianceAuthorizationResult": "compliance_authorization_result",
        "ExecutionSimulationResult": "execution_simulation_result",
        "EffectEvaluationResult": "effect_evaluation_result",
    }.get(str(variable_name or ""), str(variable_name or ""))


def _result_outputs(status: dict[str, Any]) -> dict[str, Any]:
    result = status.get("result") if isinstance(status.get("result"), dict) else {}
    return result.get("outputs") if isinstance(result.get("outputs"), dict) else {}


def _unwrap_context_value(value: Any) -> Any:
    if isinstance(value, list):
        for item in reversed(value):
            found = _unwrap_context_value(item)
            if found not in (None, {}, []):
                return found
        return None
    if not isinstance(value, dict):
        return value
    if "value" in value:
        return _unwrap_context_value(value["value"])
    if "output" in value:
        return _unwrap_context_value(value["output"])
    if "artifact" in value:
        return _unwrap_context_value(value["artifact"])
    return value


def _safe_detail_value(value: Any, *, depth: int = 0) -> Any:
    if depth > DETAIL_MAX_DEPTH:
        return {"_truncated": "max_depth"}
    if isinstance(value, (int, float, bool)) or value is None:
        return deepcopy(value)
    if isinstance(value, str):
        if len(value) > DETAIL_MAX_STRING:
            return value[:DETAIL_MAX_STRING] + "...[truncated]"
        return value
    if isinstance(value, list):
        rows = [
            _safe_detail_value(item, depth=depth + 1)
            for item in value[:DETAIL_MAX_LIST]
        ]
        if len(value) > DETAIL_MAX_LIST:
            rows.append({"_truncated_count": len(value) - DETAIL_MAX_LIST})
        return rows
    if isinstance(value, dict):
        result: dict[str, Any] = {}
        for index, (key, item) in enumerate(value.items()):
            if index >= DETAIL_MAX_DICT:
                result["_truncated_count"] = len(value) - DETAIL_MAX_DICT
                break
            key_text = str(key)
            lower_key = key_text.lower()
            if lower_key in {"content", "raw", "raw_bytes", "image", "image_data", "data_uri"}:
                result[key_text] = _summarize_opaque_value(item)
            else:
                result[key_text] = _safe_detail_value(item, depth=depth + 1)
        return result
    return str(value)


def _summarize_opaque_value(value: Any) -> Any:
    if isinstance(value, str):
        return {"type": "string", "length": len(value), "preview": value[:160]}
    if isinstance(value, (bytes, bytearray)):
        return {"type": "bytes", "length": len(value)}
    if isinstance(value, list):
        return {"type": "list", "count": len(value)}
    if isinstance(value, dict):
        return {"type": "object", "keys": list(value.keys())[:12]}
    return _safe_detail_value(value, depth=DETAIL_MAX_DEPTH)


def _detail_map(summary: Any, value: Any, *, variable: str | None = None, source: str | None = None) -> dict[str, Any]:
    detail = {
        "__all__": {
            "variable": variable,
            "source": source,
            "value": _safe_detail_value(value),
        }
    }
    if isinstance(summary, list):
        for item in summary:
            if not isinstance(item, dict) or not item.get("key"):
                continue
            key = str(item["key"])
            selected = None
            if isinstance(value, dict):
                if key in value:
                    selected = value[key]
                elif key.endswith("_count") and key[:-6] in value:
                    selected = value[key[:-6]]
            if selected is None:
                selected = item.get("value")
            detail[key] = {
                "variable": variable,
                "source": source,
                "key": key,
                "value": _safe_detail_value(selected),
            }
    return detail


def _submission_input_detail(submission: dict[str, Any], summary: Any) -> dict[str, Any]:
    value = {
        "counts": deepcopy(submission.get("counts") or {}),
        "stage_transfer": deepcopy(submission.get("stage_transfer") or {}),
        "contacts": deepcopy(submission.get("contacts") or []),
        "attachments": deepcopy(submission.get("attachments") or []),
        "environment": deepcopy(submission.get("environment") or {}),
    }
    return _detail_map(summary, value, variable="MissionInput", source="mission_input")


def _variable_detail(
    variable_name: Any,
    summary: Any,
    *,
    status: dict[str, Any],
    submission: dict[str, Any],
) -> dict[str, Any]:
    variable = str(variable_name or "")
    key = _context_key_for_variable(variable)
    if key == "mission_input":
        return _submission_input_detail(submission, summary)
    value = _unwrap_context_value(_result_outputs(status).get(key))
    if value is None:
        value = {}
    return _detail_map(summary, value, variable=variable or None, source=key)


def _submission_input_summary(submission: dict[str, Any]) -> list[dict[str, Any]]:
    counts = submission.get("counts") if isinstance(submission.get("counts"), dict) else {}
    rows = [
        {"key": "contacts", "value": counts.get("contacts", len(submission.get("contacts") or []))},
        {"key": "observations", "value": counts.get("observations")},
        {"key": "attachments", "value": counts.get("attachments", len(submission.get("attachments") or []))},
        {"key": "events", "value": counts.get("events")},
    ]
    stage = submission.get("stage_transfer") if isinstance(submission.get("stage_transfer"), dict) else {}
    phase = stage.get("phase")
    if phase:
        rows.insert(0, {"key": "phase", "value": phase})
    required = [
        f"{item.get('data_group')}={item.get('count')}"
        for item in stage.get("required_inputs") or []
        if isinstance(item, dict) and item.get("data_group")
    ]
    if required:
        rows.append({"key": "required_inputs", "value": ", ".join(required[:6])})
    return [row for row in rows if row.get("value") not in (None, "", [])][:8]


def _variable_summary(
    variable_name: Any,
    *,
    status: dict[str, Any],
    submission: dict[str, Any],
) -> list[dict[str, Any]]:
    variable = str(variable_name or "")
    if not variable:
        return []
    key = _context_key_for_variable(variable)
    if key == "mission_input":
        rows = _submission_input_summary(submission)
        return ([{"key": "variable", "value": variable}] + rows)[:8]
    value = _unwrap_context_value(_result_outputs(status).get(key))
    facts = _scalar_facts(value if isinstance(value, dict) else {})
    if facts:
        return ([{"key": "variable", "value": variable}] + facts)[:8]
    if isinstance(value, list):
        return [{"key": "variable", "value": variable}, {"key": f"{key}_count", "value": len(value)}]
    if isinstance(value, (str, int, float, bool)):
        return [{"key": "variable", "value": variable}, {"key": key, "value": value}]
    return [{"key": "variable", "value": variable}, {"key": "source", "value": key}]


def _output_cards(status: dict[str, Any]) -> list[dict[str, Any]]:
    result = status.get("result") if isinstance(status.get("result"), dict) else {}
    cards = []
    for index, row in enumerate(result.get("activity_results") or []):
        if not isinstance(row, dict):
            continue
        output = row.get("output") if isinstance(row.get("output"), dict) else {}
        cards.append({
            "index": index + 1,
            "activity_id": row.get("activity_id"),
            "work_item": row.get("work_item"),
            "role": row.get("role"),
            "agent": row.get("agent"),
            "status": row.get("status"),
            "execution_mode": _execution_mode(row),
            "facts": _scalar_facts(output),
            "error": row.get("error"),
        })
    return cards


def _normalize_trace(payload: Any) -> list[dict[str, Any]]:
    rows = _trace_items(payload)[-30:]
    normalized = []
    for row in rows:
        event = row.get("event") or row.get("event_type") or row.get("type") or "trace"
        normalized.append({
            "timestamp": row.get("timestamp") or row.get("time"),
            "event": event,
            "activity_id": row.get("activity_id") or row.get("activatity_id"),
            "role": row.get("role"),
            "agent": row.get("agent") or row.get("target"),
            "instance_id": row.get("instance_id") or row.get("agent_instance_id"),
            "message": row.get("message") or row.get("error") or row.get("status"),
            "attempt": row.get("attempt"),
            "execution_mode": row.get("execution_mode"),
            "is_stub": bool(row.get("is_stub")),
            "is_mock": bool(row.get("is_mock")),
            "dependencies": [str(item) for item in row.get("dependencies") or [] if item],
            "child_activity_id": row.get("child_activity_id"),
            "last_heartbeat": row.get("last_heartbeat") or row.get("heartbeat_at"),
        })
    return normalized


def _coverage_rows(value: Any, *, kind: str) -> list[dict[str, Any]]:
    """Normalize scenario declarations without treating them as execution evidence."""
    rows: list[dict[str, Any]] = []

    def append(item: Any, category: str | None = None) -> None:
        if isinstance(item, str):
            source = {"name": item}
        elif isinstance(item, dict):
            source = item
        else:
            return
        if kind == "algorithm":
            item_id = (
                source.get("algorithm_id")
                or source.get("id")
                or source.get("model_id")
                or source.get("name")
            )
        else:
            item_id = (
                source.get("function_point_id")
                or source.get("function_id")
                or source.get("id")
                or source.get("code")
                or source.get("name")
            )
        if not item_id:
            return
        rows.append({
            "id": str(item_id),
            "requirement_id": source.get("requirement_id"),
            "name": source.get("name") or str(item_id),
            "category": (
                source.get("category")
                or source.get("algorithm_category")
                or source.get("phase")
                or category
            ),
            "model_id": source.get("model_id"),
            "version": source.get("version") or source.get("model_version"),
            "agent": source.get("agent") or source.get("agent_role"),
            "assigned_agents": [str(row) for row in source.get("assigned_agents") or [] if row],
            "tier": source.get("tier"),
            "function_points": [str(row) for row in source.get("function_points") or [] if row],
            "activity_ids": [
                str(row)
                for row in (
                    source.get("activity_ids")
                    or source.get("activities")
                    or ([source.get("activity_id")] if source.get("activity_id") else [])
                )
                if row
            ],
        })

    if isinstance(value, list):
        for item in value:
            append(item)
    elif isinstance(value, dict):
        descriptor_keys = {
            "id", "name", "algorithm_id", "model_id", "function_point_id", "function_id", "code"
        }
        if descriptor_keys.intersection(value):
            append(value)
        else:
            for category, items in value.items():
                if isinstance(items, list):
                    for item in items:
                        append(item, str(category))
                else:
                    append(items, str(category))
    return rows


def _algorithm_evidence(value: Any, *, depth: int = 0, category: str | None = None) -> list[dict[str, Any]]:
    """Extract only algorithm/model identifiers explicitly returned by Commander."""
    if depth > 7:
        return []
    records: list[dict[str, Any]] = []
    if isinstance(value, list):
        for item in value:
            records.extend(_algorithm_evidence(item, depth=depth + 1, category=category))
        return records
    if not isinstance(value, dict):
        return records

    algorithm_id = value.get("algorithm_id")
    model_id = value.get("model_id")
    if algorithm_id or model_id:
        records.append({
            "id": str(algorithm_id or model_id),
            "name": value.get("algorithm_name") or value.get("name") or str(algorithm_id or model_id),
            "category": value.get("algorithm_category") or value.get("category") or category,
            "model_id": model_id,
            "version": value.get("model_version") or value.get("version"),
            "params": value.get("params") or value.get("parameter_count"),
            "flops": value.get("flops"),
            "execution_mode": value.get("execution_mode"),
            "duration_ms": value.get("duration_ms"),
            "input_summary": value.get("input_summary"),
            "result_summary": value.get("result_summary") or value.get("summary"),
            "backend_type": value.get("backend_type"),
        })

    selected = value.get("selected_algorithms")
    if isinstance(selected, list):
        for item in selected:
            if isinstance(item, str):
                records.append({"id": item, "name": item, "category": category})
            elif isinstance(item, dict):
                records.extend(_algorithm_evidence(item, depth=depth + 1, category=category))

    algorithm_library = value.get("algorithm_library")
    if isinstance(algorithm_library, dict):
        for library_id, library_evidence in algorithm_library.items():
            if not isinstance(library_evidence, dict) or library_evidence.get("used") is not True:
                continue
            records.append({
                "id": str(library_id),
                "name": str(library_id),
                "category": "algorithm_library",
                "version": library_evidence.get("version"),
            })

    catalog = value.get("algorithm_catalog")
    if isinstance(catalog, dict):
        for catalog_category, items in catalog.items():
            if not isinstance(items, list):
                continue
            for item in items:
                if isinstance(item, str):
                    records.append({
                        "id": item,
                        "name": item,
                        "category": str(catalog_category),
                    })
                elif isinstance(item, dict):
                    records.extend(
                        _algorithm_evidence(item, depth=depth + 1, category=str(catalog_category))
                    )

    skipped = {"selected_algorithms", "algorithm_catalog", "algorithm_library"}
    for key, item in value.items():
        if key in skipped:
            continue
        if isinstance(item, (dict, list)):
            records.extend(_algorithm_evidence(item, depth=depth + 1, category=category))
    return records


def _explicit_function_points(value: Any, *, depth: int = 0) -> list[str]:
    if depth > 7:
        return []
    found: list[str] = []
    if isinstance(value, list):
        for item in value:
            if isinstance(item, (dict, list)):
                found.extend(_explicit_function_points(item, depth=depth + 1))
        return found
    if not isinstance(value, dict):
        return found
    for key in ("function_point_id", "function_id"):
        if value.get(key):
            found.append(str(value[key]))
    for key in ("function_points", "function_point_ids"):
        values = value.get(key)
        if isinstance(values, list):
            for item in values:
                if isinstance(item, str):
                    found.append(item)
                elif isinstance(item, dict):
                    candidate = item.get("function_point_id") or item.get("id") or item.get("name")
                    if candidate:
                        found.append(str(candidate))
    explicit_keys = {"function_point_id", "function_id", "function_points", "function_point_ids"}
    for key, item in value.items():
        if key in explicit_keys:
            continue
        if isinstance(item, (dict, list)):
            found.extend(_explicit_function_points(item, depth=depth + 1))
    return list(dict.fromkeys(found))


def _status_rank(status: str) -> int:
    return {
        "failed": 5,
        "error": 5,
        "running": 4,
        "completed": 3,
        "done": 3,
        "queued": 2,
        "pending": 1,
    }.get(status, 0)


def _role_key(value: Any) -> str:
    return str(value or "").strip().casefold().replace("-", "_").replace(" ", "_")


def _agent_instance_key(agent: Any, role: Any, mode: Any) -> str | None:
    agent_text = str(agent or "").strip()
    role_text = str(role or "").strip()
    if not agent_text and not role_text:
        return None
    mode_text = str(mode or "unspecified").strip().casefold()
    source = agent_text or role_text
    slug = _role_key(source)
    prefix = "local" if mode_text == "local_agent" or agent_text.startswith("Local_") else "runtime"
    return f"{prefix}:{slug}"


def _build_agent_view(
    activities: list[dict[str, Any]],
    trace_rows: list[dict[str, Any]],
    submission: dict[str, Any],
) -> dict[str, Any]:
    roles: dict[str, dict[str, Any]] = {}
    instances: dict[str, dict[str, Any]] = {}
    functional_agents = []
    for declaration in submission.get("functional_agents") or []:
        if not isinstance(declaration, dict) or not declaration.get("agent_id"):
            continue
        functional_agents.append({
            "agent_id": str(declaration["agent_id"]),
            "name": declaration.get("name") or declaration["agent_id"],
            "responsibilities": list(declaration.get("responsibilities") or []),
            "backend_roles": list(declaration.get("backend_roles") or []),
            "activity_ids": list(declaration.get("activity_ids") or []),
            "status": "declared",
        })

    for declaration in submission.get("required_agents") or []:
        if isinstance(declaration, str):
            declaration = {"role": declaration}
        if not isinstance(declaration, dict) or not declaration.get("role"):
            continue
        role = str(declaration["role"])
        roles[_role_key(role)] = {
            "role": role,
            "status": "declared",
            "activity_count": 0,
            "call_count": 0,
            "required": bool(declaration.get("required", True)),
            "instance_policy": declaration.get("instance_policy"),
        }

    for activity in activities:
        role = str(activity.get("role") or "")
        if role:
            role_key = _role_key(role)
            role_row = roles.setdefault(role_key, {
                "role": role,
                "status": "pending",
                "activity_count": 0,
                "call_count": 0,
                "required": None,
                "instance_policy": None,
            })
            role_row["activity_count"] += 1
            if _status_rank(str(activity.get("status") or "")) > _status_rank(role_row["status"]):
                role_row["status"] = activity.get("status")

        mode = str(activity.get("execution_mode") or "unspecified")
        agent_name = str(activity.get("agent") or "")
        instance_id = activity.get("instance_id") or _agent_instance_key(agent_name, role, mode)
        if not instance_id:
            continue
        instance_key = str(instance_id)
        is_stub = bool(activity.get("is_stub")) or mode in {"stub", "simulation_executor"}
        is_mock = bool(activity.get("is_mock")) or "mock" in mode.lower() or mode == "simulated_adapter"
        row = instances.setdefault(instance_key, {
            "instance_id": instance_key,
            "agent": agent_name or None,
            "role": role or None,
            "status": activity.get("status") or "unknown",
            "execution_mode": mode,
            "is_stub": is_stub,
            "is_mock": is_mock,
            "real_service": not is_stub and not is_mock,
            "activity_count": 0,
            "call_count": 0,
            "duration_ms": 0.0,
            "retry_count": 0,
            "last_heartbeat": activity.get("last_heartbeat"),
        })
        row["activity_count"] += 1
        if activity.get("duration_ms") is not None:
            row["duration_ms"] += float(activity["duration_ms"])
        if activity.get("retry_count") is not None:
            row["retry_count"] += int(activity["retry_count"])
        if _status_rank(str(activity.get("status") or "")) > _status_rank(str(row["status"])):
            row["status"] = activity.get("status")

    for event in trace_rows:
        event_name = str(event.get("event") or "")
        if not event_name.startswith(("agent_call_", "local_agent_call_")):
            continue
        role = str(event.get("role") or "")
        if role:
            role_row = roles.setdefault(_role_key(role), {
                "role": role,
                "status": "unknown",
                "activity_count": 0,
                "call_count": 0,
                "required": None,
                "instance_policy": None,
            })
            if event_name in {"agent_call_attempt", "local_agent_call_completed"}:
                role_row["call_count"] += 1
        event_mode = str(event.get("execution_mode") or "unspecified")
        if event_mode == "unspecified" and str(event_name).startswith("local_agent_call_"):
            event_mode = "local_agent"
        instance_id = event.get("instance_id") or _agent_instance_key(event.get("agent"), role, event_mode)
        if instance_id:
            instance_key = str(instance_id)
            mode = event_mode
            is_stub = bool(event.get("is_stub")) or mode in {"stub", "simulation_executor"}
            is_mock = bool(event.get("is_mock")) or "mock" in mode.lower() or mode == "simulated_adapter"
            instance = instances.setdefault(instance_key, {
                "instance_id": instance_key,
                "agent": event.get("agent"),
                "role": role or None,
                "status": "unknown",
                "execution_mode": mode,
                "is_stub": is_stub,
                "is_mock": is_mock,
                "real_service": not is_stub and not is_mock,
                "activity_count": 0,
                "call_count": 0,
                "duration_ms": 0.0,
                "retry_count": 0,
                "last_heartbeat": None,
            })
            if event_name in {"agent_call_attempt", "local_agent_call_completed"}:
                instance["call_count"] += 1
            if event.get("last_heartbeat"):
                instance["last_heartbeat"] = event["last_heartbeat"]
            event_status = (
                "running" if event_name == "agent_call_attempt"
                else "completed" if event_name in {"agent_call_completed", "local_agent_call_completed"}
                else "failed" if event_name in {"agent_call_failed", "local_agent_call_failed"}
                else "unknown"
            )
            if event_status != "unknown":
                instance["status"] = event_status

    instance_rows = list(instances.values())
    for row in instance_rows:
        row["duration_ms"] = round(row["duration_ms"], 3) if row["duration_ms"] else None
    role_rows = list(roles.values())
    return {
        "counts": {
            "workflow_activity_count": len(activities),
            "role_count": len(role_rows),
            "planned_role_count": sum(1 for row in role_rows if row.get("required") is not None),
            "instance_count": len(instance_rows),
            "real_instance_count": sum(1 for row in instance_rows if row["real_service"]),
            "functional_agent_count": len(functional_agents),
        },
        "functional_agents": functional_agents,
        "roles": role_rows,
        "instances": instance_rows,
        "heartbeat_available": any(row.get("last_heartbeat") for row in instance_rows),
    }


def _merged_activity_rows(status: dict[str, Any], work_payload: Any) -> list[dict[str, Any]]:
    result_rows = _result_activity_map(status)
    source_rows = _work_items(work_payload)
    if not source_rows:
        seen: set[int] = set()
        source_rows = []
        for row in result_rows.values():
            if id(row) not in seen:
                source_rows.append(row)
                seen.add(id(row))
    merged_rows = []
    for work in source_rows:
        result = result_rows.get(str(work.get("activity_id") or work.get("activatity_id"))) \
            or result_rows.get(str(work.get("work_item"))) \
            or {}
        merged_rows.append({**work, **result})
    return merged_rows


def _build_algorithm_view(
    status: dict[str, Any],
    work_payload: Any,
    trace_payload: Any,
    submission: dict[str, Any],
) -> dict[str, Any]:
    items: dict[str, dict[str, Any]] = {}
    for declared in _coverage_rows(submission.get("algorithm_coverage"), kind="algorithm"):
        key = declared["id"].casefold()
        items[key] = {
            "algorithm_id": declared["id"],
            "requirement_id": declared["requirement_id"],
            "name": declared["name"],
            "category": declared["category"],
            "model_id": declared["model_id"],
            "version": declared["version"],
            "agent": declared["agent"],
            "assigned_agents": declared["assigned_agents"],
            "tier": declared["tier"],
            "function_points": declared["function_points"],
            "activity_ids": declared["activity_ids"],
            "status": "declared",
            "execution_status": None,
            "duration_ms": None,
            "execution_mode": None,
            "params": None,
            "flops": None,
            "input_summary": None,
            "result_summary": None,
            "evidence_refs": [],
            "declared_by_scenario": True,
        }

    def merge_evidence(evidence: dict[str, Any], source: dict[str, Any], evidence_ref: str) -> None:
        item_id = str(evidence.get("id") or "")
        if not item_id:
            return
        key = item_id.casefold()
        state = str(source.get("status") or "").lower()
        verified = state in SUCCESS_STATES
        executing = state == "running"
        row = items.setdefault(key, {
            "algorithm_id": item_id,
            "requirement_id": evidence.get("requirement_id"),
            "name": evidence.get("name") or item_id,
            "category": evidence.get("category"),
            "model_id": evidence.get("model_id"),
            "version": evidence.get("version"),
            "agent": source.get("agent"),
            "assigned_agents": [],
            "tier": evidence.get("tier"),
            "function_points": [],
            "activity_ids": [],
            "status": "declared",
            "execution_status": None,
            "duration_ms": None,
            "execution_mode": None,
            "params": None,
            "flops": None,
            "input_summary": None,
            "result_summary": None,
            "evidence_refs": [],
            "declared_by_scenario": False,
        })
        row["name"] = evidence.get("name") or row["name"]
        for field in ("category", "model_id", "version", "params", "flops"):
            if evidence.get(field) is not None:
                row[field] = evidence[field]
        if source.get("agent"):
            row["agent"] = source["agent"]
        row["execution_status"] = state or row["execution_status"]
        if verified:
            row["status"] = "verified"
        elif executing and row["status"] != "verified":
            row["status"] = "executing"
        elif state in FAILED_STATES and row["status"] != "verified":
            row["status"] = "declared"
        metrics = source.get("metrics") if isinstance(source.get("metrics"), dict) else {}
        evidence_duration = evidence.get("duration_ms")
        metric_duration = metrics.get("duration_ms")
        if evidence_duration is not None:
            duration_value = float(evidence_duration)
            if duration_value > 0 or row.get("duration_ms") is None:
                row["duration_ms"] = evidence_duration
        elif metric_duration is not None:
            duration_value = float(metric_duration)
            if duration_value > 0 or row.get("duration_ms") is None:
                row["duration_ms"] = metric_duration
        row["execution_mode"] = evidence.get("execution_mode") or _execution_mode(source)
        explicit_input_summary = evidence.get("input_summary")
        if explicit_input_summary in (None, ""):
            explicit_input_summary = source.get("input_summary")
        explicit_result_summary = evidence.get("result_summary")
        if explicit_result_summary in (None, ""):
            explicit_result_summary = source.get("result_summary") or source.get("summary")
        input_value = source.get("input") if isinstance(source.get("input"), dict) else {}
        output_value = source.get("output") if isinstance(source.get("output"), dict) else {}
        input_facts = _scalar_facts(input_value)
        result_facts = _scalar_facts(output_value)
        if explicit_input_summary not in (None, ""):
            row["input_summary"] = explicit_input_summary
        elif input_facts:
            row["input_summary"] = input_facts
        if explicit_result_summary not in (None, ""):
            row["result_summary"] = explicit_result_summary
        elif result_facts:
            row["result_summary"] = result_facts
        if evidence_ref not in row["evidence_refs"]:
            row["evidence_refs"].append(evidence_ref)

    for row in _merged_activity_rows(status, work_payload):
        activity_ref = str(row.get("activity_id") or row.get("activatity_id") or row.get("work_item") or "unknown")
        for evidence in _algorithm_evidence(row):
            merge_evidence(evidence, row, f"activity:{activity_ref}")

    for index, event in enumerate(_trace_items(trace_payload)):
        event_name = str(event.get("event") or event.get("event_type") or "")
        if event_name in {
            "agent_call_completed", "local_agent_call_completed", "algorithm_completed", "model_inference_completed"
        }:
            event_state = "completed"
        elif event_name in {"agent_call_attempt", "agent_call_started", "algorithm_started", "model_inference_started"}:
            event_state = "running"
        elif event_name in {"agent_call_failed", "local_agent_call_failed", "algorithm_failed", "model_inference_failed"}:
            event_state = "failed"
        else:
            event_state = str(event.get("status") or "")
        source = {**event, "status": event_state, "agent": event.get("agent") or event.get("target")}
        for evidence in _algorithm_evidence(event):
            merge_evidence(evidence, source, f"trace:{index}")

    rows = list(items.values())
    return {
        "counts": {
            "total": len(rows),
            "planned": sum(1 for row in rows if row["declared_by_scenario"]),
            "declared": sum(1 for row in rows if row["status"] == "declared"),
            "executing": sum(1 for row in rows if row["status"] == "executing"),
            "verified": sum(1 for row in rows if row["status"] == "verified"),
        },
        "items": rows,
    }


def _build_function_point_view(
    status: dict[str, Any],
    work_payload: Any,
    trace_payload: Any,
    submission: dict[str, Any],
    algorithm_catalog: dict[str, Any] | None = None,
) -> dict[str, Any]:
    algorithm_catalog = algorithm_catalog if isinstance(algorithm_catalog, dict) else {}
    rows: dict[str, dict[str, Any]] = {
        str(item["function_id"]).casefold(): {
            "function_point_id": item["function_id"], "name": item["name"],
            "category": item.get("f2t2ea_stage", "").upper(),
            "ooda_phase": item.get("ooda_phase"), "f2t2ea_stage": item.get("f2t2ea_stage"),
            "status": "not_applicable", "execution_status": None, "activity_ids": [],
            "evidence_refs": [], "declared_by_scenario": False, "scope": "not_applicable",
            "candidate_algorithms": [], "actual_algorithms": [], "skills": [], "agents": [],
        }
        for item in operational_functions()
    }
    for declared in _coverage_rows(submission.get("function_point_coverage"), kind="function"):
        row = rows.setdefault(declared["id"].casefold(), {"function_point_id": declared["id"], "name": declared["name"], "category": declared["category"], "ooda_phase": None, "f2t2ea_stage": None, "candidate_algorithms": [], "actual_algorithms": [], "skills": [], "agents": []})
        row.update({"name": declared["name"] or row.get("name"), "category": declared["category"] or row.get("category"), "status": "declared", "execution_status": None, "activity_ids": declared["activity_ids"], "evidence_refs": [], "declared_by_scenario": True, "scope": "planned"})

    conditional = {str(item).casefold() for item in submission.get("conditional_function_points") or []}
    for key in conditional:
        if key in rows:
            rows[key]["scope"] = "conditional"
            if rows[key]["status"] == "declared":
                rows[key]["status"] = "conditional"

    for package in algorithm_catalog.get("algorithms") or []:
        if not isinstance(package, dict):
            continue
        for coverage in package.get("operational_functions") or []:
            point_id = str(coverage.get("function_id") if isinstance(coverage, dict) else coverage)
            row = rows.get(point_id.casefold())
            if not row:
                continue
            candidate = {"algorithm_id": package.get("algorithm_id"), "name": package.get("display_name") or package.get("algorithm_id"), "role": coverage.get("role") if isinstance(coverage, dict) else None, "runtime_status": package.get("runtime_status"), "onnx_model_provided": package.get("onnx_model_provided", False), "onnx_runtime_available": package.get("onnx_runtime_available", False)}
            if candidate not in row["candidate_algorithms"]:
                row["candidate_algorithms"].append(candidate)

    for skill_id, binding in SKILL_ALGORITHM_BINDINGS.items():
        for point_id in binding.get("function_points") or []:
            row = rows.get(str(point_id).casefold())
            if row:
                row["skills"].append({"skill_id": skill_id, "name": binding.get("name")})
                row["agents"] = list(dict.fromkeys([*row["agents"], *binding.get("agents", [])]))

    for activity in _merged_activity_rows(status, work_payload):
        activity_id = str(
            activity.get("activity_id") or activity.get("activatity_id") or activity.get("work_item") or ""
        )
        activity_state = str(activity.get("status") or "pending").lower()
        explicit_ids = _explicit_function_points(activity)
        for row in rows.values():
            if activity_id and activity_id in row["activity_ids"]:
                explicit_ids.append(row["function_point_id"])
        for point_id in dict.fromkeys(explicit_ids):
            key = point_id.casefold()
            row = rows.setdefault(key, {
                "function_point_id": point_id,
                "name": point_id,
                "category": None,
                "status": "declared",
                "execution_status": None,
                "activity_ids": [],
                "evidence_refs": [],
                "declared_by_scenario": False,
            })
            row["execution_status"] = activity_state
            if activity_state in SUCCESS_STATES:
                row["status"] = "verified"
            elif activity_state == "running" and row["status"] != "verified":
                row["status"] = "executing"
            elif activity_state in FAILED_STATES and row["status"] not in {"verified", "executing"}:
                row["status"] = "failed"
            reference = f"activity:{activity_id or 'unknown'}"
            if reference not in row["evidence_refs"]:
                row["evidence_refs"].append(reference)

    for index, event in enumerate(_trace_items(trace_payload)):
        point_ids = _explicit_function_points(event)
        if not point_ids:
            continue
        event_name = str(event.get("event") or event.get("event_type") or "")
        if event_name in {
            "agent_call_completed", "local_agent_call_completed",
            "function_point_completed", "activity_completed",
        }:
            event_state = "completed"
        elif event_name in {
            "agent_call_attempt", "agent_call_started",
            "function_point_started", "activity_started",
        }:
            event_state = "running"
        elif event_name in {
            "agent_call_failed", "local_agent_call_failed",
            "function_point_failed", "activity_failed",
        }:
            event_state = "failed"
        else:
            event_state = str(event.get("status") or "").lower()
        for point_id in point_ids:
            key = point_id.casefold()
            row = rows.setdefault(key, {
                "function_point_id": point_id,
                "name": point_id,
                "category": None,
                "status": "declared",
                "execution_status": None,
                "activity_ids": [],
                "evidence_refs": [],
                "declared_by_scenario": False,
            })
            row["execution_status"] = event_state or row["execution_status"]
            if event_state in SUCCESS_STATES:
                row["status"] = "verified"
            elif event_state == "running" and row["status"] != "verified":
                row["status"] = "executing"
            elif event_state in FAILED_STATES and row["status"] not in {"verified", "executing"}:
                row["status"] = "failed"
            reference = f"trace:{index}"
            if reference not in row["evidence_refs"]:
                row["evidence_refs"].append(reference)

    # Actual selections are evidence only.  They never complete a function by
    # themselves: completion remains gated by the associated activity/event.
    for activity in _merged_activity_rows(status, work_payload):
        activity_id = str(activity.get("activity_id") or activity.get("activatity_id") or activity.get("work_item") or "")
        activity_ids = set(_explicit_function_points(activity))
        for declared in rows.values():
            if activity_id and activity_id in declared.get("activity_ids", []):
                activity_ids.add(str(declared["function_point_id"]))
        if not activity_ids:
            continue
        for evidence in _algorithm_evidence(activity):
            for point_id in activity_ids:
                row = rows.get(str(point_id).casefold())
                if row:
                    actual = {"algorithm_id": evidence.get("id"), "name": evidence.get("name") or evidence.get("id"), "activity_id": activity.get("activity_id") or activity.get("work_item"), "status": activity.get("status")}
                    if actual not in row["actual_algorithms"]:
                        row["actual_algorithms"].append(actual)

    items = list(rows.values())
    return {
        "counts": {
            "total": len(items),
            "planned": sum(1 for row in items if row.get("scope") == "planned"),
            "conditional": sum(1 for row in items if row.get("scope") == "conditional"),
            "not_applicable": sum(1 for row in items if row.get("scope") == "not_applicable"),
            "declared": sum(1 for row in items if row["status"] == "declared"),
            "executing": sum(1 for row in items if row["status"] == "executing"),
            "verified": sum(1 for row in items if row["status"] == "verified"),
            "failed": sum(1 for row in items if row["status"] == "failed"),
        },
        "items": items,
    }


def _build_execution_graph(activities: list[dict[str, Any]], trace_payload: Any) -> dict[str, Any]:
    nodes = [{
        "id": str(row.get("activity_id") or row.get("work_item") or f"activity-{row['index']}"),
        "activity_id": row.get("activity_id"),
        "work_item": row.get("work_item"),
        "role": row.get("role"),
        "type": row.get("type"),
        "status": row.get("status"),
    } for row in activities]
    node_ids = {row["id"] for row in nodes}
    aliases = {
        str(alias): node["id"]
        for node, activity in zip(nodes, activities)
        for alias in (activity.get("activity_id"), activity.get("work_item"))
        if alias
    }
    edges: list[dict[str, str]] = []
    seen: set[tuple[str, str]] = set()

    def add_edge(source: Any, target: Any) -> None:
        source_id = aliases.get(str(source), str(source))
        target_id = aliases.get(str(target), str(target))
        if source_id not in node_ids or target_id not in node_ids or source_id == target_id:
            return
        key = (source_id, target_id)
        if key not in seen:
            edges.append({"source": source_id, "target": target_id})
            seen.add(key)

    for activity, node in zip(activities, nodes):
        for dependency in activity.get("depends_on") or []:
            add_edge(dependency, node["id"])

    for event in _trace_items(trace_payload):
        if str(event.get("event") or event.get("event_type") or "") != "flow_child_activity_scheduled":
            continue
        target = event.get("child_activity_id")
        for dependency in event.get("dependencies") or []:
            add_edge(dependency, target)

    return {
        "nodes": nodes,
        "edges": edges,
        "dependency_source": "backend" if edges else "not_provided",
    }


def _build_metrics(status: dict[str, Any], activities: list[dict[str, Any]], trace_payload: Any) -> dict[str, Any]:
    result = status.get("result") if isinstance(status.get("result"), dict) else {}
    summary = result.get("summary") if isinstance(result.get("summary"), dict) else {}
    result_metrics = result.get("metrics") if isinstance(result.get("metrics"), dict) else {}
    trace_rows = _trace_items(trace_payload)
    attempts = [row for row in trace_rows if str(row.get("event") or row.get("event_type") or "") == "agent_call_attempt"]
    completed_calls = [row for row in trace_rows if str(row.get("event") or row.get("event_type") or "") in {"agent_call_completed", "local_agent_call_completed"}]
    failed_calls = [row for row in trace_rows if str(row.get("event") or row.get("event_type") or "") in {"agent_call_failed", "local_agent_call_failed"}]
    actual_retries = sum(1 for row in attempts if int(row.get("attempt") or 1) > 1)
    activity_durations = [float(row["duration_ms"]) for row in activities if row.get("duration_ms") is not None]
    activity_duration = sum(activity_durations)
    workflow_duration = summary.get("duration_ms")
    if workflow_duration is None:
        workflow_duration = result_metrics.get("duration_ms")
    alert_count = result_metrics.get("alert_count")
    if alert_count is None:
        alert_count = len([item for item in result.get("warnings") or [] if item])
    return {
        "workflow_duration_ms": workflow_duration,
        "activity_duration_total_ms": round(activity_duration, 3) if activity_durations else None,
        "average_latency_ms": (
            round(activity_duration / len(activity_durations), 3) if activity_durations else None
        ),
        "agent_call_attempts": len(attempts),
        "agent_call_completed": len(completed_calls),
        "agent_call_failed": len(failed_calls),
        "retry_count": actual_retries,
        "throughput": result_metrics.get("throughput"),
        "recovery_time_ms": result_metrics.get("recovery_time_ms"),
        "alert_count": alert_count,
    }


def _build_provenance(
    status: dict[str, Any],
    submission: dict[str, Any],
    algorithms: dict[str, Any],
    projection: dict[str, Any],
) -> dict[str, Any]:
    result = status.get("result") if isinstance(status.get("result"), dict) else {}
    activity_rows = result.get("activity_results") or []
    records = []
    for row in activity_rows:
        if not isinstance(row, dict):
            continue
        activity_id = row.get("activity_id") or row.get("work_item")
        algorithm_ids = [
            item["algorithm_id"]
            for item in algorithms.get("items") or []
            if f"activity:{activity_id}" in (item.get("evidence_refs") or [])
        ]
        records.append({
            "activity_id": row.get("activity_id"),
            "work_item": row.get("work_item"),
            "agent": row.get("agent"),
            "status": row.get("status"),
            "algorithm_ids": algorithm_ids,
            "output_facts": _scalar_facts(row.get("output") or {}),
        })
    return {
        "workflow_id": status.get("workflow_id"),
        "run_id": submission.get("run_id"),
        "chain_id": status.get("chain_id") or submission.get("chain_id"),
        "snapshot_sequence": submission.get("snapshot_sequence"),
        "transport": submission.get("transport"),
        "inputs": [{
            "id": row.get("id"),
            "kind": row.get("kind"),
            "checksum": deepcopy(row.get("checksum") or {}),
        } for row in submission.get("attachments") or [] if isinstance(row, dict)],
        "activities": records,
        "projection": {
            "status": projection.get("status"),
            "applied_count": projection.get("applied_count", 0),
        },
    }


def _build_activity_details(
    status: dict[str, Any],
    work_payload: Any,
    trace_payload: Any,
    submission: dict[str, Any],
    activities: list[dict[str, Any]],
    agents: dict[str, Any],
    algorithms: dict[str, Any],
    provenance: dict[str, Any],
) -> dict[str, dict[str, Any]]:
    """Join browser-safe execution evidence around each explicit activity."""
    merged_rows = _merged_activity_rows(status, work_payload)
    raw_trace = _trace_items(trace_payload)
    provenance_rows = provenance.get("activities") or []
    details: dict[str, dict[str, Any]] = {}

    for activity in activities:
        activity_id = activity.get("activity_id")
        work_item = activity.get("work_item")
        detail_key = str(activity_id or work_item or f"activity-{activity['index']}")
        aliases = {str(value) for value in (activity_id, work_item) if value}
        merged = next((
            row for row in merged_rows
            if aliases.intersection({
                str(value)
                for value in (row.get("activity_id"), row.get("activatity_id"), row.get("work_item"))
                if value
            })
        ), {})
        metrics = merged.get("metrics") if isinstance(merged.get("metrics"), dict) else {}
        input_value = merged.get("input") if isinstance(merged.get("input"), dict) else {}
        output_value = merged.get("output") if isinstance(merged.get("output"), dict) else {}
        provenance_row = next((
            row for row in provenance_rows
            if aliases.intersection({
                str(value) for value in (row.get("activity_id"), row.get("work_item")) if value
            })
        ), {})

        trace_events = []
        trace_refs = []
        for trace_index, row in enumerate(raw_trace):
            trace_activity_id = row.get("activity_id") or row.get("activatity_id")
            if not trace_activity_id or str(trace_activity_id) not in aliases:
                continue
            normalized = _normalize_trace({"trace": [row]})
            if normalized:
                trace_events.append(normalized[0])
                trace_refs.append(f"trace:{trace_index}")

        activity_refs = {f"activity:{alias}" for alias in aliases}
        algorithm_rows = []
        for row in algorithms.get("items") or []:
            evidence_match = activity_refs.intersection(set(row.get("evidence_refs") or []))
            if not evidence_match:
                continue
            algorithm_row = deepcopy(row)
            algorithm_row["runtime_observed"] = True
            algorithm_row["planned_for_activity"] = False
            algorithm_rows.append(algorithm_row)
        instance_id = activity.get("instance_id")
        matching_instances = [
            deepcopy(row)
            for row in agents.get("instances") or []
            if instance_id and str(row.get("instance_id")) == str(instance_id)
        ]
        explicit_input_summary = merged.get("input_summary")
        explicit_output_summary = merged.get("result_summary") or merged.get("output_summary")
        safe_input_summary = _safe_explicit_summary(explicit_input_summary)
        safe_output_summary = _safe_explicit_summary(explicit_output_summary)
        variable_input_summary = _variable_summary(
            merged.get("input_variable"),
            status=status,
            submission=submission,
        )
        variable_output_summary = _variable_summary(
            merged.get("output_variable"),
            status=status,
            submission=submission,
        )
        input_summary = safe_input_summary if safe_input_summary is not None else (
            _scalar_facts(input_value) or variable_input_summary
        )
        output_summary = safe_output_summary if safe_output_summary is not None else (
            _scalar_facts(output_value)
            or variable_output_summary
            or deepcopy(provenance_row.get("output_facts") or [])
        )
        input_detail = (
            _detail_map(input_summary, input_value, source="activity_input")
            if input_value else _variable_detail(
                merged.get("input_variable"),
                input_summary,
                status=status,
                submission=submission,
            )
        )
        output_detail = (
            _detail_map(output_summary, output_value, source="activity_output")
            if output_value else _variable_detail(
                merged.get("output_variable"),
                output_summary,
                status=status,
                submission=submission,
            )
        )
        evidence_refs = list(dict.fromkeys(
            trace_refs
            + [ref for row in algorithm_rows for ref in row.get("evidence_refs") or []]
        ))
        details[detail_key] = {
            "activity_id": activity_id,
            "work_item": work_item,
            "type": activity.get("type"),
            "status": activity.get("status"),
            "depends_on": deepcopy(activity.get("depends_on") or []),
            "started_at": merged.get("started_at") or metrics.get("started_at"),
            "finished_at": merged.get("finished_at") or metrics.get("finished_at"),
            "input_summary": input_summary or None,
            "output_summary": output_summary or None,
            "input_detail": input_detail,
            "output_detail": output_detail,
            "agent_call": {
                "role": activity.get("role"),
                "agent": activity.get("agent"),
                "instance_id": instance_id,
                "execution_mode": activity.get("execution_mode"),
                "duration_ms": activity.get("duration_ms"),
                "retry_count": activity.get("retry_count"),
                "last_heartbeat": activity.get("last_heartbeat"),
                "instances": matching_instances,
            },
            "algorithms": algorithm_rows,
            "trace_refs": trace_refs,
            "trace_events": trace_events,
            "evidence_refs": evidence_refs,
        }
    return details


def build_workflow_view(
    status: dict[str, Any],
    *,
    work_list: Any = None,
    trace: Any = None,
    submission: dict[str, Any] | None = None,
    projection: dict[str, Any] | None = None,
    backend_transport: str = "commander",
    current_run_id: str = "",
    algorithm_catalog: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Build the stable workflow view returned to the browser."""
    status = status if isinstance(status, dict) else {}
    state = str(status.get("status") or ("unavailable" if status.get("error") else "unknown")).lower()
    activities = _normalize_activities(status, work_list)
    trace_rows = _normalize_trace(trace)
    total = len(activities)
    completed = sum(1 for row in activities if row["status"] in DONE_ACTIVITY_STATES)
    running = sum(1 for row in activities if row["status"] == "running")
    failed = sum(1 for row in activities if row["status"] in {"failed", "error"})
    if state == "completed":
        progress = 100
    elif state in {"failed", "error", "unavailable"}:
        progress = round(completed / total * 100) if total else 0
    elif total:
        progress = min(99, round(completed / total * 100))
    else:
        progress = 5 if state == "queued" else (10 if state == "running" else 0)

    result = status.get("result") if isinstance(status.get("result"), dict) else {}
    warnings = [str(item) for item in result.get("warnings") or [] if item]
    if status.get("last_error") and str(status["last_error"]) not in warnings:
        warnings.append(str(status["last_error"]))
    projection = projection if isinstance(projection, dict) else {}
    if projection.get("reason") and str(projection["reason"]) not in warnings:
        warnings.append(str(projection["reason"]))

    submission_run_id = str((submission or {}).get("run_id") or status.get("run_id") or "")
    stale_run = bool(current_run_id and submission_run_id and current_run_id != submission_run_id)
    if stale_run and "该结果属于上一轮仿真，未写入当前态势" not in warnings:
        warnings.append("该结果属于上一轮仿真，未写入当前态势")

    submission_view = deepcopy(submission or {})
    agents = _build_agent_view(activities, trace_rows, submission_view)
    algorithms = _build_algorithm_view(status, work_list, trace, submission_view)
    _apply_observed_activity_durations(activities, algorithms)
    function_points = _build_function_point_view(status, work_list, trace, submission_view, algorithm_catalog)
    execution_graph = _build_execution_graph(activities, trace)
    metrics = _build_metrics(status, activities, trace)
    provenance = _build_provenance(status, submission_view, algorithms, projection)
    activity_details = _build_activity_details(
        status,
        work_list,
        trace,
        submission_view,
        activities,
        agents,
        algorithms,
        provenance,
    )

    return {
        "schema_version": VIEW_SCHEMA_VERSION,
        "workflow_id": status.get("workflow_id"),
        "status": state,
        "progress_pct": progress,
        # Commander uses ``paused`` for resumable workflow failures. A failed
        # activity is nevertheless terminal for the current Director
        # checkpoint and must not be polled forever as if it were still live.
        "terminal": state in TERMINAL_STATES or failed > 0,
        "submitted_at": status.get("submitted_at"),
        "started_at": status.get("started_at"),
        "finished_at": status.get("finished_at"),
        "current_activity": status.get("current_activity"),
        "backend": {
            "available": not bool(status.get("error")),
            "transport": backend_transport,
            "contract": (
                "amos.commander.projection.v1"
                if backend_transport == "gateway"
                else "commander-manager"
            ),
            "error_code": status.get("code") if status.get("error") else None,
            "error": status.get("detail") if status.get("error") else None,
        },
        "run": {
            "run_id": submission_run_id or None,
            "chain_id": status.get("chain_id") or (submission or {}).get("chain_id"),
            "current": not stale_run,
        },
        "submission": submission_view,
        "orchestration": {
            "counts": {
                "total": total,
                "completed": completed,
                "running": running,
                "failed": failed,
            },
            "activities": activities,
            "trace": trace_rows,
        },
        "result": {
            "summary": deepcopy(result.get("summary") or {}),
            "warnings": warnings,
            "cards": _output_cards(status),
            "analysis": deepcopy(projection.get("analysis") or {}),
            "applied_count": projection.get("applied_count", 0),
            "projection_status": projection.get("status"),
        },
        "recovery": {
            "can_resume": state in {"failed", "error", "checkpoint_only"},
            "reason": status.get("last_error") or (status.get("detail") if status.get("error") else None),
        },
        "agents": agents,
        "algorithms": algorithms,
        "function_points": function_points,
        "execution_graph": execution_graph,
        "activity_details": activity_details,
        "metrics": metrics,
        "provenance": provenance,
    }


__all__ = [
    "VIEW_SCHEMA_VERSION",
    "build_submission_snapshot",
    "build_workflow_view",
]
