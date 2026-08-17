"""Rule table definitions used by compliance authorization algorithms."""

from __future__ import annotations

from copy import deepcopy
from typing import Any

from decision_agents.common.schemas import AgentRequest, CandidatePlan


BLOCKING_ACTION_TERMS = (
    "execute",
    "execution",
    "strike",
    "fire",
    "launch",
    "engage",
    "irreversible",
)
REVIEW_TERMS = ("restricted", "boundary", "high risk", "high-risk")


DEMO_RULE_TABLE: list[dict[str, Any]] = [
    {
        "rule_id": "RULE-BLOCK-001",
        "severity": "blocking",
        "condition": {
            "field": "actions",
            "contains_any": list(BLOCKING_ACTION_TERMS),
        },
        "message": "Plan contains direct execution wording blocked in this demo.",
        "suggestion": (
            "Rewrite the action as monitoring, reassessment, reporting, "
            "or decision-support handoff."
        ),
    },
    {
        "rule_id": "RULE-CONSTRAINT-001",
        "severity": "warning",
        "condition": {
            "field": "combined",
            "contains_any": list(REVIEW_TERMS),
        },
        "message": "Plan or constraints mention restricted/high-risk review terms.",
        "suggestion": "Require human review and document the boundary or risk concern.",
    },
    {
        "rule_id": "RULE-SIM-001",
        "severity": "warning",
        "condition": {
            "field": "combined",
            "missing_all": ["simulation-only", "decision-support"],
        },
        "message": "Plan does not explicitly preserve simulation-only decision-support framing.",
        "suggestion": "Add simulation-only and decision-support framing before handoff.",
    },
    {
        "rule_id": "AUTH-STATE-PENDING",
        "severity": "warning",
        "condition": {"authorization_status_in": ["pending_review", "unknown"]},
        "message": "Authorization is pending, unknown, or requires clarification.",
        "suggestion": "Obtain human approval before marking the plan approved.",
    },
    {
        "rule_id": "AUTH-STATE-DENIED",
        "severity": "blocking",
        "condition": {"authorization_status_in": ["denied"]},
        "message": "Authorization is denied for this handoff.",
        "suggestion": "Revise the plan or resubmit it for review.",
    },
    {
        "rule_id": "AUTH-STATE-EXPIRED",
        "severity": "blocking",
        "condition": {"authorization_status_in": ["expired"]},
        "message": "Authorization has expired.",
        "suggestion": "Renew authorization before continuing.",
    },
]


LAW_OF_WAR_RULE_TABLE: list[dict[str, Any]] = [
    {
        "rule_id": "LOW-DISTINCTION-001",
        "severity": "blocking",
        "condition": {
            "target_or_effect_contains_any": [
                "civilian",
                "civilian object",
                "hospital",
                "school",
                "medical",
                "residential",
            ],
            "missing_all": ["monitor", "observe", "avoid", "protect", "no strike"],
        },
        "message": "Plan touches civilian or protected objects without protective framing.",
        "suggestion": "Reframe as observation, avoidance, or human legal review of protected status.",
    },
    {
        "rule_id": "LOW-PROPORTIONALITY-001",
        "severity": "warning",
        "condition": {
            "risk_note_contains_any": [
                "collateral",
                "civilian harm",
                "disproportionate",
                "high collateral",
            ],
        },
        "message": "Plan indicates proportionality or collateral-harm concerns.",
        "suggestion": "Require human legal review and document expected civilian-risk mitigation.",
    },
    {
        "rule_id": "LOW-NECESSITY-001",
        "severity": "warning",
        "condition": {
            "field": "combined",
            "contains_any": ["military necessity", "necessity unclear", "unclear necessity"],
        },
        "message": "Military necessity is unclear or requires documentation.",
        "suggestion": "Document the concrete decision-support purpose and alternatives.",
    },
    {
        "rule_id": "LOW-HUMANITY-001",
        "severity": "blocking",
        "condition": {
            "field": "combined",
            "contains_any": ["unnecessary suffering", "inhumane", "punitive harm"],
        },
        "message": "Plan language raises humanity or unnecessary suffering concerns.",
        "suggestion": "Remove punitive or inhumane effects and route to legal review.",
    },
    {
        "rule_id": "LOW-PRECAUTIONS-001",
        "severity": "warning",
        "condition": {
            "target_or_effect_contains_any": ["civilian", "protected", "urban", "residential"],
            "missing_all": ["precaution", "verify", "avoid", "minimize", "review"],
        },
        "message": "Plan lacks explicit precautions for protected or civilian-sensitive context.",
        "suggestion": "Add verification, avoidance, or minimization precautions before handoff.",
    },
    {
        "rule_id": "LOW-HORS-DE-COMBAT-001",
        "severity": "blocking",
        "condition": {
            "field": "combined",
            "contains_any": ["surrendered", "detained", "wounded combatant", "hors de combat"],
        },
        "message": "Plan references persons hors de combat or otherwise protected from attack.",
        "suggestion": "Remove engagement wording and route to protective handling review.",
    },
    {
        "rule_id": "LOW-AUTH-SCOPE-001",
        "severity": "warning",
        "condition": {
            "requires_any_scope": ["law-of-war", "roe", "legal-review", "human-authorization"]
        },
        "message": "Authorization scope does not explicitly include law-of-war or ROE review.",
        "suggestion": "Add law-of-war/ROE review to the authorization scope.",
    },
]


def load_rule_table(include_law_of_war: bool = False) -> list[dict[str, Any]]:
    """Return a copy of the demo rule table, optionally with Law of War rules."""
    table = deepcopy(DEMO_RULE_TABLE)
    if include_law_of_war:
        table.extend(deepcopy(LAW_OF_WAR_RULE_TABLE))
    return table


def match_rule_table(
    plan: CandidatePlan,
    request: AgentRequest,
    *,
    include_law_of_war: bool = False,
) -> list[dict[str, Any]]:
    """Return authoritative structured rules matched by a candidate plan."""
    context = _rule_context(plan, request)
    return [
        rule
        for rule in load_rule_table(include_law_of_war=include_law_of_war)
        if _rule_matches(rule["condition"], context)
    ]


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
    return {
        "actions": [action.lower() for action in plan.actions],
        "combined": combined,
        "risk_notes": " ".join(plan.risk_notes).lower(),
        "target_or_effect": " ".join(
            [
                plan.name,
                " ".join(plan.target_ids),
                *plan.actions,
                *plan.expected_effects,
                *plan.risk_notes,
            ]
        ).lower(),
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
        if not any(term in target_or_effect for term in condition["target_or_effect_contains_any"]):
            return False
        return not condition.get("missing_all") or all(
            term not in target_or_effect for term in condition["missing_all"]
        )

    field_value = context.get(condition.get("field"), "")
    field_text = " ".join(field_value) if isinstance(field_value, list) else str(field_value)
    if "contains_any" in condition:
        return any(term in field_text for term in condition["contains_any"])
    if "missing_all" in condition:
        return all(term not in field_text for term in condition["missing_all"])
    return False


def _constraint_text(constraint: dict[str, Any] | str) -> str:
    if isinstance(constraint, str):
        return constraint
    return " ".join(str(value) for value in constraint.values())
