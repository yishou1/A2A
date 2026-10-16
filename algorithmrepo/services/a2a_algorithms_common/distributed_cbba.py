"""Deterministic distributed bidding for cooperative task allocation.

The implementation keeps CBBA state local to each participant.  A coordinator
may start rounds and verify convergence, but it never computes the winning
assignment itself.
"""

from __future__ import annotations

from copy import deepcopy
from hashlib import sha256
import json
from typing import Any, Iterable


ALGORITHM_ID = "deterministic_cbba"
SCHEMA_VERSION = "cbba-coordination/v1"


def _as_float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _as_int(value: Any, default: int = 0) -> int:
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return default


def _clamp(value: float, low: float = 0.0, high: float = 1.0) -> float:
    return max(low, min(high, float(value)))


def _tokens(value: Any) -> set[str]:
    if value in (None, ""):
        return set()
    if isinstance(value, str):
        values = value.replace(",", " ").replace(";", " ").split()
    else:
        values = list(value)
    return {str(item).strip().lower() for item in values if str(item).strip()}


def _task_id(task: dict[str, Any], index: int) -> str:
    return str(task.get("task_id") or task.get("id") or f"TASK-{index:03d}")


def expand_task_slots(tasks: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    """Expand cooperative tasks into deterministic single-winner slots."""
    slots: list[dict[str, Any]] = []
    for index, raw in enumerate(tasks or [], start=1):
        task = deepcopy(raw)
        parent_id = _task_id(task, index)
        required_slots = max(
            1,
            min(
                64,
                _as_int(
                    task.get("required_slots", task.get("slot_count", 1)),
                    1,
                ),
            ),
        )
        for slot_index in range(1, required_slots + 1):
            slot = deepcopy(task)
            slot.update(
                {
                    "task_id": parent_id,
                    "parent_task_id": parent_id,
                    "slot_id": f"{parent_id}#slot-{slot_index:03d}",
                    "slot_index": slot_index,
                    "required_slots": required_slots,
                    "distinct_agents": bool(
                        task.get("distinct_agents", required_slots > 1)
                    ),
                }
            )
            slots.append(slot)
    return sorted(slots, key=lambda item: item["slot_id"])


def _normalized_priority(task: dict[str, Any]) -> float:
    explicit = task.get("priority_score")
    if explicit not in (None, ""):
        value = _as_float(explicit, 0.0)
        return _clamp(value / 100.0 if value > 1.0 else value)
    value = _as_float(task.get("priority", task.get("threat_score", 0.5)), 0.5)
    if value > 1.0:
        return _clamp(1.0 / value)
    return _clamp(value)


def _winner_key(record: dict[str, Any] | None) -> tuple[float, str]:
    if not record or not record.get("winner_agent_id"):
        return (float("-inf"), "")
    # The lexicographically smaller agent id wins an exact bid tie.
    agent_id = str(record["winner_agent_id"])
    inverted = "".join(chr(0x10FFFF - ord(char)) for char in agent_id)
    return (_as_float(record.get("bid"), float("-inf")), inverted)


def better_winner(
    candidate: dict[str, Any] | None,
    current: dict[str, Any] | None,
) -> bool:
    return _winner_key(candidate) > _winner_key(current)


class CBBAParticipant:
    """One Agent's local task view, resource state, bundle and winner table."""

    def __init__(
        self,
        agent_id: str,
        *,
        resource_state: dict[str, Any] | None = None,
        tasks: Iterable[dict[str, Any]] | None = None,
        coordination_id: str = "",
        max_bundle_size: int | None = None,
    ):
        if not str(agent_id).strip():
            raise ValueError("agent_id is required")
        self.agent_id = str(agent_id)
        self.coordination_id = str(coordination_id)
        self.resource_state = deepcopy(resource_state or {})
        self.local_tasks: dict[str, dict[str, Any]] = {}
        self.bundle: list[str] = []
        self.winner_table: dict[str, dict[str, Any]] = {}
        self.local_bids: dict[str, float] = {}
        self.round = 0
        self.max_bundle_size = max_bundle_size
        self.set_tasks(tasks or [])

    @property
    def available(self) -> bool:
        state = self.resource_state
        if state.get("available") is False or state.get("ready") is False:
            return False
        if str(state.get("status", "available")).lower() in {
            "offline",
            "unavailable",
            "failed",
            "not_ready",
        }:
            return False
        if _as_int(state.get("available_task_slots"), 1) <= 0:
            return False
        return True

    def set_tasks(self, tasks: Iterable[dict[str, Any]]) -> None:
        slots = expand_task_slots(tasks)
        self.local_tasks = {slot["slot_id"]: slot for slot in slots}
        self.bundle = [slot_id for slot_id in self.bundle if slot_id in self.local_tasks]
        self.winner_table = {
            slot_id: record
            for slot_id, record in self.winner_table.items()
            if slot_id in self.local_tasks
        }
        self.local_bids = {
            slot_id: self.utility(task) for slot_id, task in self.local_tasks.items()
        }

    def update_resource_state(self, resource_state: dict[str, Any]) -> None:
        self.resource_state = deepcopy(resource_state or {})
        self.local_bids = {
            slot_id: self.utility(task) for slot_id, task in self.local_tasks.items()
        }
        if not self.available:
            self.release_all()

    def _capacity(self) -> int:
        state_capacity = max(
            0,
            _as_int(
                self.resource_state.get(
                    "available_task_slots",
                    self.resource_state.get("capacity", 1),
                ),
                1,
            ),
        )
        if self.max_bundle_size is not None:
            state_capacity = min(state_capacity, max(0, int(self.max_bundle_size)))
        return state_capacity

    def _eligible(self, task: dict[str, Any]) -> bool:
        if not self.available:
            return False
        capabilities = _tokens(
            self.resource_state.get("capabilities")
            or self.resource_state.get("skills")
        )
        required_capabilities = _tokens(
            task.get("required_capabilities") or task.get("required_skills")
        )
        if required_capabilities and not required_capabilities.issubset(capabilities):
            return False

        resource_types = _tokens(
            self.resource_state.get("resource_types")
            or self.resource_state.get("resource_type")
            or self.resource_state.get("role")
        )
        required_types = _tokens(task.get("required_resource_types"))
        if required_types and resource_types.isdisjoint(required_types):
            return False

        requirements = task.get("minimum_resources") or {}
        for name, minimum in requirements.items():
            actual = _as_float(self.resource_state.get(name), float("-inf"))
            if actual < _as_float(minimum):
                return False
        return True

    def utility(self, task: dict[str, Any]) -> float:
        """Compute a deterministic local bid from existing task/resource data."""
        if not self._eligible(task):
            return float("-inf")
        state = self.resource_state
        required = _tokens(task.get("required_capabilities") or task.get("required_skills"))
        capabilities = _tokens(state.get("capabilities") or state.get("skills"))
        capability_match = 1.0 if not required else len(required & capabilities) / len(required)
        readiness = _clamp(
            _as_float(state.get("readiness", state.get("capacity", 1.0)), 1.0)
        )
        load = _clamp(_as_float(state.get("load", state.get("active_load", 0.0)), 0.0))
        available_slots = max(0, _as_int(state.get("available_task_slots"), 1))
        max_slots = max(1, _as_int(state.get("max_concurrent_tasks"), available_slots or 1))
        capacity_ratio = _clamp(available_slots / max_slots)
        local_costs = state.get("task_costs") if isinstance(state.get("task_costs"), dict) else {}
        cost = _as_float(
            local_costs.get(
                task.get("task_id"),
                local_costs.get(task.get("slot_id"), task.get("task_cost", task.get("cost", 0.0))),
            ),
            0.0,
        )
        score = (
            _normalized_priority(task) * 100.0
            + capability_match * 30.0
            + readiness * 20.0
            + capacity_ratio * 10.0
            - load * 20.0
            - cost
        )
        return round(score, 6)

    def _can_add_to_bundle(self, task: dict[str, Any]) -> bool:
        if not task.get("distinct_agents"):
            return True
        parent_id = task["parent_task_id"]
        return not any(
            self.local_tasks[slot_id]["parent_task_id"] == parent_id
            for slot_id in self.bundle
        )

    def _own_record(self, slot_id: str) -> dict[str, Any]:
        return {
            "slot_id": slot_id,
            "task_id": self.local_tasks[slot_id]["parent_task_id"],
            "winner_agent_id": self.agent_id,
            "bid": self.local_bids[slot_id],
        }

    def prune_lost_bundle(self) -> bool:
        first_lost = next(
            (
                index
                for index, slot_id in enumerate(self.bundle)
                if self.winner_table.get(slot_id, {}).get("winner_agent_id") != self.agent_id
            ),
            None,
        )
        if first_lost is None:
            return False
        released = self.bundle[first_lost:]
        self.bundle = self.bundle[:first_lost]
        for slot_id in released:
            if self.winner_table.get(slot_id, {}).get("winner_agent_id") == self.agent_id:
                self.winner_table.pop(slot_id, None)
        return True

    def build_bundle(self) -> bool:
        changed = self.prune_lost_bundle()
        if not self.available:
            return self.release_all() or changed
        capacity = self._capacity()
        while len(self.bundle) < capacity:
            candidates: list[tuple[float, str]] = []
            for slot_id, task in self.local_tasks.items():
                if slot_id in self.bundle or not self._can_add_to_bundle(task):
                    continue
                own = self._own_record(slot_id)
                if own["bid"] == float("-inf"):
                    continue
                if better_winner(own, self.winner_table.get(slot_id)):
                    candidates.append((own["bid"], slot_id))
            if not candidates:
                break
            _, slot_id = sorted(candidates, key=lambda item: (-item[0], item[1]))[0]
            self.bundle.append(slot_id)
            self.winner_table[slot_id] = self._own_record(slot_id)
            changed = True
        return changed

    def merge_winner_table(
        self,
        incoming: dict[str, dict[str, Any]],
        *,
        active_agent_ids: Iterable[str] | None = None,
    ) -> bool:
        changed = self.reconcile_active_agents(active_agent_ids)
        active = set(str(item) for item in active_agent_ids or [])
        for slot_id in sorted(self.local_tasks):
            candidate = deepcopy((incoming or {}).get(slot_id))
            if not candidate:
                continue
            winner = str(candidate.get("winner_agent_id") or "")
            if not winner or (active and winner not in active):
                continue
            candidate["bid"] = round(_as_float(candidate.get("bid"), float("-inf")), 6)
            if better_winner(candidate, self.winner_table.get(slot_id)):
                self.winner_table[slot_id] = candidate
                changed = True
        return self.prune_lost_bundle() or changed

    def reconcile_active_agents(
        self,
        active_agent_ids: Iterable[str] | None,
    ) -> bool:
        if active_agent_ids is None:
            return False
        active = {str(item) for item in active_agent_ids}
        removed = [
            slot_id
            for slot_id, record in self.winner_table.items()
            if str(record.get("winner_agent_id")) not in active
        ]
        for slot_id in removed:
            self.winner_table.pop(slot_id, None)
        return bool(removed)

    def release_all(self) -> bool:
        changed = bool(self.bundle)
        for slot_id in list(self.bundle):
            if self.winner_table.get(slot_id, {}).get("winner_agent_id") == self.agent_id:
                self.winner_table.pop(slot_id, None)
                changed = True
        self.bundle = []
        return changed

    def winner_table_digest(self) -> str:
        encoded = json.dumps(
            self.winner_table_snapshot(),
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        return sha256(encoded).hexdigest()

    def winner_table_snapshot(self) -> dict[str, dict[str, Any]]:
        return {
            slot_id: deepcopy(self.winner_table[slot_id])
            for slot_id in sorted(self.winner_table)
        }

    def snapshot(self) -> dict[str, Any]:
        return {
            "schema_version": SCHEMA_VERSION,
            "algorithm": ALGORITHM_ID,
            "coordination_id": self.coordination_id,
            "agent_id": self.agent_id,
            "round": self.round,
            "available": self.available,
            "local_task_view": [deepcopy(self.local_tasks[key]) for key in sorted(self.local_tasks)],
            "resource_state": deepcopy(self.resource_state),
            "candidate_bundle": list(self.bundle),
            "local_bids": {
                key: value for key, value in sorted(self.local_bids.items()) if value != float("-inf")
            },
            "winner_table": self.winner_table_snapshot(),
            "winner_table_digest": self.winner_table_digest(),
        }


def _assignment_rows(winner_table: dict[str, dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        {
            "task_id": record["task_id"],
            "slot_id": slot_id,
            "winner_agent_id": record["winner_agent_id"],
            "bid": record["bid"],
        }
        for slot_id, record in sorted(winner_table.items())
    ]


def run_local_cbba_consensus(
    tasks: Iterable[dict[str, Any]],
    agents: Iterable[dict[str, Any]],
    *,
    coordination_id: str = "CBBA-LOCAL",
    max_rounds: int = 32,
) -> dict[str, Any]:
    """Run deterministic rounds over independent local participant states."""
    participants = [
        CBBAParticipant(
            str(agent.get("agent_id") or agent.get("id")),
            resource_state=agent,
            tasks=tasks,
            coordination_id=coordination_id,
            max_bundle_size=agent.get("max_bundle_size"),
        )
        for agent in agents
        if agent.get("agent_id") or agent.get("id")
    ]
    participants = sorted(
        (participant for participant in participants if participant.available),
        key=lambda participant: participant.agent_id,
    )
    return converge_participants(
        participants,
        tasks=tasks,
        coordination_id=coordination_id,
        max_rounds=max_rounds,
    )


def converge_participants(
    participants: Iterable[CBBAParticipant],
    *,
    tasks: Iterable[dict[str, Any]] | None = None,
    coordination_id: str = "CBBA-LOCAL",
    max_rounds: int = 32,
) -> dict[str, Any]:
    """Advance existing independent participant states until they agree."""
    participants = sorted(
        (participant for participant in participants if participant.available),
        key=lambda participant: participant.agent_id,
    )
    active_ids = [participant.agent_id for participant in participants]
    if tasks is None:
        tasks = (
            list(participants[0].local_tasks.values()) if participants else []
        )
    consensus_reached = False
    rounds_completed = 0
    for round_index in range(1, max(1, int(max_rounds)) + 1):
        rounds_completed = round_index
        for participant in participants:
            participant.round = round_index
            participant.build_bundle()
        tables = [participant.winner_table_snapshot() for participant in participants]
        for participant in participants:
            for table in tables:
                participant.merge_winner_table(table, active_agent_ids=active_ids)
            participant.build_bundle()
        digests = {participant.winner_table_digest() for participant in participants}
        if len(digests) == 1:
            consensus_reached = True
            break

    winner_table = participants[0].winner_table_snapshot() if consensus_reached and participants else {}
    assignments = _assignment_rows(winner_table)
    return {
        "schema_version": SCHEMA_VERSION,
        "algorithm": ALGORITHM_ID,
        "coordination_id": coordination_id,
        "consensus_reached": consensus_reached,
        "rounds_completed": rounds_completed,
        "participant_ids": active_ids,
        "winner_table": winner_table,
        "assignments": assignments,
        "unassigned_slots": sorted(
            set(slot["slot_id"] for slot in expand_task_slots(tasks))
            - set(winner_table)
        ),
        "participant_states": [participant.snapshot() for participant in participants],
        "authorization_required": True,
        "execution_dispatched": False,
    }


def apply_assignments_to_tasks(
    tasks: Iterable[dict[str, Any]],
    allocation: dict[str, Any],
) -> list[dict[str, Any]]:
    """Attach converged slot winners without changing task intent or authorization."""
    by_task: dict[str, list[dict[str, Any]]] = {}
    for row in allocation.get("assignments") or []:
        by_task.setdefault(str(row.get("task_id")), []).append(row)
    result = []
    for index, raw in enumerate(tasks or [], start=1):
        task = deepcopy(raw)
        task_id = _task_id(task, index)
        rows = sorted(by_task.get(task_id, []), key=lambda item: str(item.get("slot_id")))
        task.update(
            {
                "assigned_resources": [str(row["winner_agent_id"]) for row in rows],
                "assignment_slot_ids": [str(row["slot_id"]) for row in rows],
                "assignment_source": ALGORITHM_ID,
                "assignment_locked": bool(allocation.get("consensus_reached")),
                "coordination_id": allocation.get("coordination_id"),
            }
        )
        result.append(task)
    return result
