"""Algolib-backed closed-loop orchestration (feature/score/damage/advise services)."""
from __future__ import annotations

import time
import uuid
from typing import Any, List

from algolib_bridge import AlgorithmLibraryClient, AlgorithmLibraryError, AlgolibSettings
from closed_loop_agent.closed_loop_core import _closed_loop_optimization

AGENT_BACKEND_ENV = "CLOSED_LOOP_BACKEND"

HANDCRAFTED_KEYS = (
    "pre_area",
    "spectral_delta",
    "texture_delta",
    "heat_signature",
    "crater_density",
    "std_spectral",
    "max_spectral",
    "high_change_ratio",
    "severe_damage_ratio",
    "collapse_ratio",
    "post_brightness",
    "brightness_drop",
    "normalized_distance",
    "detection_confidence",
    "threat_score",
)


def use_closed_loop_algolib() -> bool:
    return AlgolibSettings.load(agent_backend_env=AGENT_BACKEND_ENV).backend == "algolib"


def _safe_dict(value: Any) -> dict:
    return dict(value) if isinstance(value, dict) else {}


def _extract_upstream_results(arguments: dict) -> dict:
    results = _safe_dict(arguments.get("results"))
    if results:
        return results
    previous = arguments.get("previous_results")
    if isinstance(previous, list) and previous:
        merged: dict = {}
        for item in previous:
            if isinstance(item, dict):
                merged.update(item)
        return merged
    return {}


def _handcrafted_from_target(target: dict) -> dict:
    features = {}
    for key in HANDCRAFTED_KEYS:
        if key in target and target.get(key) not in (None, ""):
            try:
                features[key] = float(target[key])
            except (TypeError, ValueError):
                continue
    return features


def _situation_label(threat_score: float, damage_prob: float) -> str:
    if threat_score >= 0.75 or damage_prob >= 0.7:
        return "critical"
    if threat_score >= 0.55 or damage_prob >= 0.45:
        return "watch"
    return "stable"


