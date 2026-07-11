"""Single source of truth for Commander, Agent Card and Nacos skill discovery."""

from __future__ import annotations

from copy import deepcopy
from typing import Any, Dict, List, Tuple


SKILL_DEFINITIONS: Tuple[Dict[str, Any], ...] = (
    {
        "id": "track_threat_situation_analysis",
        "name": "Track Threat Situation Analysis",
        "description": "Run the complete tracking, prediction, grouping, protected-asset impact and risk-priority pipeline.",
        "tags": ["tracking", "prediction", "grouping", "risk"],
    },
    {
        "id": "trajectory_tracking",
        "name": "Trajectory Tracking",
        "description": "Maintain multi-target tracks from perception detections.",
        "tags": ["tracking", "trajectory", "simulation"],
    },
    {
        "id": "trajectory_prediction",
        "name": "Trajectory Prediction",
        "description": "Generate physical-baseline and optional trained ST-GNN trajectory predictions.",
        "tags": ["prediction", "st-gnn", "trajectory"],
    },
    {
        "id": "group_detection",
        "name": "Group Detection",
        "description": "Detect likely formations/groups from spatial, heading and speed similarity.",
        "tags": ["group", "formation", "asset"],
    },
    {
        "id": "threat_ranking",
        "name": "Threat Ranking",
        "description": "Rank individual tracks by simulation-only situation-awareness priority.",
        "tags": ["ranking", "risk", "track"],
    },
    {
        "id": "group_threat_ranking",
        "name": "Group Threat Ranking",
        "description": "Rank detected groups by simulation-only situation-awareness priority.",
        "tags": ["ranking", "risk", "group"],
    },
    {
        "id": "protected_asset_impact_analysis",
        "name": "Protected Asset Impact Analysis",
        "description": "Estimate simulation-only attention priority for protected assets affected by tracks.",
        "tags": ["asset", "impact", "simulation"],
    },
)
SUPPORTED_SKILLS = tuple(skill["id"] for skill in SKILL_DEFINITIONS)


def agent_card_skills() -> List[Dict[str, Any]]:
    result = deepcopy(list(SKILL_DEFINITIONS))
    for skill in result:
        skill["inputModes"] = ["application/json"]
        skill["outputModes"] = ["application/json"]
    return result


def nacos_skill_ids() -> str:
    return ",".join(SUPPORTED_SKILLS)
