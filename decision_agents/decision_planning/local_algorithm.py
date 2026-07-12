"""Lightweight candidate-plan generation and recommendation algorithms."""

from __future__ import annotations

import os

from collections import defaultdict
from math import exp, tanh
from typing import Any

from decision_agents.common.algorithm_registry import (
    AlgorithmSpec,
    UnknownAlgorithmError,
    missing_required_fields,
    select_algorithm,
)
from decision_agents.common.onnx_adapter import OnnxAlgorithmSpec, run_onnx_or_fallback
from decision_agents.common.schemas import (
    AgentRequest,
    AgentResponse,
    CandidatePlan,
    Resource,
    RiskAssessment,
    ScheduledTask,
)
from decision_agents.knowledge.retrieval import retrieve_rag_result


MIN_CANDIDATE_PLANS = 3
DEFAULT_SCORING_WEIGHTS = {
    "coverage": 0.35,
    "risk_alignment": 0.30,
    "resource_efficiency": 0.20,
    "constraint_fit": 0.15,
}
PLANNING_LOGISTIC_WEIGHTS = {
    "intercept": -1.45,
    "coverage": 1.20,
    "risk_alignment": 1.05,
    "resource_efficiency": 0.60,
    "constraint_fit": 0.85,
    "authorization": 0.55,
    "lstm_trend": 0.75,
    "priority": 0.45,
    "objective_fit": 0.35,
}
LSTM_INPUT_WEIGHTS = {
    "input": (0.55, 0.35, 0.20, -0.10),
    "forget": (-0.20, 0.10, 0.15, 0.35),
    "output": (0.35, 0.30, 0.10, 0.20),
    "candidate": (0.70, 0.45, 0.25, 0.15),
}
LSTM_RECURRENT_WEIGHTS = {
    "input": 0.25,
    "forget": 0.40,
    "output": 0.30,
    "candidate": 0.55,
}
LSTM_BIASES = {
    "input": -0.10,
    "forget": 0.35,
    "output": 0.05,
    "candidate": -0.25,
}
MAX_LSTM_STEPS = 12


def _small_decision_planning(request: AgentRequest) -> dict:
    candidates = generate_candidate_plans(request)
    scored = score_candidate_plans(candidates, request, DEFAULT_SCORING_WEIGHTS)
    scored, rag_payload = enhance_plans_with_rag(scored, request)
    recommended = scored[0] if scored else None
    return {
        "candidate_plans": [plan.model_dump(mode="json") for plan in scored],
        "recommended_plan_id": recommended.id if recommended else None,
        "method": "template_generation_multi_factor_scoring",
        "scoring_weights": DEFAULT_SCORING_WEIGHTS,
        "weight_source": "default",
        "handoff_notes": [
            "Plans are simulation-only decision-support candidates.",
            "Compliance and authorization checks are required before any downstream handoff.",
        ],
        **rag_payload,
    }


def _medium_decision_planning(request: AgentRequest) -> dict:
    weights, weight_source = _scoring_weights_from_constraints(request)
    candidates = generate_candidate_plans(request)
    baseline_scored = score_candidate_plans(candidates, request, weights)
    target_trends = predict_target_trends(request)
    scored, plan_scores = score_candidate_plans_with_logistic(
        baseline_scored,
        request,
        target_trends,
    )
    scored, rag_payload = enhance_plans_with_rag(scored, request)
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
        "handoff_notes": [
            "Plans are simulation-only decision-support candidates.",
            "Compliance and authorization checks are required before any downstream handoff.",
        ],
        **rag_payload,
    }


def _onnx_decision_planning(request: AgentRequest) -> dict:
    spec = OnnxAlgorithmSpec(
        model_path=os.getenv("DECISION_PLANNING_ONNX_MODEL", "models/decision_planning.onnx"),
        input_names=("input",),
        output_names=(),
        preprocess_fn=lambda item: {"input": item.model_dump_json()},
        postprocess_fn=lambda outputs, item: {
            "onnx_outputs": [
                output.tolist() if hasattr(output, "tolist") else output
                for output in outputs
            ],
            "method": "onnx_decision_planning",
        },
        fallback_algorithm_id="decision_planning_logistic",
        fallback_run_fn=_medium_decision_planning,
        metadata={"category": "decision_planning"},
    )
    return run_onnx_or_fallback(request, spec)


