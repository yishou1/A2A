"""Lightweight compliance, rule, and authorization checks."""

from __future__ import annotations

import os

from typing import Any

from decision_agents.algorithms.onnx_adapter import OnnxAlgorithmSpec, run_onnx_or_fallback
from decision_agents.algorithms.registry import (
    AlgorithmSpec,
    UnknownAlgorithmError,
    missing_required_fields,
    select_algorithm,
)
from decision_agents.knowledge.retrieval import retrieve_evidence
from decision_agents.knowledge.rule_tables import (
    BLOCKING_ACTION_TERMS,
    REVIEW_TERMS,
    load_rule_table,
)
from decision_agents.schemas import (
    AgentRequest,
    AgentResponse,
    CandidatePlan,
    ComplianceCheckResult,
    PlanComplianceResult,
    RuleEvidence,
    RuleViolation,
)


def _small_compliance_authorization(request: AgentRequest) -> dict:
    result = evaluate_compliance(request)
    return {
        **result.model_dump(mode="json"),
        "method": "rule_keyword_authorization_check",
    }


def _medium_compliance_authorization(request: AgentRequest) -> dict:
    result = evaluate_compliance(request, use_rule_table=True)
    return {
        **result.model_dump(mode="json"),
        "method": "rule_table_dsl_authorization_check",
        "rule_table_version": "law-of-war-demo-v1",
    }


def _onnx_compliance_authorization(request: AgentRequest) -> dict:
    spec = OnnxAlgorithmSpec(
        model_path=os.getenv(
            "COMPLIANCE_AUTHORIZATION_ONNX_MODEL",
            "models/compliance_authorization.onnx",
        ),
        input_names=("input",),
        output_names=(),
        preprocess_fn=lambda item: {"input": item.model_dump_json()},
        postprocess_fn=lambda outputs, item: {
            "onnx_outputs": [
                output.tolist() if hasattr(output, "tolist") else output
                for output in outputs
            ],
            "method": "onnx_compliance_authorization",
        },
        fallback_algorithm_id="compliance_authorization_medium",
        fallback_run_fn=_medium_compliance_authorization,
        metadata={"category": "compliance_authorization"},
    )
    return run_onnx_or_fallback(request, spec)


ALGORITHMS = [
    AlgorithmSpec(
        algorithm_id="compliance_authorization_small",
        category="compliance_authorization",
        parameter_size="small",
        required_fields=("candidate_plans", "authorization"),
        run_fn=_small_compliance_authorization,
    ),
    AlgorithmSpec(
        algorithm_id="compliance_authorization_medium",
        category="compliance_authorization",
        parameter_size="medium",
        required_fields=("candidate_plans", "authorization"),
        run_fn=_medium_compliance_authorization,
    ),
    AlgorithmSpec(
        algorithm_id="compliance_authorization_onnx",
        category="compliance_authorization",
        parameter_size="large",
        required_fields=("candidate_plans", "authorization"),
        run_fn=_onnx_compliance_authorization,
    ),
]


def run_compliance_authorization(request: AgentRequest) -> AgentResponse:
    try:
        algorithm = select_algorithm(request, ALGORITHMS)
    except UnknownAlgorithmError as exc:
        return AgentResponse(
            status="error",
            agent="compliance_authorization_agent",
            result={"available_algorithms": exc.available_algorithms},
            summary=str(exc),
            warnings=[f"unknown_algorithm:{exc.algorithm_id}"],
        )
    missing = missing_required_fields(request, algorithm.required_fields)
    if missing:
        return AgentResponse(
            status="input_required",
            agent="compliance_authorization_agent",
            selected_algorithms=[algorithm.algorithm_id],
            summary="Missing required fields for compliance and authorization checks.",
            warnings=[f"missing:{field}" for field in missing],
        )
    result = algorithm.run_fn(request)
    decision = result.get("decision", "unknown")
    selected_algorithms = [algorithm.algorithm_id]
    warnings = []
    onnx_info = result.get("onnx", {})
    if onnx_info.get("fallback"):
        fallback_algorithm_id = onnx_info.get("fallback_algorithm_id")
        if fallback_algorithm_id:
            selected_algorithms.append(fallback_algorithm_id)
        warnings.append(f"onnx_fallback:{onnx_info.get('reason', 'unavailable')}")
    return AgentResponse(
        agent="compliance_authorization_agent",
        selected_algorithms=selected_algorithms,
        result=result,
        rag_evidence=result.get("evidence", []),
        summary=(
            f"Compliance decision is {decision}; "
            f"human_approval_required={result.get('requires_human_approval')}."
        ),
        warnings=warnings,
    )


