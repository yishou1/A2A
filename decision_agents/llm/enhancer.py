"""Optional LLM enhancement for natural-language parsing and explanations."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol

from pydantic import ValidationError

from decision_agents.config import get_settings
from decision_agents.llm.client import LLMClientError, OpenAICompatibleClient
from decision_agents.llm.prompts import (
    EXPLAIN_SYSTEM_PROMPT,
    PARSE_SYSTEM_PROMPT,
    explain_user_prompt,
    parse_user_prompt,
)
from decision_agents.schemas import AgentRequest, AgentResponse


class ChatJSONClient(Protocol):
    def chat_json(self, *, system_prompt: str, user_prompt: str) -> dict[str, Any]:
        ...


@dataclass
class ParsedLLMRequest:
    request: AgentRequest
    enhancement: dict[str, Any] = field(default_factory=dict)


def llm_enabled() -> bool:
    return get_settings().enable_llm


def parse_natural_language(
    *,
    agent_name: str,
    query: str,
    client: ChatJSONClient | None = None,
) -> ParsedLLMRequest:
    client = client or OpenAICompatibleClient(get_settings())
    payload = client.chat_json(
        system_prompt=PARSE_SYSTEM_PROMPT,
        user_prompt=parse_user_prompt(agent_name, query),
    )
    request_payload = payload.get("request")
    if not isinstance(request_payload, dict):
        raise LLMClientError("LLM parse result must include a request object.")
    try:
        request = AgentRequest.model_validate(request_payload)
    except ValidationError as exc:
        raise LLMClientError(f"LLM request object failed schema validation: {exc}") from exc
    enhancement = {
        "enabled": True,
        "intent": str(payload.get("intent") or ""),
        "selected_algorithm_reason": str(payload.get("selected_algorithm_reason") or ""),
        "explanation": "",
        "missing_fields_advice": _string_list(payload.get("missing_fields_advice")),
    }
    return ParsedLLMRequest(request=request, enhancement=enhancement)


def explain_response(
    *,
    agent_name: str,
    query: str,
    request: AgentRequest,
    response: AgentResponse,
    enhancement: dict[str, Any],
    client: ChatJSONClient | None = None,
) -> AgentResponse:
    del query
    client = client or OpenAICompatibleClient(get_settings())
    payload = client.chat_json(
        system_prompt=EXPLAIN_SYSTEM_PROMPT,
        user_prompt=explain_user_prompt(
            agent_name,
            request.model_dump_json(indent=2),
            response.model_dump_json(indent=2),
        ),
    )
    merged = {
        **enhancement,
        "enabled": True,
        "explanation": str(payload.get("explanation") or ""),
    }
    advice = _string_list(payload.get("missing_fields_advice"))
    if advice:
        merged["missing_fields_advice"] = advice
    return attach_enhancement(response, merged)


def attach_enhancement(
    response: AgentResponse,
    enhancement: dict[str, Any],
) -> AgentResponse:
    result = dict(response.result)
    result["llm_enhancement"] = {
        "enabled": bool(enhancement.get("enabled")),
        "intent": str(enhancement.get("intent") or ""),
        "selected_algorithm_reason": str(enhancement.get("selected_algorithm_reason") or ""),
        "explanation": str(enhancement.get("explanation") or ""),
        "missing_fields_advice": _string_list(enhancement.get("missing_fields_advice")),
    }
    return response.model_copy(update={"result": result})


def _string_list(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    return [str(item) for item in value]

