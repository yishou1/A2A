"""Persistent A2A HTTP service for task scheduling and resource allocation."""

from __future__ import annotations

import os
from copy import deepcopy
from pathlib import Path
from typing import Any

import yaml

from a2a_protocol.server import A2ABaseAgent
from a2a_sdk import AgentRuntimeSDK
from task_scheduling_agent.agent import TaskSchedulingAgent


TASK_SCHEDULING_COMMANDS = {
    "allocate_tasks_and_resources",
    "task_scheduling_resource_allocation",
}


def _unwrap_context_value(value: Any) -> Any:
    if isinstance(value, list):
        return _unwrap_context_value(value[-1]) if value else {}
    if isinstance(value, dict) and "value" in value:
        return _unwrap_context_value(value["value"])
    return value


def _threat_result(payload: dict[str, Any]) -> dict[str, Any]:
    input_data = payload.get("input") if isinstance(payload.get("input"), dict) else {}
    value = _unwrap_context_value(
        input_data.get("threat_assessment_result")
        or input_data.get("risk_assessments")
        or {}
    )
    if isinstance(value, dict) and "threat_assessment_result" in value:
        value = _unwrap_context_value(value["threat_assessment_result"])
    if isinstance(value, dict) and "artifact" in value and "risk_assessments" not in value:
        value = _unwrap_context_value(value["artifact"])
    if isinstance(value, list):
        return {"risk_assessments": value}
    if isinstance(value, dict) and value:
        return value

    mission = input_data.get("mission_input")
    if not isinstance(mission, dict):
        return {}
    tracks = []
    risks = []
    for index, contact in enumerate(mission.get("contacts") or [], start=1):
        if not isinstance(contact, dict):
            continue
        metadata = contact.get("metadata") if isinstance(contact.get("metadata"), dict) else {}
        target_id = str(
            contact.get("track_id") or contact.get("contact_id") or f"T-{index:03d}"
        )
        classification = str(
            contact.get("classification")
            or metadata.get("classification")
            or "unknown"
        ).upper()
        confidence = _score01(contact.get("confidence", metadata.get("confidence")), 0.5)
        protected = any(
            token in classification
            for token in ("FISHING", "CIVILIAN", "MERCHANT", "FRIENDLY")
        )
        probability = 0.05 if protected else max(0.5, confidence)
        geo = contact.get("geo") if isinstance(contact.get("geo"), dict) else metadata.get("geo") or {}
        track = {
            "track_id": target_id,
            "object_type": classification,
            "confidence": confidence,
            "current_point": deepcopy(geo),
        }
        tracks.append(track)
        risks.append({
            "target_id": target_id,
            "priority": index,
            "risk": "low" if protected else ("high" if probability >= 0.75 else "medium"),
            "probability": probability,
            "threat_score": probability * 100.0,
            "rationale": (
                "Protected or civilian classification; continue monitoring."
                if protected
                else f"Mission contact classification={classification}, confidence={confidence:.2f}."
            ),
            "triggered_rules": [],
        })
    return {"tracks": tracks, "risk_assessments": risks}


def _score01(value: Any, default: float = 0.5) -> float:
    try:
        score = float(value)
    except (TypeError, ValueError):
        return default
    if score > 1.0:
        score /= 100.0
    return max(0.0, min(1.0, score))