def evaluate_compliance(
    request: AgentRequest,
    *,
    use_rule_table: bool = False,
) -> ComplianceCheckResult:
    selected_plan = _select_plan(request.candidate_plans)
    per_plan_results = [
        _evaluate_plan(plan, request, use_rule_table=use_rule_table)
        for plan in request.candidate_plans
    ]
    selected_result = next(
        result for result in per_plan_results if result.plan_id == selected_plan.id
    )

    return ComplianceCheckResult(
        **selected_result.model_dump(mode="python"),
        selected_plan_id=selected_plan.id,
        authorization_status=request.authorization,
        per_plan_results=per_plan_results,
    )


def _evaluate_plan(
    plan: CandidatePlan,
    request: AgentRequest,
    *,
    use_rule_table: bool = False,
) -> PlanComplianceResult:
    violations: list[RuleViolation] = []
    blocked_items: list[str] = []
    suggestions: list[str] = []

    if use_rule_table:
        violations.extend(_check_rule_table(plan, request, include_law_of_war=True))
    else:
        violations.extend(_check_plan_actions(plan))
        violations.extend(_check_constraints(plan, request.constraints))
        violations.extend(_check_authorization(plan, request))

    requires_review = any(
        violation.severity in {"warning", "blocking"} for violation in violations
    )
    has_blocking = any(violation.severity == "blocking" for violation in violations)
    if has_blocking:
        decision = "blocked"
        approved = False
    elif requires_review:
        decision = "review_required"
        approved = False
    else:
        decision = "approved"
        approved = True

    evidence = _collect_evidence(plan, request, violations)
    violations = _bind_evidence_to_violations(violations, evidence)
    for violation in violations:
        if violation.severity == "blocking":
            blocked_items.append(violation.item)
        if violation.suggestion:
            suggestions.append(violation.suggestion)

    return PlanComplianceResult(
        plan_id=plan.id,
        plan_status=plan.status,
        decision=decision,
        approved_for_demo_handoff=approved,
        requires_human_approval=requires_review,
        violations=violations,
        blocked_items=sorted(set(blocked_items)),
        evidence=evidence,
        adjustment_suggestions=sorted(set(suggestions)),
    )


def _check_rule_table(
    plan: CandidatePlan,
    request: AgentRequest,
    *,
    include_law_of_war: bool = False,
) -> list[RuleViolation]:
    violations = []
    context = _rule_context(plan, request)
    for rule in load_rule_table(include_law_of_war=include_law_of_war):
        if not _rule_matches(rule["condition"], context):
            continue
        item = plan.id
        if rule["rule_id"] == "RULE-BLOCK-001":
            item = _first_matching_action(plan, BLOCKING_ACTION_TERMS) or plan.id
        violations.append(
            RuleViolation(
                rule_id=rule["rule_id"],
                severity=rule["severity"],
                item=item,
                message=rule["message"],
                suggestion=rule["suggestion"],
            )
        )
    violations.extend(_approved_scope_violations(plan, request))
    return violations


def _rule_context(plan: CandidatePlan, request: AgentRequest) -> dict[str, Any]:
    combined = " ".join(
        [
            plan.name,
            *plan.actions,
            *plan.expected_effects,
            *plan.risk_notes,
            *[_constraint_text(constraint) for constraint in request.constraints],
        ]
    ).lower()
    risk_notes = " ".join(plan.risk_notes).lower()
    target_or_effect = " ".join(
        [
            plan.name,
            " ".join(plan.target_ids),
            *plan.actions,
            *plan.expected_effects,
            *plan.risk_notes,
        ]
    ).lower()
    return {
        "actions": [action.lower() for action in plan.actions],
        "combined": combined,
        "risk_notes": risk_notes,
        "target_or_effect": target_or_effect,
        "authorization_status": request.authorization.status,
        "authorization_scope": [item.lower() for item in request.authorization.scope],
    }


