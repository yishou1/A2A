"""Prompts for optional LLM parsing and explanation."""

from __future__ import annotations


AGENT_TASKS = {
    "decision_planning_agent": (
        "Convert user text into an AgentRequest for candidate plan generation. "
        "Use scheduled_tasks, resources, risk_assessments, and constraints."
    ),
    "compliance_authorization_agent": (
        "Convert user text into an AgentRequest for compliance and authorization "
        "checking. Use candidate_plans, constraints, and authorization."
    ),
}


PARSE_SYSTEM_PROMPT = """You convert natural language into a JSON object for a deterministic algorithm agent.
Do not invent final analytical conclusions. Only extract structured input.
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


EXPLAIN_SYSTEM_PROMPT = """You explain deterministic algorithm results.
Do not change scores, compliance decisions, violations, or plans.
Return valid JSON only:
{
  "explanation": "concise Chinese explanation",
  "missing_fields_advice": []
}
"""


def parse_user_prompt(agent_name: str, query: str) -> str:
    task = AGENT_TASKS.get(agent_name, "Convert user text into an AgentRequest.")
    return f"Agent task: {task}\n\nUser text:\n{query}"


def explain_user_prompt(agent_name: str, request_json: str, response_json: str) -> str:
    task = AGENT_TASKS.get(agent_name, "Explain the algorithm result.")
    return (
        f"Agent task: {task}\n\n"
        f"Validated AgentRequest JSON:\n{request_json}\n\n"
        f"Algorithm AgentResponse JSON:\n{response_json}"
    )
