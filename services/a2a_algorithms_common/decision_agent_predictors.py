"""Predictors that expose A2A decision-agent algorithms as HTTP services."""

from __future__ import annotations

import os
import sys

from contextlib import contextmanager
from pathlib import Path
from typing import Any

from .decision_agent_onnx import feature_tensor, load_metadata, run_scalar_model, target_history_tensor


def _ensure_a2a_import_path() -> None:
    configured = os.environ.get("A2A_REPO_ROOT")
    if configured:
        a2a_root = Path(configured).expanduser().resolve()
    else:
        a2a_root = Path(__file__).resolve().parents[3] / "A2A"
    if str(a2a_root) not in sys.path:
        sys.path.insert(0, str(a2a_root))


_ensure_a2a_import_path()

from decision_agents.common.schemas import AgentRequest  # noqa: E402
from decision_agents.compliance_authorization import local_algorithm as compliance_algorithms  # noqa: E402
from decision_agents.decision_planning import local_algorithm as planning_algorithms  # noqa: E402


def predict_decision_planning_core(inputs: dict[str, Any], params: dict[str, Any]) -> dict[str, Any]:
    del params
    request = AgentRequest.model_validate(inputs)
    if not request.scheduled_tasks:
        raise ValueError("scheduled_tasks is required.")
    if not request.resources:
        raise ValueError("resources is required.")

    weights, weight_source = planning_algorithms._scoring_weights_from_constraints(request)
    candidates = planning_algorithms.generate_candidate_plans(request)
    baseline_scored = planning_algorithms.score_candidate_plans(candidates, request, weights)
    target_trends, lstm_runtime = _predict_target_trends_with_onnx(request)
    scored, plan_scores, lr_runtime = _score_candidate_plans_with_onnx(
        baseline_scored,
        request,
        target_trends,
    )
    recommended = scored[0] if scored else None
    return {
        "candidate_plans": [plan.model_dump(mode="json") for plan in scored],
        "recommended_plan_id": recommended.id if recommended else None,
        "plan_scores": plan_scores,
        "target_trends": list(target_trends.values()),
        "method": "template_generation_logistic_lstm_scoring",
        "algorithm_stages": [
            "decision_planning_logistic",
            "decision_planning_lstm",
        ],
        "scoring_weights": weights,
        "weight_source": weight_source,
        "model_runtime": {
            "decision_planning_lstm": lstm_runtime,
            "decision_planning_lr": lr_runtime,
        },
        "handoff_notes": [
            "Plans are simulation-only decision-support candidates.",
            "Compliance and authorization checks are required before any downstream handoff.",
        ],
        "rag_evidence": [],
        "rag_answer": "",
        "rag_model_profile": {"enabled": False, "backend": "out_of_scope"},
        "rag_warnings": ["rag_out_of_scope"],
    }


def predict_compliance_authorization_core(
    inputs: dict[str, Any],
    params: dict[str, Any],
) -> dict[str, Any]:
    del params
    request = AgentRequest.model_validate(inputs)
    if not request.candidate_plans:
        raise ValueError("candidate_plans is required.")

    with _without_compliance_rag_evidence():
        result = compliance_algorithms.evaluate_compliance(request, use_rule_table=True)
    payload = {
        **result.model_dump(mode="json"),
        "method": "rule_table_logistic_calibration",
        "algorithm_stages": ["compliance_authorization_logistic"],
        "rule_table_version": "law-of-war-demo-v1",
    }
    calibration, runtime = _calibrate_compliance_with_onnx(result, request)
    payload.update(calibration)
    payload.update(
        {
            "model_runtime": {"compliance_authorization_lr": runtime},
            "rag_evidence": [],
            "rag_answer": "",
            "rag_model_profile": {"enabled": False, "backend": "out_of_scope"},
            "rag_warnings": ["rag_out_of_scope"],
        }
    )
    return payload


def _predict_target_trends_with_onnx(request: AgentRequest) -> tuple[dict[str, dict[str, Any]], dict[str, Any]]:
    trend_by_target = {}
    runtimes: list[dict[str, Any]] = []
    requested_targets = set(planning_algorithms._task_targets(request.scheduled_tasks))
    requested_targets.update(risk.target_id for risk in request.risk_assessments)
    histories = {history.target_id: history for history in request.target_histories}

    for target_id in sorted(requested_targets | set(histories)):
        history = histories.get(target_id)
        steps = list(history.steps if history else [])
        if len(steps) >= 12:
            result = run_scalar_model("decision_planning_lstm.onnx", target_history_tensor(steps))
            runtime = {**result.runtime, "target_id": target_id}
            if result.value is not None:
                score = round(result.value, 4)
                runtime["used"] = True
            else:
                score = planning_algorithms._lstm_trend_score(steps)
                runtime["used"] = False
        else:
            score = planning_algorithms._lstm_trend_score(steps)
            runtime = {
                "model": "decision_planning_lstm.onnx",
                "target_id": target_id,
                "backend": "python_formula",
                "fallback": True,
                "used": False,
                "reason": "insufficient_sequence_length",
                "required_steps": 12,
                "actual_steps": len(steps),
            }
        runtimes.append(runtime)
        trend_by_target[target_id] = {
            "target_id": target_id,
            "trend": planning_algorithms._trend_label(score),
            "trend_score": score,
        }
    return trend_by_target, {"targets": runtimes}


