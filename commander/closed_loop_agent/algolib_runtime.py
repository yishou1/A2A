"""Algolib-backed closed-loop orchestration (feature/score/damage/advise services)."""
from __future__ import annotations

import os
import time
import uuid
from dataclasses import dataclass
from typing import Any, List, Optional, Sequence, Tuple

from algolib_bridge import AlgorithmLibraryClient, AlgorithmLibraryError, AlgolibSettings
from algolib_bridge.client import AlgorithmRunCall
from algolib_bridge.llm_planner import AlgolibLLMPlannerError, plan_algorithm_calls
from closed_loop_agent.agent_results_mapping import execution_gate_from_results
from closed_loop_agent.closed_loop_core import (
    _apply_action,
    _build_live_targets,
    _closed_loop_optimization,
)

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

ALLOWED_CLOSED_LOOP_ALGORITHMS = [
    "mission_feature_adapter",
    "mission_completion_scorer",
    "xbd_damage_assessor",
    "closed_loop_decision_advisor",
]

CLOSED_LOOP_STAGE_TASKS = [
    {
        "task": "mission_feature_adaptation",
        "default_algorithm_id": "mission_feature_adapter",
        "description": "Convert upstream agent results into mission feature vectors.",
    },
    {
        "task": "mission_completion_scoring",
        "default_algorithm_id": "mission_completion_scorer",
        "description": "Score mission completion from extracted mission features.",
    },
    {
        "task": "xbd_damage_assessment",
        "default_algorithm_id": "xbd_damage_assessor",
        "description": "Estimate damage probability for each target.",
    },
    {
        "task": "closed_loop_decision_advice",
        "default_algorithm_id": "closed_loop_decision_advisor",
        "description": "Recommend per-target actions for the closed-loop controller.",
    },
]


@dataclass
class ClosedLoopStagePlan:
    plan: dict[str, Any]
    calls_by_task: dict[str, AlgorithmRunCall]
    selected_algorithms: list[str]
    planner_mode: str
    warnings: list[str]

    def call_for(self, task: str) -> AlgorithmRunCall | None:
        return self.calls_by_task.get(task)


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


def _stage_plan_specs(
    *,
    feature_mode: str,
    preferred_damage_mode: str,
    device: Optional[str],
) -> list[dict[str, Any]]:
    specs: list[dict[str, Any]] = []
    for spec in CLOSED_LOOP_STAGE_TASKS:
        params: dict[str, Any] = {}
        if spec["default_algorithm_id"] == "xbd_damage_assessor":
            if preferred_damage_mode:
                params["damage_input_mode"] = preferred_damage_mode
            if device:
                params["device"] = device
        elif spec["default_algorithm_id"] == "mission_feature_adapter":
            params["mode"] = feature_mode if feature_mode in {"strict", "fixture", "hybrid"} else "hybrid"
        specs.append({**spec, "params": params})
    return specs


def _default_stage_call(
    client: AlgorithmLibraryClient,
    *,
    algorithm_id: str,
    task: str,
    inputs: dict[str, Any],
    params: Optional[dict[str, Any]] = None,
) -> AlgorithmRunCall:
    version = client.settings.default_version
    backend_type = client.settings.default_backend_type
    return AlgorithmRunCall(
        algorithm_id=algorithm_id,
        version=version,
        backend_type=backend_type,
        inputs=inputs,
        params=dict(params or {}),
        reason="closed_loop_stage_default",
    )


