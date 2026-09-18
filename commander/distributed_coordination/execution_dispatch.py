"""Bridge approved execution-control commands to distributed execution Agents."""

from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
from hashlib import sha256
import json
from typing import Any, Callable


def _authorization_reference(authorization: dict[str, Any]) -> str:
    encoded = json.dumps(
        authorization, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    return f"sha256:{sha256(encoded).hexdigest()}"


def _discover_agents(registry: Any) -> dict[str, dict[str, Any]]:
    discovered: dict[str, dict[str, Any]] = {}
    for status in ("idle", "busy"):
        for target in registry.discover_service("A2A-Agent", {"status": status}):
            metadata = target.get("metadata") if isinstance(target.get("metadata"), dict) else {}
            if str(metadata.get("role") or "").lower() != "cooperative_execution":
                continue
            agent_id = str(metadata.get("agent_id") or "").strip()
            if agent_id:
                discovered[agent_id] = deepcopy(target)
    return discovered


def _client(target: dict[str, Any], factory: Callable | None):
    if factory:
        return factory(target)
    from a2a_protocol.client import A2AClient

    return A2AClient(target.get("ip"), target.get("port"))


def _task_payload(
    *,
    workflow_id: str,
    work_item: str,
    command: str,
    arguments: dict[str, Any],
) -> dict[str, Any]:
    return {
        "schema_version": "1.0",
        "workflow_id": workflow_id,
        "work_item": work_item,
        "command": command,
        "required_skill": command,
        "required_skills": [command],
        "input": arguments,
        "output_hint": "cooperative_execution_result",
    }


def prepare_distributed_execution(
    execution_control_result: dict[str, Any],
    *,
    workflow_id: str,
    registry: Any,
    planned_start_at: str,
    client_factory: Callable | None = None,
) -> dict[str, Any]:
    """Reserve, prepare and synchronize selected Agents without actuating them."""
    if registry is None:
        raise ValueError("distributed execution preparation requires an Agent registry")
    try:
        parsed_start = datetime.fromisoformat(str(planned_start_at).replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError("planned_start_at must be ISO-8601") from exc
    if parsed_start.tzinfo is None:
        raise ValueError("planned_start_at must include a timezone")
    if parsed_start.astimezone(timezone.utc) <= datetime.now(timezone.utc):
        raise ValueError("planned_start_at must be in the future")

    output_data = (
        execution_control_result.get("output_data")
        if isinstance(execution_control_result.get("output_data"), dict)
        else execution_control_result
    )
    authorization = deepcopy(output_data.get("authorization") or {})
    decision = str(authorization.get("decision") or "").lower()
    if authorization.get("execution_blocked") is not False or decision not in {
        "approved",
        "authorized",
    }:
        raise ValueError("approved execution-control authorization is required")
    authorization["authorization_id"] = _authorization_reference(authorization)

    commands = [row for row in output_data.get("commands") or [] if isinstance(row, dict)]
    assignments = []
    for command_row in commands:
        resources = [str(item) for item in command_row.get("assigned_resources") or []]
        slots = [str(item) for item in command_row.get("assignment_slot_ids") or []]
        if resources and len(resources) != len(slots):
            raise ValueError("assigned_resources and assignment_slot_ids must have equal length")
        for agent_id, slot_id in zip(resources, slots):
            assignments.append(
                {
                    "agent_id": agent_id,
                    "slot_id": slot_id,
                    "task_id": str(command_row.get("task_id") or slot_id.split("#", 1)[0]),
                    "coordination_id": str(command_row.get("coordination_id") or ""),
                    "command_id": str(command_row.get("command_id") or ""),
                    "assignment_source": command_row.get("assignment_source"),
                    "assignment_locked": bool(command_row.get("assignment_locked", False)),
                }
            )
    if not assignments:
        raise ValueError("execution-control result contains no distributed assignments")
    if any(not row["coordination_id"] for row in assignments):
        raise ValueError("every distributed assignment requires coordination_id")
    if any(not row["assignment_locked"] for row in assignments):
        raise ValueError("every distributed assignment must be consensus-locked")
    if any(row["assignment_source"] != "deterministic_cbba" for row in assignments):
        raise ValueError("every distributed assignment must come from deterministic_cbba")
    coordination_ids = {row["coordination_id"] for row in assignments}
    if len(coordination_ids) != 1:
        raise ValueError("one preparation request must contain one coordination_id")

    participants = sorted({row["agent_id"] for row in assignments})
    targets = _discover_agents(registry)
    missing = sorted(set(participants) - set(targets))
    if missing:
        raise RuntimeError(f"assigned execution Agents are unavailable: {', '.join(missing)}")

    prepared = []
    for row in sorted(assignments, key=lambda item: (item["agent_id"], item["slot_id"])):
        client = _client(targets[row["agent_id"]], client_factory)
        assignment = {
            "coordination_id": row["coordination_id"],
            "task_id": row["task_id"],
            "slot_id": row["slot_id"],
            "winner_agent_id": row["agent_id"],
            "assigned_resources": [row["agent_id"]],
            "assignment_source": row["assignment_source"],
            "assignment_locked": row["assignment_locked"],
        }
        common = {
            "authorization": authorization,
            "coordination_id": row["coordination_id"],
            "slot_id": row["slot_id"],
        }
        reserve_response = client.send_message(
            _task_payload(
                workflow_id=workflow_id,
                work_item=f"{workflow_id}:{row['slot_id']}:reserve",
                command="reserve_cooperative_slot",
                arguments={
                    **common,
                    "assignment": assignment,
                    "participant_ids": participants,
                    "planned_start_at": planned_start_at,
                },
            )
        )
        prepare_response = client.send_message(
            _task_payload(
                workflow_id=workflow_id,
                work_item=f"{workflow_id}:{row['slot_id']}:prepare",
                command="prepare_cooperative_slot",
                arguments=common,
            )
        )
        prepared.append(
            {
                **row,
                "reserve_response": reserve_response,
                "prepare_response": prepare_response,
            }
        )

    synchronized = []
    for local in sorted(assignments, key=lambda item: (item["agent_id"], item["slot_id"])):
        agent_id = local["agent_id"]
        peers = [
            {
                "agent_id": peer_id,
                "ip": targets[peer_id].get("ip"),
                "port": targets[peer_id].get("port"),
            }
            for peer_id in participants
            if peer_id != agent_id
        ]
        response = _client(targets[agent_id], client_factory).exchange_execution_coordination(
            {
                "message_type": "synchronize",
                "coordination_id": local["coordination_id"],
                "local_slot_id": local["slot_id"],
                "peers": peers,
            }
        )
        synchronized.append(response)
    if not synchronized or not all(row.get("synchronized") for row in synchronized):
        raise RuntimeError("distributed execution Agents did not reach readiness consensus")
    return {
        "schema_version": "cooperative-execution-preparation/v1",
        "workflow_id": workflow_id,
        "planned_start_at": planned_start_at,
        "participant_ids": participants,
        "assignments": assignments,
        "prepared": prepared,
        "synchronization": synchronized,
        "ready_for_external_execution": True,
        "actuator_connected": False,
        "execution_dispatched": False,
    }
