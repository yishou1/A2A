"""Backend-owned OODA and F2T2EA mission phase projection."""

from __future__ import annotations

from typing import Any


F2T2EA_PHASES = ("FIND", "FIX", "TRACK", "TARGET", "ENGAGE", "ASSESS")
OODA_PHASES = ("OBSERVE", "ORIENT", "DECIDE", "ACT")
F2T2EA_TO_OODA = {
    "FIND": "OBSERVE",
    "FIX": "OBSERVE",
    "TRACK": "ORIENT",
    "TARGET": "DECIDE",
    "ENGAGE": "ACT",
    "ASSESS": "ACT",
}


def _active_status(clock: dict[str, Any]) -> str:
    director_status = str(clock.get("director_status") or "")
    if director_status == "awaiting_analysis":
        return "waiting_backend"
    if director_status == "awaiting_authorization":
        return "waiting_authorization"
    if director_status == "error":
        return "failed"
    if str(clock.get("lifecycle") or "") == "paused":
        return "paused"
    return "active"


def build_mission_phase_state(
    story: dict[str, Any],
    clock: dict[str, Any],
) -> dict[str, Any]:
    """Return one authoritative phase contract for dashboard rendering."""
    timeline = [item for item in story.get("timeline") or [] if isinstance(item, dict)]
    current_f2 = str(timeline[-1].get("phase") or "FIND").upper() if timeline else "FIND"
    if current_f2 not in F2T2EA_PHASES:
        current_f2 = "FIND"
    current_index = F2T2EA_PHASES.index(current_f2)
    lifecycle = str(clock.get("lifecycle") or "")
    active_status = _active_status(clock)

    f2_items = []
    for index, phase in enumerate(F2T2EA_PHASES):
        if lifecycle == "completed" or index < current_index:
            status = "completed"
        elif index == current_index:
            status = active_status
        else:
            status = "pending"
        f2_items.append({"phase": phase, "status": status})

    ooda_items = []
    for ooda in OODA_PHASES:
        members = [item for item in f2_items if F2T2EA_TO_OODA[item["phase"]] == ooda]
        statuses = {item["status"] for item in members}
        if statuses == {"completed"}:
            status = "completed"
        elif "failed" in statuses:
            status = "failed"
        else:
            status = next(
                (
                    value for value in (
                        "waiting_authorization", "waiting_backend", "paused", "active",
                    )
                    if value in statuses
                ),
                "pending",
            )
        ooda_items.append({"phase": ooda, "status": status})

    return {
        "schema_version": "amos.mission-phases.v1",
        "source": "simulation_director",
        "f2t2ea": {"current": current_f2, "items": f2_items},
        "ooda": {"current": F2T2EA_TO_OODA[current_f2], "items": ooda_items},
    }


__all__ = [
    "F2T2EA_PHASES",
    "F2T2EA_TO_OODA",
    "OODA_PHASES",
    "build_mission_phase_state",
]
