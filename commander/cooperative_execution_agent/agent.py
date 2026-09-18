from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
import os
from pathlib import Path
import re
from typing import Any, Callable

from fastapi import Depends, HTTPException

from a2a_protocol.server import A2ABaseAgent, verify_token
from cooperative_execution_agent.state import (
    ExecutionStateError,
    ExecutionStateStore,
    TERMINAL_STATES,
)


COMMANDS = {
    "reserve_cooperative_slot",
    "prepare_cooperative_slot",
    "record_execution_event",
    "abort_cooperative_slot",
    "get_cooperative_execution_status",
}
SHA256_RE = re.compile(r"^[0-9a-fA-F]{64}$")


def _tokens(value: Any) -> list[str]:
    if value in (None, ""):
        return []
    if isinstance(value, str):
        values = value.replace(",", " ").replace(";", " ").split()
    else:
        values = list(value)
    return sorted({str(item).strip() for item in values if str(item).strip()})


def _required_text(value: Any, field: str) -> str:
    text = str(value or "").strip()
    if not text:
        raise ExecutionStateError(f"{field} is required")
    return text


def _parse_timestamp(value: Any, field: str) -> datetime:
    text = _required_text(value, field)
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ExecutionStateError(f"{field} must be ISO-8601") from exc
    if parsed.tzinfo is None:
        raise ExecutionStateError(f"{field} must include a timezone")
    return parsed.astimezone(timezone.utc)


