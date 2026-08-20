"""Mission feature adapters for SC2LE replay proxy and online Agent results."""
from __future__ import annotations

from typing import Any, Dict, List, Optional, Sequence

from closed_loop_agent.mission_feature_schema import (
    AGENT_REQUIRED_BLOCKS,
    FEATURE_ORDER,
    FEATURE_SCHEMA,
    FEATURE_VERSION,
    LABEL_FIELDS,
    LATENCY_REFERENCE_MS,
    assert_no_label_leakage,
    clamp,
    empty_feature_bundle,
    validate_feature_ranges,
    values_to_vector,
)


def _safe_dict(value: Any) -> dict:
    return value if isinstance(value, dict) else {}


def _safe_list(value: Any) -> list:
    return value if isinstance(value, list) else []


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
    return clamp(score)


def _result_block(results: dict, *keys: str) -> dict:
    for key in keys:
        block = _safe_dict(results.get(key))
        if block:
            return block
    return {}


def _result_to_completion(result: str) -> float:
    normalized = str(result or "").strip().lower()
    if normalized in {"win", "victory"}:
        return 1.0
    if normalized in {"loss", "defeat"}:
        return 0.0
    if normalized in {"tie", "draw", "undecided"}:
        return 0.5
    return 0.5


def _sc2le_proxy_inputs(
    *,
    mmr: float,
    apm: float,
    duration_sec: float,
    opponent_mmr: float,
) -> dict:
    mmr_norm = clamp(mmr / 6000.0)
    apm_norm = clamp(apm / 400.0)
    duration_norm = clamp(duration_sec / 1800.0)
    opponent_norm = clamp(opponent_mmr / 6000.0)
    relative_mmr = clamp((mmr - opponent_mmr + 1500.0) / 3000.0)
    return {
        "mmr_norm": mmr_norm,
        "apm_norm": apm_norm,
        "duration_norm": duration_norm,
        "opponent_norm": opponent_norm,
        "relative_mmr": relative_mmr,
    }


def build_features_from_sc2le_proxy(
    *,
    mmr: float,
    apm: float,
    duration_sec: float,
    opponent_mmr: float,
    result: str | None = None,
) -> dict:
    """Build proxy mission features from replay metadata without label leakage."""
    inputs = _sc2le_proxy_inputs(
        mmr=float(mmr or 3000.0),
        apm=float(apm or 120.0),
        duration_sec=float(duration_sec or 0.0),
        opponent_mmr=float(opponent_mmr or 3000.0),
    )
    assert_no_label_leakage(inputs, context="sc2le_proxy")

    proxy_damage_rate = clamp(
        0.35 + 0.35 * inputs["relative_mmr"] + 0.15 * inputs["apm_norm"] + 0.15 * inputs["mmr_norm"]
    )
    values = {
        "damage_rate": round(proxy_damage_rate, 4),
        "asset_readiness": round(inputs["mmr_norm"], 4),
        "control_timeliness": round(inputs["apm_norm"], 4),
        "intel_confidence": round(clamp(0.45 + 0.40 * inputs["mmr_norm"] + 0.15 * inputs["apm_norm"]), 4),
        "threat_pressure": round(clamp(0.35 + 0.40 * inputs["opponent_norm"] + 0.25 * inputs["duration_norm"]), 4),
        "ammo_pressure": round(inputs["duration_norm"], 4),
        "comm_quality": round(clamp(0.50 + 0.30 * inputs["apm_norm"] + 0.20 * inputs["mmr_norm"]), 4),
    }
    bundle = {
        "feature_version": FEATURE_VERSION,
        "values": values,
        "sources": {
            "damage_rate": "proxy_damage_rate(mmr,apm,relative_mmr)",
            "asset_readiness": "mmr_norm",
            "control_timeliness": "apm_norm",
            "intel_confidence": "mmr/apm blend",
            "threat_pressure": "opponent_mmr/duration blend",
            "ammo_pressure": "duration_norm",
            "comm_quality": "apm/mmr blend",
        },
        "warnings": [],
        "label": {
            "task_completion": round(_result_to_completion(result or ""), 4),
            "source": "Result/completion label only",
        },
    }
    bundle["warnings"].extend(validate_feature_ranges(values))
    return bundle


