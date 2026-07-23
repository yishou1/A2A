"""Algolib-backed closed-loop orchestration (feature/score/damage/advise services)."""
from __future__ import annotations

import os
import time
import uuid
from typing import Any, List, Optional, Sequence, Tuple

from algolib_bridge import AlgorithmLibraryClient, AlgorithmLibraryError, AlgolibSettings
from closed_loop_agent.closed_loop_core import _closed_loop_optimization

AGENT_BACKEND_ENV = "CLOSED_LOOP_BACKEND"

# Prefer images when available; force with damage_input_mode / CLOSED_LOOP_DAMAGE_INPUT_MODE.
DAMAGE_INPUT_MODE_AUTO = "auto"
DAMAGE_INPUT_MODE_FEATURES = "features"
DAMAGE_INPUT_MODE_IMAGES = "images"

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
    nested = _safe_dict(target.get("handcrafted_features") or target.get("features"))
    source = {**nested, **target}
    for key in HANDCRAFTED_KEYS:
        if key in source and source.get(key) not in (None, ""):
            try:
                features[key] = float(source[key])
            except (TypeError, ValueError):
                continue
    return features


def _looks_like_image_payload(value: Any) -> bool:
    if value is None or value == "":
        return False
    if isinstance(value, str) and value.strip():
        return True
    if isinstance(value, dict):
        return bool(value.get("path") or value.get("base64") or value.get("data") or value.get("content"))
    return False


def _looks_like_polygon(value: Any) -> bool:
    if value is None or value == "" or value == []:
        return False
    if isinstance(value, str) and value.strip():
        return True
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes)):
        return len(value) >= 3
    return False


def extract_image_pair(target: dict) -> Tuple[Any, Any]:
    """Optional image interface: resolve pre/post payloads from a target dict."""
    images = _safe_dict(target.get("images") or target.get("image_pair") or target.get("damage_images"))
    pre = (
        target.get("pre_image")
        or target.get("pre_disaster_image")
        or images.get("pre")
        or images.get("pre_image")
        or images.get("pre_disaster")
    )
    post = (
        target.get("post_image")
        or target.get("post_disaster_image")
        or images.get("post")
        or images.get("post_image")
        or images.get("post_disaster")
    )
    return pre, post


def extract_polygon(target: dict) -> Any:
    """Optional polygon interface for images-mode damage assessment."""
    geometry = _safe_dict(target.get("geometry") or target.get("roi"))
    return (
        target.get("polygon")
        or target.get("building_polygon")
        or target.get("wkt")
        or geometry.get("polygon")
        or geometry.get("coordinates")
    )


def has_images_inputs(target: dict) -> bool:
    pre, post = extract_image_pair(target)
    polygon = extract_polygon(target)
    return _looks_like_image_payload(pre) and _looks_like_image_payload(post) and _looks_like_polygon(polygon)


def resolve_damage_input_mode(
    target: dict,
    *,
    preferred_mode: str = DAMAGE_INPUT_MODE_AUTO,
) -> str:
    """
    Choose damage assessor input mode.

    - auto: images if pre/post/polygon present, else features
    - features / images: forced (images still requires complete inputs)
    """
    mode = str(preferred_mode or DAMAGE_INPUT_MODE_AUTO).strip().lower()
    if mode not in {DAMAGE_INPUT_MODE_AUTO, DAMAGE_INPUT_MODE_FEATURES, DAMAGE_INPUT_MODE_IMAGES}:
        mode = DAMAGE_INPUT_MODE_AUTO
    if mode == DAMAGE_INPUT_MODE_FEATURES:
        return DAMAGE_INPUT_MODE_FEATURES
    if mode == DAMAGE_INPUT_MODE_IMAGES:
        return DAMAGE_INPUT_MODE_IMAGES if has_images_inputs(target) else DAMAGE_INPUT_MODE_FEATURES
    return DAMAGE_INPUT_MODE_IMAGES if has_images_inputs(target) else DAMAGE_INPUT_MODE_FEATURES


def build_damage_assessor_inputs(
    target: dict,
    *,
    sample_id: str = "",
    preferred_mode: str = DAMAGE_INPUT_MODE_AUTO,
    device: Optional[str] = None,
) -> Tuple[Optional[dict], str, List[str]]:
    """
    Build xbd_damage_assessor request inputs.

    Returns (inputs_or_none, selected_mode, warnings).
    When inputs is None, caller should use a local probability fallback.
    """
    warnings: List[str] = []
    sid = str(sample_id or target.get("sample_id") or target.get("target_id") or "")
    mode = resolve_damage_input_mode(target, preferred_mode=preferred_mode)

    if mode == DAMAGE_INPUT_MODE_IMAGES:
        pre, post = extract_image_pair(target)
        polygon = extract_polygon(target)
        if not (_looks_like_image_payload(pre) and _looks_like_image_payload(post) and _looks_like_polygon(polygon)):
            warnings.append(f"xbd_damage_assessor:images_incomplete_fallback_features:{sid}")
            mode = DAMAGE_INPUT_MODE_FEATURES
        else:
            payload: dict[str, Any] = {
                "input_mode": "images",
                "sample_id": sid,
                "pre_image": pre,
                "post_image": post,
                "polygon": polygon,
            }
            if device:
                payload["device"] = device
            return payload, DAMAGE_INPUT_MODE_IMAGES, warnings

    handcrafted = _handcrafted_from_target(target)
    if not handcrafted:
        warnings.append(f"xbd_damage_assessor:skipped_no_inputs:{sid}")
        return None, DAMAGE_INPUT_MODE_FEATURES, warnings

    payload = {
        "input_mode": "features",
        "sample_id": sid,
        "handcrafted_features": handcrafted,
    }
    cnn = target.get("cnn_embedding")
    if isinstance(cnn, list) and cnn:
        payload["cnn_embedding"] = cnn
    return payload, DAMAGE_INPUT_MODE_FEATURES, warnings


