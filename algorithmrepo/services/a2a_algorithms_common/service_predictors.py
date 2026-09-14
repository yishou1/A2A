"""Predictor entrypoints used by python_http_service apps."""
from __future__ import annotations

from pathlib import Path

from .algorithm_profiles import resolve_algorithm_profile
from .association_rules import (
    choose_primary_rule,
    discretize_situation,
    load_or_mine_rules,
    match_rules,
    rule_artifact_identity,
)
from .clustering import cluster_points
from .conditional_tabular_gan import generate_conditional_samples
from .closed_loop_advisor import advise
from .execution_planner import run_planner
from .federated_fedavg import federated_average
from .mission_feature_adapter import build_features_from_agent_results, build_features_from_sc2le_proxy
from .mission_feature_schema import DEFAULT_MODEL_METADATA_PATH, DEFAULT_MODEL_PATH
from .mission_scorer import score_mission
from .motion_prediction import motion_predictor_identity, predict_single_track
from .intent_gaussian_naive_bayes import predict_intents
from .track_threat_algorithms import (
    graph_relation_reasoner,
    multimodal_feature_fuser,
    target_type_classifier,
    track_state_updater,
    trajectory_predictor,
)
from .threat_priority_random_forest import predict_threat_priorities
from .xbd_damage_classifier import assess_damage, damage_model_loaded


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[2]


def mission_model_loaded() -> bool:
    root = _repo_root()
    return (root / DEFAULT_MODEL_PATH).exists() and (root / DEFAULT_MODEL_METADATA_PATH).exists()


def xbd_damage_model_loaded() -> bool:
    return damage_model_loaded()


def predict_execution_rule_matcher(inputs: dict, params: dict) -> dict:
    phase = str(inputs.get("phase") or "strike")
    situation = dict(inputs.get("situation") or {})
    if not situation:
        raise ValueError("situation is required")
    items = discretize_situation(situation, phase)
    rules = load_or_mine_rules()
    matched = match_rules(items, rules, phase=phase)
    primary = choose_primary_rule(
        matched,
        default_executor_role="artillery" if phase == "strike" else "assault",
    )
    return {
        "matched_rules": matched,
        "primary_rule": primary,
        "matched_items": sorted(items),
        "model": rule_artifact_identity(),
    }


def predict_clustering_engine(inputs: dict, params: dict) -> dict:
    return cluster_points(inputs, params)


def predict_threat_priority_random_forest(inputs: dict, params: dict) -> dict:
    return predict_threat_priorities(inputs, params)


def predict_intent_gaussian_naive_bayes(inputs: dict, params: dict) -> dict:
    return predict_intents(inputs, params)


def predict_federated_fedavg_aggregator(inputs: dict, params: dict) -> dict:
    return federated_average(inputs, params)


def predict_conditional_tabular_gan(inputs: dict, params: dict) -> dict:
    return generate_conditional_samples(inputs, params)


def predict_trajectory_linear_predictor(inputs: dict, params: dict) -> dict:
    track = dict(inputs.get("track") or {})
    profile = resolve_algorithm_profile(params, inputs)
    result = predict_single_track(track, profile=profile)
    if not result.get("ok"):
        raise ValueError(result.get("error", {}).get("message", "prediction failed"))
    return {
        "velocity": result["velocity"],
        "aim_point": result["aim_point"],
        "execute_at": result["execute_at"],
        "future_t": result["future_t"],
        "model": result["model"],
        "fit": result["fit"],
        "history_points": result["history_points"],
        "model_identity": motion_predictor_identity(),
        "track_id": result.get("track_id"),
        "algorithm_profile": result.get("algorithm_profile", profile),
    }


def predict_execution_control_planner(inputs: dict, params: dict) -> dict:
    profile = resolve_algorithm_profile(params, inputs)
    payload = run_planner(
        {
            "phase": inputs.get("phase") or "strike",
            "results": inputs.get("results") or {},
            "context": inputs.get("context") or {},
            "profile": profile,
        }
    )
    output = payload["output_data"]
    return {
        "phase": output.get("phase"),
        "assessment_status": output.get("assessment_status", "ready"),
        "missing_fields": output.get("missing_fields", []),
        "commands": output.get("commands", []),
        "tracks": output.get("tracks", []),
        "coordination": output.get("coordination", {"groups": []}),
        "matched_rules": output.get("matched_rules", []),
        "prediction_details": output.get("prediction_details", []),
        "latency_ms": output.get("latency_ms"),
        "algorithm_profile": output.get("algorithm_profile", profile),
        "profile_config": output.get("profile_config") or {},
    }


def predict_mission_feature_adapter(inputs: dict, params: dict) -> dict:
    profile = resolve_algorithm_profile(params, inputs)
    source_type = str(inputs.get("source_type") or "")
    mode = str(inputs.get("mode") or params.get("mode") or "strict")
    if source_type == "sc2le_proxy":
        proxy = dict(inputs.get("sc2le_proxy") or {})
        bundle = build_features_from_sc2le_proxy(
            mmr=float(proxy.get("mmr") or 3000.0),
            apm=float(proxy.get("apm") or 120.0),
            duration_sec=float(proxy.get("duration_sec") or 0.0),
            opponent_mmr=float(proxy.get("opponent_mmr") or 3000.0),
            result=str(proxy.get("result") or ""),
        )
    elif source_type == "agent_results":
        bundle = build_features_from_agent_results(inputs.get("agent_results") or {}, mode=mode)
    else:
        raise ValueError("source_type must be sc2le_proxy or agent_results")
    return {
        "feature_version": bundle["feature_version"],
        "values": bundle["values"],
        "sources": bundle.get("sources") or {},
        "warnings": bundle.get("warnings") or [],
        "assessment_status": bundle.get("assessment_status", "ready"),
        "missing_fields": bundle.get("missing_fields") or [],
        "algorithm_profile": profile,
    }


def predict_mission_completion_scorer(inputs: dict, params: dict) -> dict:
    profile = resolve_algorithm_profile(params, inputs)
    features = dict(inputs.get("features") or {})
    if not features:
        raise ValueError("features is required")
    bundle = {
        "feature_version": "mission_features_v2",
        "values": features,
        "warnings": [],
        "assessment_status": "ready",
    }
    return score_mission(
        bundle,
        profile=profile,
        model_path=params.get("model_path"),
        metadata_path=params.get("metadata_path"),
    )


def predict_closed_loop_decision_advisor(inputs: dict, params: dict) -> dict:
    profile = resolve_algorithm_profile(params, inputs)
    target = dict(inputs.get("target") or {})
    if not target:
        raise ValueError("target is required")
    return advise(
        target,
        float(inputs.get("damage_probability", 0.0)),
        str(inputs.get("situation") or "watch"),
        float(inputs.get("mission_completion", 0.0)),
        profile=profile,
    )


def predict_xbd_damage_assessor(inputs: dict, params: dict) -> dict:
    device = str(params.get("device") or inputs.get("device") or "cpu")
    return assess_damage(inputs, device=device)


def predict_target_type_classifier(inputs: dict, params: dict) -> dict:
    return target_type_classifier(inputs, params)


def predict_trajectory_predictor(inputs: dict, params: dict) -> dict:
    return trajectory_predictor(inputs, params)


def predict_multimodal_feature_fuser(inputs: dict, params: dict) -> dict:
    return multimodal_feature_fuser(inputs, params)


def predict_track_state_updater(inputs: dict, params: dict) -> dict:
    return track_state_updater(inputs, params)


def predict_graph_relation_reasoner(inputs: dict, params: dict) -> dict:
    return graph_relation_reasoner(inputs, params)
