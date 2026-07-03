"""Map upstream Agent results to closed-loop mission feature vectors."""
from __future__ import annotations

import json
from typing import Any, List, Optional, Sequence

CONTROL_LATENCY_SLA_MS = 2000.0
DEFAULT_COMM_QUALITY = 0.88

MISSION_FEATURE_NAMES = (
    "damage_rate",
    "asset_readiness",
    "control_timeliness",
    "intel_confidence",
    "threat_pressure",
    "ammo_pressure",
    "comm_quality",
)


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


def control_latency_ms_from_results(results: Optional[dict], default_ms: float = 0.0) -> float:
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
    return max(0.0, float(default_ms))


def comm_quality_from_results(results: Optional[dict], default: float = DEFAULT_COMM_QUALITY) -> float:
    results = _safe_dict(results)
    communication = _result_block(results, "communication")
    output = _safe_dict(communication.get("output_data"))
    for key in ("delivery_rate", "coordination_score", "team_sync", "comm_quality"):
        value = output.get(key)
        if value is not None and value != "":
            return _normalize_score(value, default)
    return _clamp(default)


def asset_readiness_from_results(results: Optional[dict], default: float = 0.7) -> float:
    results = _safe_dict(results)
    resource = _result_block(results, "resource_allocation")
    output = _safe_dict(resource.get("output_data"))
    readiness = output.get("readiness")
    if readiness is not None and readiness != "":
        return _normalize_score(readiness, default)
    sectors = _safe_list(output.get("sectors"))
    if sectors:
        values = [_normalize_score(_safe_dict(item).get("readiness"), default) for item in sectors]
        return _clamp(_mean(values))
    return _clamp(default)


def ammo_pressure_from_results(results: Optional[dict], default: float = 0.5) -> float:
    results = _safe_dict(results)
    resource = _result_block(results, "resource_allocation")
    output = _safe_dict(resource.get("output_data"))
    for key in ("supply_pressure", "ammo_pressure", "resource_pressure"):
        value = output.get(key)
        if value is not None and value != "":
            return _normalize_score(value, default)
    return _clamp(default)


def intel_confidence_from_results(results: Optional[dict], default: float = 0.82) -> float:
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
            confs.append(_normalize_score(item.get("conf"), default))
    if confs:
        return _clamp(_mean(confs))
    fused_track = _safe_dict(fusion_out.get("fused_track"))
    if fused_track.get("det_conf") is not None:
        return _normalize_score(fused_track.get("det_conf"), default)
    return _clamp(default)


def threat_pressure_from_results(results: Optional[dict], default: float = 0.70) -> float:
    results = _safe_dict(results)
    threat = _result_block(results, "threat_evaluation", "evaluator")
    output = _safe_dict(threat.get("output_data"))
    ranked = _safe_list(output.get("ranked_targets"))
    if ranked:
        scores = [_normalize_score(_safe_dict(item).get("score"), default) for item in ranked]
        return _clamp(_mean(scores))
    for key in ("priority_score", "eval_score", "threat_score"):
        value = output.get(key)
        if value is not None and value != "":
            return _normalize_score(value, default)
    return _clamp(default)


def damage_rate_from_results(
    results: Optional[dict],
    damage_probs: Optional[Sequence[float]] = None,
    default: float = 0.0,
) -> float:
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
    return _clamp(default)