def run_closed_loop_via_algolib(arguments: dict) -> dict:
    settings = AlgolibSettings.load(agent_backend_env=AGENT_BACKEND_ENV)
    client = AlgorithmLibraryClient(settings)
    request_id = str(arguments.get("request_id") or f"cl-{uuid.uuid4().hex[:10]}")
    start = time.perf_counter()
    upstream = _extract_upstream_results(arguments)
    feature_mode = str(arguments.get("feature_mode") or "hybrid")
    warnings: List[str] = []

    adapter_out = client.run_outputs(
        algorithm_id="mission_feature_adapter",
        inputs={
            "source_type": "agent_results",
            "mode": feature_mode if feature_mode in {"strict", "fixture", "hybrid"} else "hybrid",
            "agent_results": upstream,
        },
        request_id=request_id,
        trace_id=request_id,
    )
    if adapter_out.get("assessment_status") == "insufficient_data":
        warnings.append("mission_feature_adapter:insufficient_data")

    feature_values = dict(adapter_out.get("values") or {})
    mission_out = {
        "mission_completion": None,
        "mission_result": None,
        "assessment_status": adapter_out.get("assessment_status"),
        "warnings": list(adapter_out.get("warnings") or []),
    }
    if feature_values and adapter_out.get("assessment_status") != "insufficient_data":
        mission_out = client.run_outputs(
            algorithm_id="mission_completion_scorer",
            inputs={"features": feature_values},
            request_id=request_id,
            trace_id=request_id,
        )
    else:
        warnings.append("mission_completion_scorer:skipped_missing_features")

    mission_completion = float(mission_out.get("mission_completion") or 0.0)

    targets = arguments.get("targets")
    if not isinstance(targets, list):
        targets = []
    targets = [dict(item) for item in targets if isinstance(item, dict)]

    assessments: List[dict] = []
    commands: List[dict] = []
    probs: List[float] = []

    for index, target in enumerate(targets):
        handcrafted = _handcrafted_from_target(target)
        sample_id = str(target.get("sample_id") or target.get("target_id") or f"target-{index}")
        if handcrafted:
            damage_out = client.run_outputs(
                algorithm_id="xbd_damage_assessor",
                inputs={
                    "input_mode": "features",
                    "sample_id": sample_id,
                    "handcrafted_features": handcrafted,
                },
                request_id=f"{request_id}-{index}",
                trace_id=request_id,
            )
            if damage_out.get("assessment_status") == "insufficient_data":
                damage_prob = float(target.get("damage_probability") or 0.0)
                warnings.append(f"xbd_damage_assessor:insufficient_data:{sample_id}")
            else:
                damage_prob = float(damage_out.get("damage_probability") or 0.0)
        else:
            damage_prob = float(target.get("damage_probability") or target.get("threat_score") or 0.5)
            warnings.append(f"xbd_damage_assessor:skipped_no_handcrafted:{sample_id}")

        probs.append(damage_prob)
        threat_score = float(target.get("threat_score") or 0.5)
        situation = _situation_label(threat_score, damage_prob)
        advice = client.run_outputs(
            algorithm_id="closed_loop_decision_advisor",
            inputs={
                "target": target,
                "damage_probability": damage_prob,
                "situation": situation,
                "mission_completion": mission_completion,
            },
            request_id=f"{request_id}-adv-{index}",
            trace_id=request_id,
        )
        action = str(advice.get("action") or advice.get("recommended_action") or "observe")
        effect_delta = float(advice.get("effect_delta") or advice.get("priority") or 0.0)
        assessments.append(
            {
                "target_id": target.get("target_id") or sample_id,
                "damage_probability": round(damage_prob, 4),
                "situation": situation,
                "action": action,
                "effect_delta": effect_delta,
                "advice": advice,
            }
        )
        commands.append(
            {
                "command_id": f"CL-ALG-{index + 1:03d}",
                "target_id": target.get("target_id") or sample_id,
                "action": action,
                "priority": round(max(damage_prob, threat_score), 4),
                "source": "closed_loop_decision_advisor",
            }
        )

    latency_ms = round((time.perf_counter() - start) * 1000.0, 3)
    mean_damage = round(sum(probs) / len(probs), 4) if probs else 0.0
    meets = mission_completion >= float(mission_out.get("threshold") or 0.5) if mission_out.get("mission_completion") is not None else False

    output_data = {
        "cycles": 1,
        "targets": targets,
        "assessments": assessments,
        "commands": commands,
        "mission_assessment": mission_out,
        "feature_bundle": adapter_out,
        "mission_completion_initial": round(mission_completion, 4),
        "mission_completion_final": round(mission_completion, 4),
        "mission_completion_improvement": 0.0,
        "mean_damage_probability": mean_damage,
        "meets_requirements": meets,
        "backend": "algolib",
        "algorithms": [
            "mission_feature_adapter",
            "mission_completion_scorer",
            "xbd_damage_assessor",
            "closed_loop_decision_advisor",
        ],
        "warnings": warnings + list(mission_out.get("warnings") or []),
        "latency_ms": latency_ms,
        "transport": settings.transport,
    }
    return {
        "task_type": "closed_loop_optimization",
        "input_data": arguments,
        "output_data": output_data,
        "accuracy": mean_damage,
        "latency": latency_ms / 1000.0,
    }


def run_closed_loop_with_backend(arguments: dict) -> dict:
    settings = AlgolibSettings.load(agent_backend_env=AGENT_BACKEND_ENV)
    if settings.backend != "algolib":
        result = _closed_loop_optimization(arguments)
        if isinstance(result.get("output_data"), dict):
            result["output_data"].setdefault("backend", "local")
        return result

    try:
        return run_closed_loop_via_algolib(arguments)
    except AlgorithmLibraryError as exc:
        if not settings.fallback_local:
            raise
        result = _closed_loop_optimization(arguments)
        output_data = result.setdefault("output_data", {})
        if isinstance(output_data, dict):
            warnings = list(output_data.get("warnings") or [])
            warnings.append(f"algolib_fallback:{exc}")
            output_data["warnings"] = warnings
            output_data["backend"] = "local_fallback"
        return result