ALGORITHMS = [
    AlgorithmSpec(
        algorithm_id="decision_planning_small",
        category="decision_planning",
        parameter_size="small",
        required_fields=("scheduled_tasks", "resources"),
        run_fn=_small_decision_planning,
    ),
    AlgorithmSpec(
        algorithm_id="decision_planning_logistic",
        category="decision_planning",
        parameter_size="medium",
        required_fields=("scheduled_tasks", "resources"),
        run_fn=_medium_decision_planning,
    ),
    AlgorithmSpec(
        algorithm_id="decision_planning_lstm",
        category="decision_planning",
        parameter_size="medium",
        required_fields=("scheduled_tasks", "resources"),
        run_fn=_medium_decision_planning,
    ),
    AlgorithmSpec(
        algorithm_id="decision_planning_medium",
        category="decision_planning",
        parameter_size="medium",
        required_fields=("scheduled_tasks", "resources"),
        run_fn=_medium_decision_planning,
    ),
    AlgorithmSpec(
        algorithm_id="decision_planning_onnx",
        category="decision_planning",
        parameter_size="large",
        required_fields=("scheduled_tasks", "resources"),
        run_fn=_onnx_decision_planning,
    ),
]


def run_decision_planning(request: AgentRequest) -> AgentResponse:
    try:
        algorithm = select_algorithm(request, ALGORITHMS)
    except UnknownAlgorithmError as exc:
        return AgentResponse(
            status="error",
            agent="decision_planning_agent",
            result={"available_algorithms": exc.available_algorithms},
            summary=str(exc),
            warnings=[f"unknown_algorithm:{exc.algorithm_id}"],
        )
    missing = missing_required_fields(request, algorithm.required_fields)
    if missing:
        return AgentResponse(
            status="input_required",
            agent="decision_planning_agent",
            selected_algorithms=[algorithm.algorithm_id],
            summary="Missing required fields for decision planning.",
            warnings=[f"missing:{field}" for field in missing],
        )
    result = algorithm.run_fn(request)
    plan_count = len(result.get("candidate_plans", []))
    recommended_plan_id = result.get("recommended_plan_id") or "none"
    selected_algorithms = [algorithm.algorithm_id]
    for stage in result.get("algorithm_stages", []):
        if stage not in selected_algorithms:
            selected_algorithms.append(stage)
    warnings = []
    onnx_info = result.get("onnx", {})
    if onnx_info.get("fallback"):
        fallback_algorithm_id = onnx_info.get("fallback_algorithm_id")
        if fallback_algorithm_id and fallback_algorithm_id not in selected_algorithms:
            selected_algorithms.append(fallback_algorithm_id)
        warnings.append(f"onnx_fallback:{onnx_info.get('reason', 'unavailable')}")
    return AgentResponse(
        agent="decision_planning_agent",
        selected_algorithms=selected_algorithms,
        result=result,
        rag_evidence=result.get("rag_evidence", []),
        summary=(
            f"Generated {plan_count} candidate plan(s); recommended "
            f"{recommended_plan_id} for simulation-only decision support."
        ),
        warnings=warnings,
    )


def generate_candidate_plans(request: AgentRequest) -> list[CandidatePlan]:
    provided = [plan.model_copy(deep=True) for plan in request.candidate_plans]
    generated = [
        _priority_monitoring_plan(request),
        _broad_surveillance_plan(request),
        _resource_sparing_plan(request),
    ]
    plans = [*provided, *generated]
    deduped: dict[str, CandidatePlan] = {}
    for plan in plans:
        deduped.setdefault(plan.id, plan)
    return list(deduped.values())[: max(len(deduped), MIN_CANDIDATE_PLANS)]