def _build_closed_loop_stage_plan(
    client: AlgorithmLibraryClient,
    *,
    request_id: str,
    feature_mode: str,
    preferred_damage_mode: str,
    device: Optional[str],
) -> ClosedLoopStagePlan:
    settings = client.settings
    warnings: list[str] = []
    stage_specs = _stage_plan_specs(
        feature_mode=feature_mode,
        preferred_damage_mode=preferred_damage_mode,
        device=device,
    )
    algorithms: list[dict[str, Any]] = []
    try:
        algorithms = client.list_algorithms()
    except AlgorithmLibraryError as exc:
        warnings.append(f"closed_loop_stage_plan:list_algorithms_failed:{exc}")
    active_ids = {str(item.get("algorithm_id")): item for item in algorithms if isinstance(item, dict)}

    if not settings.enable_llm:
        calls = {
            spec["task"]: _default_stage_call(
                client,
                algorithm_id=spec["default_algorithm_id"],
                task=spec["task"],
                inputs={},
                params=spec.get("params"),
            )
            for spec in stage_specs
        }
        plan = {
            "intent": "default_algorithm_stage",
            "task": "closed_loop_stage",
            "algorithm_calls": [
                {
                    "task": spec["task"],
                    "algorithm_id": spec["default_algorithm_id"],
                    "version": str(active_ids.get(spec["default_algorithm_id"], {}).get("version", settings.default_version)),
                    "backend_type": str(
                        active_ids.get(spec["default_algorithm_id"], {}).get(
                            "backend_type",
                            settings.default_backend_type,
                        )
                    ),
                    "params": dict(spec.get("params") or {}),
                    "reason": "LLM disabled; using default algorithm.",
                    "inputs": {"_source": "caller_structured_inputs"},
                }
                for spec in stage_specs
            ],
            "missing_fields": [],
            "explanation": "LLM disabled; using default algorithms for the closed-loop stage.",
            "mode": "fixed",
        }
        return ClosedLoopStagePlan(
            plan=plan,
            calls_by_task=calls,
            selected_algorithms=[call.algorithm_id for call in calls.values()],
            planner_mode="fixed",
            warnings=warnings,
        )

    try:
        calls, plan = plan_algorithm_calls(
            settings=settings,
            algorithms=algorithms,
            call_specs=stage_specs,
            task="closed_loop_stage",
        )
        if isinstance(plan, dict):
            plan["request_id"] = request_id
    except (AlgorithmLibraryError, AlgolibLLMPlannerError, ValueError, TypeError) as exc:
        warnings.append(f"closed_loop_stage_plan:llm_failed:{exc}")
        calls = [
            _default_stage_call(
                client,
                algorithm_id=spec["default_algorithm_id"],
                task=spec["task"],
                inputs={},
                params=spec.get("params"),
            )
            for spec in stage_specs
        ]
        plan = {
            "intent": "llm_stage_plan_fallback",
            "task": "closed_loop_stage",
            "algorithm_calls": [
                {
                    "task": spec["task"],
                    "algorithm_id": spec["default_algorithm_id"],
                    "version": str(active_ids.get(spec["default_algorithm_id"], {}).get("version", settings.default_version)),
                    "backend_type": str(
                        active_ids.get(spec["default_algorithm_id"], {}).get(
                            "backend_type",
                            settings.default_backend_type,
                        )
                    ),
                    "params": dict(spec.get("params") or {}),
                    "reason": f"plan_failed_fallback:{exc}",
                    "inputs": {"_source": "caller_structured_inputs"},
                }
                for spec in stage_specs
            ],
            "missing_fields": [],
            "explanation": f"LLM stage planning failed, fallback to default stage algorithms: {exc}",
            "mode": "fallback",
            "fallback_reason": str(exc),
            "request_id": request_id,
        }
        return ClosedLoopStagePlan(
            plan=plan,
            calls_by_task={spec["task"]: call for spec, call in zip(stage_specs, calls)},
            selected_algorithms=[call.algorithm_id for call in calls],
            planner_mode="fallback",
            warnings=warnings,
        )

    calls_by_task: dict[str, AlgorithmRunCall] = {}
    for spec, call in zip(stage_specs, calls):
        calls_by_task[spec["task"]] = call
    plan = dict(plan or {})
    plan.setdefault("task", "closed_loop_stage")
    plan.setdefault("mode", "llm")
    plan.setdefault("request_id", request_id)
    plan.setdefault("selected_algorithms", [call.algorithm_id for call in calls])
    return ClosedLoopStagePlan(
        plan=plan,
        calls_by_task=calls_by_task,
        selected_algorithms=[call.algorithm_id for call in calls],
        planner_mode="llm",
        warnings=warnings,
    )


