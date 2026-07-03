"""Execution control core: upstream fusion, rule matching, motion prediction, command synthesis."""
from __future__ import annotations

import time
from typing import Any, Dict, List, Optional

from execution_control_agent.association_rules import (
    choose_primary_rule,
    discretize_situation,
    load_or_mine_rules,
    match_rules,
)
from execution_control_agent.motion_prediction import build_track_histories, predict_tracks


def _safe_dict(value: Any) -> dict:
    return value if isinstance(value, dict) else {}


def _safe_list(value: Any) -> list:
    return value if isinstance(value, list) else []


def _clamp(value: float, low: float = 0.0, high: float = 1.0) -> float:
    return max(low, min(high, float(value)))


def _normalize_score(value: Any, default: float) -> float:
    if value is None or value == "":
        return default
    try:
        score = float(value)
    except (TypeError, ValueError):
        return default
    if score > 1.0:
        score = score / 100.0
    return _clamp(score)


def _result_block(results: dict, *keys: str) -> dict:
    for key in keys:
        block = _safe_dict(results.get(key))
        if block:
            return block
    return {}


def extract_upstream_results(arguments: dict) -> dict:
    results = _safe_dict(arguments.get("results"))
    if results:
        return results
    context = _safe_dict(arguments.get("context"))
    snapshot_results = _safe_dict(context.get("agent_results"))
    if snapshot_results:
        return snapshot_results
    return {}


def build_situation(results: dict, *, phase: str, context: dict | None = None) -> dict:
    context = _safe_dict(context)
    perception = _result_block(results, "perception_detection", "recon")
    threat = _result_block(results, "threat_evaluation", "evaluator")
    resource = _result_block(results, "resource_allocation")
    plan = _result_block(results, "plan_decision")
    communication = _result_block(results, "communication")

    perception_out = _safe_dict(perception.get("output_data"))
    threat_out = _safe_dict(threat.get("output_data"))
    resource_out = _safe_dict(resource.get("output_data"))
    plan_out = _safe_dict(plan.get("output_data"))
    communication_out = _safe_dict(communication.get("output_data"))

    detections = _safe_list(perception_out.get("detections"))
    det_conf = 0.82
    if detections:
        det_conf = _normalize_score(_safe_dict(detections[0]).get("conf"), det_conf)
    elif perception_out.get("report_text") or perception_out.get("report"):
        det_conf = 0.82

    threat_score = 0.70
    for key in ("priority_score", "eval_score", "threat_score"):
        if threat_out.get(key) not in (None, ""):
            threat_score = _normalize_score(threat_out.get(key), threat_score)
            break

    readiness = _normalize_score(resource_out.get("readiness"), float(context.get("resource_readiness") or 0.81))
    delivery_rate = _normalize_score(
        communication_out.get("delivery_rate"),
        float(context.get("comm_delivery_rate") or 0.88),
    )

    commander_decision = (
        plan_out.get("decision")
        or context.get("commander_decision")
        or _latest_collection_text(context, "commander_decision")
        or "ASSAULT"
    )

    return {
        "phase": phase,
        "threat_score": threat_score,
        "intel_confidence": det_conf,
        "resource_readiness": readiness,
        "communication_quality": delivery_rate,
        "commander_decision": str(commander_decision),
    }


def _latest_collection_text(context: dict, key: str) -> str | None:
    entries = context.get(key)
    if not isinstance(entries, list) or not entries:
        return None
    latest = entries[-1]
    if isinstance(latest, dict):
        return latest.get("value")
    return str(latest)


def synthesize_commands(
    *,
    phase: str,
    matched_rules: List[dict],
    prediction_details: List[dict],
    default_executor_role: str,
) -> List[dict]:
    primary = choose_primary_rule(matched_rules, default_executor_role=default_executor_role)
    consequent = dict(primary.get("consequent") or {})
    commands: List[dict] = []

    if prediction_details:
        for index, detail in enumerate(prediction_details, start=1):
            commands.append(
                {
                    "command_id": f"CMD-{phase[:3].upper()}-{index:03d}",
                    "executor_role": consequent.get("executor_role", default_executor_role),
                    "action": consequent.get("action"),
                    "target_id": detail.get("track_id"),
                    "aim_point": dict(detail.get("aim_point") or {}),
                    "execute_at": detail.get("execute_at"),
                    "priority": consequent.get("priority", 0.5),
                    "rule_id": primary.get("rule_id"),
                    "coordination_group": consequent.get("coordination_group", "GROUP-DEFAULT"),
                }
            )
    else:
        commands.append(
            {
                "command_id": f"CMD-{phase[:3].upper()}-001",
                "executor_role": consequent.get("executor_role", default_executor_role),
                "action": consequent.get("action"),
                "target_id": "T-000",
                "aim_point": {"x": 0.0, "y": 0.0},
                "execute_at": 0.0,
                "priority": consequent.get("priority", 0.5),
                "rule_id": primary.get("rule_id"),
                "coordination_group": consequent.get("coordination_group", "GROUP-DEFAULT"),
            }
        )
    return commands


def run_execution_control(arguments: dict) -> dict:
    start = time.perf_counter()
    phase = str(arguments.get("phase") or arguments.get("control_phase") or "strike").strip().lower()
    if phase not in {"strike", "assault"}:
        phase = "strike"
    default_executor_role = "artillery" if phase == "strike" else "assault"

    results = extract_upstream_results(arguments)
    context = _safe_dict(arguments.get("context"))
    situation = build_situation(results, phase=phase, context=context)
    current_items = discretize_situation(situation, phase)
    rules = load_or_mine_rules()
    matched_rules = match_rules(current_items, rules, phase=phase)

    tracks_source = build_track_histories(results)
    tracks, prediction_details = predict_tracks(tracks_source)
    commands = synthesize_commands(
        phase=phase,
        matched_rules=matched_rules,
        prediction_details=prediction_details,
        default_executor_role=default_executor_role,
    )

    groups: Dict[str, List[str]] = {}
    for command in commands:
        group = str(command.get("coordination_group") or "GROUP-DEFAULT")
        groups.setdefault(group, []).append(str(command.get("command_id")))

    latency_ms = round((time.perf_counter() - start) * 1000.0, 3)
    output_data = {
        "phase": phase,
        "situation": situation,
        "matched_items": sorted(current_items),
        "commands": commands,
        "tracks": tracks,
        "coordination": {
            "groups": [
                {"group_id": group_id, "command_ids": command_ids, "joint_strike": len(command_ids) > 1}
                for group_id, command_ids in sorted(groups.items())
            ]
        },
        "latency_ms": latency_ms,
        "matched_rules": matched_rules,
        "prediction_details": prediction_details,
    }
    return {
        "task_type": "execution_control",
        "input_data": arguments,
        "output_data": output_data,
        "accuracy": round(float(matched_rules[0]["confidence"]), 4) if matched_rules else 0.0,
        "latency": latency_ms / 1000.0,
    }