def _score_candidate_plans_with_onnx(
    candidate_plans,
    request: AgentRequest,
    target_trends: dict[str, dict[str, Any]],
):
    feature_order = _feature_order(
        "decision_planning_lr.onnx",
        [
            "coverage",
            "risk_alignment",
            "resource_efficiency",
            "constraint_fit",
            "authorization",
            "lstm_trend",
            "priority",
            "objective_fit",
        ],
    )
    task_targets = planning_algorithms._task_targets(request.scheduled_tasks)
    risk_by_target = {risk.target_id: risk for risk in request.risk_assessments}
    available_resources = [
        resource for resource in request.resources if resource.status == "available"
    ]
    total_available = max(len(available_resources), 1)
    plan_scores = []
    scored = []
    runtimes: list[dict[str, Any]] = []

    for plan in candidate_plans:
        features = planning_algorithms._planning_logistic_features(
            plan,
            request,
            task_targets,
            risk_by_target,
            total_available,
            target_trends,
        )
        result = run_scalar_model("decision_planning_lr.onnx", feature_tensor(features, feature_order))
        runtime = {**result.runtime, "plan_id": plan.id}
        if result.value is None:
            probability = planning_algorithms._logistic_probability(features, planning_algorithms.PLANNING_LOGISTIC_WEIGHTS)
            runtime["used"] = False
        else:
            probability = round(result.value, 6)
            runtime["used"] = True
        runtimes.append(runtime)
        final_score = round(0.7 * probability * 100.0 + 0.3 * plan.score, 2)
        scored_plan = plan.model_copy(
            update={
                "score": final_score,
                "status": "candidate",
                "rationale": (
                    f"logistic_probability={round(probability, 4)}, "
                    f"lstm_trend_score={features['lstm_trend']}, "
                    f"baseline_score={plan.score}"
                ),
            }
        )
        scored.append(scored_plan)
        plan_scores.append(
            {
                "plan_id": plan.id,
                "logistic_probability": round(probability, 4),
                "lstm_trend_score": features["lstm_trend"],
                "baseline_score": round(features["baseline_score"], 2),
                "final_score": final_score,
                "features": features,
            }
        )

    scored.sort(key=lambda item: (-item.score, item.id))
    if scored:
        scored[0] = scored[0].model_copy(update={"status": "recommended"})
    score_by_plan = {item["plan_id"]: item for item in plan_scores}
    ordered_scores = [score_by_plan[plan.id] for plan in scored]
    return scored, ordered_scores, {"plans": runtimes}


def _calibrate_compliance_with_onnx(result, request: AgentRequest) -> tuple[dict[str, Any], dict[str, Any]]:
    feature_order = _feature_order(
        "compliance_authorization_lr.onnx",
        [
            "blocking_violation_count",
            "warning_violation_count",
            "authorization_status_score",
            "authorization_out_of_scope",
            "rag_evidence_count",
            "law_of_war_rule_hit",
        ],
    )
    features = compliance_algorithms._compliance_logistic_features(result, request)
    selected_result = run_scalar_model("compliance_authorization_lr.onnx", feature_tensor(features, feature_order))
    runtime = dict(selected_result.runtime)
    if selected_result.value is None:
        calibration = compliance_algorithms._calibrate_compliance_result(result, request)
        runtime["used"] = False
        return calibration, runtime

    risk_probability = float(selected_result.value)
    decision = compliance_algorithms._calibrated_decision(result, risk_probability, features)
    per_plan_scores = []
    per_plan_runtimes = []
    for plan_result in result.per_plan_results:
        plan_features = compliance_algorithms._plan_logistic_features(plan_result, request)
        plan_onnx = run_scalar_model("compliance_authorization_lr.onnx", feature_tensor(plan_features, feature_order))
        plan_runtime = {**plan_onnx.runtime, "plan_id": plan_result.plan_id}
        if plan_onnx.value is None:
            plan_risk_probability = compliance_algorithms._logistic_probability(
                plan_features,
                compliance_algorithms.COMPLIANCE_LOGISTIC_WEIGHTS,
            )
            plan_runtime["used"] = False
        else:
            plan_risk_probability = float(plan_onnx.value)
            plan_runtime["used"] = True
        per_plan_runtimes.append(plan_runtime)
        per_plan_scores.append(
            {
                "plan_id": plan_result.plan_id,
                "risk_probability": round(plan_risk_probability, 4),
                "compliance_probability": round(1.0 - plan_risk_probability, 4),
                "features": plan_features,
            }
        )

    runtime.update({"used": True, "per_plan": per_plan_runtimes})
    approved = decision == "approved"
    return (
        {
            "decision": decision,
            "approved_for_demo_handoff": approved,
            "requires_human_approval": decision in {"blocked", "review_required"},
            "compliance_probability": round(1.0 - risk_probability, 4),
            "risk_probability": round(risk_probability, 4),
            "logistic_features": features,
            "per_plan_logistic_scores": per_plan_scores,
        },
        runtime,
    )


def _feature_order(model_name: str, fallback: list[str]) -> list[str]:
    metadata = load_metadata(model_name)
    feature_order = metadata.get("feature_order")
    if isinstance(feature_order, list) and all(isinstance(item, str) for item in feature_order):
        return feature_order
    return fallback


@contextmanager
def _without_compliance_rag_evidence():
    original = compliance_algorithms._collect_evidence
    compliance_algorithms._collect_evidence = lambda *_args, **_kwargs: []
    try:
        yield
    finally:
        compliance_algorithms._collect_evidence = original
