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


def _input_summary(arguments: dict) -> dict:
    """Keep execution envelopes inspectable without echoing upstream context."""
    arguments = arguments if isinstance(arguments, dict) else {}
    results = arguments.get("results") if isinstance(arguments.get("results"), dict) else {}
    context = arguments.get("context") if isinstance(arguments.get("context"), dict) else {}
    return {
        "phase": arguments.get("phase") or arguments.get("control_phase"),
        "result_keys": sorted(str(key) for key in results),
        "context_keys": sorted(str(key) for key in context),
        "result_count": len(results),
        "context_count": len(context),
    }


def _clamp(value: float, low: float = 0.0, high: float = 1.0) -> float:
    return max(low, min(high, float(value)))


def _normalize_score(value: Any) -> Optional[float]:
    if value is None or value == "":
        return None
    try:
        score = float(value)
    except (TypeError, ValueError):
        return None
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


def _first_score(*values: Any) -> Optional[float]:
    for value in values:
        score = _normalize_score(value)
        if score is not None:
            return score
    return None


def build_situation(results: dict, *, phase: str, context: dict | None = None) -> tuple[dict, List[str]]:
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

    missing: List[str] = []
    detections = _safe_list(perception_out.get("detections"))
    det_conf = None
    if detections:
        det_conf = _first_score(*[_safe_dict(item).get("conf") for item in detections])
    fused_track = _safe_dict(_result_block(results, "data_fusion").get("output_data")).get("fused_track")
    if det_conf is None:
        det_conf = _normalize_score(_safe_dict(fused_track).get("det_conf"))
    if det_conf is None:
        missing.append("perception_detection.detections[].conf")

    threat_score = _first_score(
        threat_out.get("priority_score"),
        threat_out.get("eval_score"),
        threat_out.get("threat_score"),
    )
    if threat_score is None:
        missing.append("threat_evaluation.priority_score")

    readiness = _normalize_score(resource_out.get("readiness"))
    if readiness is None:
        missing.append("resource_allocation.readiness")
    delivery_rate = _normalize_score(communication_out.get("delivery_rate"))
    if delivery_rate is None:
        missing.append("communication.delivery_rate")

    commander_decision = (
        plan_out.get("decision")
        or ("ASSAULT" if plan_out.get("recommended_plan_id") or plan_out.get("recommended_plan") else None)
        or context.get("commander_decision")
        or _latest_collection_text(context, "commander_decision")
    )
    if not commander_decision:
        missing.append("plan_decision.decision")

    situation = {
        "phase": phase,
        "threat_score": threat_score,
        "intel_confidence": det_conf,
        "resource_readiness": readiness,
        "communication_quality": delivery_rate,
        "commander_decision": str(commander_decision) if commander_decision else None,
    }
    return situation, sorted(set(missing))


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

    if not prediction_details:
        return commands
    for index, detail in enumerate(prediction_details, start=1):
        commands.append(
            {
                "command_id": f"CMD-{phase[:3].upper()}-{index:03d}",
                "executor_role": consequent.get("executor_role", default_executor_role),
                "action": consequent.get("action"),
                "target_id": detail.get("track_id"),
                "aim_point": dict(detail.get("aim_point") or {}),
                "execute_at": detail.get("execute_at"),
                "priority": consequent.get("priority"),
                "rule_id": primary.get("rule_id"),
                "coordination_group": consequent.get("coordination_group"),
            }
        )
    return commands


def _selected_plan(plan_out: dict) -> dict:
    recommended = _safe_dict(plan_out.get("recommended_plan"))
    if recommended:
        return recommended
    recommended_id = plan_out.get("recommended_plan_id")
    candidates = _safe_list(plan_out.get("candidate_plans"))
    for candidate in candidates:
        item = _safe_dict(candidate)
        if recommended_id and item.get("id") == recommended_id:
            return item
    return _safe_dict(candidates[0]) if candidates else {}


def _risk_by_target(results: dict) -> dict[str, float]:
    threat = _result_block(results, "threat_evaluation", "evaluator")
    threat_out = _safe_dict(threat.get("output_data"))
    risks = _safe_list(threat_out.get("risk_assessments")) + _safe_list(threat_out.get("ranked_targets"))
    mapping: dict[str, float] = {}
    for item in risks:
        row = _safe_dict(item)
        target_id = row.get("target_id") or row.get("item_id")
        score = _first_score(row.get("probability"), row.get("score"), row.get("threat_score"))
        if target_id and score is not None:
            mapping[str(target_id)] = score
    return mapping


def _task_priority_score(value: Any) -> Optional[float]:
    if value is None or value == "":
        return None
    try:
        priority = float(value)
    except (TypeError, ValueError):
        return _normalize_score(value)
    if priority >= 1.0:
        return _clamp(1.0 / priority)
    return _normalize_score(priority)


