"""Map upstream Agent results to closed-loop mission feature vectors."""
from __future__ import annotations

import json
from typing import Any, List, Optional, Sequence

from closed_loop_agent.mission_feature_schema import FEATURE_ORDER, LATENCY_REFERENCE_MS

MISSION_FEATURE_NAMES = tuple(FEATURE_ORDER)
CONTROL_LATENCY_SLA_MS = LATENCY_REFERENCE_MS


def _safe_dict(value: Any) -> dict:
    return value if isinstance(value, dict) else {}


def _safe_list(value: Any) -> list:
    return value if isinstance(value, list) else []


def _clamp(value: float, low: float = 0.0, high: float = 1.0) -> float:
    return max(low, min(high, float(value)))


def _mean(values: Sequence[float]) -> float:
    if not values:
        return 0.0
    return sum(float(value) for value in values) / len(values)


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


def _unwrap_context_value(value: Any) -> Any:
    if isinstance(value, list):
        if not value:
            return None
        return _unwrap_context_value(value[-1])
    if isinstance(value, dict) and "value" in value:
        return _unwrap_context_value(value.get("value"))
    return value


def _numeric_values(items: Sequence[dict], *keys: str) -> list[float]:
    values: list[float] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        for key in keys:
            score = _normalize_score(item.get(key))
            if score is not None:
                values.append(score)
                break
    return values


def _targets_from_packet(packet: Any) -> list[dict]:
    packet = _safe_dict(_unwrap_context_value(packet))
    targets = packet.get("targets") or packet.get("tracks") or []
    return [item for item in targets if isinstance(item, dict)]


def _contacts_from_context(context: dict) -> list[dict]:
    mission = context.get("mission_input") if isinstance(context.get("mission_input"), dict) else {}
    contacts = mission.get("contacts") if isinstance(mission.get("contacts"), list) else []
    return [item for item in contacts if isinstance(item, dict)]


def _detections_from_targets(targets: Sequence[dict], contacts: Sequence[dict]) -> list[dict]:
    detections: list[dict] = []
    for item in list(targets) + list(contacts):
        track_id = item.get("track_id") or item.get("target_id") or item.get("contact_id")
        confidence = _normalize_score(item.get("confidence") or _safe_dict(item.get("metadata")).get("confidence"))
        if not track_id or confidence is None:
            continue
        detection = {
            "track_id": str(track_id),
            "conf": confidence,
        }
        class_name = item.get("class") or item.get("classification") or item.get("object_type")
        if class_name:
            detection["class_name"] = class_name
        detections.append(detection)
    return detections


def _history_points_from_contact(contact: dict) -> list[dict]:
    metadata = _safe_dict(contact.get("metadata"))
    history = metadata.get("history_path") or contact.get("history_path") or []
    points: list[dict] = []
    for index, point in enumerate(history if isinstance(history, list) else []):
        if not isinstance(point, dict):
            continue
        x = point.get("x", point.get("lng", point.get("lon")))
        y = point.get("y", point.get("lat"))
        t = point.get("t", point.get("sim_time", point.get("timestamp", index)))
        if x is None or y is None or t is None:
            continue
        points.append({"t": t, "x": x, "y": y})
    return points


def _track_history_from_contacts(contacts: Sequence[dict]) -> list[dict]:
    histories: list[dict] = []
    for contact in contacts:
        track_id = contact.get("track_id") or contact.get("target_id") or contact.get("contact_id")
        points = _history_points_from_contact(contact)
        if not track_id or len(points) < 2:
            continue
        metadata = _safe_dict(contact.get("metadata"))
        history = {"track_id": str(track_id), "history": points}
        for key in ("weapon_prep_sec", "flight_time_sec"):
            value = contact.get(key, metadata.get(key))
            if value is not None:
                history[key] = value
        histories.append(history)
    return histories


def _readiness_from_resources(resources: Sequence[dict]) -> Optional[float]:
    if not resources:
        return None
    scores: list[float] = []
    for item in resources:
        if not isinstance(item, dict):
            continue
        capacity = _normalize_score(item.get("capacity"))
        if capacity is None:
            capacity = 1.0
        status = str(item.get("status") or "").strip().lower()
        available = status in {"available", "ready", "idle", "online", "active"}
        scores.append(capacity if available else 0.0)
    return _clamp(_mean(scores)) if scores else None