def score_candidate_plans(
    candidate_plans: list[CandidatePlan],
    request: AgentRequest,
    weights: dict[str, float] | None = None,
) -> list[CandidatePlan]:
    weights = weights or DEFAULT_SCORING_WEIGHTS
    task_targets = _task_targets(request.scheduled_tasks)
    risk_by_target = {risk.target_id: risk for risk in request.risk_assessments}
    available_resources = [resource for resource in request.resources if resource.status == "available"]
    total_available = max(len(available_resources), 1)

    scored = []
    for plan in candidate_plans:
        coverage = _coverage_score(plan, task_targets)
        risk_alignment = _risk_alignment_score(plan, risk_by_target)
        resource_efficiency = _resource_efficiency_score(plan, total_available)
        constraint_fit = _constraint_fit_score(plan, request.constraints)
        score = round(
            coverage * weights["coverage"]
            + risk_alignment * weights["risk_alignment"]
            + resource_efficiency * weights["resource_efficiency"]
            + constraint_fit * weights["constraint_fit"],
            2,
        )
        plan = plan.model_copy(
            update={
                "score": score,
                "status": "candidate",
                "rationale": (
                    f"coverage={coverage}, risk_alignment={risk_alignment}, "
                    f"resource_efficiency={resource_efficiency}, "
                    f"constraint_fit={constraint_fit}"
                ),
            }
        )
        scored.append(plan)

    scored.sort(key=lambda item: (-item.score, item.id))
    if scored:
        scored[0] = scored[0].model_copy(update={"status": "recommended"})
    return scored


def enhance_plans_with_rag(
    plans: list[CandidatePlan],
    request: AgentRequest,
) -> tuple[list[CandidatePlan], dict[str, Any]]:
    rag_result = retrieve_rag_result(
        _planning_rag_query(plans, request),
        purpose="planning",
        top_k=4,
    )
    evidence_summary = _evidence_summary(rag_result.evidence)
    if not evidence_summary:
        return plans, _rag_payload(rag_result)

    enhanced = []
    assumption = f"RAG evidence considered: {evidence_summary}"
    for plan in plans:
        assumptions = list(plan.assumptions)
        if assumption not in assumptions:
            assumptions.append(assumption)
        rationale = plan.rationale
        if evidence_summary and evidence_summary not in rationale:
            rationale = f"{rationale}; rag_evidence={evidence_summary}" if rationale else (
                f"rag_evidence={evidence_summary}"
            )
        enhanced.append(plan.model_copy(update={"assumptions": assumptions, "rationale": rationale}))
    return enhanced, _rag_payload(rag_result)


