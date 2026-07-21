"""Minimal A2A-compatible JSON-RPC service for the local agents."""

from __future__ import annotations

import json

from typing import Any
from uuid import uuid4

from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Route

from decision_agents.common.base_agent import AlgorithmAgent
from decision_agents.common.config import get_settings
from decision_agents.common.definitions import AGENT_DEFINITIONS as SHARED_AGENT_DEFINITIONS
from decision_agents.compliance_authorization.agent import ComplianceAuthorizationAgent
from decision_agents.decision_planning.agent import DecisionPlanningAgent


AGENT_CLASSES = {
    "decision_planning": DecisionPlanningAgent,
    "compliance_authorization": ComplianceAuthorizationAgent,
}

AGENT_DEFINITIONS = {
    key: {**definition, "agent_class": AGENT_CLASSES[key]}
    for key, definition in SHARED_AGENT_DEFINITIONS.items()
}


def build_agent_card(agent_key: str, host: str, port: int) -> dict[str, Any]:
    definition = AGENT_DEFINITIONS[agent_key]
    base_url = f"http://{host}:{port}"
    settings = get_settings()
    llm_note = (
        " Supports natural-language input when ENABLE_LLM=true; JSON AgentRequest input is always supported."
        if settings.enable_llm
        else " JSON AgentRequest input is supported; natural-language input requires ENABLE_LLM=true."
    )
    return {
        "name": definition["agent_name"],
        "description": f"{definition['description']}{llm_note}",
        "url": f"{base_url}/",
        "version": "0.1.0",
        "defaultInputModes": ["text", "text/plain"],
        "defaultOutputModes": ["text", "text/plain"],
        "capabilities": {
            "streaming": False,
            "pushNotifications": False,
        },
        "skills": [
            {
                "id": definition["skill_id"],
                "name": definition["skill_name"],
                "description": definition["description"],
                "tags": [
                    "project-613",
                    "decision-support",
                    "llm-enhanced",
                    definition["skill_id"],
                ],
                "examples": [
                    "Send a JSON AgentRequest payload through message/send.",
                    "When ENABLE_LLM=true, send natural-language task text through message/send.",
                ],
            }
        ],
    }


def build_app(agent_key: str, host: str, port: int) -> Starlette:
    definition = AGENT_DEFINITIONS[agent_key]
    agent = definition["agent_class"]()
    agent_card = build_agent_card(agent_key, host, port)

    async def agent_card_route(request: Request) -> JSONResponse:
        del request
        return JSONResponse(agent_card)

    async def message_send(request: Request) -> JSONResponse:
        try:
            payload = await request.json()
        except json.JSONDecodeError:
            return JSONResponse(_jsonrpc_error(None, -32700, "Invalid JSON"), status_code=400)
        if not isinstance(payload, dict):
            return JSONResponse(
                _jsonrpc_error(None, -32600, "JSON-RPC payload must be an object"),
                status_code=400,
            )
        request_id = payload.get("id")
        if payload.get("method") != "message/send":
            return JSONResponse(
                _jsonrpc_error(request_id, -32601, "Only message/send is supported"),
                status_code=404,
            )
        text = _extract_message_text(payload)
        if text is None:
            return JSONResponse(
                _jsonrpc_error(request_id, -32602, "message text part is required"),
                status_code=400,
            )
        response = agent.handle_query(text)
        return JSONResponse(
            {
                "jsonrpc": "2.0",
                "id": request_id,
                "result": _task_result(agent, definition["output_hint"], response),
            }
        )

    return Starlette(
        routes=[
            Route("/.well-known/agent-card.json", agent_card_route, methods=["GET"]),
            Route("/", message_send, methods=["POST"]),
        ]
    )


def _extract_message_text(payload: dict[str, Any]) -> str | None:
    message = (payload.get("params") or {}).get("message") or {}
    parts = message.get("parts") or []
    for part in parts:
        if isinstance(part, dict) and part.get("kind") == "text":
            return part.get("text")
        root = part.get("root") if isinstance(part, dict) else None
        if isinstance(root, dict) and root.get("kind") == "text":
            return root.get("text")
    return None


def _task_result(
    agent: AlgorithmAgent,
    artifact_name: str,
    response,
) -> dict[str, Any]:
    task_id = str(uuid4())
    context_id = str(uuid4())
    message_id = uuid4().hex
    text = response.model_dump_json(indent=2)
    if response.status == "completed":
        return {
            "id": task_id,
            "contextId": context_id,
            "kind": "task",
            "status": {"state": "completed"},
            "artifacts": [
                {
                    "artifactId": str(uuid4()),
                    "name": artifact_name,
                    "parts": [{"kind": "text", "text": text}],
                }
            ],
        }
    state = "input-required" if response.status == "input_required" else "failed"
    return {
        "id": task_id,
        "contextId": context_id,
        "kind": "task",
        "status": {
            "state": state,
            "message": {
                "kind": "message",
                "messageId": message_id,
                "contextId": context_id,
                "role": "agent",
                "parts": [{"kind": "text", "text": text}],
            },
        },
    }


def _jsonrpc_error(
    request_id: Any,
    code: int,
    message: str,
) -> dict[str, Any]:
    return {
        "jsonrpc": "2.0",
        "id": request_id,
        "error": {"code": code, "message": message},
    }
