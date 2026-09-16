"""Round orchestration for Agent-to-Agent CBBA consensus."""

from __future__ import annotations

from copy import deepcopy
from typing import Any, Callable

from services.a2a_algorithms_common.distributed_cbba import (
    ALGORITHM_ID,
    SCHEMA_VERSION,
)


class A2ACBBAOrchestrator:
    """Start rounds and verify consensus without calculating task winners."""

    def __init__(
        self,
        *,
        client_factory: Callable[[dict[str, Any]], Any] | None = None,
    ):
        self.client_factory = client_factory

    @staticmethod
    def _participant_id(participant: dict[str, Any]) -> str:
        return str(
            participant.get("agent_id")
            or participant.get("id")
            or f"{participant.get('ip')}:{participant.get('port')}"
        )

    def _client(self, participant: dict[str, Any]):
        if self.client_factory is not None:
            return self.client_factory(participant)
        from a2a_protocol.client import A2AClient

        return A2AClient(participant.get("ip"), participant.get("port"))

    def _exchange(self, participant: dict[str, Any], message: dict[str, Any]) -> dict[str, Any]:
        client = self._client(participant)
        return client.exchange_coordination(message)

    def coordinate(
        self,
        tasks: list[dict[str, Any]],
        participants: list[dict[str, Any]],
        *,
        coordination_id: str,
        max_rounds: int = 16,
    ) -> dict[str, Any]:
        ordered = sorted(
            (deepcopy(item) for item in participants),
            key=self._participant_id,
        )
        active: list[dict[str, Any]] = []
        failures: list[dict[str, Any]] = []
        for participant in ordered:
            participant_id = self._participant_id(participant)
            try:
                self._exchange(
                    participant,
                    {
                        "schema_version": SCHEMA_VERSION,
                        "message_type": "initialize",
                        "coordination_id": coordination_id,
                        "participant_id": participant_id,
                        "tasks": deepcopy(tasks),
                        "round": 0,
                    },
                )
                participant["agent_id"] = participant_id
                active.append(participant)
            except Exception as exc:
                failures.append({"agent_id": participant_id, "error": str(exc)})

        consensus_reached = False
        states: list[dict[str, Any]] = []
        rounds_completed = 0
        for round_index in range(1, max(1, int(max_rounds)) + 1):
            rounds_completed = round_index
            active_ids = [item["agent_id"] for item in active]
            survivors: list[dict[str, Any]] = []
            for participant in active:
                peers = [
                    {
                        "agent_id": peer["agent_id"],
                        "ip": peer.get("ip"),
                        "port": peer.get("port"),
                    }
                    for peer in active
                    if peer["agent_id"] != participant["agent_id"]
                ]
                try:
                    self._exchange(
                        participant,
                        {
                            "schema_version": SCHEMA_VERSION,
                            "message_type": "synchronize",
                            "coordination_id": coordination_id,
                            "round": round_index,
                            "active_agent_ids": active_ids,
                            "peers": peers,
                        },
                    )
                    survivors.append(participant)
                except Exception as exc:
                    failures.append(
                        {"agent_id": participant["agent_id"], "error": str(exc)}
                    )
            active = survivors
            active_ids = [item["agent_id"] for item in active]
            states = []
            for participant in list(active):
                try:
                    state = self._exchange(
                        participant,
                        {
                            "schema_version": SCHEMA_VERSION,
                            "message_type": "reconcile",
                            "coordination_id": coordination_id,
                            "round": round_index,
                            "active_agent_ids": active_ids,
                        },
                    )
                    states.append(state)
                except Exception as exc:
                    active.remove(participant)
                    failures.append(
                        {"agent_id": participant["agent_id"], "error": str(exc)}
                    )
            digests = {
                str(state.get("winner_table_digest"))
                for state in states
                if state.get("winner_table_digest")
            }
            if states and len(states) == len(active) and len(digests) == 1:
                consensus_reached = True
                break

        winner_table = states[0].get("winner_table", {}) if consensus_reached and states else {}
        assignments = [
            {
                "task_id": record.get("task_id"),
                "slot_id": slot_id,
                "winner_agent_id": record.get("winner_agent_id"),
                "bid": record.get("bid"),
            }
            for slot_id, record in sorted(winner_table.items())
        ]
        return {
            "schema_version": SCHEMA_VERSION,
            "algorithm": ALGORITHM_ID,
            "coordination_id": coordination_id,
            "consensus_reached": consensus_reached,
            "rounds_completed": rounds_completed,
            "participant_ids": [item["agent_id"] for item in active],
            "winner_table": winner_table,
            "assignments": assignments,
            "participant_states": states,
            "failures": failures,
            "authorization_required": True,
            "execution_dispatched": False,
            "transport": "a2a_agent_to_agent",
        }