def _normalize_risk_assessments(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    normalized: list[dict[str, Any]] = []
    for index, raw in enumerate(rows, start=1):
        target_id = str(raw.get("target_id") or raw.get("item_id") or f"T-{index:03d}")
        probability = _score01(raw.get("probability", raw.get("threat_score")))
        raw_score = raw.get("threat_score", probability * 100.0)
        try:
            threat_score = float(raw_score)
        except (TypeError, ValueError):
            threat_score = probability * 100.0
        if 0.0 <= threat_score <= 1.0:
            threat_score *= 100.0
        threat_score = max(0.0, min(100.0, threat_score))
        risk = str(raw.get("risk") or raw.get("risk_level") or "").strip().lower()
        if risk not in {"high", "medium", "low"}:
            risk = "high" if probability >= 0.75 else ("medium" if probability >= 0.4 else "low")
        normalized.append(
            {
                "target_id": target_id,
                "priority": max(1, int(raw.get("priority") or index)),
                "risk": risk,
                "threat_score": threat_score,
                "probability": probability,
                "rationale": str(
                    raw.get("rationale")
                    or raw.get("reason")
                    or f"Normalized upstream threat assessment for {target_id}."
                ),
                "triggered_rules": list(raw.get("triggered_rules") or []),
            }
        )
    return normalized


def _track_position(track: dict[str, Any]) -> tuple[float | None, float | None]:
    for candidate in (
        track,
        track.get("position"),
        track.get("current_point"),
        track.get("geo"),
    ):
        if not isinstance(candidate, dict):
            continue
        lat = candidate.get("lat")
        lon = candidate.get("lon", candidate.get("lng"))
        if lat is not None and lon is not None:
            return float(lat), float(lon)
    return None, None


def _mission_platforms(payload: dict[str, Any]) -> list[dict[str, Any]]:
    context = payload.get("context") if isinstance(payload.get("context"), dict) else {}
    mission = context.get("mission_input") if isinstance(context.get("mission_input"), dict) else {}
    platforms = mission.get("friendly_platforms") or []
    normalized: list[dict[str, Any]] = []
    for raw in platforms:
        if not isinstance(raw, dict):
            continue
        platform_id = str(raw.get("platform_id") or raw.get("id") or "").strip()
        if not platform_id:
            continue
        metadata = raw.get("metadata") if isinstance(raw.get("metadata"), dict) else {}
        position = metadata.get("position") if isinstance(metadata.get("position"), dict) else {}
        sensors = list(metadata.get("sensors") or raw.get("sensors") or [])
        platform_type = str(raw.get("platform_type") or raw.get("type") or "generic")
        munitions = raw.get("munitions", 0)
        try:
            ammo = max(0.0, min(1.0, float(munitions) / max(float(munitions), 1.0)))
        except (TypeError, ValueError):
            ammo = 0.0
        role = "strike" if ammo > 0.0 else ("sensor" if sensors else "support")
        if role == "support":
            continue
        readiness = _score01(raw.get("readiness"), 1.0)
        normalized.append(
            {
                "platform_id": platform_id,
                "role": role,
                "asset_type": platform_type,
                "modality": str(sensors[0]) if sensors else "generic",
                "available": readiness > 0.0,
                "battery": readiness,
                "link_quality": _score01(metadata.get("comms_strength"), 1.0),
                "ammo": ammo,
                "lat": position.get("lat"),
                "lon": position.get("lon", position.get("lng")),
            }
        )
    return normalized


def build_scheduler_input(payload: dict[str, Any]) -> dict[str, Any]:
    threat = _threat_result(payload)
    risks = [item for item in threat.get("risk_assessments") or [] if isinstance(item, dict)]
    tracks = [item for item in threat.get("tracks") or [] if isinstance(item, dict)]
    tracks_by_id = {
        str(item.get("track_id") or item.get("id") or ""): item for item in tracks
    }
    tasks: list[dict[str, Any]] = []
    for index, risk in enumerate(risks, start=1):
        target_id = str(risk.get("target_id") or risk.get("item_id") or f"T-{index:03d}")
        track = tracks_by_id.get(target_id, {})
        lat, lon = _track_position(track)
        task = {
            "task_id": f"TASK-{index:03d}",
            "target_id": target_id,
            "task_type": "monitor",
            "priority": _score01(risk.get("probability", risk.get("threat_score"))),
            "threat_score": _score01(risk.get("probability", risk.get("threat_score"))),
            "confidence": _score01(track.get("track_quality", track.get("confidence")), 0.7),
            "class_name": str(track.get("object_type") or track.get("class_name") or "unknown"),
        }
        if lat is not None and lon is not None:
            task.update({"lat": lat, "lon": lon})
        tasks.append(task)

    context = payload.get("context") if isinstance(payload.get("context"), dict) else {}
    mission = context.get("mission_input") if isinstance(context.get("mission_input"), dict) else {}
    environment = mission.get("environment") if isinstance(mission.get("environment"), dict) else {}
    network = environment.get("network") if isinstance(environment.get("network"), dict) else {}
    degraded = network.get("degraded", network.get("degraded_count", 0))
    return {
        "request_id": payload.get("work_item") or payload.get("task_id"),
        "trace_id": payload.get("workflow_id"),
        "mission_id": payload.get("workflow_id") or mission.get("scenario_id"),
        "phase": str((mission.get("stage_transfer") or {}).get("phase") or "planning").lower(),
        "jamming_level": _score01(degraded, 0.0),
        "tasks": tasks,
        "platforms": _mission_platforms(payload),
    }


def _resource_rows(payload: dict[str, Any], result: dict[str, Any]) -> list[dict[str, Any]]:
    context = payload.get("context") if isinstance(payload.get("context"), dict) else {}
    mission = context.get("mission_input") if isinstance(context.get("mission_input"), dict) else {}
    rows: list[dict[str, Any]] = []
    for raw in mission.get("friendly_platforms") or []:
        if not isinstance(raw, dict):
            continue
        resource_id = str(raw.get("platform_id") or raw.get("id") or "").strip()
        if not resource_id:
            continue
        metadata = deepcopy(raw.get("metadata")) if isinstance(raw.get("metadata"), dict) else {}
        sensors = list(metadata.get("sensors") or [])
        resource_type = "sensor" if sensors else str(raw.get("platform_type") or "platform")
        readiness = _score01(raw.get("readiness"), 1.0)
        rows.append(
            {
                "id": resource_id,
                "type": resource_type,
                "status": "available" if readiness > 0.0 else "offline",
                "capacity": readiness,
                "location": raw.get("location"),
                "attributes": {
                    "platform_type": raw.get("platform_type"),
                    "sensors": sensors,
                    "munitions": raw.get("munitions", 0),
                    **metadata,
                },
            }
        )
    if rows:
        return rows

    seen: set[str] = set()
    for assignment in (result.get("sensor_assignments") or []) + (result.get("reattack_plan") or []):
        if not isinstance(assignment, dict):
            continue
        resource_id = str(assignment.get("sensor_id") or assignment.get("asset_id") or "").strip()
        if not resource_id or resource_id in seen:
            continue
        seen.add(resource_id)
        rows.append(
            {
                "id": resource_id,
                "type": "sensor" if assignment.get("sensor_id") else "strike",
                "status": "available",
                "capacity": 1.0,
                "location": None,
                "attributes": {"source": "scheduler_result"},
            }
        )
    return rows


def normalize_task_scheduling_result(
    payload: dict[str, Any], result: dict[str, Any]
) -> dict[str, Any]:
    threat = _threat_result(payload)
    risks = _normalize_risk_assessments(
        [item for item in threat.get("risk_assessments") or [] if isinstance(item, dict)]
    )
    resources = _resource_rows(payload, result)
    known_resource_ids = {str(row.get("id")) for row in resources if row.get("id")}
    assignments: dict[str, list[str]] = {}
    reattack_targets: set[str] = set()
    for row in result.get("sensor_assignments") or []:
        if isinstance(row, dict) and row.get("target_id") and row.get("sensor_id"):
            assignments.setdefault(str(row["target_id"]), []).append(str(row["sensor_id"]))
    for row in result.get("reattack_plan") or []:
        if isinstance(row, dict) and row.get("target_id"):
            target_id = str(row["target_id"])
            asset_id = str(row.get("asset_id") or "")
            if not known_resource_ids or asset_id in known_resource_ids:
                reattack_targets.add(target_id)
                if asset_id:
                    assignments.setdefault(target_id, []).append(asset_id)

    scheduled_tasks = []
    for index, risk in enumerate(risks, start=1):
        target_id = str(risk.get("target_id") or risk.get("item_id") or f"T-{index:03d}")
        required = ["sensor"]
        if target_id in reattack_targets:
            required.append("strike")
        scheduled_tasks.append(
            {
                "id": f"TASK-{index:03d}",
                "target_id": target_id,
                "priority": max(1, int(risk.get("priority") or index)),
                "task_type": "reattack" if target_id in reattack_targets else "monitor",
                "deadline": None,
                "required_resource_types": required,
                "assigned_resources": list(dict.fromkeys(assignments.get(target_id, []))),
            }
        )

    target_histories = threat.get("target_histories") or []
    if not target_histories:
        target_histories = [
            {
                "target_id": str(risk.get("target_id") or f"T-{index:03d}"),
                "steps": [
                    {
                        "timestamp": None,
                        "risk_score": _score01(risk.get("threat_score")) * 100.0,
                        "probability": _score01(risk.get("probability", risk.get("threat_score"))),
                        "priority": max(1, int(risk.get("priority") or index)),
                        "resource_pressure": 0.5,
                    }
                ],
            }
            for index, risk in enumerate(risks, start=1)
        ]

    context = payload.get("context") if isinstance(payload.get("context"), dict) else {}
    objectives = context.get("planning_objectives") or [
        "Prioritize confirmed high-risk contacts",
        "Preserve available sensing resources",
    ]
    return {
        "mission_id": result.get("mission_id") or payload.get("workflow_id"),
        "scheduled_tasks": scheduled_tasks,
        "resources": resources,
        "risk_assessments": deepcopy(risks),
        "target_histories": deepcopy(target_histories),
        "planning_objectives": list(objectives),
        "sensor_assignments": deepcopy(result.get("sensor_assignments") or []),
        "reattack_plan": deepcopy(result.get("reattack_plan") or []),
        "algorithm": result.get("algorithm"),
        "selected_algorithms": deepcopy(result.get("selected_algorithms") or []),
        "algorithm_calls": deepcopy(result.get("algorithm_calls") or []),
        "algorithm_invocations": deepcopy(result.get("algorithm_invocations") or []),
        "llm_plan": deepcopy(result.get("llm_plan") or {}),
        "algolib_result": deepcopy(result.get("algolib_result") or {}),
        "summary": {
            "scheduled_task_count": len(scheduled_tasks),
            "resource_count": len(resources),
        },
    }


def _runtime_config() -> dict[str, Any]:
    path = Path(os.environ.get("TASK_SCHEDULING_CONFIG", "config/default.yaml"))
    if not path.is_absolute():
        path = Path(__file__).resolve().parents[1] / path
    config: dict[str, Any] = {}
    if path.is_file():
        loaded = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        if isinstance(loaded, dict):
            config = loaded
    backend = os.environ.get("TASK_SCHEDULING_BACKEND", "algolib").strip().lower()
    config["backend"] = backend
    config.setdefault("algorithm_library", {})["enabled"] = backend in {
        "algolib",
        "algorithm_library",
        "library",
    }
    return config


class TaskSchedulingA2AAgent(A2ABaseAgent):
    def __init__(self, port: int, *, scheduler: TaskSchedulingAgent | None = None, **kwargs):
        super().__init__(
            name="Task_Scheduling_Agent",
            description="Schedules mission tasks and allocates available simulation resources.",
            role="task_scheduling",
            port=port,
            skills=[
                {
                    "id": "task_scheduling_resource_allocation",
                    "name": "Task Scheduling and Resource Allocation",
                    "description": "Convert threat assessments into scheduled tasks and resource allocations.",
                    "tags": ["task_scheduling", "resource_allocation", "planning"],
                }
            ],
            **kwargs,
        )
        self.scheduler = scheduler or TaskSchedulingAgent(use_mock=True, config=_runtime_config())

    def execute_task(self, payload: dict[str, Any]):
        command = str(payload.get("command") or "allocate_tasks_and_resources")
        if command not in TASK_SCHEDULING_COMMANDS:
            raise ValueError(f"Unsupported command: {command}")
        result = self.scheduler.run(build_scheduler_input(payload))
        normalized = normalize_task_scheduling_result(payload, result)
        output_hint = payload.get("output_hint") or "task_scheduling_result"
        return {output_hint: normalized}, "Task scheduling and resource allocation completed"


if __name__ == "__main__":
    port = int(os.environ.get("TASK_SCHEDULING_AGENT_PORT", "10201"))
    heartbeat_interval = float(os.environ.get("A2A_HEARTBEAT_INTERVAL", "5"))
    runtime = AgentRuntimeSDK.from_agent(
        TaskSchedulingA2AAgent(port=port),
        heartbeat_interval=heartbeat_interval,
        extra_metadata={"capability": "task_scheduling"},
    )
    try:
        runtime.serve()
    finally:
        runtime.close()
