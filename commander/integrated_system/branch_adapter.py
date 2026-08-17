from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any, Dict, Optional


PROJECT_ROOT = Path(__file__).resolve().parents[1]
RUNNER_PATH = PROJECT_ROOT / "integrated_system" / "branch_runner.py"


def _default_branch_python() -> str:
    candidate = Path("D:/wangyu/Anaconda/python.exe")
    if candidate.exists():
        return str(candidate)
    return sys.executable


def branch_python() -> str:
    return os.environ.get("INTEGRATED_AGENT_PYTHON", _default_branch_python())


def run_branch_capability(blackboard: Dict[str, Any], capability: str) -> Optional[Dict[str, Any]]:
    if capability not in {
        "cognition",
        "tracking",
        "decision_planning",
        "compliance_authorization",
        "execution_control",
        "threat_assessment",
        "effect_evaluation",
    }:
        return None

    if capability == "threat_assessment":
        return derive_threat_assessment(blackboard)
    if capability == "effect_evaluation":
        return derive_effect_evaluation(blackboard)

    payload = {"capability": capability, "blackboard": blackboard}
    command = [branch_python(), str(RUNNER_PATH)]
    completed = subprocess.run(
        command,
        input=json.dumps(payload, ensure_ascii=False),
        capture_output=True,
        text=True,
        cwd=str(PROJECT_ROOT),
        timeout=180,
    )
    if completed.returncode != 0:
        raise RuntimeError(
            f"branch capability failed for {capability}: {completed.stderr.strip() or completed.stdout.strip()}"
        )
    return json.loads(completed.stdout)


def derive_threat_assessment(blackboard: Dict[str, Any]) -> Dict[str, Any]:
    tracking = blackboard.get("results", {}).get("tracking", {}).get("result", {})
    tracking_meta = blackboard.get("results", {}).get("tracking", {}).get("meta", {})
    artifact = tracking.get("artifact", {})
    ranked = artifact.get("unified_threat_ranking", [])
    top_rank = ranked[0] if ranked else {}
    return {
        "status": "completed",
        "capability": "threat_assessment",
        "agent": "track_threat_agent",
        "result": {
            "ranked_threats": [
                {
                    "contact_id": item.get("entity_id") or item.get("item_id"),
                    "priority_score": item.get("score"),
                    "level": item.get("level"),
                    "rank": item.get("rank"),
                    "entity_type": item.get("entity_type"),
                    "item_id": item.get("item_id"),
                    "source_track_id": item.get("source_track_id"),
                    "protected_asset_name": item.get("protected_asset_name"),
                }
                for item in ranked
            ],
            "top_priority": top_rank.get("entity_id") or top_rank.get("item_id"),
        },
        "confidence": 0.84,
        "evidence": ["Derived from wc track_threat_agent unified threat ranking."],
        "warnings": [],
        "next_suggestion": "continue",
        "meta": {
            "execution_mode": "derived_from_tracking",
            "algorithm_catalog": tracking_meta.get("algorithm_catalog", {}),
        },
    }


def derive_effect_evaluation(blackboard: Dict[str, Any]) -> Dict[str, Any]:
    execution = blackboard.get("results", {}).get("execution_control", {}).get("result", {})
    execution_meta = blackboard.get("results", {}).get("execution_control", {}).get("meta", {})
    closed_loop = execution.get("closed_loop_output", {})
    closed_metrics = closed_loop.get("closed_loop_optimization", {})
    requirements = closed_loop.get("requirement_report", {})
    completion_ratio = float(closed_metrics.get("mission_completion_final", execution.get("simulated_score", 0.0)) or 0.0)
    performance = closed_loop.get("performance_report", {})
    task_accuracy = float(performance.get("task_completion_accuracy", completion_ratio) or completion_ratio)
    requirement_flags = [bool(value) for key, value in requirements.items() if str(key).startswith("meets_")]
    requirement_score = (
        sum(1 for value in requirement_flags if value) / float(len(requirement_flags))
        if requirement_flags
        else (1.0 if bool(closed_loop.get("meets_requirements", False)) else 0.0)
    )
    overall_score = (0.7 * completion_ratio) + (0.2 * task_accuracy) + (0.1 * requirement_score)
    meets = bool(closed_loop.get("meets_requirements", False))
    return {
        "status": "completed",
        "capability": "effect_evaluation",
        "agent": "closed_loop_agent",
        "result": {
            "overall_score": round(overall_score, 4),
            "completion_ratio": round(float(execution.get("completion_ratio", completion_ratio) or 0.0), 4),
            "replan_count": int(blackboard.get("summary", {}).get("replan_count", 0)),
            "assessment": "mission_effective" if overall_score >= 0.6 else "mission_requires_replan",
            "meets_requirements": meets,
            "requirement_report": requirements,
            "performance_report": performance,
            "score_breakdown": {
                "completion_ratio_weight": 0.7,
                "task_accuracy_weight": 0.2,
                "requirement_score_weight": 0.1,
                "completion_ratio_value": round(completion_ratio, 4),
                "task_accuracy_value": round(task_accuracy, 4),
                "requirement_score_value": round(requirement_score, 4),
            },
        },
        "confidence": 0.88,
        "evidence": ["Derived from zh closed_loop_agent evaluation output."],
        "warnings": [] if meets else ["Closed-loop requirement report indicates gaps."],
        "next_suggestion": "continue" if overall_score >= 0.6 else "replan",
        "meta": {
            "execution_mode": "derived_from_execution_control",
            "algorithm_catalog": execution_meta.get("algorithm_catalog", {}),
        },
    }