def synthesize_plan_commands(
    *,
    phase: str,
    results: dict,
    matched_rules: List[dict],
    default_executor_role: str,
) -> List[dict]:
    plan = _result_block(results, "plan_decision")
    plan_out = _safe_dict(plan.get("output_data"))
    resource = _result_block(results, "resource_allocation")
    resource_out = _safe_dict(resource.get("output_data"))
    selected = _selected_plan(plan_out)
    scheduled_tasks = _safe_list(resource_out.get("scheduled_tasks"))
    risk_by_target = _risk_by_target(results)
    target_ids: list[str] = []
    for target_id in _safe_list(selected.get("target_ids")):
        if target_id:
            target_ids.append(str(target_id))
    for task in scheduled_tasks:
        target_id = _safe_dict(task).get("target_id")
        if target_id:
            target_ids.append(str(target_id))
    target_ids = list(dict.fromkeys(target_ids))
    if not target_ids:
        return []

    primary = choose_primary_rule(matched_rules, default_executor_role=default_executor_role)
    consequent = dict(primary.get("consequent") or {})
    actions = [str(item) for item in _safe_list(selected.get("actions")) if item]
    action = consequent.get("action") or (actions[0] if actions else None)
    commands: List[dict] = []
    task_by_target = {
        str(task.get("target_id")): task
        for task in scheduled_tasks
        if isinstance(task, dict) and task.get("target_id")
    }
    for index, target_id in enumerate(target_ids, start=1):
        task = _safe_dict(task_by_target.get(target_id))
        priority_score = risk_by_target.get(target_id)
        if priority_score is None:
            task_priority = _task_priority_score(task.get("priority"))
            priority_score = task_priority
        command = {
            "command_id": f"CMD-{phase[:3].upper()}-{index:03d}",
            "executor_role": consequent.get("executor_role") or default_executor_role,
            "action": action or task.get("task_type"),
            "target_id": target_id,
            "priority": priority_score,
            "rule_id": primary.get("rule_id"),
            "coordination_group": consequent.get("coordination_group"),
            "source": "decision_plan" if selected else "scheduled_task",
            "assigned_resources": list(task.get("assigned_resources") or []),
            "assignment_slot_ids": list(task.get("assignment_slot_ids") or []),
            "assignment_source": task.get("assignment_source"),
            "coordination_id": task.get("coordination_id"),
        }
        commands.append({key: value for key, value in command.items() if value is not None})
    return commands


def run_execution_control(arguments: dict) -> dict:
    start = time.perf_counter()
    phase = str(arguments.get("phase") or arguments.get("control_phase") or "").strip().lower()
    if phase not in {"strike", "assault"}:
        latency_ms = round((time.perf_counter() - start) * 1000.0, 3)
        return {
            "task_type": "execution_control",
            "input_data": _input_summary(arguments),
            "output_data": {
                "phase": phase or None,
                "assessment_status": "insufficient_data",
                "missing_fields": ["phase"],
                "commands": [],
                "tracks": [],
                "prediction_details": [],
                "execution_mode": "input_required",
                "latency_ms": latency_ms,
            },
            "accuracy": 0.0,
            "latency": latency_ms / 1000.0,
        }
    default_executor_role = "artillery" if phase == "strike" else "assault"

    results = extract_upstream_results(arguments)
    context = _safe_dict(arguments.get("context"))
    compliance = _result_block(results, "compliance_authorization", "authorization")
    compliance_out = _safe_dict(compliance.get("output_data")) or compliance
    compliance_decision = str(
        compliance_out.get("decision")
        or compliance_out.get("status")
        or ""
    ).lower()
    approved_for_handoff = compliance_out.get("approved_for_demo_handoff")
    execution_blocked = (
        compliance_decision in {"blocked", "review_required", "pending_review", "denied"}
        or not compliance_decision
    )
    if approved_for_handoff is False:
        execution_blocked = True
    situation, missing_fields = build_situation(results, phase=phase, context=context)
    tracks_source = build_track_histories(results)
    tracks, prediction_details = predict_tracks(tracks_source)
    plan_commands_available = bool(
        synthesize_plan_commands(
            phase=phase,
            results=results,
            matched_rules=[],
            default_executor_role=default_executor_role,
        )
    )
    if not tracks_source and not plan_commands_available:
        missing_fields.append("data_fusion.track_history")
    if not prediction_details:
        if not plan_commands_available:
            missing_fields.append("data_fusion.track_history.predictable_points")
    missing_fields = sorted(set(missing_fields))

    if missing_fields:
        latency_ms = round((time.perf_counter() - start) * 1000.0, 3)
        return {
            "task_type": "execution_control",
            "input_data": _input_summary(arguments),
            "output_data": {
                "phase": phase,
                "situation": situation,
                "assessment_status": "insufficient_data",
                "missing_fields": missing_fields,
                "commands": [],
                "tracks": tracks,
                "prediction_details": prediction_details,
                "authorization": {
                    "decision": compliance_decision or None,
                    "approved_for_demo_handoff": approved_for_handoff,
                    "execution_blocked": True,
                    "reason": "missing required upstream Agent results",
                },
                "execution_mode": "input_required",
                "latency_ms": latency_ms,
            },
            "accuracy": 0.0,
            "latency": latency_ms / 1000.0,
        }
    current_items = discretize_situation(situation, phase)
    rules = load_or_mine_rules()
    matched_rules = match_rules(current_items, rules, phase=phase)

    commands = []
    if not execution_blocked:
        commands = synthesize_commands(
            phase=phase,
            matched_rules=matched_rules,
            prediction_details=prediction_details,
            default_executor_role=default_executor_role,
        )
        if not commands:
            commands = synthesize_plan_commands(
                phase=phase,
                results=results,
                matched_rules=matched_rules,
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
        "assessment_status": "authorization_required" if execution_blocked else "ready",
        "missing_fields": [],
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
        "authorization": {
            "decision": compliance_decision,
            "approved_for_demo_handoff": approved_for_handoff,
            "execution_blocked": execution_blocked,
            "reason": "human approval required before execution" if execution_blocked else "approved",
        },
        "execution_mode": "preview_only" if execution_blocked else "simulation",
    }
    return {
        "task_type": "execution_control",
        "input_data": _input_summary(arguments),
        "output_data": output_data,
        "accuracy": round(float(matched_rules[0]["confidence"]), 4) if matched_rules else 0.0,
        "latency": latency_ms / 1000.0,
    }
