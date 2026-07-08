"""Resolve agent-specific prompt modules."""

from __future__ import annotations

from importlib import import_module
from types import ModuleType


PROMPT_MODULES = {
    "decision_planning_agent": "decision_agents.decision_planning.prompts",
    "compliance_authorization_agent": "decision_agents.compliance_authorization.prompts",
}


def get_prompt_module(agent_name: str) -> ModuleType:
    module_name = PROMPT_MODULES.get(agent_name)
    if not module_name:
        raise ValueError(f"Unknown decision agent: {agent_name}")
    return import_module(module_name)
