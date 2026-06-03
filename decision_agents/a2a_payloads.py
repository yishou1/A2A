"""Bridge helpers between A2A workflow payloads and decision-agent schemas."""

from __future__ import annotations

import json

from pathlib import Path
from typing import Any

from decision_agents.schemas import AgentResponse


PROJECT_ROOT = Path(__file__).resolve().parents[1]

AGENT_REQUEST_FIELDS = {
    "request_id",
    "agent_profile",
    "algorithm_id",
    "algorithm_params",
    "observations",
    "tracks",
    "risk_assessments",
    "scheduled_tasks",
    "resources",
    "candidate_plans",
    "constraints",
    "authorization",
}

SAMPLE_INPUTS = {
    "track_threat_agent": "track_threat_input.json",
    "decision_planning_agent": "decision_planning_input.json",
    "compliance_authorization_agent": "compliance_authorization_input.json",
}


def build_agent_request_payload(agent_name: str, payload: dict[str, Any]) -> dict[str, Any]:
    explicit_request = _find_agent_request(payload)
    if explicit_request is not None:
        request_payload: dict[str, Any] = {}
        _merge_request_fields(request_payload, explicit_request, allow_empty=True)
        return request_payload

    request_payload = _load_sample_request(agent_name)
    context = payload.get("context") if isinstance(payload.get("context"), dict) else {}
    input_payload = payload.get("input") if isinstance(payload.get("input"), dict) else {}

    _merge_request_fields(request_payload, context)
    _merge_previous_agent_outputs(request_payload, context)
    _merge_request_fields(request_payload, input_payload)
    _merge_request_fields(request_payload, payload)

    workflow_id = payload.get("workflow_id") or context.get("workflow_id")
    if workflow_id and "request_id" not in request_payload:
        request_payload["request_id"] = str(workflow_id)
    return request_payload


def run_agent_payload(algorithm_agent, agent_name: str, payload: dict[str, Any]) -> AgentResponse:
    request_payload = build_agent_request_payload(agent_name, payload)
    return algorithm_agent.handle_query(json.dumps(request_payload, ensure_ascii=False))


def agent_response_to_a2a_response(
    *,
    payload: dict[str, Any],
    response: AgentResponse,
    agent_name: str,
    work_list_size: int,
) -> dict[str, Any]:
    response_payload = response.model_dump(mode="json")
    status = {
        "completed": "Completed",
        "input_required": "InputRequired",
        "error": "Error",
    }.get(response.status, response.status)
    return {
        "work_item": payload.get("work_item") or payload.get("task_id", "work-item-001"),
        "workflow_id": payload.get("workflow_id"),
        "status": status,
        "agent": agent_name,
        "message": response.summary,
        "selected_algorithms": list(response.selected_algorithms),
        "result": response.result,
        "rag_evidence": response.rag_evidence,
        "warnings": response.warnings,
        "agent_response": response_payload,
        "work_list_size": work_list_size,
    }


def _find_agent_request(payload: dict[str, Any]) -> dict[str, Any] | None:
    for candidate in (
        payload,
        payload.get("input") if isinstance(payload.get("input"), dict) else {},
        payload.get("context") if isinstance(payload.get("context"), dict) else {},
    ):
        agent_request = candidate.get("agent_request")
        if isinstance(agent_request, dict):
            return agent_request
    return None


def _load_sample_request(agent_name: str) -> dict[str, Any]:
    sample_name = SAMPLE_INPUTS.get(agent_name)
    if not sample_name:
        return {}
    sample_path = PROJECT_ROOT / "data" / "samples" / sample_name
    if not sample_path.exists():
        return {}
    return json.loads(sample_path.read_text(encoding="utf-8"))


def _merge_request_fields(
    target: dict[str, Any],
    source: dict[str, Any],
    *,
    allow_empty: bool = False,
) -> None:
    if not isinstance(source, dict):
        return
    for field in AGENT_REQUEST_FIELDS:
        if field in source and source[field] is not None:
            if not allow_empty and source[field] in ([], {}):
                continue
            target[field] = source[field]


def _merge_previous_agent_outputs(target: dict[str, Any], context: dict[str, Any]) -> None:
    track_result = _result_from_context(context, "track_threat")
    if track_result:
        if track_result.get("tracks"):
            target["tracks"] = track_result["tracks"]
        if track_result.get("risk_assessments"):
            target["risk_assessments"] = track_result["risk_assessments"]

    planning_result = _result_from_context(context, "decision_planning")
    if planning_result and planning_result.get("candidate_plans"):
        target["candidate_plans"] = planning_result["candidate_plans"]


def _result_from_context(context: dict[str, Any], role: str) -> dict[str, Any]:
    direct = context.get(f"{role}_result")
    if isinstance(direct, dict):
        return direct

    outputs = context.get("agent_outputs")
    if isinstance(outputs, dict):
        output = outputs.get(role) or outputs.get(f"{role}_agent")
        if isinstance(output, dict):
            result = output.get("result")
            if isinstance(result, dict):
                return result
            agent_response = output.get("agent_response")
            if isinstance(agent_response, dict) and isinstance(agent_response.get("result"), dict):
                return agent_response["result"]
    return {}
