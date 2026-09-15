"""Adapters for independently packaged decision-support model capabilities."""

from __future__ import annotations

import json
import os

from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any

import numpy as np

from decision_support import compliance, planning
from decision_support.schemas import AgentRequest, CandidatePlan, ComplianceCheckResult


DEFAULT_PROVIDERS = ("CPUExecutionProvider",)
PACKAGE_VERSIONS = {
    "decision_plan_recommender_onnx": "1.0.0",
    "target_trend_predictor_onnx": "1.0.0",
    "compliance_risk_scorer_onnx": "1.0.0",
}


@dataclass(frozen=True)
class CapabilityResult:
    value: float | None
    runtime: dict[str, Any]


def algorithm_repository_root() -> Path:
    configured = os.getenv("DECISION_CAPABILITY_PACKAGE_ROOT")
    if configured:
        return Path(configured).expanduser().resolve()
    return Path(__file__).resolve().parents[2]


def algorithm_package(algorithm_id: str) -> Path:
    try:
        version = PACKAGE_VERSIONS[algorithm_id]
    except KeyError as exc:
        raise ValueError(f"Unknown decision capability: {algorithm_id}") from exc
    return algorithm_repository_root() / "examples" / algorithm_id / version


def run_scalar_capability(algorithm_id: str, inputs: np.ndarray) -> CapabilityResult:
    package = algorithm_package(algorithm_id)
    model_path = package / "model.onnx"
    runtime = {
        "algorithm_id": algorithm_id,
        "version": PACKAGE_VERSIONS[algorithm_id],
        "model": "model.onnx",
        "backend": "onnxruntime",
        "fallback": False,
    }
    if not model_path.is_file():
        runtime.update(
            {"backend": "python_formula", "fallback": True, "reason": "model_not_found"}
        )
        return CapabilityResult(None, runtime)
    try:
        session = _load_session(str(model_path), DEFAULT_PROVIDERS)
        input_name = session.get_inputs()[0].name
        output = session.run(None, {input_name: inputs.astype(np.float32)})[0]
        runtime.update(
            {
                "model_path": str(model_path),
                "input_name": input_name,
                "output_name": session.get_outputs()[0].name,
            }
        )
        return CapabilityResult(float(output.reshape(-1)[0]), runtime)
    except Exception as exc:  # pragma: no cover - verified through fallback tests.
        runtime.update(
            {
                "backend": "python_formula",
                "fallback": True,
                "reason": f"onnx_runtime_failed:{type(exc).__name__}",
            }
        )
        return CapabilityResult(None, runtime)


def load_capability_metadata(algorithm_id: str) -> dict[str, Any]:
    path = algorithm_package(algorithm_id) / "model.metadata.json"
    if not path.is_file():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


def feature_tensor(features: dict[str, float], feature_order: list[str]) -> np.ndarray:
    return np.asarray(
        [[float(features.get(name, 0.0)) for name in feature_order]],
        dtype=np.float32,
    )


def target_history_tensor(steps) -> np.ndarray:
    values = [
        [
            float(step.risk_score) / 100.0,
            float(step.probability),
            1.0 / max(float(step.priority), 1.0),
            float(step.resource_pressure),
        ]
        for step in list(steps)[-12:]
    ]
    return np.asarray([values], dtype=np.float32)


