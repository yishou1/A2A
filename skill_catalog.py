from __future__ import annotations

from copy import deepcopy
from typing import Iterable


DEFAULT_INPUT_SCHEMA = {"type": "object", "additionalProperties": True}
DEFAULT_OUTPUT_SCHEMA = {
    "type": ["object", "array", "string", "number", "boolean", "null"]
}

CONTEXT_ENTRY_ARRAY_SCHEMA = {
    "type": "array",
    "items": {"type": "object"},
}

SKILL_CONTRACTS = {
    "scan_beach_defenses": {
        "input_schema": {
            "type": "object",
            "required": ["sector"],
            "properties": {"sector": {"type": "string"}},
            "additionalProperties": False,
        },
        "output_schema": {"type": "string"},
    },
    "suppress_beach_sector_A": {
        "input_schema": {
            "type": "object",
            "required": ["coordinates"],
            "properties": {
                "coordinates": {"type": "string"},
                "intensity": {"type": "string"},
                "recon_report": CONTEXT_ENTRY_ARRAY_SCHEMA,
            },
            "additionalProperties": False,
        },
        "output_schema": {"type": "string"},
    },
    "evaluate_strike": {
        "input_schema": {
            "type": "object",
            "required": ["coordinates"],
            "properties": {
                "coordinates": {"type": "string"},
                "recon_report": CONTEXT_ENTRY_ARRAY_SCHEMA,
                "strike_result": CONTEXT_ENTRY_ARRAY_SCHEMA,
                "mock_eval_score": {"type": "integer"},
            },
            "additionalProperties": False,
        },
        "output_schema": {"type": ["integer", "number"]},
    },
    "capture_beachhead": {
        "input_schema": {
            "type": "object",
            "required": ["coordinates"],
            "properties": {
                "coordinates": {"type": "string"},
                "recon_report": CONTEXT_ENTRY_ARRAY_SCHEMA,
                "strike_result": CONTEXT_ENTRY_ARRAY_SCHEMA,
                "eval_score": CONTEXT_ENTRY_ARRAY_SCHEMA,
                "commander_decision": CONTEXT_ENTRY_ARRAY_SCHEMA,
            },
            "additionalProperties": False,
        },
        "output_schema": {"type": "string"},
    },
    "analyze_and_replanning": {
        "input_schema": {
            "type": "object",
            "required": ["recon_report", "strike_result"],
            "properties": {
                "recon_report": CONTEXT_ENTRY_ARRAY_SCHEMA,
                "strike_result": CONTEXT_ENTRY_ARRAY_SCHEMA,
            },
            "additionalProperties": False,
        },
        "output_schema": {"type": ["object", "string"]}
    },
}


def skill_contract(skill_id: str | None) -> dict:
    contract = deepcopy(SKILL_CONTRACTS.get(str(skill_id or ""), {}))
    contract.setdefault("input_schema", deepcopy(DEFAULT_INPUT_SCHEMA))
    contract.setdefault("output_schema", deepcopy(DEFAULT_OUTPUT_SCHEMA))
    return contract


def enrich_skill_contract(skill: dict) -> dict:
    enriched = deepcopy(skill)
    contract = skill_contract(enriched.get("id"))
    enriched.setdefault("input_schema", contract["input_schema"])
    enriched.setdefault("output_schema", contract["output_schema"])
    return enriched


# The distributed-agent interface spec requires registration of the following
# professional capabilities. Each entry is a self-describing A2A skill so it can
# be advertised on the agent card, published to Nacos metadata and matched by the
# scheduler during discovery / delayed binding.
PROFESSIONAL_SKILLS = {
    "detect": {
        "id": "detect",
        "name": "Target Detection",
        "description": "检测：发现战场环境中的目标与威胁信号。",
        "tags": ["detect", "detection", "sensing", "检测", "探测"],
        "capability": "detect",
    },
    "locate": {
        "id": "locate",
        "name": "Target Localization",
        "description": "定位：解算目标的地理/相对坐标位置。",
        "tags": ["locate", "localization", "position", "定位"],
        "capability": "locate",
    },
    "track": {
        "id": "track",
        "name": "Target Tracking",
        "description": "跟踪：对目标进行持续航迹跟踪与状态更新。",
        "tags": ["track", "tracking", "trajectory", "跟踪", "航迹"],
        "capability": "track",
    },
    "identify": {
        "id": "identify",
        "name": "Target Identification",
        "description": "识别：判定目标的类型、属性与敌我属性。",
        "tags": ["identify", "identification", "recognition", "识别"],
        "capability": "identify",
    },
    "threat_evaluation": {
        "id": "threat_evaluation",
        "name": "Threat Evaluation",
        "description": "威胁评估：评估目标的威胁等级与优先级。",
        "tags": ["threat", "threat_evaluation", "assessment", "威胁评估"],
        "capability": "threat_evaluation",
    },
    "target_assignment": {
        "id": "target_assignment",
        "name": "Target Assignment",
        "description": "目标分配：将火力/资源分配到具体目标。",
        "tags": ["target", "target_assignment", "allocation", "目标分配"],
        "capability": "target_assignment",
    },
    "route_planning": {
        "id": "route_planning",
        "name": "Route Planning",
        "description": "航路规划：规划平台/兵力的机动与突击航路。",
        "tags": ["route", "route_planning", "path", "航路规划", "路径规划"],
        "capability": "route_planning",
    },
    "strike_effect_evaluation": {
        "id": "strike_effect_evaluation",
        "name": "Strike Effect Evaluation",
        "description": "打击效果评估：评估打击行动的毁伤与作战效果。",
        "tags": [
            "strike",
            "strike_effect_evaluation",
            "damage_assessment",
            "打击效果评估",
            "毁伤评估",
        ],
        "capability": "strike_effect_evaluation",
    },
}


# Default professional capabilities advertised by each demo role. Agents may
# override or extend these lists when registering.
ROLE_CAPABILITIES = {
    "recon": ["detect", "locate", "track", "identify"],
    "artillery": ["target_assignment", "route_planning"],
    "assault": ["route_planning", "target_assignment"],
    "evaluator": ["threat_evaluation", "strike_effect_evaluation"],
}


def all_capabilities() -> list[str]:
    return list(PROFESSIONAL_SKILLS.keys())


def build_skill(capability: str) -> dict:
    """Return a fresh copy of a professional skill definition by capability slug."""
    skill = PROFESSIONAL_SKILLS.get(capability)
    if skill is None:
        raise KeyError(f"Unknown professional capability: {capability}")
    return enrich_skill_contract(skill)


def skills_for_capabilities(capabilities: Iterable[str]) -> list[dict]:
    skills = []
    for capability in capabilities or []:
        if capability in PROFESSIONAL_SKILLS:
            skills.append(enrich_skill_contract(PROFESSIONAL_SKILLS[capability]))
    return skills


def professional_skills_for_role(role: str) -> list[dict]:
    return skills_for_capabilities(ROLE_CAPABILITIES.get(role, []))