def _coverage_from_schedule(tasks: Sequence[dict], resources: Sequence[dict]) -> Optional[float]:
    if not tasks:
        return None
    available_types = {
        str(item.get("type") or "").lower()
        for item in resources
        if isinstance(item, dict) and str(item.get("status") or "").lower() in {"available", "ready", "idle", "online", "active"}
    }
    covered = 0
    for task in tasks:
        required = [
            str(item).lower()
            for item in (task.get("required_resource_types") or [])
        ] if isinstance(task, dict) else []
        if not required or any(item in available_types for item in required):
            covered += 1
    return _clamp(covered / max(1, len(tasks)))


def execution_gate_from_results(results: Optional[dict]) -> dict:
    """Evaluate whether upstream produced an authorized, non-empty execution."""
    results = _safe_dict(results)
    execution = _result_block(results, "execution_control", "simulation_execution")
    output = _safe_dict(execution.get("output_data")) or execution
    authorization = _safe_dict(output.get("authorization"))
    commands = _safe_list(output.get("commands"))

    decision = str(
        authorization.get("decision")
        or output.get("compliance_decision")
        or "unknown"
    ).strip().lower()
    approved = authorization.get("approved_for_demo_handoff")
    explicitly_blocked = bool(
        output.get("execution_blocked")
        or authorization.get("execution_blocked")
        or decision in {"blocked", "review_required", "pending_review", "denied"}
        or approved is False
    )
    evidence_present = bool(execution) and any(
        key in output for key in ("commands", "execution_mode", "execution_blocked", "authorization")
    )
    gate_passed = bool(evidence_present and not explicitly_blocked and commands)

    if not evidence_present:
        reason = "missing_execution_evidence"
    elif explicitly_blocked:
        reason = "execution_blocked"
    elif not commands:
        reason = "no_commands_executed"
    else:
        reason = "authorized_execution_with_commands"

    return {
        "execution_evidence_present": evidence_present,
        "execution_blocked": explicitly_blocked,
        "execution_mode": output.get("execution_mode"),
        "compliance_decision": decision,
        "approved_for_demo_handoff": approved,
        "executed_command_count": len(commands),
        "meets_execution_requirement": gate_passed,
        "reason": reason,
    }


def control_latency_ms_from_results(results: Optional[dict], default_ms: Optional[float] = None) -> Optional[float]:
    results = _safe_dict(results)
    execution = _result_block(results, "execution_control", "artillery", "assault")
    output = _safe_dict(execution.get("output_data"))
    for key in ("latency_ms", "control_latency_ms", "median_latency_ms"):
        value = output.get(key)
        if value is not None and value != "":
            try:
                return max(0.0, float(value))
            except (TypeError, ValueError):
                continue
    return max(0.0, float(default_ms)) if default_ms is not None else None


def comm_quality_from_results(results: Optional[dict], default: Optional[float] = None) -> Optional[float]:
    results = _safe_dict(results)
    communication = _result_block(results, "communication")
    output = _safe_dict(communication.get("output_data"))
    for key in ("delivery_rate", "coordination_score", "team_sync", "comm_quality"):
        value = output.get(key)
        if value is not None and value != "":
            return _normalize_score(value)
    return _clamp(default) if default is not None else None


def asset_readiness_from_results(results: Optional[dict], default: Optional[float] = None) -> Optional[float]:
    results = _safe_dict(results)
    resource = _result_block(results, "resource_allocation")
    output = _safe_dict(resource.get("output_data"))
    readiness = output.get("readiness")
    if readiness is not None and readiness != "":
        return _normalize_score(readiness)
    sectors = _safe_list(output.get("sectors"))
    if sectors:
        values = [_normalize_score(_safe_dict(item).get("readiness")) for item in sectors]
        values = [value for value in values if value is not None]
        if not values:
            return _clamp(default) if default is not None else None
        return _clamp(_mean(values))
    return _clamp(default) if default is not None else None


def ammo_pressure_from_results(results: Optional[dict], default: Optional[float] = None) -> Optional[float]:
    results = _safe_dict(results)
    resource = _result_block(results, "resource_allocation")
    output = _safe_dict(resource.get("output_data"))
    for key in ("supply_pressure", "ammo_pressure", "resource_pressure"):
        value = output.get(key)
        if value is not None and value != "":
            return _normalize_score(value)
    return _clamp(default) if default is not None else None


