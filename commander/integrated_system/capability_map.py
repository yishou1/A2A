from __future__ import annotations

from copy import deepcopy


CAPABILITY_SEQUENCE = [
    "cognition",
    "tracking",
    "threat_assessment",
    "decision_planning",
    "compliance_authorization",
    "execution_control",
    "effect_evaluation",
]


CAPABILITY_MAP = {
    "perception": {
        "label": "Perception and Detection",
        "preferred_agents": ["recon_agent"],
        "implemented_by": "simulated_adapter",
        "stub": True,
    },
    "cognition": {
        "label": "Cognition and Intelligence Extraction",
        "preferred_agents": ["tactical_intelligence_agent"],
        "implemented_by": "demo_adapter",
        "stub": False,
    },
    "communication": {
        "label": "Information Sharing and Communication",
        "preferred_agents": ["communication_service_stub"],
        "implemented_by": "simulated_adapter",
        "stub": True,
    },
    "tracking": {
        "label": "Track Generation and Maintenance",
        "preferred_agents": ["track_threat_agent"],
        "implemented_by": "demo_adapter",
        "stub": False,
    },
    "threat_assessment": {
        "label": "Threat Assessment and Ranking",
        "preferred_agents": ["track_threat_agent", "threat_rank_service_stub"],
        "implemented_by": "demo_adapter",
        "stub": False,
    },
    "resource_scheduling": {
        "label": "Scheduling and Resource Allocation",
        "preferred_agents": ["scheduler_service_stub"],
        "implemented_by": "simulated_adapter",
        "stub": True,
    },
    "decision_planning": {
        "label": "Decision Planning",
        "preferred_agents": ["decision_planning_agent"],
        "implemented_by": "demo_adapter",
        "stub": False,
    },
    "compliance_authorization": {
        "label": "Compliance and Authorization",
        "preferred_agents": ["compliance_authorization_agent"],
        "implemented_by": "demo_adapter",
        "stub": False,
    },
    "execution_control": {
        "label": "Execution Control",
        "preferred_agents": ["simulation_execution_agent"],
        "implemented_by": "simulation_executor",
        "stub": True,
    },
    "effect_evaluation": {
        "label": "Effect Evaluation and Closed Loop",
        "preferred_agents": ["closed_loop_agent", "evaluator_agent"],
        "implemented_by": "demo_adapter",
        "stub": False,
    },
}


def capability_config(capability: str) -> dict:
    if capability not in CAPABILITY_MAP:
        raise KeyError(f"Unknown capability: {capability}")
    return deepcopy(CAPABILITY_MAP[capability])


def preferred_agent(capability: str) -> str:
    config = capability_config(capability)
    return config["preferred_agents"][0]