def _rule_matches(condition: dict[str, Any], context: dict[str, Any]) -> bool:
    if "authorization_status_in" in condition:
        return context["authorization_status"] in condition["authorization_status_in"]
    if "requires_any_scope" in condition:
        scopes = " ".join(context.get("authorization_scope", []))
        return not any(term in scopes for term in condition["requires_any_scope"])
    if "risk_note_contains_any" in condition:
        risk_notes = context.get("risk_notes", "")
        return any(term in risk_notes for term in condition["risk_note_contains_any"])
    if "target_or_effect_contains_any" in condition:
        target_or_effect = context.get("target_or_effect", "")
        terms = condition["target_or_effect_contains_any"]
        if not any(term in target_or_effect for term in terms):
            return False
        if "missing_all" in condition:
            return all(term not in target_or_effect for term in condition["missing_all"])
        return True

    field_value = context.get(condition.get("field"), "")
    if isinstance(field_value, list):
        field_text = " ".join(field_value)
    else:
        field_text = str(field_value)
    if "contains_any" in condition:
        return any(term in field_text for term in condition["contains_any"])
    if "missing_all" in condition:
        return all(term not in field_text for term in condition["missing_all"])
    return False


def _first_matching_action(
    plan: CandidatePlan,
    terms: tuple[str, ...],
) -> str | None:
    for action in plan.actions:
        text = action.lower()
        if any(term in text for term in terms):
            return f"{plan.id}:{action}"
    return None


def _approved_scope_violations(
    plan: CandidatePlan,
    request: AgentRequest,
) -> list[RuleViolation]:
    authorization = request.authorization
    if authorization.status != "approved":
        return []
    if authorization.approved_plan_ids and plan.id not in authorization.approved_plan_ids:
        return [
            RuleViolation(
                rule_id="AUTH-STATE-APPROVED",
                severity="warning",
                item=plan.id,
                message="Authorization is approved but does not explicitly include this plan.",
                suggestion="Confirm approval scope or add this plan to approved_plan_ids.",
            )
        ]
    if authorization.scope and not _plan_within_scope(plan, authorization.scope):
        return [
            RuleViolation(
                rule_id="AUTH-STATE-APPROVED",
                severity="warning",
                item=plan.id,
                message="Authorization scope does not clearly cover this plan.",
                suggestion="Update scope to include simulation-only decision-support handoff.",
            )
        ]
    return []


def _select_plan(plans: list[CandidatePlan]) -> CandidatePlan:
    recommended = [plan for plan in plans if plan.status == "recommended"]
    if recommended:
        return sorted(recommended, key=lambda item: (-item.score, item.id))[0]
    return sorted(plans, key=lambda item: (-item.score, item.id))[0]


def _check_plan_actions(plan: CandidatePlan) -> list[RuleViolation]:
    violations = []
    for action in plan.actions:
        text = action.lower()
        if any(term in text for term in BLOCKING_ACTION_TERMS):
            violations.append(
                RuleViolation(
                    rule_id="RULE-BLOCK-001",
                    severity="blocking",
                    item=f"{plan.id}:{action}",
                    message="Plan contains direct execution wording blocked in this demo.",
                    suggestion=(
                        "Rewrite the action as monitoring, reassessment, reporting, "
                        "or decision-support handoff."
                    ),
                )
            )
    return violations


def _check_constraints(
    plan: CandidatePlan,
    constraints: list[dict[str, Any] | str],
) -> list[RuleViolation]:
    combined = " ".join(
        [
            plan.name,
            *plan.actions,
            *plan.expected_effects,
            *plan.risk_notes,
            *[_constraint_text(constraint) for constraint in constraints],
        ]
    ).lower()
    violations = []
    if any(term in combined for term in REVIEW_TERMS):
        violations.append(
            RuleViolation(
                rule_id="RULE-CONSTRAINT-001",
                severity="warning",
                item=plan.id,
                message="Plan or constraints mention restricted/high-risk review terms.",
                suggestion="Require human review and document the boundary or risk concern.",
            )
        )
    if "simulation-only" not in combined and "decision-support" not in combined:
        violations.append(
            RuleViolation(
                rule_id="RULE-SIM-001",
                severity="warning",
                item=plan.id,
                message="Plan does not explicitly preserve simulation-only decision-support framing.",
                suggestion="Add simulation-only and decision-support framing before handoff.",
            )
        )
    return violations