def intel_confidence_from_results(results: Optional[dict], default: Optional[float] = None) -> Optional[float]:
    results = _safe_dict(results)
    perception = _result_block(results, "perception_detection", "recon")
    fusion = _result_block(results, "data_fusion")
    perception_out = _safe_dict(perception.get("output_data"))
    fusion_out = _safe_dict(fusion.get("output_data"))
    detections = _safe_list(perception_out.get("detections"))
    confs = []
    for detection in detections:
        item = _safe_dict(detection)
        if item.get("conf") is not None:
            score = _normalize_score(item.get("conf"))
            if score is not None:
                confs.append(score)
    if confs:
        return _clamp(_mean(confs))
    fused_track = _safe_dict(fusion_out.get("fused_track"))
    if fused_track.get("det_conf") is not None:
        return _normalize_score(fused_track.get("det_conf"))
    return _clamp(default) if default is not None else None


def threat_pressure_from_results(results: Optional[dict], default: Optional[float] = None) -> Optional[float]:
    results = _safe_dict(results)
    threat = _result_block(results, "threat_evaluation", "evaluator")
    output = _safe_dict(threat.get("output_data"))
    ranked = _safe_list(output.get("ranked_targets"))
    if ranked:
        scores = [_normalize_score(_safe_dict(item).get("score")) for item in ranked]
        scores = [value for value in scores if value is not None]
        if not scores:
            return _clamp(default) if default is not None else None
        return _clamp(_mean(scores))
    for key in ("priority_score", "eval_score", "threat_score"):
        value = output.get(key)
        if value is not None and value != "":
            return _normalize_score(value)
    return _clamp(default) if default is not None else None


def damage_rate_from_results(
    results: Optional[dict],
    damage_probs: Optional[Sequence[float]] = None,
    default: Optional[float] = None,
) -> Optional[float]:
    if damage_probs:
        return _clamp(_mean(damage_probs))
    results = _safe_dict(results)
    damage = _result_block(results, "damage_confirmation")
    output = _safe_dict(damage.get("output_data"))
    engaged = output.get("engaged_targets")
    destroyed = output.get("confirmed_destroyed")
    if engaged is not None and destroyed is not None:
        try:
            engaged_count = max(1, int(engaged))
            destroyed_count = int(destroyed)
            return _clamp(destroyed_count / engaged_count)
        except (TypeError, ValueError):
            pass
    return _clamp(default) if default is not None else None


def mission_vector_from_results(
    results: Optional[dict],
    *,
    damage_probs: Optional[Sequence[float]] = None,
    targets: Optional[Sequence[dict]] = None,
    control_latency_sla_ms: float = CONTROL_LATENCY_SLA_MS,
    mode: str = "strict",
) -> List[float]:
    """Build the 7-d mission vector in schema order."""
    from closed_loop_agent.mission_feature_adapter import build_features_from_agent_results, legacy_mission_vector_from_bundle

    bundle = build_features_from_agent_results(
        results,
        damage_probs=damage_probs,
        targets=targets,
        mode=mode,
        latency_reference_ms=control_latency_sla_ms,
    )
    return legacy_mission_vector_from_bundle(bundle)


def _latest_collection_entry(context: dict, key: str):
    entries = context.get(key)
    if not isinstance(entries, list) or not entries:
        return None
    return entries[-1]


def _entry_value(entry):
    if isinstance(entry, dict):
        return entry.get("value")
    return entry


def _execution_control_phase(value) -> str | None:
    if not isinstance(value, dict):
        return None
    output_data = _safe_dict(value.get("output_data"))
    phase = output_data.get("phase") or value.get("phase")
    return str(phase).strip().lower() if phase else None


def _latest_execution_control_output(context: dict, *, phase: str | None = None) -> dict:
    entries = context.get("execution_control_result")
    if not isinstance(entries, list):
        return {}
    selected = []
    for entry in entries:
        value = _entry_value(entry)
        if not isinstance(value, dict):
            continue
        entry_phase = _execution_control_phase(value)
        if phase and entry_phase != phase:
            continue
        selected.append(value)
    if not selected:
        return {}
    latest = selected[-1]
    return _safe_dict(latest.get("output_data")) or latest


