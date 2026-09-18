"""Deterministic, non-actuating execution simulation for offline workflows."""

from __future__ import annotations

from copy import deepcopy
from hashlib import sha256
import json
from pathlib import Path
from typing import Any


PROFILE_PATH = Path(__file__).resolve().parents[2] / "config" / "rule_simulation_profiles.json"
REQUIRED_POLICY_FIELDS = {
    "minimum_intel_confidence",
    "minimum_resource_readiness",
    "minimum_communication_quality",
    "maximum_threat_pressure",
    "success_threshold",
    "partial_threshold",
    "threat_penalty_weight",
    "weights",
}


def _dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _latest(value: Any) -> Any:
    while isinstance(value, list):
        value = value[-1] if value else None
    if isinstance(value, dict) and "value" in value:
        return _latest(value.get("value"))
    return value


def _score(value: Any) -> float | None:
    if value in (None, "") or isinstance(value, bool):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if number > 1.0 and number <= 100.0:
        number /= 100.0
    if number < 0.0 or number > 1.0:
        return None
    return number


def load_simulation_profile(name: str, path: str | Path | None = None) -> dict[str, Any]:
    profile_name = str(name or "").strip().lower()
    if not profile_name:
        raise ValueError("simulation profile is required")
    profile_path = Path(path) if path else PROFILE_PATH
    profiles = json.loads(profile_path.read_text(encoding="utf-8"))
    profile = profiles.get(profile_name)
    if not isinstance(profile, dict):
        raise ValueError(f"unknown simulation profile: {profile_name}")
    missing = sorted(REQUIRED_POLICY_FIELDS - set(profile))
    if missing:
        raise ValueError(f"simulation profile missing fields: {', '.join(missing)}")
    weights = _dict(profile.get("weights"))
    required_weights = {
        "intel_confidence",
        "resource_readiness",
        "communication_quality",
        "target_completeness",
    }
    if set(weights) != required_weights or abs(sum(float(v) for v in weights.values()) - 1.0) > 1e-9:
        raise ValueError("simulation profile weights must contain the four inputs and sum to 1")
    return deepcopy(profile)


def _execution_control(input_data: dict[str, Any]) -> dict[str, Any]:
    value = _latest(input_data.get("execution_control_result"))
    value = _dict(value)
    return _dict(value.get("output_data")) or value


def _mission_input(payload: dict[str, Any]) -> dict[str, Any]:
    input_data = _dict(payload.get("input"))
    context = _dict(payload.get("context"))
    return (
        _dict(input_data.get("mission_input"))
        or _dict(context.get("mission_input"))
        or _dict(_dict(input_data.get("context")).get("mission_input"))
    )


def _authorization(
    input_data: dict[str, Any],
    execution_control: dict[str, Any],
    mission_input: dict[str, Any],
) -> dict[str, Any]:
    return (
        _dict(execution_control.get("authorization"))
        or _dict(input_data.get("authorization"))
        or _dict(mission_input.get("simulation_authorization"))
    )


def _simulation_inputs(
    input_data: dict[str, Any],
    execution_control: dict[str, Any],
    mission_input: dict[str, Any],
) -> tuple[dict[str, float | None], dict[str, str]]:
    explicit = _dict(input_data.get("simulation_inputs")) or _dict(
        mission_input.get("simulation_inputs")
    )
    situation = _dict(execution_control.get("situation"))
    values: dict[str, float | None] = {}
    sources: dict[str, str] = {}
    aliases = {
        "intel_confidence": ("intel_confidence",),
        "resource_readiness": ("resource_readiness", "asset_readiness"),
        "communication_quality": ("communication_quality", "comm_quality"),
        "threat_pressure": ("threat_pressure", "threat_score"),
        "supply_pressure": ("supply_pressure", "ammo_pressure"),
    }
    for field, names in aliases.items():
        for name in names:
            value = _score(explicit.get(name))
            if value is not None:
                values[field] = value
                sources[field] = f"simulation_inputs.{name}"
                break
        if field not in values:
            for name in names:
                value = _score(situation.get(name))
                if value is not None:
                    values[field] = value
                    sources[field] = f"execution_control.situation.{name}"
                    break
        values.setdefault(field, None)
    return values, sources


