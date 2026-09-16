"""Agent-local CBBA session storage and message handling."""

from __future__ import annotations

from collections import OrderedDict
from copy import deepcopy
import threading
from typing import Any, Callable

from services.a2a_algorithms_common.distributed_cbba import (
    SCHEMA_VERSION,
    CBBAParticipant,
)


class AgentCoordinationStore:
    """Keep coordination state separate from workflow and execution state."""

    def __init__(
        self,
        agent_id: str,
        resource_state_provider: Callable[[], dict[str, Any]],
        *,
        max_sessions: int = 64,
    ):
        self.agent_id = str(agent_id)
        self.resource_state_provider = resource_state_provider
        self.max_sessions = max(1, int(max_sessions))
        self._sessions: OrderedDict[str, CBBAParticipant] = OrderedDict()
        self._lock = threading.RLock()

    def _session(self, coordination_id: str) -> CBBAParticipant:
        try:
            session = self._sessions[str(coordination_id)]
        except KeyError as exc:
            raise KeyError(f"unknown coordination_id: {coordination_id}") from exc
        self._sessions.move_to_end(str(coordination_id))
        return session

    def initialize(self, message: dict[str, Any]) -> dict[str, Any]:
        coordination_id = str(message.get("coordination_id") or "").strip()
        if not coordination_id:
            raise ValueError("coordination_id is required")
        participant_id = str(message.get("participant_id") or self.agent_id)
        with self._lock:
            participant = CBBAParticipant(
                participant_id,
                resource_state=self.resource_state_provider(),
                tasks=message.get("tasks") or [],
                coordination_id=coordination_id,
                max_bundle_size=message.get("max_bundle_size"),
            )
            participant.round = max(0, int(message.get("round") or 0))
            participant.build_bundle()
            self._sessions[coordination_id] = participant
            self._sessions.move_to_end(coordination_id)
            while len(self._sessions) > self.max_sessions:
                self._sessions.popitem(last=False)
            return participant.snapshot()

    def state(self, coordination_id: str) -> dict[str, Any]:
        with self._lock:
            participant = self._session(coordination_id)
            participant.update_resource_state(self.resource_state_provider())
            participant.build_bundle()
            return participant.snapshot()

    def merge(self, message: dict[str, Any]) -> dict[str, Any]:
        coordination_id = str(message.get("coordination_id") or "")
        with self._lock:
            participant = self._session(coordination_id)
            participant.round = max(participant.round, int(message.get("round") or 0))
            participant.update_resource_state(self.resource_state_provider())
            changed = participant.merge_winner_table(
                deepcopy(message.get("winner_table") or {}),
                active_agent_ids=message.get("active_agent_ids"),
            )
            changed = participant.build_bundle() or changed
            snapshot = participant.snapshot()
            snapshot["changed"] = changed
            return snapshot

    def reconcile(self, message: dict[str, Any]) -> dict[str, Any]:
        coordination_id = str(message.get("coordination_id") or "")
        with self._lock:
            participant = self._session(coordination_id)
            participant.round = max(participant.round, int(message.get("round") or 0))
            changed = participant.reconcile_active_agents(
                message.get("active_agent_ids") or []
            )
            changed = participant.build_bundle() or changed
            snapshot = participant.snapshot()
            snapshot["changed"] = changed
            return snapshot

    def close(self, coordination_id: str) -> dict[str, Any]:
        with self._lock:
            removed = self._sessions.pop(str(coordination_id), None)
        return {
            "schema_version": SCHEMA_VERSION,
            "coordination_id": str(coordination_id),
            "agent_id": self.agent_id,
            "closed": removed is not None,
        }

    def handle(self, message: dict[str, Any]) -> dict[str, Any]:
        message_type = str(message.get("message_type") or "").strip().lower()
        if message_type == "initialize":
            return self.initialize(message)
        if message_type in {"state", "state_request"}:
            return self.state(str(message.get("coordination_id") or ""))
        if message_type in {"merge", "winner_table"}:
            return self.merge(message)
        if message_type == "reconcile":
            return self.reconcile(message)
        if message_type == "close":
            return self.close(str(message.get("coordination_id") or ""))
        raise ValueError(f"unsupported CBBA coordination message_type: {message_type}")