class CooperativeExecutionAgent(A2ABaseAgent):
    """A real multi-process coordination node with no built-in actuator.

    The Agent reserves assigned slots, reaches peer consensus on readiness and
    records externally observed execution events. It never fabricates an
    execution outcome and does not contain a weapon/device control adapter.
    """

    def __init__(
        self,
        *,
        agent_id: str,
        port: int,
        state_db: str | Path,
        resource_types: list[str] | str,
        capabilities: list[str] | str,
        allowed_event_sources: list[str] | str = (),
        execution_client_factory: Callable[[dict[str, Any]], Any] | None = None,
        **kwargs,
    ):
        self.agent_id = _required_text(agent_id, "agent_id")
        self.resource_types = _tokens(resource_types)
        self.execution_capabilities = _tokens(capabilities)
        if not self.resource_types:
            raise ValueError("resource_types must not be empty")
        if not self.execution_capabilities:
            raise ValueError("capabilities must not be empty")
        self.allowed_event_sources = set(_tokens(allowed_event_sources))
        self.execution_store = ExecutionStateStore(state_db)
        self.execution_client_factory = execution_client_factory
        skills = [
            {
                "id": command,
                "name": command.replace("_", " ").title(),
                "description": "Non-actuating cooperative execution control operation.",
                "tags": ["cooperative_execution", "distributed", "non_actuating"],
            }
            for command in sorted(COMMANDS)
        ]
        super().__init__(
            name=self.agent_id,
            description=(
                "Distributed cooperative execution control node. Records only "
                "verified external events and has no built-in actuator."
            ),
            role="cooperative_execution",
            port=port,
            skills=skills,
            **kwargs,
        )
        self._setup_execution_routes()

    def get_agent_card(self) -> dict[str, Any]:
        card = super().get_agent_card()
        card["executionCoordinationEndpoint"] = "/execution/coordination"
        card["executionSessionsEndpoint"] = "/execution/sessions"
        card["actuatorConnected"] = False
        return card

    def _coordination_resource_state(self) -> dict[str, Any]:
        state = super()._coordination_resource_state()
        active_sessions = len(
            [
                row
                for row in self.execution_store.list_sessions()
                if row.get("status") not in TERMINAL_STATES
            ]
        )
        available_slots = max(0, self.max_concurrent_tasks - active_sessions)
        state.update(
            {
                "agent_id": self.agent_id,
                "role": self.role,
                "resource_types": list(self.resource_types),
                "capabilities": list(self.execution_capabilities),
                "skills": list(self.execution_capabilities),
                "active_tasks": active_sessions,
                "available_task_slots": available_slots,
                "load": active_sessions / self.max_concurrent_tasks,
                "available": bool(self.ready and available_slots > 0),
            }
        )
        return state

    def heartbeat_metadata(self) -> dict[str, Any]:
        metadata = super().heartbeat_metadata()
        sessions = self.execution_store.list_sessions()
        active = [row for row in sessions if row.get("status") not in TERMINAL_STATES]
        metadata.update(
            {
                "agent_id": self.agent_id,
                "resource_types": ",".join(self.resource_types),
                "execution_capabilities": ",".join(self.execution_capabilities),
                "execution_sessions_active": str(len(active)),
                "active_tasks": str(len(active)),
                "available_task_slots": str(
                    max(0, self.max_concurrent_tasks - len(active))
                ),
                "status": "busy" if active else "idle",
                "execution_adapter_status": "external_event_only",
                "actuator_connected": "false",
            }
        )
        return metadata

    @staticmethod
    def _authorization(arguments: dict[str, Any]) -> tuple[str, dict[str, Any]]:
        authorization = arguments.get("authorization")
        if not isinstance(authorization, dict):
            raise ExecutionStateError("authorization object is required")
        decision = str(
            authorization.get("decision") or authorization.get("status") or ""
        ).lower()
        if decision not in {"approved", "authorized"}:
            raise ExecutionStateError("execution requires approved authorization")
        reference = _required_text(
            authorization.get("authorization_id")
            or authorization.get("approval_id")
            or authorization.get("reference"),
            "authorization reference",
        )
        expires_at = authorization.get("expires_at")
        if expires_at and _parse_timestamp(expires_at, "authorization.expires_at") <= datetime.now(timezone.utc):
            raise ExecutionStateError("authorization has expired")
        return reference, deepcopy(authorization)

    def _assignment(self, arguments: dict[str, Any]) -> tuple[dict[str, Any], str, str, str]:
        assignment = arguments.get("assignment")
        if not isinstance(assignment, dict):
            assignment = arguments
        coordination_id = _required_text(assignment.get("coordination_id"), "coordination_id")
        task_id = _required_text(assignment.get("task_id"), "task_id")
        slot_id = _required_text(assignment.get("slot_id"), "slot_id")
        assigned = _tokens(
            assignment.get("assigned_resources")
            or assignment.get("winner_agent_id")
        )
        if self.agent_id not in assigned:
            raise ExecutionStateError(
                f"slot {slot_id} is not assigned to {self.agent_id}"
            )
        if not bool(assignment.get("assignment_locked", False)):
            raise ExecutionStateError("assignment must be consensus-locked")
        if str(assignment.get("assignment_source") or "") != "deterministic_cbba":
            raise ExecutionStateError("assignment_source must be deterministic_cbba")
        return deepcopy(assignment), coordination_id, task_id, slot_id

    def _arguments(self, payload: dict[str, Any]) -> dict[str, Any]:
        value = payload.get("input")
        return deepcopy(value) if isinstance(value, dict) else {}

    def execute_task(self, payload: dict[str, Any]):
        command = str(payload.get("command") or "")
        if command not in COMMANDS:
            raise ExecutionStateError(f"unsupported command: {command}")
        arguments = self._arguments(payload)
        if command == "reserve_cooperative_slot":
            result = self._reserve(arguments)
        elif command == "prepare_cooperative_slot":
            result = self._prepare(arguments)
        elif command == "record_execution_event":
            result = self._record_event(arguments)
        elif command == "abort_cooperative_slot":
            result = self._abort(arguments)
        else:
            result = self._status(arguments)
        return {"cooperative_execution_result": result}, f"{command} accepted"

    def _reserve(self, arguments: dict[str, Any]) -> dict[str, Any]:
        authorization_ref, _ = self._authorization(arguments)
        assignment, coordination_id, task_id, slot_id = self._assignment(arguments)
        participant_ids = _tokens(
            arguments.get("participant_ids") or assignment.get("assigned_resources")
        )
        if self.agent_id not in participant_ids:
            participant_ids.append(self.agent_id)
        planned_start_at = arguments.get("planned_start_at")
        if planned_start_at:
            _parse_timestamp(planned_start_at, "planned_start_at")
        session = self.execution_store.reserve(
            coordination_id=coordination_id,
            slot_id=slot_id,
            task_id=task_id,
            agent_id=self.agent_id,
            authorization_ref=authorization_ref,
            participant_ids=participant_ids,
            assignment=assignment,
            planned_start_at=str(planned_start_at) if planned_start_at else None,
            max_active_sessions=self.max_concurrent_tasks,
        )
        return self._result(session)

    def _prepare(self, arguments: dict[str, Any]) -> dict[str, Any]:
        authorization_ref, _ = self._authorization(arguments)
        coordination_id = _required_text(arguments.get("coordination_id"), "coordination_id")
        slot_id = _required_text(arguments.get("slot_id"), "slot_id")
        session = self.execution_store.get(coordination_id, slot_id)
        if not session or session.get("agent_id") != self.agent_id:
            raise ExecutionStateError("execution slot is not reserved by this Agent")
        if session.get("authorization_ref") != authorization_ref:
            raise ExecutionStateError("authorization does not match the slot reservation")
        session = self.execution_store.transition(
            coordination_id,
            slot_id,
            "ready",
            reason="local resource and authorization checks passed",
        )
        return self._result(session)

    def _record_event(self, arguments: dict[str, Any]) -> dict[str, Any]:
        authorization_ref, _ = self._authorization(arguments)
        coordination_id = _required_text(arguments.get("coordination_id"), "coordination_id")
        slot_id = _required_text(arguments.get("slot_id"), "slot_id")
        event = arguments.get("event")
        if not isinstance(event, dict):
            raise ExecutionStateError("event object is required")
        event_id = _required_text(event.get("event_id"), "event.event_id")
        event_type = _required_text(event.get("event_type"), "event.event_type").lower()
        if event_type not in {"started", "completed", "failed", "aborted"}:
            raise ExecutionStateError("unsupported external event_type")
        source = _required_text(event.get("source"), "event.source")
        if not self.allowed_event_sources or source not in self.allowed_event_sources:
            raise ExecutionStateError("external event source is not allowlisted")
        observed_at = _required_text(event.get("observed_at"), "event.observed_at")
        observed = _parse_timestamp(observed_at, "event.observed_at")
        evidence = event.get("evidence")
        if not isinstance(evidence, dict):
            raise ExecutionStateError("event.evidence object is required")
        _required_text(evidence.get("uri"), "event.evidence.uri")
        checksum = _required_text(evidence.get("sha256"), "event.evidence.sha256")
        if not SHA256_RE.fullmatch(checksum):
            raise ExecutionStateError("event.evidence.sha256 must be a SHA-256 hex digest")
        session = self.execution_store.get(coordination_id, slot_id)
        if not session:
            raise ExecutionStateError("unknown execution slot")
        if session.get("authorization_ref") != authorization_ref:
            raise ExecutionStateError("authorization does not match the slot reservation")
        if event_type == "started" and session.get("planned_start_at"):
            planned = _parse_timestamp(session["planned_start_at"], "planned_start_at")
            tolerance = float(os.environ.get("COOP_EXECUTION_CLOCK_TOLERANCE_SECONDS", "2"))
            if (observed - planned).total_seconds() < -tolerance:
                raise ExecutionStateError("started event precedes the synchronized start window")
        session, duplicate = self.execution_store.record_event(
            event_id=event_id,
            coordination_id=coordination_id,
            slot_id=slot_id,
            event_type=event_type,
            source=source,
            observed_at=observed_at,
            evidence=deepcopy(evidence),
        )
        result = self._result(session)
        result["event_duplicate"] = duplicate
        return result

    def _abort(self, arguments: dict[str, Any]) -> dict[str, Any]:
        authorization_ref, _ = self._authorization(arguments)
        coordination_id = _required_text(arguments.get("coordination_id"), "coordination_id")
        slot_id = _required_text(arguments.get("slot_id"), "slot_id")
        reason = _required_text(arguments.get("reason"), "reason")
        session = self.execution_store.get(coordination_id, slot_id)
        if not session:
            raise ExecutionStateError("unknown execution slot")
        if session.get("authorization_ref") != authorization_ref:
            raise ExecutionStateError("authorization does not match the slot reservation")
        session = self.execution_store.transition(
            coordination_id, slot_id, "aborted", reason=reason
        )
        return self._result(session)

    def _status(self, arguments: dict[str, Any]) -> dict[str, Any]:
        coordination_id = _required_text(arguments.get("coordination_id"), "coordination_id")
        slot_id = _required_text(arguments.get("slot_id"), "slot_id")
        session = self.execution_store.get(coordination_id, slot_id)
        if not session:
            raise ExecutionStateError("unknown execution slot")
        return self._result(session)

    def _result(self, session: dict[str, Any]) -> dict[str, Any]:
        return {
            "schema_version": "cooperative-execution/v1",
            "agent_id": self.agent_id,
            "session": deepcopy(session),
            "events": self.execution_store.events(
                str(session.get("coordination_id")), str(session.get("slot_id"))
            ),
            "actuator_connected": False,
            "execution_data_source": "verified_external_events",
            "fabricated_effects": False,
        }

    def execution_coordination(self, payload: dict[str, Any]) -> dict[str, Any]:
        message_type = str(payload.get("message_type") or "").lower()
        coordination_id = _required_text(payload.get("coordination_id"), "coordination_id")
        slot_id = payload.get("slot_id")
        if message_type == "state_request":
            sessions = [
                row
                for row in self.execution_store.list_sessions()
                if row.get("coordination_id") == coordination_id
                and (not slot_id or row.get("slot_id") == slot_id)
            ]
            return {
                "schema_version": "cooperative-execution-coordination/v1",
                "agent_id": self.agent_id,
                "coordination_id": coordination_id,
                "sessions": sessions,
            }
        if message_type != "synchronize":
            raise ExecutionStateError("unsupported execution coordination message_type")
        local_slot_id = _required_text(payload.get("local_slot_id"), "local_slot_id")
        local = self.execution_store.get(coordination_id, local_slot_id)
        if not local or local.get("status") not in {"ready", "synchronized"}:
            raise ExecutionStateError("local slot is not ready for synchronization")
        states = {self.agent_id: local}
        peer_results = []
        for peer in sorted(payload.get("peers") or [], key=lambda row: str(row.get("agent_id"))):
            peer_id = _required_text(peer.get("agent_id"), "peer.agent_id")
            try:
                if self.execution_client_factory:
                    client = self.execution_client_factory(peer)
                else:
                    from a2a_protocol.client import A2AClient

                    client = A2AClient(peer.get("ip"), peer.get("port"))
                response = client.exchange_execution_coordination(
                    {
                        "message_type": "state_request",
                        "coordination_id": coordination_id,
                    }
                )
                peer_sessions = response.get("sessions") or []
                ready = next(
                    (
                        row
                        for row in peer_sessions
                        if row.get("agent_id") == peer_id
                        and row.get("status") in {"ready", "synchronized"}
                    ),
                    None,
                )
                if ready:
                    states[peer_id] = ready
                peer_results.append({"agent_id": peer_id, "received": True, "ready": bool(ready)})
            except Exception as exc:
                peer_results.append(
                    {"agent_id": peer_id, "received": False, "ready": False, "error": str(exc)}
                )
        expected = set(local.get("participant_ids") or [])
        all_ready = bool(expected) and expected.issubset(states)
        start_times = {
            str(row.get("planned_start_at"))
            for agent_id, row in states.items()
            if agent_id in expected
        }
        start_consistent = len(start_times) == 1 and None not in start_times and "None" not in start_times
        if all_ready and start_consistent:
            local = self.execution_store.transition(
                coordination_id,
                local_slot_id,
                "synchronized",
                reason="all distributed participants reported ready",
            )
        return {
            "schema_version": "cooperative-execution-coordination/v1",
            "agent_id": self.agent_id,
            "coordination_id": coordination_id,
            "all_ready": all_ready,
            "start_time_consistent": start_consistent,
            "synchronized": local.get("status") == "synchronized",
            "participant_states": states,
            "peer_results": peer_results,
            "session": local,
        }

    def _setup_execution_routes(self) -> None:
        @self.app.post("/execution/coordination")
        async def execution_coordination_route(
            payload: dict, token: str = Depends(verify_token)
        ):
            try:
                return self.execution_coordination(payload)
            except ExecutionStateError as exc:
                raise HTTPException(status_code=422, detail=str(exc)) from exc

        @self.app.get("/execution/sessions")
        async def execution_sessions(token: str = Depends(verify_token)):
            return {
                "agent_id": self.agent_id,
                "sessions": self.execution_store.list_sessions(),
                "actuator_connected": False,
            }