def _check_authorization(
    plan: CandidatePlan,
    request: AgentRequest,
) -> list[RuleViolation]:
    authorization = request.authorization
    if authorization.status == "approved":
        if authorization.approved_plan_ids and plan.id not in authorization.approved_plan_ids:
            return [
                RuleViolation(
                    rule_id="AUTH-STATE-APPROVED",
                    severity="warning",
                    item=plan.id,
                    message="Authorization is approved but does not explicitly include this plan.",
                    suggestion="Confirm approval scope or add this plan to approved_plan_ids.",
                )
            ]
        if authorization.scope and not _plan_within_scope(plan, authorization.scope):
            return [
                RuleViolation(
                    rule_id="AUTH-STATE-APPROVED",
                    severity="warning",
                    item=plan.id,
                    message="Authorization scope does not clearly cover this plan.",
                    suggestion="Update scope to include simulation-only decision-support handoff.",
                )
            ]
        return []

    if authorization.status == "pending_review":
        return [
            RuleViolation(
                rule_id="AUTH-STATE-PENDING",
                severity="warning",
                item=plan.id,
                message="Authorization is pending review.",
                suggestion="Obtain human approval before marking the plan approved.",
            )
        ]
    if authorization.status == "denied":
        return [
            RuleViolation(
                rule_id="AUTH-STATE-DENIED",
                severity="blocking",
                item=plan.id,
                message="Authorization is denied for this handoff.",
                suggestion="Revise the plan or resubmit it for review.",
            )
        ]
    if authorization.status == "expired":
        return [
            RuleViolation(
                rule_id="AUTH-STATE-EXPIRED",
                severity="blocking",
                item=plan.id,
                message="Authorization has expired.",
                suggestion="Renew authorization before continuing.",
            )
        ]
    return [
        RuleViolation(
            rule_id="AUTH-STATE-PENDING",
            severity="warning",
            item=plan.id,
            message="Authorization status is unknown.",
            suggestion="Clarify authorization status before handoff.",
        )
    ]


def _collect_evidence(
    plan: CandidatePlan,
    request: AgentRequest,
    violations: list[RuleViolation],
) -> list[RuleEvidence]:
    query_parts = [
        request.authorization.status,
        " ".join(request.authorization.scope),
        plan.name,
        " ".join(plan.actions),
        " ".join(_constraint_text(constraint) for constraint in request.constraints),
        " ".join(violation.rule_id for violation in violations),
        " ".join(violation.message for violation in violations),
    ]
    evidence = retrieve_evidence(" ".join(query_parts), top_k=6)
    by_rule: dict[str, RuleEvidence] = {}
    for item in evidence:
        by_rule.setdefault(item.rule_id, item)
    return list(by_rule.values())


def _bind_evidence_to_violations(
    violations: list[RuleViolation],
    evidence: list[RuleEvidence],
) -> list[RuleViolation]:
    evidence_ids = {item.rule_id for item in evidence}
    bound = []
    for violation in violations:
        linked = [violation.rule_id] if violation.rule_id in evidence_ids else []
        bound.append(violation.model_copy(update={"evidence_rule_ids": linked}))
    return bound


def _plan_within_scope(plan: CandidatePlan, scope: list[str]) -> bool:
    scope_text = " ".join(scope).lower()
    plan_text = " ".join(
        [plan.name, *plan.actions, *plan.expected_effects, *plan.assumptions]
    ).lower()
    if "simulation-only" in scope_text and (
        "simulation" in plan_text or "decision-support" in plan_text
    ):
        return True
    if "decision-support" in scope_text and "decision-support" in plan_text:
        return True
    return False


def _constraint_text(constraint: dict[str, Any] | str) -> str:
    if isinstance(constraint, str):
        return constraint
    return " ".join(str(value) for value in constraint.values())