class OnnxPlanningCapabilities:
    """Planning model capabilities backed by independent ONNX packages."""

    def predict_target_trends(
        self, request: AgentRequest
    ) -> tuple[dict[str, dict[str, Any]], dict[str, Any]]:
        trend_by_target: dict[str, dict[str, Any]] = {}
        runtimes: list[dict[str, Any]] = []
        requested_targets = set(planning._task_targets(request.scheduled_tasks))
        requested_targets.update(risk.target_id for risk in request.risk_assessments)
        histories = {history.target_id: history for history in request.target_histories}

        for target_id in sorted(requested_targets | set(histories)):
            history = histories.get(target_id)
            steps = list(history.steps if history else [])
            if len(steps) >= 12:
                result = run_scalar_capability(
                    "target_trend_predictor_onnx", target_history_tensor(steps)
                )
                runtime = {**result.runtime, "target_id": target_id}
                if result.value is not None:
                    score = round(result.value, 4)
                    runtime["used"] = True
                else:
                    score = planning._lstm_trend_score(steps)
                    runtime["used"] = False
            else:
                score = planning._lstm_trend_score(steps)
                runtime = {
                    "algorithm_id": "target_trend_predictor_onnx",
                    "version": PACKAGE_VERSIONS["target_trend_predictor_onnx"],
                    "model": "model.onnx",
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
                "trend": planning._trend_label(score),
                "trend_score": score,
            }
        return trend_by_target, {
            "algorithm_id": "target_trend_predictor_onnx",
            "version": PACKAGE_VERSIONS["target_trend_predictor_onnx"],
            "targets": runtimes,
        }

    def score_candidate_plans(
        self,
        candidate_plans: list[CandidatePlan],
        request: AgentRequest,
        target_trends: dict[str, dict[str, Any]],
    ) -> tuple[list[CandidatePlan], list[dict[str, Any]], dict[str, Any]]:
        algorithm_id = "decision_plan_recommender_onnx"
        feature_order = _feature_order(
            algorithm_id,
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
        task_targets = planning._task_targets(request.scheduled_tasks)
        risk_by_target = {risk.target_id: risk for risk in request.risk_assessments}
        available_resources = [
            resource for resource in request.resources if resource.status == "available"
        ]
        total_available = max(len(available_resources), 1)
        plan_scores: list[dict[str, Any]] = []
        scored: list[CandidatePlan] = []
        runtimes: list[dict[str, Any]] = []

        for plan in candidate_plans:
            features = planning._planning_logistic_features(
                plan,
                request,
                task_targets,
                risk_by_target,
                total_available,
                target_trends,
            )
            result = run_scalar_capability(
                algorithm_id, feature_tensor(features, feature_order)
            )
            runtime = {**result.runtime, "plan_id": plan.id}
            if result.value is None:
                probability = planning._logistic_probability(
                    features, planning.PLANNING_LOGISTIC_WEIGHTS
                )
                runtime["used"] = False
            else:
                probability = round(result.value, 6)
                runtime["used"] = True
            runtimes.append(runtime)
            final_score = round(0.7 * probability * 100.0 + 0.3 * plan.score, 2)
            scored.append(
                plan.model_copy(
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
            )
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
        return scored, ordered_scores, {
            "algorithm_id": algorithm_id,
            "version": PACKAGE_VERSIONS[algorithm_id],
            "plans": runtimes,
        }


class OnnxComplianceCapabilities:
    """Compliance scoring backed by an independent ONNX package."""

    def calibrate(
        self,
        result: ComplianceCheckResult,
        request: AgentRequest,
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        algorithm_id = "compliance_risk_scorer_onnx"
        feature_order = _feature_order(
            algorithm_id,
            [
                "blocking_violation_count",
                "warning_violation_count",
                "authorization_status_score",
                "authorization_out_of_scope",
                "rag_evidence_count",
                "law_of_war_rule_hit",
            ],
        )
        features = compliance._compliance_logistic_features(result, request)
        selected = run_scalar_capability(
            algorithm_id, feature_tensor(features, feature_order)
        )
        runtime = dict(selected.runtime)
        if selected.value is None:
            calibration = compliance._calibrate_compliance_result(result, request)
            runtime["used"] = False
            return calibration, runtime

        risk_probability = float(selected.value)
        decision = compliance._calibrated_decision(
            result, risk_probability, features
        )
        per_plan_scores: list[dict[str, Any]] = []
        per_plan_runtimes: list[dict[str, Any]] = []
        for plan_result in result.per_plan_results:
            plan_features = compliance._plan_logistic_features(plan_result, request)
            plan_onnx = run_scalar_capability(
                algorithm_id, feature_tensor(plan_features, feature_order)
            )
            plan_runtime = {**plan_onnx.runtime, "plan_id": plan_result.plan_id}
            if plan_onnx.value is None:
                plan_probability = compliance._logistic_probability(
                    plan_features, compliance.COMPLIANCE_LOGISTIC_WEIGHTS
                )
                plan_runtime["used"] = False
            else:
                plan_probability = float(plan_onnx.value)
                plan_runtime["used"] = True
            per_plan_runtimes.append(plan_runtime)
            per_plan_scores.append(
                {
                    "plan_id": plan_result.plan_id,
                    "risk_probability": round(plan_probability, 4),
                    "compliance_probability": round(1.0 - plan_probability, 4),
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


def _feature_order(algorithm_id: str, fallback: list[str]) -> list[str]:
    feature_order = load_capability_metadata(algorithm_id).get("feature_order")
    if isinstance(feature_order, list) and all(
        isinstance(item, str) for item in feature_order
    ):
        return feature_order
    return fallback


@lru_cache(maxsize=8)
def _load_session(model_path: str, providers: tuple[str, ...]):
    try:
        import onnxruntime as ort
    except ModuleNotFoundError as exc:
        raise RuntimeError("onnxruntime_not_installed") from exc
    return ort.InferenceSession(model_path, providers=list(providers))
