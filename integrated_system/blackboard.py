from __future__ import annotations

from copy import deepcopy
from typing import Any

from workflow_state_store import utc_now_iso


def create_blackboard(mission_input: dict, workflow_id: str) -> dict:
    return {
        "workflow_id": workflow_id,
        "created_at": utc_now_iso(),
        "mission_input": deepcopy(mission_input),
        "situation": {
            "objective": mission_input.get("objective"),
            "contacts": deepcopy(mission_input.get("contacts", [])),
            "friendly_platforms": deepcopy(mission_input.get("friendly_platforms", [])),
            "constraints": deepcopy(mission_input.get("constraints", {})),
            "environment": deepcopy(mission_input.get("environment", {})),
        },
        "operator": {
            "intervention_required": False,
            "approval_override": mission_input.get("approval_override"),
            "notes": [],
            "adjustments": [],
        },
        "results": {},
        "trace": [],
        "summary": {
            "completed_capabilities": [],
            "warnings": [],
            "replan_count": 0,
        },
    }


def append_trace(blackboard: dict, event: str, **fields: Any) -> dict:
    item = {"timestamp": utc_now_iso(), "event": event}
    item.update(fields)
    blackboard.setdefault("trace", []).append(item)
    return item


def record_result(blackboard: dict, capability: str, envelope: dict) -> dict:
    snapshot = deepcopy(envelope)
    blackboard.setdefault("results", {})[capability] = snapshot
    completed = blackboard.setdefault("summary", {}).setdefault("completed_capabilities", [])
    if capability not in completed:
        completed.append(capability)
    warnings = snapshot.get("warnings") or []
    if warnings:
        blackboard["summary"].setdefault("warnings", []).extend(warnings)
    append_trace(
        blackboard,
        "capability_completed",
        capability=capability,
        agent=snapshot.get("agent"),
        next_suggestion=snapshot.get("next_suggestion"),
    )
    return snapshot


def latest_result(blackboard: dict, capability: str) -> dict:
    return deepcopy(blackboard.get("results", {}).get(capability, {}))


def apply_adjustment(blackboard: dict, adjustment: dict) -> None:
    operator = blackboard.setdefault("operator", {})
    operator.setdefault("adjustments", []).append(deepcopy(adjustment))
    note = adjustment.get("note")
    if note:
        operator.setdefault("notes", []).append(str(note))
    if "approval_override" in adjustment:
        operator["approval_override"] = adjustment.get("approval_override")
    append_trace(blackboard, "operator_adjustment_applied", adjustment=deepcopy(adjustment))