def _run_outputs_from_stage_plan(
    client: AlgorithmLibraryClient,
    *,
    stage_plan: ClosedLoopStagePlan,
    algorithm_id: str,
    task: str,
    inputs: dict[str, Any],
    params: Optional[dict[str, Any]] = None,
    request_id: str,
    trace_id: str,
) -> dict:
    planned_call = stage_plan.call_for(task)
    if planned_call is None:
        planned_call = _default_stage_call(
            client,
            algorithm_id=algorithm_id,
            task=task,
            inputs=inputs,
            params=params,
        )
    call = AlgorithmRunCall(
        algorithm_id=planned_call.algorithm_id or algorithm_id,
        version=planned_call.version or client.settings.default_version,
        backend_type=planned_call.backend_type or client.settings.default_backend_type,
        inputs=inputs,
        params=dict(planned_call.params or params or {}),
        reason=planned_call.reason,
    )
    result = client.run_algorithm(request_id=request_id, trace_id=trace_id, call=call)
    if not result.get("ok", False):
        error = result.get("error") if isinstance(result.get("error"), dict) else {}
        raise AlgorithmLibraryError(
            f"{call.algorithm_id} failed: {error.get('code', 'UNKNOWN')}: {error.get('message', '')}"
        )
    outputs = result.get("outputs")
    if not isinstance(outputs, dict):
        raise AlgorithmLibraryError(f"{call.algorithm_id} response missing outputs object.")
    return outputs