def verify_sc2le_proxy_no_result_leakage(
    *,
    mmr: float,
    apm: float,
    duration_sec: float,
    opponent_mmr: float,
) -> dict:
    win_bundle = build_features_from_sc2le_proxy(
        mmr=mmr,
        apm=apm,
        duration_sec=duration_sec,
        opponent_mmr=opponent_mmr,
        result="Win",
    )
    loss_bundle = build_features_from_sc2le_proxy(
        mmr=mmr,
        apm=apm,
        duration_sec=duration_sec,
        opponent_mmr=opponent_mmr,
        result="Loss",
    )
    passed = win_bundle["values"] == loss_bundle["values"]
    return {
        "passed": passed,
        "win_values": win_bundle["values"],
        "loss_values": loss_bundle["values"],
    }


def _block_present(results: dict, keys: Sequence[str]) -> bool:
    block = _result_block(results, *keys)
    return bool(block)


def _missing_agent_fields(results: dict, *, mode: str) -> List[str]:
    if mode != "strict":
        return []
    missing: List[str] = []
    for feature_name, keys in AGENT_REQUIRED_BLOCKS.items():
        if not _block_present(results, keys):
            missing.append(feature_name)
    return missing


def build_features_from_agent_results(
    results: Optional[dict],
    *,
    damage_probs: Optional[Sequence[float]] = None,
    targets: Optional[Sequence[dict]] = None,
    mode: str = "strict",
    latency_reference_ms: float = LATENCY_REFERENCE_MS,
) -> dict:
    """Build unified mission features from standardized Agent results."""
    results = _safe_dict(results)
    warnings: List[str] = []
    sources: Dict[str, str] = {}
    values: Dict[str, float] = {}
    missing = _missing_agent_fields(results, mode=mode)

    if mode == "strict" and missing:
        return {
            "feature_version": FEATURE_VERSION,
            "values": {name: 0.0 for name in FEATURE_ORDER},
            "sources": sources,
            "warnings": warnings,
            "assessment_status": "insufficient_data",
            "missing_fields": missing,
        }

    if mode in {"fixture", "test"}:
        warnings.append("using_fixture")

    damage = _result_block(results, "damage_confirmation")
    damage_out = _safe_dict(damage.get("output_data"))
    if damage_probs is not None:
        values["damage_rate"] = clamp(_mean(damage_probs))
        sources["damage_rate"] = "damage_probs"
    elif targets:
        values["damage_rate"] = clamp(_mean([float(item.get("damage_probability", 0.0)) for item in targets]))
        sources["damage_rate"] = "targets.damage_probability"
    elif damage_out.get("engaged_targets") is not None and damage_out.get("confirmed_destroyed") is not None:
        engaged = max(1, int(damage_out.get("engaged_targets")))
        destroyed = int(damage_out.get("confirmed_destroyed"))
        values["damage_rate"] = clamp(destroyed / engaged)
        sources["damage_rate"] = "damage_confirmation"
    elif mode in {"fixture", "test", "hybrid"}:
        values["damage_rate"] = clamp(float(FEATURE_SCHEMA["damage_rate"]["missing_policy_fixture"]))
        sources["damage_rate"] = "fixture_default"
    else:
        missing.append("damage_rate")

    resource = _result_block(results, "resource_allocation")
    resource_out = _safe_dict(resource.get("output_data"))
    readiness = _normalize_score(resource_out.get("readiness"))
    if readiness is not None:
        values["asset_readiness"] = readiness
        sources["asset_readiness"] = "resource_allocation.readiness"
    elif targets:
        values["asset_readiness"] = clamp(0.92 - 0.18 * _mean([float(item.get("ammo_need", 0.5)) for item in targets]))
        sources["asset_readiness"] = "targets.ammo_need"
    elif mode in {"fixture", "test", "hybrid"}:
        values["asset_readiness"] = float(FEATURE_SCHEMA["asset_readiness"]["missing_policy_fixture"])
        sources["asset_readiness"] = "fixture_default"
    else:
        missing.append("asset_readiness")

    execution = _result_block(results, "execution_control", "artillery", "assault")
    execution_out = _safe_dict(execution.get("output_data"))
    latency_ms = None
    for key in ("latency_ms", "control_latency_ms", "median_latency_ms"):
        if execution_out.get(key) not in (None, ""):
            try:
                latency_ms = max(0.0, float(execution_out.get(key)))
                break
            except (TypeError, ValueError):
                continue
    if latency_ms is not None:
        values["control_timeliness"] = clamp(1.0 - latency_ms / max(1.0, float(latency_reference_ms)))
        sources["control_timeliness"] = "execution_control.latency_ms"
    elif mode in {"fixture", "test", "hybrid"}:
        values["control_timeliness"] = float(FEATURE_SCHEMA["control_timeliness"]["missing_policy_fixture"])
        sources["control_timeliness"] = "fixture_default"
    else:
        missing.append("control_timeliness")

    perception = _result_block(results, "perception_detection", "recon")
    fusion = _result_block(results, "data_fusion")
    perception_out = _safe_dict(perception.get("output_data"))
    fusion_out = _safe_dict(fusion.get("output_data"))
    detections = _safe_list(perception_out.get("detections"))
    confs = [_normalize_score(_safe_dict(item).get("conf")) for item in detections]
    confs = [value for value in confs if value is not None]
    if confs:
        values["intel_confidence"] = clamp(_mean(confs))
        sources["intel_confidence"] = "perception_detection.detections.conf"
    elif _normalize_score(_safe_dict(fusion_out.get("fused_track")).get("det_conf")) is not None:
        values["intel_confidence"] = _normalize_score(_safe_dict(fusion_out.get("fused_track")).get("det_conf"))
        sources["intel_confidence"] = "data_fusion.fused_track.det_conf"
    elif targets:
        values["intel_confidence"] = clamp(_mean([float(item.get("detection_confidence", 0.7)) for item in targets]))
        sources["intel_confidence"] = "targets.detection_confidence"
    elif mode in {"fixture", "test", "hybrid"}:
        values["intel_confidence"] = float(FEATURE_SCHEMA["intel_confidence"]["missing_policy_fixture"])
        sources["intel_confidence"] = "fixture_default"
    else:
        missing.append("intel_confidence")

    threat = _result_block(results, "threat_evaluation", "evaluator")
    threat_out = _safe_dict(threat.get("output_data"))
    ranked = _safe_list(threat_out.get("ranked_targets"))
    if ranked:
        scores = [_normalize_score(_safe_dict(item).get("score")) for item in ranked]
        scores = [value for value in scores if value is not None]
        if scores:
            values["threat_pressure"] = clamp(_mean(scores))
            sources["threat_pressure"] = "threat_evaluation.ranked_targets"
    if "threat_pressure" not in values:
        for key in ("priority_score", "eval_score", "threat_score"):
            score = _normalize_score(threat_out.get(key))
            if score is not None:
                values["threat_pressure"] = score
                sources["threat_pressure"] = f"threat_evaluation.{key}"
                break
    if "threat_pressure" not in values and targets:
        values["threat_pressure"] = clamp(
            _mean(
                [
                    float(item.get("threat_score", 0.5)) * (1.0 - float(item.get("damage_probability", 0.0)))
                    for item in targets
                ]
            )
        )
        sources["threat_pressure"] = "targets.threat_score"
    elif "threat_pressure" not in values and mode in {"fixture", "test", "hybrid"}:
        values["threat_pressure"] = float(FEATURE_SCHEMA["threat_pressure"]["missing_policy_fixture"])
        sources["threat_pressure"] = "fixture_default"
    elif "threat_pressure" not in values:
        missing.append("threat_pressure")

    supply = None
    for key in ("supply_pressure", "ammo_pressure", "resource_pressure"):
        supply = _normalize_score(resource_out.get(key))
        if supply is not None:
            values["ammo_pressure"] = supply
            sources["ammo_pressure"] = f"resource_allocation.{key}"
            break
    if "ammo_pressure" not in values and targets:
        values["ammo_pressure"] = clamp(_mean([float(item.get("ammo_need", 0.5)) for item in targets]))
        sources["ammo_pressure"] = "targets.ammo_need"
    elif "ammo_pressure" not in values and mode in {"fixture", "test", "hybrid"}:
        values["ammo_pressure"] = float(FEATURE_SCHEMA["ammo_pressure"]["missing_policy_fixture"])
        sources["ammo_pressure"] = "fixture_default"
    elif "ammo_pressure" not in values:
        missing.append("ammo_pressure")

    communication = _result_block(results, "communication")
    communication_out = _safe_dict(communication.get("output_data"))
    comm = None
    for key in ("delivery_rate", "coordination_score", "team_sync", "comm_quality"):
        comm = _normalize_score(communication_out.get(key))
        if comm is not None:
            values["comm_quality"] = comm
            sources["comm_quality"] = f"communication.{key}"
            break
    if "comm_quality" not in values and mode in {"fixture", "test", "hybrid"}:
        values["comm_quality"] = float(FEATURE_SCHEMA["comm_quality"]["missing_policy_fixture"])
        sources["comm_quality"] = "fixture_default"
    elif "comm_quality" not in values:
        missing.append("comm_quality")

    if mode == "strict" and missing:
        return {
            "feature_version": FEATURE_VERSION,
            "values": {name: 0.0 for name in FEATURE_ORDER},
            "sources": sources,
            "warnings": warnings,
            "assessment_status": "insufficient_data",
            "missing_fields": sorted(set(missing)),
        }

    for name in FEATURE_ORDER:
        values.setdefault(name, float(FEATURE_SCHEMA[name]["missing_policy_fixture"]))

    assert_no_label_leakage(dict(values), context="agent_results")
    for forbidden in LABEL_FIELDS:
        if forbidden in results:
            warnings.append(f"ignored_label_field:{forbidden}")

    bundle = {
        "feature_version": FEATURE_VERSION,
        "values": {name: round(float(values[name]), 4) for name in FEATURE_ORDER},
        "sources": sources,
        "warnings": warnings,
        "assessment_status": "ready",
    }
    bundle["warnings"].extend(validate_feature_ranges(bundle["values"]))
    return bundle


def normalize_feature_bundle(bundle: dict, *, metadata: Optional[dict] = None) -> dict:
    """Apply shared clipping and version checks."""
    metadata = _safe_dict(metadata)
    expected_version = metadata.get("feature_version") or FEATURE_VERSION
    warnings = list(bundle.get("warnings") or [])
    if bundle.get("feature_version") != expected_version:
        warnings.append(
            f"feature_version_mismatch: bundle={bundle.get('feature_version')} metadata={expected_version}"
        )
    values = {name: round(clamp(float(bundle.get("values", {}).get(name, 0.0))), 4) for name in FEATURE_ORDER}
    normalized = dict(bundle)
    normalized["values"] = values
    normalized["warnings"] = warnings + validate_feature_ranges(values)
    return normalized


def bundle_to_vector(bundle: dict) -> List[float]:
    return values_to_vector(bundle.get("values") or {})


def legacy_mission_vector_from_bundle(bundle: dict) -> List[float]:
    return bundle_to_vector(bundle)
