"""Shared identity and protocol definitions for decision agents."""

from __future__ import annotations

from typing import Any


AGENT_DEFINITIONS: dict[str, dict[str, Any]] = {
    "decision_planning": {
        "agent_name": "decision_planning_agent",
        "runtime_name": "Decision_Planning_Agent",
        "description": "Generates and scores simulation-only decision-support plans.",
        "role": "decision_planning",
        "skill_id": "decision_planning_analysis",
        "skill_name": "Decision Planning Analysis",
        "command": "decision_planning",
        "output_hint": "decision_planning_result",
        "default_port": 10202,
    },
    "compliance_authorization": {
        "agent_name": "compliance_authorization_agent",
        "runtime_name": "Compliance_Authorization_Agent",
        "description": "Checks rules, law-of-war constraints, and authorization status.",
        "role": "compliance_authorization",
        "skill_id": "compliance_authorization_analysis",
        "skill_name": "Compliance Authorization Analysis",
        "command": "compliance_authorization",
        "output_hint": "compliance_authorization_result",
        "default_port": 10203,
    },
}


def agent_definition(agent_key: str) -> dict[str, Any]:
    return AGENT_DEFINITIONS[agent_key]


def agent_definition_for_role(role: str) -> dict[str, Any] | None:
    return next(
        (definition for definition in AGENT_DEFINITIONS.values() if definition["role"] == role),
        None,
    )