def score_candidate_plans_with_logistic(
    candidate_plans: list[CandidatePlan],
    request: AgentRequest,
    target_trends: dict[str, dict[str, Any]],
) -> tuple[list[CandidatePlan], list[dict[str, Any]]]:
    task_targets = _task_targets(request.scheduled_tasks)
    risk_by_target = {risk.target_id: risk for risk in request.risk_assessments}
    available_resources = [
        resource for resource in request.resources if resource.status == "available"
    ]
    total_available = max(len(available_resources), 1)
    plan_scores = []
    scored = []

    for plan in candidate_plans:
        features = _planning_logistic_features(
            plan,
            request,
            task_targets,
            risk_by_target,
            total_available,
            target_trends,
        )
        probability = _logistic_probability(features, PLANNING_LOGISTIC_WEIGHTS)
        final_score = round(0.7 * probability * 100.0 + 0.3 * plan.score, 2)
        plan = plan.model_copy(
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
        scored.append(plan)
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
    return scored, ordered_scores


def predict_target_trends(request: AgentRequest) -> dict[str, dict[str, Any]]:
    trend_by_target = {}
    requested_targets = set(_task_targets(request.scheduled_tasks))
    requested_targets.update(risk.target_id for risk in request.risk_assessments)
    histories = {history.target_id: history for history in request.target_histories}

    for target_id in sorted(requested_targets | set(histories)):
        history = histories.get(target_id)
        score = _lstm_trend_score(history.steps if history else [])
        trend_by_target[target_id] = {
            "target_id": target_id,
            "trend": _trend_label(score),
            "trend_score": score,
        }
    return trend_by_target


def _planning_logistic_features(
    plan: CandidatePlan,
    request: AgentRequest,
    task_targets: list[str],
    risk_by_target: dict[str, RiskAssessment],
    total_available: int,
    target_trends: dict[str, dict[str, Any]],
) -> dict[str, float]:
    return {
        "coverage": _coverage_score(plan, task_targets) / 100.0,
        "risk_alignment": _risk_alignment_score(plan, risk_by_target) / 100.0,
        "resource_efficiency": _resource_efficiency_score(plan, total_available) / 100.0,
        "constraint_fit": _constraint_fit_score(plan, request.constraints) / 100.0,
        "authorization": _authorization_feature(request),
        "lstm_trend": _plan_trend_score(plan, target_trends),
        "priority": _priority_feature(plan, request.scheduled_tasks),
        "objective_fit": _objective_fit_score(plan, request.planning_objectives),
        "baseline_score": plan.score,
    }


def _logistic_probability(features: dict[str, float], weights: dict[str, float]) -> float:
    z = weights.get("intercept", 0.0)
    for key, weight in weights.items():
        if key == "intercept":
            continue
        z += weight * features.get(key, 0.0)
    return round(_sigmoid(z), 6)


def _lstm_trend_score(steps) -> float:
    recent_steps = list(steps)[-MAX_LSTM_STEPS:]
    if not recent_steps:
        return 0.5

    hidden = 0.0
    cell = 0.0
    first = recent_steps[0]
    last = recent_steps[-1]
    for step in recent_steps:
        vector = (
            step.risk_score / 100.0,
            step.probability,
            1.0 / max(float(step.priority), 1.0),
            step.resource_pressure,
        )
        input_gate = _sigmoid(_lstm_gate("input", vector, hidden))
        forget_gate = _sigmoid(_lstm_gate("forget", vector, hidden))
        output_gate = _sigmoid(_lstm_gate("output", vector, hidden))
        candidate = tanh(_lstm_gate("candidate", vector, hidden))
        cell = forget_gate * cell + input_gate * candidate
        hidden = output_gate * tanh(cell)

    risk_delta = (last.risk_score - first.risk_score) / 100.0
    probability_delta = last.probability - first.probability
    score = _sigmoid(2.2 * hidden + 1.1 * risk_delta + 0.8 * probability_delta)
    return round(score, 4)


def _lstm_gate(name: str, vector: tuple[float, float, float, float], hidden: float) -> float:
    weights = LSTM_INPUT_WEIGHTS[name]
    return (
        LSTM_BIASES[name]
        + sum(weight * value for weight, value in zip(weights, vector))
        + LSTM_RECURRENT_WEIGHTS[name] * hidden
    )


def _trend_label(score: float) -> str:
    if score >= 0.6:
        return "rising"
    if score <= 0.4:
        return "falling"
    return "stable"


def _plan_trend_score(
    plan: CandidatePlan,
    target_trends: dict[str, dict[str, Any]],
) -> float:
    scores = [
        target_trends[target_id]["trend_score"]
        for target_id in plan.target_ids
        if target_id in target_trends
    ]
    if not scores:
        return 0.5
    return round(sum(scores) / len(scores), 4)


def _authorization_feature(request: AgentRequest) -> float:
    return {
        "approved": 1.0,
        "pending_review": 0.55,
        "unknown": 0.45,
        "expired": 0.15,
        "denied": 0.0,
    }.get(request.authorization.status, 0.45)


def _priority_feature(plan: CandidatePlan, tasks: list[ScheduledTask]) -> float:
    priorities = [
        task.priority
        for task in tasks
        if task.target_id and task.target_id in set(plan.target_ids)
    ]
    if not priorities:
        return 0.5
    return round(sum(1.0 / max(priority, 1) for priority in priorities) / len(priorities), 4)


def _objective_fit_score(plan: CandidatePlan, planning_objectives: list[str]) -> float:
    if not planning_objectives:
        return 0.5
    objectives = " ".join(planning_objectives).lower()
    matches = 0
    if any(term in objectives for term in ("coverage", "coverage_first", "broad")):
        matches += 1 if "BROAD" in plan.id else 0
    if any(term in objectives for term in ("risk", "risk_first", "priority")):
        matches += 1 if "PRIORITY" in plan.id else 0
    if any(term in objectives for term in ("resource", "resource_sparing", "conserve")):
        matches += 1 if "RESOURCE-SPARING" in plan.id else 0
    if matches:
        return 1.0
    return 0.45


def _sigmoid(value: float) -> float:
    if value >= 0:
        z = exp(-value)
        return 1.0 / (1.0 + z)
    z = exp(value)
    return z / (1.0 + z)


def _planning_rag_query(plans: list[CandidatePlan], request: AgentRequest) -> str:
    return " ".join(
        [
            "planning constraints authorization compliance decision-support",
            " ".join(request.planning_objectives),
            " ".join(_constraint_text(constraint) for constraint in request.constraints),
            request.authorization.status,
            " ".join(request.authorization.scope),
            " ".join(task.task_type for task in request.scheduled_tasks),
            " ".join(plan.name for plan in plans),
            " ".join(" ".join(plan.actions) for plan in plans),
        ]
    )


def _rag_payload(rag_result) -> dict[str, Any]:
    return {
        "rag_evidence": [
            evidence.model_dump(mode="json")
            for evidence in rag_result.evidence
        ],
        "rag_answer": rag_result.answer,
        "rag_model_profile": rag_result.model_profile,
        "rag_warnings": rag_result.warnings,
        "rag_query": rag_result.rewritten_query,
        "rag_keywords": rag_result.keywords,
    }


def _evidence_summary(evidence) -> str:
    return "; ".join(
        f"{item.rule_id} {item.title}"
        for item in evidence[:3]
    )


def _scoring_weights_from_constraints(
    request: AgentRequest,
) -> tuple[dict[str, float], str]:
    for constraint in request.constraints:
        if not isinstance(constraint, dict):
            continue
        if constraint.get("type") != "scoring_weights":
            continue
        candidate = {
            key: float(constraint.get(key, DEFAULT_SCORING_WEIGHTS[key]))
            for key in DEFAULT_SCORING_WEIGHTS
        }
        total = sum(max(value, 0.0) for value in candidate.values())
        if total <= 0:
            return DEFAULT_SCORING_WEIGHTS, "default_invalid_config"
        normalized = {
            key: round(max(value, 0.0) / total, 4)
            for key, value in candidate.items()
        }
        return normalized, "constraints.scoring_weights"
    return DEFAULT_SCORING_WEIGHTS, "default"


def _priority_monitoring_plan(request: AgentRequest) -> CandidatePlan:
    sorted_tasks = _tasks_by_risk_and_priority(request.scheduled_tasks, request.risk_assessments)
    targets = [task.target_id for task in sorted_tasks if task.target_id]
    top_targets = targets[: max(1, min(2, len(targets)))]
    resources = _assign_resources(sorted_tasks[: len(top_targets)], request.resources)
    return CandidatePlan(
        id="PLAN-PRIORITY-MONITOR",
        name="Priority monitoring and reassessment",
        target_ids=top_targets,
        assigned_resources=resources,
        actions=[
            "focus available resources on highest-priority targets",
            "increase observation cadence for selected targets",
            "reassess risk ranking after the next review window",
        ],
        expected_effects=[
            "improves confidence on the highest-risk items",
            "keeps the plan in decision-support mode",
        ],
        assumptions=["highest-risk targets should receive first attention"],
        risk_notes=_risk_notes(top_targets, request.risk_assessments),
    )


def _broad_surveillance_plan(request: AgentRequest) -> CandidatePlan:
    targets = [target for target in _task_targets(request.scheduled_tasks)]
    resources = [resource.id for resource in request.resources if resource.status == "available"]
    return CandidatePlan(
        id="PLAN-BROAD-SURVEILLANCE",
        name="Broad surveillance coverage",
        target_ids=targets,
        assigned_resources=resources,
        actions=[
            "spread available resources across all scheduled targets",
            "maintain broad-area monitoring continuity",
            "defer prioritization changes until updated risk evidence arrives",
        ],
        expected_effects=[
            "maximizes target coverage",
            "reduces chance of losing lower-priority targets",
        ],
        assumptions=["coverage is preferred over concentrated monitoring"],
        risk_notes=_risk_notes(targets, request.risk_assessments),
    )


def _resource_sparing_plan(request: AgentRequest) -> CandidatePlan:
    sorted_tasks = _tasks_by_risk_and_priority(request.scheduled_tasks, request.risk_assessments)
    target = sorted_tasks[0].target_id if sorted_tasks and sorted_tasks[0].target_id else None
    resources = _assign_resources(sorted_tasks[:1], request.resources)[:1]
    return CandidatePlan(
        id="PLAN-RESOURCE-SPARING",
        name="Resource-sparing watch",
        target_ids=[target] if target else [],
        assigned_resources=resources,
        actions=[
            "monitor only the top-priority target with minimum viable resources",
            "hold remaining resources for follow-up tasking",
            "escalate to broader coverage if risk increases",
        ],
        expected_effects=[
            "preserves resource availability",
            "accepts reduced coverage for lower-priority targets",
        ],
        assumptions=["resource conservation is valuable in the current window"],
        risk_notes=_risk_notes([target] if target else [], request.risk_assessments),
    )


def _assign_resources(tasks: list[ScheduledTask], resources: list[Resource]) -> list[str]:
    available = [resource for resource in resources if resource.status == "available"]
    used: set[str] = set()
    assigned = []
    by_type: dict[str, list[Resource]] = defaultdict(list)
    for resource in available:
        by_type[resource.type].append(resource)

    for task in tasks:
        preferred = task.required_resource_types or []
        selected = None
        for resource_type in preferred:
            selected = _first_unused(by_type.get(resource_type, []), used)
            if selected:
                break
        if selected is None:
            selected = _first_unused(available, used)
        if selected:
            used.add(selected.id)
            assigned.append(selected.id)
    return assigned


def _first_unused(resources: list[Resource], used: set[str]) -> Resource | None:
    for resource in resources:
        if resource.id not in used:
            return resource
    return None


def _task_targets(tasks: list[ScheduledTask]) -> list[str]:
    return [task.target_id for task in tasks if task.target_id]


def _tasks_by_risk_and_priority(
    tasks: list[ScheduledTask],
    risks: list[RiskAssessment],
) -> list[ScheduledTask]:
    risk_score = {risk.target_id: risk.threat_score for risk in risks}
    return sorted(
        tasks,
        key=lambda task: (
            -risk_score.get(task.target_id or "", 0.0),
            task.priority,
            task.id,
        ),
    )


def _risk_notes(target_ids: list[str], risks: list[RiskAssessment]) -> list[str]:
    by_target = {risk.target_id: risk for risk in risks}
    notes = []
    for target_id in target_ids:
        risk = by_target.get(target_id)
        if risk:
            notes.append(
                f"{target_id}: {risk.risk} risk, score={risk.threat_score}, "
                f"priority={risk.priority}"
            )
    return notes


def _coverage_score(plan: CandidatePlan, task_targets: list[str]) -> float:
    if not task_targets:
        return 50.0
    covered = len(set(plan.target_ids) & set(task_targets))
    return round(covered / len(set(task_targets)) * 100.0, 2)


def _risk_alignment_score(
    plan: CandidatePlan,
    risk_by_target: dict[str, RiskAssessment],
) -> float:
    if not risk_by_target:
        return 60.0
    total = sum(risk.threat_score for risk in risk_by_target.values())
    if total <= 0:
        return 50.0
    covered = sum(
        risk_by_target[target_id].threat_score
        for target_id in set(plan.target_ids)
        if target_id in risk_by_target
    )
    return round(covered / total * 100.0, 2)


def _resource_efficiency_score(plan: CandidatePlan, total_available: int) -> float:
    if not plan.assigned_resources:
        return 25.0
    ratio = len(set(plan.assigned_resources)) / total_available
    if ratio <= 0.5:
        return 100.0
    if ratio <= 0.8:
        return 75.0
    return 55.0


def _constraint_fit_score(
    plan: CandidatePlan,
    constraints: list[dict[str, Any] | str],
) -> float:
    if not constraints:
        return 85.0
    text = " ".join(_constraint_text(constraint) for constraint in constraints).lower()
    score = 85.0
    if "simulation-only" in text or "decision-support" in text:
        score += 10.0
    if "resource" in text and "conserve" in text and "RESOURCE-SPARING" in plan.id:
        score += 5.0
    if "broad" in text and "BROAD" in plan.id:
        score += 5.0
    if "restricted" in text and len(plan.target_ids) > 1:
        score -= 15.0
    return round(max(0.0, min(score, 100.0)), 2)


def _constraint_text(constraint: dict[str, Any] | str) -> str:
    if isinstance(constraint, str):
        return constraint
    return " ".join(str(value) for value in constraint.values())