def _structured_summary(value):
    if isinstance(value, dict):
        output_data = _safe_dict(value.get("output_data"))
        return output_data.get("message") or value.get("message") or value
    return value


def build_standard_results_from_context(
    context: dict,
    *,
    latest_value,
) -> dict:
    """Map beachhead demo context collections to standard closed-loop result keys."""
    context = _safe_dict(context)
    recon_report = latest_value(context, "recon_report")
    strike_result = latest_value(context, "strike_result")
    eval_score = latest_value(context, "eval_score")
    assault_result = latest_value(context, "assault_result")
    commander_decision = latest_value(context, "commander_decision")
    intelligence_packet = latest_value(context, "intelligence_packet")
    threat_assessment = _safe_dict(latest_value(context, "threat_assessment_result"))
    task_scheduling = _safe_dict(latest_value(context, "task_scheduling_result"))
    decision_planning = _safe_dict(latest_value(context, "decision_planning_result"))
    compliance = _safe_dict(latest_value(context, "compliance_authorization_result"))
    planning_input = _safe_dict(context.get("planning_input"))
    contacts = _contacts_from_context(context)
    intelligence_targets = _targets_from_packet(intelligence_packet)

    simulation_output = _safe_dict(latest_value(context, "execution_simulation_result"))
    simulation_output = _safe_dict(simulation_output.get("output_data")) or simulation_output
    strike_ec_output = _latest_execution_control_output(context, phase="strike")
    assault_ec_output = _latest_execution_control_output(context, phase="assault")
    execution_output = simulation_output or assault_ec_output or strike_ec_output

    structured = context.get("structured_detections")
    detections = structured if isinstance(structured, list) else []
    if not detections:
        detections = _detections_from_targets(intelligence_targets, contacts)
    threat_values = _numeric_values(
        _safe_list(threat_assessment.get("risk_assessments")),
        "probability",
        "threat_score",
    )
    threat_values.extend(
        _numeric_values(
            _safe_list(threat_assessment.get("unified_threat_ranking")),
            "score",
            "threat_score",
        )
    )
    threat_values.extend(_numeric_values(intelligence_targets, "threat_score"))
    threat_values.extend(_numeric_values(contacts, "threat_score"))
    eval_score_normalized = _normalize_score(eval_score)
    if eval_score_normalized is not None:
        threat_values.append(eval_score_normalized)
    threat_score = _clamp(max(threat_values)) if threat_values else None

    latency_ms = execution_output.get("latency_ms")
    if latency_ms is None:
        latency_ms = context.get("execution_latency_ms") or context.get("last_strike_latency_ms")
    delivery_rate = _normalize_score(context.get("comm_delivery_rate"))
    readiness = _normalize_score(context.get("resource_readiness"))
    supply_pressure = _normalize_score(context.get("supply_pressure"))
    resources = _safe_list(task_scheduling.get("resources")) or _safe_list(planning_input.get("resources")) or _safe_list(context.get("resources"))
    scheduled_tasks = (
        _safe_list(task_scheduling.get("scheduled_tasks"))
        or _safe_list(planning_input.get("scheduled_tasks"))
        or _safe_list(context.get("scheduled_tasks"))
    )
    if readiness is None:
        readiness = _readiness_from_resources(resources)
    if delivery_rate is None:
        delivery_rate = _coverage_from_schedule(scheduled_tasks, resources)
    if supply_pressure is None and scheduled_tasks and resources:
        supply_pressure = _clamp(len(scheduled_tasks) / max(1, len(resources) + len(scheduled_tasks)))

    track_history = context.get("structured_track_history")
    if not isinstance(track_history, list) and execution_output.get("tracks"):
        track_history = execution_output.get("tracks")
    if not isinstance(track_history, list) or not track_history:
        track_history = _safe_list(planning_input.get("target_histories")) or _safe_list(task_scheduling.get("target_histories"))
    if not isinstance(track_history, list) or not track_history:
        track_history = _track_history_from_contacts(contacts)

    execution_payload = dict(execution_output)
    if latency_ms is not None and execution_payload:
        execution_payload.setdefault("latency_ms", latency_ms)
    if strike_result is not None:
        execution_payload["strike_summary"] = _structured_summary(strike_result)
    if assault_result is not None:
        execution_payload["assault_summary"] = _structured_summary(assault_result)
    execution_payload["source_workflow_id"] = context.get("workflow_id")
    results: dict = {}
    if detections or recon_report is not None:
        perception_out = {}
        if detections:
            perception_out["detections"] = detections
        if recon_report is not None:
            perception_out["report_text"] = recon_report
        if context.get("workflow_id"):
            perception_out["frame_id"] = str(context.get("workflow_id"))
        results["perception_detection"] = {"output_data": perception_out}
        if detections:
            results["recognition"] = {"output_data": {"labels": detections}}
    if isinstance(track_history, list):
        results["data_fusion"] = {"output_data": {"track_history": track_history}}
    if threat_score is not None or eval_score is not None:
        threat_out = {}
        if threat_score is not None:
            threat_out["priority_score"] = threat_score
        if eval_score is not None:
            threat_out["eval_score_raw"] = eval_score
        if isinstance(threat_assessment.get("risk_assessments"), list):
            threat_out["risk_assessments"] = threat_assessment["risk_assessments"]
        if isinstance(threat_assessment.get("unified_threat_ranking"), list):
            threat_out["ranked_targets"] = threat_assessment["unified_threat_ranking"]
        results["threat_evaluation"] = {"output_data": threat_out}
    if execution_payload and any(key != "source_workflow_id" for key in execution_payload):
        results["execution_control"] = {"output_data": execution_payload}
    if delivery_rate is not None:
        results["communication"] = {"output_data": {"delivery_rate": delivery_rate}}
    resource_out = {}
    if readiness is not None:
        resource_out["readiness"] = readiness
    if supply_pressure is not None:
        resource_out["supply_pressure"] = supply_pressure
    if resource_out:
        if scheduled_tasks:
            resource_out["scheduled_tasks"] = scheduled_tasks
        if resources:
            resource_out["resources"] = resources
        results["resource_allocation"] = {"output_data": resource_out}
    if commander_decision is not None:
        plan_out = {"decision": commander_decision}
        if decision_planning:
            plan_out.update(
                {
                    "candidate_plans": decision_planning.get("candidate_plans"),
                    "recommended_plan_id": decision_planning.get("recommended_plan_id"),
                    "recommended_plan": decision_planning.get("recommended_plan"),
                }
            )
        results["plan_decision"] = {"output_data": {key: value for key, value in plan_out.items() if value not in (None, [], {})}}
    elif decision_planning:
        results["plan_decision"] = {"output_data": decision_planning}
    if compliance:
        results["compliance_authorization"] = {"output_data": compliance}
    if recon_report is not None:
        results["recon"] = {"output_data": {"report": recon_report}}
    if strike_result is not None:
        results["artillery"] = {"output_data": {"result": strike_result}}
    if eval_score is not None:
        results["evaluator"] = {"output_data": {"eval_score": eval_score}}
    if assault_result is not None:
        results["assault"] = {"output_data": {"result": assault_result}}
    return results


def mission_vector_to_csv_row(
    mission_id: str,
    vector: Sequence[float],
    task_completion: float,
    *,
    map_title: str = "live",
) -> dict:
    return {
        "replay_id": mission_id,
        "player_id": 0,
        "map_title": map_title,
        "game_version": "live",
        "duration_sec": 0.0,
        "mmr": 0.0,
        "apm": 0.0,
        "result": "Win" if task_completion >= 0.5 else "Loss",
        "race": "NA",
        "damage_rate": vector[0],
        "asset_readiness": vector[1],
        "control_timeliness": vector[2],
        "intel_confidence": vector[3],
        "threat_pressure": vector[4],
        "ammo_pressure": vector[5],
        "comm_quality": vector[6],
        "task_completion": round(float(task_completion), 4),
    }


def parse_results_json(text: str) -> dict:
    payload = json.loads(text)
    if isinstance(payload, dict) and isinstance(payload.get("results"), dict):
        return payload["results"]
    if isinstance(payload, dict) and isinstance(payload.get("input"), dict):
        input_data = payload["input"]
        if isinstance(input_data.get("results"), dict):
            return input_data["results"]
    if isinstance(payload, dict):
        return payload
    raise ValueError("Expected a JSON object containing Agent results.")