def preferred_damage_input_mode(arguments: Optional[dict] = None) -> str:
    arguments = arguments or {}
    raw = (
        arguments.get("damage_input_mode")
        or arguments.get("xbd_input_mode")
        or os.environ.get("CLOSED_LOOP_DAMAGE_INPUT_MODE")
        or DAMAGE_INPUT_MODE_AUTO
    )
    mode = str(raw).strip().lower()
    if mode not in {DAMAGE_INPUT_MODE_AUTO, DAMAGE_INPUT_MODE_FEATURES, DAMAGE_INPUT_MODE_IMAGES}:
        return DAMAGE_INPUT_MODE_AUTO
    return mode


def _situation_label(threat_score: float, damage_prob: float) -> str:
    if threat_score >= 0.75 or damage_prob >= 0.7:
        return "critical"
    if threat_score >= 0.55 or damage_prob >= 0.45:
        return "watch"
    return "stable"


def assess_target_damage_via_algolib(
    client: AlgorithmLibraryClient,
    target: dict,
    *,
    request_id: str,
    preferred_mode: str = DAMAGE_INPUT_MODE_AUTO,
    device: Optional[str] = None,
) -> Tuple[float, str, dict, List[str]]:
    """Call xbd_damage_assessor for one target; returns prob, mode, raw_out, warnings."""
    sample_id = str(target.get("sample_id") or target.get("target_id") or request_id)
    inputs, mode, warnings = build_damage_assessor_inputs(
        target,
        sample_id=sample_id,
        preferred_mode=preferred_mode,
        device=device,
    )
    if inputs is None:
        damage_prob = float(target.get("damage_probability") or target.get("threat_score") or 0.5)
        return damage_prob, mode, {}, warnings

    params = {}
    if device:
        params["device"] = device
    damage_out = client.run_outputs(
        algorithm_id="xbd_damage_assessor",
        inputs=inputs,
        params=params,
        request_id=request_id,
        trace_id=request_id,
    )
    if damage_out.get("assessment_status") == "insufficient_data":
        warnings.append(f"xbd_damage_assessor:insufficient_data:{sample_id}:{mode}")
        # images incomplete at service → try features once if available
        if mode == DAMAGE_INPUT_MODE_IMAGES:
            feature_inputs, feature_mode, feature_warnings = build_damage_assessor_inputs(
                target,
                sample_id=sample_id,
                preferred_mode=DAMAGE_INPUT_MODE_FEATURES,
            )
            warnings.extend(feature_warnings)
            if feature_inputs is not None:
                damage_out = client.run_outputs(
                    algorithm_id="xbd_damage_assessor",
                    inputs=feature_inputs,
                    params=params,
                    request_id=f"{request_id}-features",
                    trace_id=request_id,
                )
                mode = feature_mode
                if damage_out.get("assessment_status") != "insufficient_data":
                    return float(damage_out.get("damage_probability") or 0.0), mode, damage_out, warnings
        damage_prob = float(target.get("damage_probability") or 0.0)
        return damage_prob, mode, damage_out, warnings
    return float(damage_out.get("damage_probability") or 0.0), mode, damage_out, warnings


def run_closed_loop_via_algolib(arguments: dict) -> dict:
    settings = AlgolibSettings.load(agent_backend_env=AGENT_BACKEND_ENV)
    client = AlgorithmLibraryClient(settings)
    request_id = str(arguments.get("request_id") or f"cl-{uuid.uuid4().hex[:10]}")
    start = time.perf_counter()
    upstream = _extract_upstream_results(arguments)
    feature_mode = str(arguments.get("feature_mode") or "hybrid")
    damage_mode_pref = preferred_damage_input_mode(arguments)
    device = str(arguments.get("device") or os.environ.get("CLOSED_LOOP_DAMAGE_DEVICE") or "").strip() or None
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
    damage_mode_counts = {"images": 0, "features": 0}

    for index, target in enumerate(targets):
        sample_id = str(target.get("sample_id") or target.get("target_id") or f"target-{index}")
        damage_prob, used_mode, damage_out, damage_warnings = assess_target_damage_via_algolib(
            client,
            target,
            request_id=f"{request_id}-{index}",
            preferred_mode=damage_mode_pref,
            device=device,
        )
        warnings.extend(damage_warnings)
        damage_mode_counts[used_mode] = damage_mode_counts.get(used_mode, 0) + 1

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
                "damage_input_mode": used_mode,
                "damage_assessment": {
                    "assessment_status": damage_out.get("assessment_status"),
                    "damage_label": damage_out.get("damage_label"),
                    "damage_result": damage_out.get("damage_result"),
                }
                if damage_out
                else {},
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
                "damage_input_mode": used_mode,
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
        "damage_input_mode_preferred": damage_mode_pref,
        "damage_input_mode_counts": damage_mode_counts,
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