def mission_vector_from_results(
    results: Optional[dict],
    *,
    damage_probs: Optional[Sequence[float]] = None,
    targets: Optional[Sequence[dict]] = None,
    control_latency_sla_ms: float = CONTROL_LATENCY_SLA_MS,
) -> List[float]:
    """Build the 7-d mission vector in RF column order."""
    results = _safe_dict(results)
    target_list = [_safe_dict(item) for item in (targets or [])]

    if damage_probs is not None:
        damage_rate = damage_rate_from_results(results, damage_probs=damage_probs)
    elif target_list:
        damage_rate = _clamp(_mean([float(item.get("damage_probability", 0.0)) for item in target_list]))
    else:
        damage_rate = damage_rate_from_results(results)

    if target_list:
        asset_readiness = _clamp(0.92 - 0.18 * _mean([float(item.get("ammo_need", 0.5)) for item in target_list]))
        intel_confidence = _clamp(_mean([float(item.get("detection_confidence", 0.7)) for item in target_list]))
        threat_pressure = _clamp(
            _mean(
                [
                    float(item.get("threat_score", 0.5)) * (1.0 - float(item.get("damage_probability", 0.0)))
                    for item in target_list
                ]
            )
        )
        ammo_pressure = _clamp(_mean([float(item.get("ammo_need", 0.5)) for item in target_list]))
    else:
        asset_readiness = asset_readiness_from_results(results)
        intel_confidence = intel_confidence_from_results(results)
        threat_pressure = threat_pressure_from_results(results)
        ammo_pressure = ammo_pressure_from_results(results)

    latency_ms = control_latency_ms_from_results(results)
    control_timeliness = _clamp(1.0 - latency_ms / max(1.0, float(control_latency_sla_ms)))
    comm_quality = comm_quality_from_results(results)

    return [
        round(damage_rate, 4),
        round(asset_readiness, 4),
        round(control_timeliness, 4),
        round(intel_confidence, 4),
        round(threat_pressure, 4),
        round(ammo_pressure, 4),
        round(comm_quality, 4),
    ]


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

    strike_ec_output = _latest_execution_control_output(context, phase="strike")
    assault_ec_output = _latest_execution_control_output(context, phase="assault")
    execution_output = assault_ec_output or strike_ec_output

    threat_score = _normalize_score(eval_score, 0.70)
    structured = context.get("structured_detections")
    detections = structured if isinstance(structured, list) else []
    if not detections and recon_report:
        detections = [{"track_id": "recon-001", "conf": 0.82, "summary": recon_report}]

    latency_ms = execution_output.get("latency_ms")
    if latency_ms is None:
        latency_ms = float(context.get("execution_latency_ms") or context.get("last_strike_latency_ms") or 150.0)
    delivery_rate = float(context.get("comm_delivery_rate") or DEFAULT_COMM_QUALITY)
    readiness = float(context.get("resource_readiness") or 0.81)
    supply_pressure = float(context.get("supply_pressure") or 0.5)

    mission_kpi = None
    if commander_decision:
        decision_text = str(commander_decision).upper()
        if "ASSAULT" in decision_text and "RE-PLAN" not in decision_text:
            mission_kpi = 0.85
        elif "RE-PLAN" in decision_text or "ABORT" in decision_text:
            mission_kpi = 0.35

    track_history = context.get("structured_track_history")
    if not isinstance(track_history, list):
        try:
            from execution_control_agent.motion_prediction import load_track_fixture

            track_history = load_track_fixture().get("default_tracks") or []
        except Exception:
            track_history = []

    execution_payload = dict(execution_output)
    execution_payload.setdefault("latency_ms", latency_ms)
    execution_payload.setdefault("commands", [])
    execution_payload.setdefault("tracks", execution_output.get("tracks") or [])
    execution_payload.setdefault("coordination", execution_output.get("coordination") or {})
    execution_payload.setdefault("matched_rules", execution_output.get("matched_rules") or [])
    execution_payload.setdefault("prediction_details", execution_output.get("prediction_details") or [])
    execution_payload["strike_summary"] = _structured_summary(strike_result)
    execution_payload["assault_summary"] = _structured_summary(assault_result)

    return {
        "perception_detection": {
            "output_data": {
                "frame_id": str(context.get("workflow_id") or "frame"),
                "detections": detections,
                "report_text": recon_report,
            }
        },
        "recognition": {
            "output_data": {
                "labels": detections,
            }
        },
        "data_fusion": {
            "output_data": {
                "track_history": track_history,
            }
        },
        "threat_evaluation": {
            "output_data": {
                "priority_score": threat_score,
                "eval_score_raw": eval_score,
            }
        },
        "execution_control": {
            "output_data": execution_payload,
        },
        "communication": {
            "output_data": {"delivery_rate": delivery_rate}
        },
        "resource_allocation": {
            "output_data": {
                "readiness": readiness,
                "supply_pressure": supply_pressure,
            }
        },
        "plan_decision": {
            "output_data": {
                "decision": commander_decision,
                "mission_kpi": mission_kpi,
            }
        },
        "recon": {"output_data": {"report": recon_report}},
        "artillery": {"output_data": {"result": strike_result}},
        "evaluator": {"output_data": {"eval_score": eval_score}},
        "assault": {"output_data": {"result": assault_result}},
    }


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
