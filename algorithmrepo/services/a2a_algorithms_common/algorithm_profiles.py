"""Resource profiles shared by execution-control and closed-loop algorithms.

This module deliberately has no Agent, Commander, transport, or FastAPI imports.
Algorithms receive a profile through the generic ``params`` object and remain
usable as plain Python functions or independent HTTP services.
"""
from __future__ import annotations

from copy import deepcopy
from typing import Any, Mapping


DEFAULT_ALGORITHM_PROFILE = "medium"
PROFILE_ALIASES = {"small": "low", "large": "high", "balanced": "medium"}

_PROFILES: dict[str, dict[str, dict[str, Any]]] = {
    "low": {
        "execution_control": {"max_tracks": 16, "max_matched_rules": 3},
        "motion_prediction": {"backend": "sklearn_linear", "process_noise": None, "measurement_noise": None},
        "mission_completion": {"forest_trees": 32},
        "closed_loop_advisor": {"policy": "standard_v1"},
    },
    "medium": {
        "execution_control": {"max_tracks": None, "max_matched_rules": None},
        "motion_prediction": {"backend": "filterpy_kalman", "process_noise": 0.05, "measurement_noise": 1.0},
        "mission_completion": {"forest_trees": 96},
        "closed_loop_advisor": {"policy": "standard_v1"},
    },
    "high": {
        "execution_control": {"max_tracks": None, "max_matched_rules": None},
        "motion_prediction": {"backend": "filterpy_kalman", "process_noise": 0.01, "measurement_noise": 0.25},
        "mission_completion": {"forest_trees": None},
        "closed_loop_advisor": {"policy": "standard_v1"},
    },
}


def normalize_algorithm_profile(value: Any) -> str:
    """Return a supported canonical profile or raise on configuration errors."""
    name = str(value or DEFAULT_ALGORITHM_PROFILE).strip().lower()
    name = PROFILE_ALIASES.get(name, name)
    if name not in _PROFILES:
        supported = ", ".join(sorted(_PROFILES))
        raise ValueError(f"unsupported algorithm profile '{name}'; expected one of: {supported}")
    return name


def resolve_algorithm_profile(
    params: Mapping[str, Any] | None = None,
    inputs: Mapping[str, Any] | None = None,
) -> str:
    """Resolve profile from params first, then inputs, defaulting to medium."""
    params = params or {}
    inputs = inputs or {}
    return normalize_algorithm_profile(params.get("profile") or inputs.get("profile"))


def profile_config(component: str, profile: Any = None) -> dict[str, Any]:
    """Return an isolated component configuration for the selected profile."""
    name = normalize_algorithm_profile(profile)
    if component not in _PROFILES[name]:
        raise ValueError(f"unsupported algorithm profile component: {component}")
    return deepcopy(_PROFILES[name][component])


def available_algorithm_profiles() -> dict[str, dict[str, dict[str, Any]]]:
    return deepcopy(_PROFILES)
