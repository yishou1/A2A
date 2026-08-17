from __future__ import annotations

from copy import deepcopy


def simulate_execution(blackboard: dict) -> dict:
    planning = blackboard.get("results", {}).get("decision_planning", {})
    compliance = blackboard.get("results", {}).get("compliance_authorization", {})
    threats = blackboard.get("results", {}).get("threat_assessment", {})

    threat_items = threats.get("result", {}).get("ranked_threats", [])
    top_threat = threat_items[0] if threat_items else {}
    blocked = compliance.get("result", {}).get("authorized") is False
    risk = float(top_threat.get("priority_score", 0.55))
    platform_count = len(blackboard.get("mission_input", {}).get("friendly_platforms", []))
    munition_budget = sum(
        int(item.get("munitions", 0))
        for item in blackboard.get("mission_input", {}).get("friendly_platforms", [])
    )

    if blocked:
        completion = 0.0
        score = 0.1
        status = "blocked"
        suggestion = "operator_review"
        warnings = ["Execution blocked by compliance gate."]
    else:
        completion = min(1.0, 0.45 + 0.1 * platform_count + 0.05 * max(munition_budget, 1) - 0.35 * risk)
        score = min(0.95, max(0.2, completion + 0.1 - (risk * 0.15)))
        status = "completed"
        suggestion = "continue" if score >= 0.6 else "replan"
        warnings = []

    plan = deepcopy(planning.get("result", {}))
    return {
        "status": "completed",
        "capability": "execution_control",
        "agent": "simulation_execution_agent",
        "result": {
            "execution_status": status,
            "completion_ratio": round(completion, 3),
            "resource_consumption": {
                "platforms_committed": platform_count,
                "munitions_estimated": min(max(munition_budget, 1), max(platform_count * 2, 1)),
            },
            "risk_change": {
                "before": round(risk, 3),
                "after": round(max(0.05, risk - (completion * 0.4)), 3),
            },
            "plan_summary": plan.get("recommended_plan"),
            "simulated_score": round(score, 3),
        },
        "confidence": 0.72 if not blocked else 0.9,
        "evidence": [
            "Friendly platform readiness and munition budget were used to estimate completion.",
            "Threat priority score was used to estimate execution risk.",
        ],
        "warnings": warnings,
        "next_suggestion": suggestion,
    }
