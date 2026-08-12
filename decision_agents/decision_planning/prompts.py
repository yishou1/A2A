"""Prompts for the decision planning agent."""

from __future__ import annotations

import json

from typing import Any


PARSE_SYSTEM_PROMPT = """You convert natural language into a JSON object for the decision_planning_agent.
Do not invent final analytical conclusions. Only extract structured input for candidate plan generation.
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
    "target_histories": [],
    "planning_objectives": [],
    "candidate_plans": [],
    "constraints": [],
    "authorization": {"status": "unknown"}
  }
}
Prefer compute_budget="medium" when the user asks for medium, robust, stronger, or configurable behavior.
Prefer risk_policy="conservative" when the user asks for conservative, cautious, strict, or careful behavior.
"""


EXPLAIN_SYSTEM_PROMPT = """You explain decision planning algorithm results.
Do not change scores, recommended plan ids, plans, or handoff notes.
Return valid JSON only:
{
  "explanation": "concise Chinese explanation",
  "missing_fields_advice": []
}
"""


ALGOLIB_SYSTEM_PROMPT = """You are the decision_planning_agent.
Return valid JSON only.
Choose only one active algorithm from the provided algorithm catalog.
Prefer decision_planning_core when it is active.
Do not invent algorithm outputs, scores, evidence, or plans.
If required information is missing, return missing_fields and do not call an algorithm.
Only scheduled_tasks and resources are required for this algorithm call.
candidate_plans is optional input and is normally empty because the algorithm generates it.
RAG is out of scope for this step.
Your job is to understand tasks, resources, risks, constraints, and planning objectives.
Prepare inputs for candidate-plan generation, scoring, recommendation, and handoff notes.
Set algorithm_calls[0].inputs to the complete AgentRequest JSON exactly as provided.
Do not omit, rename, summarize, or reshape any AgentRequest field.
In particular, preserve risk_assessments, scheduled_tasks, resources, target_histories,
planning_objectives, constraints, authorization, agent_profile, and algorithm_params.
The JSON shape must be:
{
  "intent": "short intent",
  "algorithm_calls": [
    {
      "algorithm_id": "decision_planning_core",
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
        "Agent task: Convert user text into an AgentRequest for candidate plan "
        "generation. Use scheduled_tasks, resources, risk_assessments, and constraints."
        f"\n\nUser text:\n{query}"
    )


def explain_user_prompt(request_json: str, response_json: str) -> str:
    return (
        "Agent task: Explain the decision planning algorithm result.\n\n"
        f"Validated AgentRequest JSON:\n{request_json}\n\n"
        f"Algorithm AgentResponse JSON:\n{response_json}"
    )


def algolib_user_prompt(*, request_json: str, algorithms: list[dict[str, Any]]) -> str:
    return (
        "Agent name: decision_planning_agent\n\n"
        "Active algorithms:\n"
        f"{json.dumps(algorithms, ensure_ascii=False, indent=2)}\n\n"
        "AgentRequest JSON:\n"
        f"{request_json}"
    )