def _target_completeness(command: dict[str, Any], input_data: dict[str, Any]) -> tuple[float, list[str]]:
    checks = {
        "action": bool(command.get("action")),
        "target_id": bool(command.get("target_id")),
        "command_id": bool(command.get("command_id")),
        "target_solution": bool(
            (_dict(command.get("aim_point")).get("x") is not None
             and _dict(command.get("aim_point")).get("y") is not None)
            or input_data.get("coordinates")
        ),
    }
    missing = [name for name, present in checks.items() if not present]
    return sum(1 for present in checks.values() if present) / len(checks), missing


def _authorization_approved(authorization: dict[str, Any]) -> bool:
    decision = str(
        authorization.get("decision") or authorization.get("status") or ""
    ).lower()
    return decision in {"approved", "authorized"} and authorization.get("execution_blocked") is not True


def _cooperative_gate(command: dict[str, Any], mission_input: dict[str, Any]) -> tuple[bool, str | None]:
    config = _dict(mission_input.get("cooperative_execution"))
    if config.get("enabled") is not True:
        return True, None
    if command.get("assignment_source") != "deterministic_cbba":
        return False, "assignment_source"
    if command.get("assignment_locked") is not True:
        return False, "assignment_locked"
    if not command.get("assigned_resources") or not command.get("assignment_slot_ids"):
        return False, "assigned_resources"
    return True, None


