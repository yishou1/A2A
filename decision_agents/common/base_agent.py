"""Shared deterministic agent wrapper used before full A2A wiring."""

from __future__ import annotations

import json

from collections.abc import AsyncIterable
from typing import Any

from pydantic import ValidationError

from decision_agents.common.llm_enhancer import (
    explain_response,
    llm_enabled,
    parse_natural_language,
)
from decision_agents.common.schemas import AgentRequest, AgentResponse
from llm.client import LLMClientError


class AlgorithmAgent:
    """Small stream-compatible wrapper around pure algorithm functions."""

    SUPPORTED_CONTENT_TYPES = ["text", "text/plain"]
    agent_name = "algorithm_agent"

    async def stream(self, query: str, context_id: str) -> AsyncIterable[dict[str, Any]]:
        del context_id
        yield {
            "is_task_complete": False,
            "require_user_input": False,
            "content": f"{self.agent_name} processing input...",
        }
        response = self.handle_query(query)
        is_error = response.status == "error"
        yield {
            "is_task_complete": response.status == "completed",
            "require_user_input": response.status == "input_required" or is_error,
            "content": response.model_dump_json(indent=2),
        }

    def handle_query(self, query: str) -> AgentResponse:
        try:
            payload = json.loads(query)
        except json.JSONDecodeError:
            if llm_enabled():
                return self._handle_natural_language(query)
            return AgentResponse(
                status="input_required",
                agent=self.agent_name,  # type: ignore[arg-type]
                summary="Input must be a JSON object for this demo stage.",
                warnings=["non_json_input"],
            )
        try:
            request = AgentRequest.model_validate(payload)
        except ValidationError as exc:
            return AgentResponse(
                status="input_required",
                agent=self.agent_name,  # type: ignore[arg-type]
                summary="Input JSON does not match the shared request schema.",
                warnings=[str(exc)],
            )
        return self.run(request)

    def _handle_natural_language(self, query: str) -> AgentResponse:
        try:
            parsed = parse_natural_language(agent_name=self.agent_name, query=query)
        except LLMClientError as exc:
            return AgentResponse(
                status="input_required",
                agent=self.agent_name,  # type: ignore[arg-type]
                summary="LLM could not convert the natural-language input into a valid AgentRequest.",
                warnings=[f"llm_parse_failed:{exc}"],
            )
        response = self.run(parsed.request)
        try:
            return explain_response(
                agent_name=self.agent_name,
                query=query,
                request=parsed.request,
                response=response,
                enhancement=parsed.enhancement,
            )
        except LLMClientError as exc:
            warnings = [*response.warnings, f"llm_explanation_failed:{exc}"]
            return response.model_copy(
                update={
                    "warnings": warnings,
                    "result": {
                        **response.result,
                        "llm_enhancement": {
                            **parsed.enhancement,
                            "explanation": "",
                        },
                    },
                }
            )

    def run(self, request: AgentRequest) -> AgentResponse:
        raise NotImplementedError
