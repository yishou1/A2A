"""Adapters between distributed CBBA allocation and Commander task schedules."""

from __future__ import annotations

from copy import deepcopy
from typing import Any

from distributed_coordination.orchestrator import A2ACBBAOrchestrator
from services.a2a_algorithms_common.distributed_cbba import (
    apply_assignments_to_tasks,
    run_local_cbba_consensus,
)


DEFAULT_EXECUTION_ROLES = {
    "artillery",
    "assault",
    "recon",
    "cooperative_execution",
}


def _tokens(value: Any, fallback: str) -> list[str]:
    if value in (None, ""):
        return [fallback]
    if isinstance(value, str):
        values = value.replace(",", " ").replace(";", " ").split()
    else:
        values = list(value)
    return [str(item).strip() for item in values if str(item).strip()]


def cooperative_execution_config(
    context: dict[str, Any],
    schedule: dict[str, Any],
) -> dict[str, Any]:
    mission = context.get("mission_input") if isinstance(context.get("mission_input"), dict) else {}
    config = mission.get("cooperative_execution")
    if not isinstance(config, dict):
        config = schedule.get("cooperative_execution")
    return deepcopy(config) if isinstance(config, dict) else {}


def _merge_task_overrides(
    tasks: list[dict[str, Any]],
    config: dict[str, Any],
) -> list[dict[str, Any]]:
    override_rows = config.get("tasks") or config.get("task_overrides") or []
    if isinstance(override_rows, dict):
        overrides = {
            str(key): deepcopy(value)
            for key, value in override_rows.items()
            if isinstance(value, dict)
        }
    else:
        overrides = {
            str(row.get("task_id") or row.get("id")): deepcopy(row)
            for row in override_rows
            if isinstance(row, dict) and (row.get("task_id") or row.get("id"))
        }
    merged = []
    for task in tasks:
        row = deepcopy(task)
        task_id = str(row.get("task_id") or row.get("id") or "")
        row.update(overrides.get(task_id, {}))
        row.setdefault("task_id", task_id)
        merged.append(row)
    return merged


def _local_participants(resources: list[dict[str, Any]]) -> list[dict[str, Any]]:
    participants = []
    for resource in resources:
        resource_id = str(resource.get("id") or "").strip()
        if not resource_id:
            continue
        attributes = resource.get("attributes") if isinstance(resource.get("attributes"), dict) else {}
        status = str(resource.get("status") or "unknown").lower()
        resource_type = str(resource.get("type") or attributes.get("role") or "generic")
        capabilities = _tokens(
            attributes.get("capabilities") or attributes.get("skills"),
            resource_type,
        )
        available_task_slots = max(
            0, int(float(attributes.get("available_task_slots", 1) or 0))
        )
        participants.append(
            {
                "agent_id": resource_id,
                "role": attributes.get("role", resource_type),
                "resource_types": [resource_type],
                "capabilities": capabilities,
                "available": status in {"available", "idle", "ready"}
                and available_task_slots > 0,
                "status": status,
                "readiness": float(resource.get("capacity", 1.0) or 0.0),
                "capacity": max(1, int(float(attributes.get("task_slots", 1) or 1))),
                "available_task_slots": available_task_slots,
                "max_concurrent_tasks": max(
                    1, int(float(attributes.get("max_concurrent_tasks", 1) or 1))
                ),
                "load": float(attributes.get("load", 0.0) or 0.0),
                "task_costs": deepcopy(attributes.get("task_costs") or {}),
            }
        )
    return participants


def _remote_participants(registry: Any, config: dict[str, Any]) -> list[dict[str, Any]]:
    roles = {
        str(role).lower()
        for role in config.get("participant_roles", DEFAULT_EXECUTION_ROLES)
    }
    candidates = []
    seen = set()
    for status in ("idle", "busy"):
        for target in registry.discover_service("A2A-Agent", {"status": status}):
            key = f"{target.get('ip')}:{target.get('port')}"
            if key in seen:
                continue
            metadata = target.get("metadata") if isinstance(target.get("metadata"), dict) else {}
            role = str(metadata.get("role") or "").lower()
            if role not in roles:
                continue
            if str(metadata.get("agent_run_state", "ready")).lower() in {
                "not_ready",
                "unavailable",
            }:
                continue
            seen.add(key)
            candidates.append(
                {
                    "agent_id": str(metadata.get("agent_id") or metadata.get("platform_id") or key),
                    "ip": target.get("ip"),
                    "port": target.get("port"),
                    "role": role,
                    "metadata": deepcopy(metadata),
                }
            )
    return sorted(candidates, key=lambda item: item["agent_id"])


def _upsert_participant_resources(
    resources: list[dict[str, Any]],
    allocation: dict[str, Any],
) -> list[dict[str, Any]]:
    result = deepcopy(resources)
    known = {str(row.get("id")) for row in result if row.get("id")}
    for participant_id in allocation.get("participant_ids") or []:
        if str(participant_id) in known:
            continue
        result.append(
            {
                "id": str(participant_id),
                "type": "execution_agent",
                "status": "available",
                "capacity": 1.0,
                "location": None,
                "attributes": {
                    "source": "a2a_cbba_participant",
                    "coordination_id": allocation.get("coordination_id"),
                },
            }
        )
    return result


def coordinate_task_schedule(
    schedule: dict[str, Any],
    context: dict[str, Any],
    *,
    mode: str,
    registry: Any = None,
    client_factory=None,
) -> dict[str, Any]:
    """Apply opt-in distributed allocation without authorizing execution."""
    result = deepcopy(schedule)
    config = cooperative_execution_config(context, result)
    if not bool(config.get("enabled", False)):
        return result

    tasks = _merge_task_overrides(list(result.get("scheduled_tasks") or []), config)
    if not tasks:
        raise ValueError("cooperative execution requires scheduled_tasks")
    coordination_id = str(
        config.get("coordination_id")
        or f"CBBA-{result.get('mission_id') or context.get('workflow_id') or 'MISSION'}"
    )
    max_rounds = max(1, int(config.get("max_rounds", 16)))
    if mode == "remote":
        if registry is None:
            raise ValueError("remote cooperative execution requires an Agent registry")
        participants = _remote_participants(registry, config)
        if not participants:
            raise ValueError("no available A2A execution Agents for cooperative execution")
        allocation = A2ACBBAOrchestrator(
            client_factory=client_factory
        ).coordinate(
            tasks,
            participants,
            coordination_id=coordination_id,
            max_rounds=max_rounds,
        )
    else:
        participants = _local_participants(list(result.get("resources") or []))
        if not participants:
            raise ValueError("no local execution resources for cooperative execution")
        allocation = run_local_cbba_consensus(
            tasks,
            participants,
            coordination_id=coordination_id,
            max_rounds=max_rounds,
        )
        allocation["transport"] = "local_independent_participants"

    if not allocation.get("consensus_reached"):
        raise RuntimeError(
            f"distributed coordination did not converge after {allocation.get('rounds_completed')} rounds"
        )
    result["scheduled_tasks"] = apply_assignments_to_tasks(tasks, allocation)
    result["resources"] = _upsert_participant_resources(
        list(result.get("resources") or []), allocation
    )
    result["distributed_allocation"] = allocation
    result["cooperative_execution"] = {
        "enabled": True,
        "algorithm": allocation.get("algorithm"),
        "coordination_id": coordination_id,
        "consensus_reached": True,
        "authorization_required": True,
        "execution_dispatched": False,
    }
    return result