def simulate_rule_based_execution(
    payload: dict[str, Any],
    *,
    action_kind: str,
    profile_name: str,
    profile_path: str | Path | None = None,
) -> dict[str, Any]:
    """Evaluate one simulated action from explicit upstream data and policy."""
    policy = load_simulation_profile(profile_name, profile_path)
    input_data = _dict(payload.get("input"))
    command = _dict(input_data.get("execution_command"))
    execution_control = _execution_control(input_data)
    mission_input = _mission_input(payload)
    authorization = _authorization(input_data, execution_control, mission_input)
    values, sources = _simulation_inputs(input_data, execution_control, mission_input)
    completeness, missing_command_fields = _target_completeness(command, input_data)
    values["target_completeness"] = completeness
    sources["target_completeness"] = "execution_command"

    missing_fields = [
        field
        for field in (
            "intel_confidence",
            "resource_readiness",
            "communication_quality",
            "threat_pressure",
        )
        if values.get(field) is None
    ]
    missing_fields.extend(f"execution_command.{field}" for field in missing_command_fields)
    rules = []

    def rule(rule_id: str, passed: bool, observed: Any, requirement: Any) -> None:
        rules.append(
            {
                "rule_id": rule_id,
                "passed": bool(passed),
                "observed": observed,
                "requirement": requirement,
            }
        )

    rule("authorization", _authorization_approved(authorization), authorization, "approved and not blocked")
    cooperative_ok, cooperative_error = _cooperative_gate(command, mission_input)
    rule("cooperative_assignment", cooperative_ok, cooperative_error, "locked deterministic CBBA assignment when enabled")
    rule("command_complete", not missing_command_fields, missing_command_fields, "action, target_id, command_id and target solution")
    if values["intel_confidence"] is not None:
        rule("intel_confidence", values["intel_confidence"] >= float(policy["minimum_intel_confidence"]), values["intel_confidence"], policy["minimum_intel_confidence"])
    if values["resource_readiness"] is not None:
        rule("resource_readiness", values["resource_readiness"] >= float(policy["minimum_resource_readiness"]), values["resource_readiness"], policy["minimum_resource_readiness"])
    if values["communication_quality"] is not None:
        rule("communication_quality", values["communication_quality"] >= float(policy["minimum_communication_quality"]), values["communication_quality"], policy["minimum_communication_quality"])
    if values["threat_pressure"] is not None:
        rule("threat_pressure", values["threat_pressure"] <= float(policy["maximum_threat_pressure"]), values["threat_pressure"], policy["maximum_threat_pressure"])

    if missing_fields:
        decision = "insufficient_data"
        effect_score = None
        assessment_status = "insufficient_data"
    elif any(not row["passed"] for row in rules):
        decision = "blocked"
        effect_score = 0.0
        assessment_status = "blocked"
    else:
        base_score = sum(
            float(policy["weights"][name]) * float(values[name])
            for name in policy["weights"]
        )
        threat_factor = 1.0 - float(policy["threat_penalty_weight"]) * float(values["threat_pressure"])
        supply_pressure = values.get("supply_pressure")
        supply_factor = 1.0 if supply_pressure is None else 1.0 - 0.15 * float(supply_pressure)
        effect_score = round(max(0.0, min(1.0, base_score * threat_factor * supply_factor)), 4)
        if effect_score >= float(policy["success_threshold"]):
            decision = "simulated_success"
        elif effect_score >= float(policy["partial_threshold"]):
            decision = "simulated_partial"
        else:
            decision = "simulated_failed"
        assessment_status = "complete"

    evidence_payload = {
        "action_kind": action_kind,
        "command": command,
        "inputs": values,
        "profile": profile_name,
        "rules": rules,
        "decision": decision,
        "effect_score": effect_score,
    }
    evidence_digest = sha256(
        json.dumps(evidence_payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    return {
        "task_type": f"{action_kind}_simulation",
        "input_data": {
            "command_id": command.get("command_id"),
            "target_id": command.get("target_id"),
            "profile": profile_name,
        },
        "output_data": {
            "action": command.get("action"),
            "target_id": command.get("target_id"),
            "aim_point": deepcopy(command.get("aim_point")),
            "command_id": command.get("command_id"),
            "executor_role": action_kind,
            "assessment_status": assessment_status,
            "decision": decision,
            "status": "completed" if assessment_status == "complete" else assessment_status,
            "execution_mode": "rule_based_simulation",
            "is_real_execution": False,
            "actuator_connected": False,
            "simulated_effect_score": effect_score,
            "damage_probability": effect_score if action_kind == "artillery" else None,
            "simulation_inputs": values,
            "input_sources": sources,
            "missing_fields": sorted(set(missing_fields)),
            "rules": rules,
            "rules_passed": [row["rule_id"] for row in rules if row["passed"]],
            "rules_failed": [row["rule_id"] for row in rules if not row["passed"]],
            "evidence": {
                "type": "simulation",
                "uri": f"sim://rule-based-execution/{evidence_digest}",
                "sha256": evidence_digest,
            },
        },
        "accuracy": effect_score,
        "latency": 0.0,
    }


def evaluate_simulated_effect(payload: dict[str, Any]) -> dict[str, Any]:
    input_data = _dict(payload.get("input"))
    if "mock_eval_score" in input_data:
        explicit_score = _score(input_data.get("mock_eval_score"))
        if explicit_score is None:
            return {
                "assessment_status": "insufficient_data",
                "eval_score": 0,
                "missing_fields": ["mock_eval_score(valid range 0..100)"],
                "is_real_evaluation": False,
            }
        return {
            "assessment_status": "complete",
            "eval_score": int(round(explicit_score * 100)),
            "damage_probability": explicit_score,
            "source_evidence": {
                "type": "explicit_test_override",
                "field": "input.mock_eval_score",
            },
            "is_real_evaluation": False,
            "evaluation_mode": "explicit_test_override",
        }
    strike = _dict(_latest(input_data.get("strike_result")))
    output = _dict(strike.get("output_data")) or strike
    effect = _score(output.get("simulated_effect_score"))
    evidence = _dict(output.get("evidence"))
    valid_simulation = (
        output.get("execution_mode") == "rule_based_simulation"
        and output.get("is_real_execution") is False
        and evidence.get("type") == "simulation"
        and bool(evidence.get("sha256"))
    )
    if effect is None or not valid_simulation:
        return {
            "assessment_status": "insufficient_data",
            "eval_score": 0,
            "missing_fields": ["rule_based_simulation.simulated_effect_score/evidence"],
            "is_real_evaluation": False,
        }
    return {
        "assessment_status": "complete",
        "eval_score": int(round(effect * 100)),
        "damage_probability": effect,
        "source_evidence": deepcopy(evidence),
        "is_real_evaluation": False,
        "evaluation_mode": "deterministic_rule_projection",
    }
