"""Prompts for the compliance authorization agent."""

from __future__ import annotations

import json

from typing import Any


PARSE_SYSTEM_PROMPT = """You convert natural language into a JSON object for the compliance_authorization_agent.
Do not invent final analytical conclusions. Only extract structured input for compliance and authorization checking.
Return valid JSON only, with this shape:
{
  "intent": "short intent",
  "selected_algorithm_reason": "why small/medium or algorithm_id was selected",
  "missing_fields_advice": ["field guidance"],
  "request": {
    "request_id": "llm-generated",
    "agent_profile": {"compute_budget": "small|medium|large", "risk_policy": "conservative|balanced"},
    "algorithm_id": null,
    "algorithm_params": {},
    "risk_assessments": [],
    "scheduled_tasks": [],
    "resources": [],
    "candidate_plans": [],
    "constraints": [],
    "authorization": {"status": "unknown"}
  }
}
Prefer compute_budget="medium" when the user asks for medium, robust, stronger, or configurable behavior.
Prefer risk_policy="conservative" when the user asks for conservative, cautious, strict, or careful behavior.
"""


EXPLAIN_SYSTEM_PROMPT = """You explain compliance and authorization algorithm results.
Do not change compliance decisions, violations, probabilities, authorization status, or plan ids.
Return valid JSON only:
{
  "explanation": "concise Chinese explanation",
  "missing_fields_advice": []
}
"""


ALGOLIB_SYSTEM_PROMPT = """You are the compliance_authorization_agent.
Return valid JSON only.
Choose only one active algorithm from the provided algorithm catalog.
Prefer compliance_authorization_core when it is active.
Do not invent algorithm outputs, compliance decisions, scores, evidence, or violations.
If required information is missing, return missing_fields and do not call an algorithm.
RAG is out of scope for this step.
Your job is to check candidate plans against rules, authorization state, constraints, and review needs.
Prepare inputs for compliance decisions, violations, risk probability, authorization status, and human-review recommendations.
The JSON shape must be:
{
  "intent": "short intent",
  "algorithm_calls": [
    {
      "algorithm_id": "compliance_authorization_core",
      "version": "algorithm version",
      "backend_type": "python_http_service",
      "inputs": {},
      "params": {},
      "reason": "why this algorithm fits"
    }
  ],
  "missing_fields": [],
  "explanation": "short Chinese explanation"
}
"""


def parse_user_prompt(query: str) -> str:
    return (
        "Agent task: Convert user text into an AgentRequest for compliance and "
        "authorization checking. Use candidate_plans, constraints, and authorization."
        f"\n\nUser text:\n{query}"
    )


def explain_user_prompt(request_json: str, response_json: str) -> str:
    return (
        "Agent task: Explain the compliance and authorization algorithm result.\n\n"
        f"Validated AgentRequest JSON:\n{request_json}\n\n"
        f"Algorithm AgentResponse JSON:\n{response_json}"
    )


def algolib_user_prompt(*, request_json: str, algorithms: list[dict[str, Any]]) -> str:
    return (
        "Agent name: compliance_authorization_agent\n\n"
        "Active algorithms:\n"
        f"{json.dumps(algorithms, ensure_ascii=False, indent=2)}\n\n"
        "AgentRequest JSON:\n"
        f"{request_json}"
    )