def assess_target_damage_via_algolib(
    client: AlgorithmLibraryClient,
    target: dict,
    *,
    request_id: str,
    preferred_mode: str = DAMAGE_INPUT_MODE_AUTO,
    device: Optional[str] = None,
    stage_plan: Optional[ClosedLoopStagePlan] = None,
    llm_plans: Optional[List[dict]] = None,
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
    if stage_plan is not None:
        damage_out = _run_outputs_from_stage_plan(
            client,
            stage_plan=stage_plan,
            algorithm_id="xbd_damage_assessor",
            task="xbd_damage_assessment",
            inputs=inputs,
            params=params,
            request_id=request_id,
            trace_id=request_id,
        )
    else:
        damage_out = _run_outputs_maybe_planned(
            client,
            settings=client.settings,
            algorithm_id="xbd_damage_assessor",
            inputs=inputs,
            params=params,
            request_id=request_id,
            trace_id=request_id,
            task="xbd_damage_assessment",
            llm_plans=llm_plans,
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
                if stage_plan is not None:
                    damage_out = _run_outputs_from_stage_plan(
                        client,
                        stage_plan=stage_plan,
                        algorithm_id="xbd_damage_assessor",
                        task="xbd_damage_assessment",
                        inputs=feature_inputs,
                        params=params,
                        request_id=f"{request_id}-features",
                        trace_id=request_id,
                    )
                else:
                    damage_out = _run_outputs_maybe_planned(
                        client,
                        settings=client.settings,
                        algorithm_id="xbd_damage_assessor",
                        inputs=feature_inputs,
                        params=params,
                        request_id=f"{request_id}-features",
                        trace_id=request_id,
                        task="xbd_damage_assessment_features_fallback",
                        llm_plans=llm_plans,
                    )
                mode = feature_mode
                if damage_out.get("assessment_status") != "insufficient_data":
                    return float(damage_out.get("damage_probability") or 0.0), mode, damage_out, warnings
        damage_prob = float(target.get("damage_probability") or 0.0)
        return damage_prob, mode, damage_out, warnings
    return float(damage_out.get("damage_probability") or 0.0), mode, damage_out, warnings


def _score_mission_via_algolib(
    client: AlgorithmLibraryClient,
    *,
    agent_results: dict,
    feature_mode: str,
    request_id: str,
    stage_plan: Optional[ClosedLoopStagePlan] = None,
    llm_plans: Optional[List[dict]] = None,
) -> Tuple[dict, dict, List[str]]:
    warnings: List[str] = []
    adapter_inputs = {
        "source_type": "agent_results",
        "mode": feature_mode if feature_mode in {"strict", "fixture", "hybrid"} else "hybrid",
        "agent_results": agent_results,
    }
    if stage_plan is not None:
        adapter_out = _run_outputs_from_stage_plan(
            client,
            stage_plan=stage_plan,
            algorithm_id="mission_feature_adapter",
            task="mission_feature_adaptation",
            inputs=adapter_inputs,
            request_id=request_id,
            trace_id=request_id,
        )
    else:
        adapter_out = _run_outputs_maybe_planned(
            client,
            settings=client.settings,
            algorithm_id="mission_feature_adapter",
            inputs=adapter_inputs,
            request_id=request_id,
            trace_id=request_id,
            task="mission_feature_adaptation",
            llm_plans=llm_plans,
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
        if stage_plan is not None:
            mission_out = _run_outputs_from_stage_plan(
                client,
                stage_plan=stage_plan,
                algorithm_id="mission_completion_scorer",
                task="mission_completion_scoring",
                inputs={"features": feature_values},
                request_id=request_id,
                trace_id=request_id,
            )
        else:
            mission_out = _run_outputs_maybe_planned(
                client,
                settings=client.settings,
                algorithm_id="mission_completion_scorer",
                inputs={"features": feature_values},
                request_id=request_id,
                trace_id=request_id,
                task="mission_completion_scoring",
                llm_plans=llm_plans,
            )
    else:
        warnings.append("mission_completion_scorer:skipped_missing_features")
    return adapter_out, mission_out, warnings


def _run_outputs_maybe_planned(
    client: AlgorithmLibraryClient,
    *,
    settings: AlgolibSettings,
    algorithm_id: str,
    inputs: dict[str, Any],
    params: Optional[dict[str, Any]] = None,
    request_id: str,
    trace_id: str,
    task: str,
    llm_plans: Optional[List[dict]] = None,
) -> dict:
    if not settings.enable_llm:
        return client.run_outputs(
            algorithm_id=algorithm_id,
            inputs=inputs,
            params=params,
            request_id=request_id,
            trace_id=trace_id,
        )
    outputs, plan = client.run_outputs_with_planning(
        default_algorithm_id=algorithm_id,
        allowed_algorithm_ids=ALLOWED_CLOSED_LOOP_ALGORITHMS,
        inputs=inputs,
        params=params,
        request_id=request_id,
        trace_id=trace_id,
        task=task,
    )
    if llm_plans is not None:
        llm_plans.append({"task": task, "default_algorithm_id": algorithm_id, "plan": plan})
    return outputs


def _enrich_results_with_damage(upstream: dict, probs: Sequence[float], targets: Sequence[dict]) -> dict:
    enriched = dict(upstream)
    confirmed = sum(1 for prob in probs if float(prob) >= 0.5)
    enriched["damage_confirmation"] = {
        "output_data": {
            "engaged_targets": len(targets),
            "confirmed_destroyed": confirmed,
            "mean_damage_probability": round(sum(probs) / len(probs), 4) if probs else 0.0,
        }
    }
    return enriched


def run_closed_loop_via_algolib(arguments: dict) -> dict:
    settings = AlgolibSettings.load(agent_backend_env=AGENT_BACKEND_ENV)
    client = AlgorithmLibraryClient(settings)
    request_id = str(arguments.get("request_id") or f"cl-{uuid.uuid4().hex[:10]}")
    start = time.perf_counter()
    seed = int(arguments.get("seed") or 20260412)
    cycles = max(1, min(8, int(arguments.get("cycles") or 3)))
    upstream = _extract_upstream_results(arguments)
    feature_mode = str(arguments.get("feature_mode") or "hybrid")
    damage_mode_pref = preferred_damage_input_mode(arguments)
    device = str(arguments.get("device") or os.environ.get("CLOSED_LOOP_DAMAGE_DEVICE") or "").strip() or None
    warnings: List[str] = []

    # Beachhead often sends target_count without targets; reuse local synthesizer.
    targets, source_info = _build_live_targets(arguments, seed)
    if not arguments.get("targets"):
        warnings.append(f"targets_synthesized_from_target_count:{len(targets)}")

    history: List[dict] = []
    final_commands: List[dict] = []
    final_assessments: List[dict] = []
    adapter_out: dict = {}
    mission_out: dict = {}
    initial_completion = 0.0
    final_completion = 0.0
    update_latencies: List[float] = []
    damage_mode_counts = {"images": 0, "features": 0}
    probs: List[float] = []
    llm_plans: List[dict] = []
    stage_plan = _build_closed_loop_stage_plan(
        client,
        request_id=request_id,
        feature_mode=feature_mode,
        preferred_damage_mode=damage_mode_pref,
        device=device,
    )
    warnings.extend(stage_plan.warnings)
    if stage_plan.planner_mode != "fixed":
        llm_plans.append(stage_plan.plan)

    for cycle in range(1, cycles + 1):
        cycle_start = time.perf_counter()
        cycle_results = _enrich_results_with_damage(upstream, probs, targets) if probs else dict(upstream)
        try:
            adapter_out, mission_out, mission_warnings = _score_mission_via_algolib(
                client,
                agent_results=cycle_results,
                feature_mode=feature_mode,
                request_id=f"{request_id}-m{cycle}",
                stage_plan=stage_plan,
                llm_plans=llm_plans,
            )
            warnings.extend(mission_warnings)
        except AlgorithmLibraryError as exc:
            warnings.append(f"mission_services_failed_cycle_{cycle}:{exc}")
            mission_out = {
                "mission_completion": final_completion if cycle > 1 else 0.0,
                "assessment_status": "service_error",
                "warnings": [str(exc)],
            }
            adapter_out = adapter_out or {}

        mission_completion = float(mission_out.get("mission_completion") or 0.0)
        if cycle == 1:
            initial_completion = mission_completion
        final_completion = mission_completion

        assessments: List[dict] = []
        commands: List[dict] = []
        probs = []
        action_counts: dict[str, int] = {}

        for index, target in enumerate(targets):
            sample_id = str(target.get("sample_id") or target.get("target_id") or f"target-{index}")
            try:
                damage_prob, used_mode, damage_out, damage_warnings = assess_target_damage_via_algolib(
                    client,
                    target,
                    request_id=f"{request_id}-c{cycle}-{index}",
                    preferred_mode=damage_mode_pref,
                    device=device,
                    stage_plan=stage_plan,
                    llm_plans=llm_plans,
                )
            except AlgorithmLibraryError as exc:
                damage_prob = float(target.get("damage_probability") or target.get("threat_score") or 0.5)
                used_mode = "features"
                damage_out = {}
                damage_warnings = [f"xbd_damage_assessor:error:{sample_id}:{exc}"]
            warnings.extend(damage_warnings)
            damage_mode_counts[used_mode] = damage_mode_counts.get(used_mode, 0) + 1
            probs.append(damage_prob)

            threat_score = float(target.get("threat_score") or 0.5)
            situation = _situation_label(threat_score, damage_prob)
            try:
                advice_inputs = {
                    "target": target,
                    "damage_probability": damage_prob,
                    "situation": situation,
                    "mission_completion": mission_completion,
                }
                if stage_plan is not None:
                    advice = _run_outputs_from_stage_plan(
                        client,
                        stage_plan=stage_plan,
                        algorithm_id="closed_loop_decision_advisor",
                        task="closed_loop_decision_advice",
                        inputs=advice_inputs,
                        request_id=f"{request_id}-c{cycle}-adv-{index}",
                        trace_id=request_id,
                    )
                else:
                    advice = _run_outputs_maybe_planned(
                        client,
                        settings=settings,
                        algorithm_id="closed_loop_decision_advisor",
                        inputs=advice_inputs,
                        request_id=f"{request_id}-c{cycle}-adv-{index}",
                        trace_id=request_id,
                        task="closed_loop_decision_advice",
                        llm_plans=llm_plans,
                    )
            except AlgorithmLibraryError as exc:
                advice = {"action": "continue_tracking", "effect_delta": 0.04}
                warnings.append(f"closed_loop_decision_advisor:error:{sample_id}:{exc}")

            action = str(advice.get("action") or advice.get("recommended_action") or "continue_tracking")
            effect_delta = float(advice.get("effect_delta") or 0.0)
            priority = max(0.0, min(1.0, threat_score * (1.0 - damage_prob) + float(target.get("uncertainty") or 0.0)))
            damage_confirmed = bool(damage_out.get("damage_label") == 1) if damage_out else damage_prob >= 0.5

            commands.append(
                {
                    "command_id": f"CL-ALG-C{cycle}-{index + 1:03d}",
                    "target_id": target.get("target_id") or sample_id,
                    "action": action,
                    "priority": round(priority, 4),
                    "expected_effect_delta": round(effect_delta, 4),
                    "situation_cluster": situation,
                    "source": "closed_loop_decision_advisor",
                    "damage_input_mode": used_mode,
                }
            )
            assessments.append(
                {
                    "target_id": target.get("target_id") or sample_id,
                    "damage_probability": round(damage_prob, 4),
                    "damage_confirmed": damage_confirmed,
                    "damage_input_mode": used_mode,
                    "situation_cluster": situation,
                    "threat_score": round(threat_score, 4),
                    "uncertainty": round(float(target.get("uncertainty") or 0.0), 4),
                    "action": action,
                    "effect_delta": round(effect_delta, 4),
                    "damage_assessment": {
                        "assessment_status": damage_out.get("assessment_status"),
                        "damage_label": damage_out.get("damage_label"),
                        "damage_result": damage_out.get("damage_result"),
                    }
                    if damage_out
                    else {},
                    "advice": advice,
                }
            )
            action_counts[action] = action_counts.get(action, 0) + 1
            _apply_action(target, action, effect_delta)

        update_latency = time.perf_counter() - cycle_start
        update_latencies.append(update_latency)
        history.append(
            {
                "cycle": cycle,
                "mission_completion": round(mission_completion, 4),
                "mission_assessment": mission_out,
                "mean_damage_probability": round(sum(probs) / len(probs), 4) if probs else 0.0,
                "critical_targets": sum(1 for item in assessments if item.get("situation_cluster") == "critical"),
                "action_counts": action_counts,
                "update_latency_seconds": round(update_latency, 6),
            }
        )
        final_commands = sorted(commands, key=lambda item: float(item["priority"]), reverse=True)
        final_assessments = assessments

    total_latency = time.perf_counter() - start
    max_update_latency = max(update_latencies) if update_latencies else total_latency
    mean_damage = (
        round(sum(float(item["damage_probability"]) for item in final_assessments) / len(final_assessments), 4)
        if final_assessments
        else 0.0
    )
    mission_threshold = float(mission_out.get("threshold") or 0.5)
    meets_mission_threshold = (
        mission_out.get("mission_completion") is not None and final_completion >= mission_threshold
    )
    requirement_report = {
        "assessment_mode": "algolib_service_orchestration",
        "xbd_damage_accuracy_requirement": 0.92,
        "xbd_damage_accuracy_actual": None,
        "meets_xbd_damage_accuracy": False,
        "xbd_damage_accuracy_note": "not_evaluated_in_algolib_mode_use_local_for_offline_gates",
        "situation_update_frequency_requirement_seconds": 1.0,
        "situation_update_latency_actual_seconds": round(max_update_latency, 6),
        "meets_situation_update_frequency": bool(max_update_latency <= 1.0),
        "target_count_requirement": 50,
        "target_count_actual": len(targets),
        "meets_target_count": bool(len(targets) >= 50),
        "sc2le_proxy_model_loaded": bool(mission_out.get("mission_completion") is not None),
        "meets_mission_completion_threshold": meets_mission_threshold,
        "mission_completion_threshold": mission_threshold,
        "mission_completion_final": round(final_completion, 4),
        "feature_version": str(mission_out.get("feature_version") or adapter_out.get("feature_version") or "mission_features_v2"),
    }
    # Operational model gates that algolib can honestly claim (exclude offline xBD accuracy).
    metric_requirements_met = all(
        bool(requirement_report[key])
        for key in (
            "meets_situation_update_frequency",
            "meets_target_count",
            "sc2le_proxy_model_loaded",
        )
    )
    execution_gate = execution_gate_from_results(upstream)
    requirement_report["meets_execution_requirement"] = execution_gate["meets_execution_requirement"]
    requirement_report["execution_gate"] = execution_gate
    meets_requirements = bool(metric_requirements_met and execution_gate["meets_execution_requirement"])

    # A per-target/per-cycle fallback can emit the same dependency or
    # service error many times. Keep one copy in the public warning list and
    # retain multiplicity separately for diagnostics.
    all_warnings = warnings + list(mission_out.get("warnings") or [])
    warning_counts: dict[str, int] = {}
    unique_warnings: list[str] = []
    for warning in all_warnings:
        warning = str(warning)
        warning_counts[warning] = warning_counts.get(warning, 0) + 1
        if warning not in unique_warnings:
            unique_warnings.append(warning)

    output_data = {
        "algorithm": {
            "damage_assessment": "xbd_damage_assessor (features or images+polygon)",
            "mission_evaluation": "mission_feature_adapter + mission_completion_scorer",
            "closed_loop_policy": "closed_loop_decision_advisor",
            "backend": "algolib",
        },
        "source_info": source_info,
        "execution_control": {
            "control_cycles": cycles,
            "processed_targets": len(targets),
            "commands": final_commands,
        },
        "effect_assessment": {
            "damage_confirmed_count": sum(1 for item in final_assessments if item.get("damage_confirmed")),
            "mean_damage_probability": mean_damage,
            "target_assessments": final_assessments,
        },
        "closed_loop_optimization": {
            "mission_completion_initial": round(initial_completion, 4),
            "mission_completion_final": round(final_completion, 4),
            "mission_completion_improvement": round(final_completion - initial_completion, 4),
            "history": history,
        },
        "performance_report": {
            "max_update_latency_seconds": round(max_update_latency, 6),
            "total_agent_latency_seconds": round(total_latency, 6),
        },
        "requirement_report": requirement_report,
        "metric_requirements_met": metric_requirements_met,
        "execution_gate": execution_gate,
        "meets_requirements": meets_requirements,
        "meets_mission_threshold": meets_mission_threshold,
        # Flat aliases kept for older consumers / debugging.
        "targets": targets,
        "assessments": final_assessments,
        "commands": final_commands,
        "mission_assessment": mission_out,
        "feature_bundle": adapter_out,
        "mission_completion_initial": round(initial_completion, 4),
        "mission_completion_final": round(final_completion, 4),
        "mission_completion_improvement": round(final_completion - initial_completion, 4),
        "mean_damage_probability": mean_damage,
        "backend": "algolib",
        "damage_input_mode_preferred": damage_mode_pref,
        "damage_input_mode_counts": damage_mode_counts,
        "algorithms": [
            "mission_feature_adapter",
            "mission_completion_scorer",
            "xbd_damage_assessor",
            "closed_loop_decision_advisor",
        ],
        "selected_algorithms": list(stage_plan.selected_algorithms),
        "llm_algorithm_plans": llm_plans,
        "warnings": unique_warnings,
        "warning_counts": warning_counts,
        "latency_ms": round(total_latency * 1000.0, 3),
        "transport": settings.transport,
    }
    return {
        "task_type": "closed_loop_optimization",
        "input_data": arguments,
        "output_data": output_data,
        "accuracy": mean_damage,
        "latency": total_latency,
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
