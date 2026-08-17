"""Closed-loop action advisor ported from closed_loop_core."""
from __future__ import annotations

from typing import Tuple


_ACTION_RECOMMENDATIONS = {
    "confirm_effect_and_shift": "Damage probability is high; confirm effect and shift focus to remaining threats.",
    "re_attack": "Critical situation with persistent threat; recommend immediate re-attack.",
    "reallocate_sensor": "Uncertainty or low damage confidence; reallocate sensors to improve tracking.",
    "coordinated_suppression": "Mission completion below target with elevated threat; coordinate suppression fire.",
    "continue_tracking": "Maintain current tracking posture and monitor target evolution.",
}


def _choose_action(target: dict, damage_prob: float, situation: str, mission_completion: float) -> Tuple[str, float]:
    threat_score = float(target.get("threat_score", 0.0))
    uncertainty = float(target.get("uncertainty", 0.0))
    if damage_prob >= 0.84:
        return "confirm_effect_and_shift", 0.02
    if situation == "critical" and threat_score >= 0.72 and damage_prob < 0.72:
        return "re_attack", 0.18
    if uncertainty > 0.34 or damage_prob < 0.55:
        return "reallocate_sensor", 0.08
    if mission_completion < 0.90 and threat_score >= 0.62:
        return "coordinated_suppression", 0.12
    return "continue_tracking", 0.04


def advise(
    target: dict,
    damage_prob: float,
    situation: str,
    mission_completion: float,
) -> dict:
    """Recommend a closed-loop action for a single target."""
    action, effect_delta = _choose_action(target, damage_prob, situation, mission_completion)
    return {
        "action": action,
        "effect_delta": round(effect_delta, 4),
        "recommendation": _ACTION_RECOMMENDATIONS.get(action, action),
        "target_id": target.get("target_id"),
        "situation": situation,
        "damage_probability": round(float(damage_prob), 4),
        "mission_completion": round(float(mission_completion), 4),
    }
