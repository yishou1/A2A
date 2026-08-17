"""Unified seven-dimensional mission feature schema (mission_features_v2)."""
from __future__ import annotations

from typing import Any, Dict, List, Sequence

FEATURE_VERSION = "mission_features_v2"

FEATURE_ORDER: List[str] = [
    "damage_rate",
    "asset_readiness",
    "control_timeliness",
    "intel_confidence",
    "threat_pressure",
    "ammo_pressure",
    "comm_quality",
]

LABEL_FIELDS = frozenset(
    {
        "result",
        "completion",
        "task_completion",
        "win",
        "loss",
        "victory",
        "defeat",
    }
)

FEATURE_SCHEMA: Dict[str, Dict[str, Any]] = {
    "damage_rate": {
        "dtype": "float",
        "range": (0.0, 1.0),
        "direction": "higher_is_better",
        "missing_policy_strict": "insufficient_data",
        "missing_policy_fixture": 0.0,
        "sc2le_proxy_source": "proxy_damage_rate from mmr/apm/relative_mmr (no Result)",
        "agent_source": "damage_confirmation.confirmed_destroyed / engaged_targets",
    },
    "asset_readiness": {
        "dtype": "float",
        "range": (0.0, 1.0),
        "direction": "higher_is_better",
        "missing_policy_strict": "insufficient_data",
        "missing_policy_fixture": 0.7,
        "sc2le_proxy_source": "mmr / 6000",
        "agent_source": "resource_allocation.readiness",
    },
    "control_timeliness": {
        "dtype": "float",
        "range": (0.0, 1.0),
        "direction": "higher_is_better",
        "missing_policy_strict": "insufficient_data",
        "missing_policy_fixture": 0.85,
        "sc2le_proxy_source": "apm / 400",
        "agent_source": "1 - execution_control.latency_ms / latency_reference_ms",
    },
    "intel_confidence": {
        "dtype": "float",
        "range": (0.0, 1.0),
        "direction": "higher_is_better",
        "missing_policy_strict": "insufficient_data",
        "missing_policy_fixture": 0.82,
        "sc2le_proxy_source": "mmr/apm blend",
        "agent_source": "mean(perception_detection.detections.conf | data_fusion tracks)",
    },
    "threat_pressure": {
        "dtype": "float",
        "range": (0.0, 1.0),
        "direction": "higher_is_worse",
        "missing_policy_strict": "insufficient_data",
        "missing_policy_fixture": 0.70,
        "sc2le_proxy_source": "opponent_mmr/duration blend",
        "agent_source": "threat_evaluation aggregated score",
    },
    "ammo_pressure": {
        "dtype": "float",
        "range": (0.0, 1.0),
        "direction": "higher_is_worse",
        "missing_policy_strict": "insufficient_data",
        "missing_policy_fixture": 0.50,
        "sc2le_proxy_source": "duration / 1800",
        "agent_source": "resource_allocation.supply_pressure",
    },
    "comm_quality": {
        "dtype": "float",
        "range": (0.0, 1.0),
        "direction": "higher_is_better",
        "missing_policy_strict": "insufficient_data",
        "missing_policy_fixture": 0.88,
        "sc2le_proxy_source": "apm/mmr blend",
        "agent_source": "communication.delivery_rate",
    },
}

LATENCY_REFERENCE_MS = 2000.0
MISSION_COMPLETION_THRESHOLD = 0.5
DEFAULT_MODEL_PATH = "models/sc2le_proxy_mission_model.pkl"
DEFAULT_MODEL_METADATA_PATH = "models/sc2le_proxy_mission_model.metadata.json"
DEFAULT_EVALUATION_REPORT_PATH = "data/sc2/processed/sc2le_mission_evaluation_report.json"

AGENT_REQUIRED_BLOCKS = {
    "damage_rate": ("damage_confirmation",),
    "asset_readiness": ("resource_allocation",),
    "control_timeliness": ("execution_control",),
    "intel_confidence": ("perception_detection", "data_fusion", "recognition"),
    "threat_pressure": ("threat_evaluation", "evaluator"),
    "ammo_pressure": ("resource_allocation",),
    "comm_quality": ("communication",),
}


def clamp(value: float, low: float = 0.0, high: float = 1.0) -> float:
    return max(low, min(high, float(value)))


def empty_feature_bundle(*, warnings: List[str] | None = None) -> dict:
    return {
        "feature_version": FEATURE_VERSION,
        "values": {name: 0.0 for name in FEATURE_ORDER},
        "sources": {},
        "warnings": list(warnings or []),
    }


def values_to_vector(values: dict) -> List[float]:
    return [float(values[name]) for name in FEATURE_ORDER]


def vector_to_values(vector: Sequence[float]) -> dict:
    return {name: float(vector[index]) for index, name in enumerate(FEATURE_ORDER)}


def validate_feature_ranges(values: dict) -> List[str]:
    warnings: List[str] = []
    for name in FEATURE_ORDER:
        value = float(values.get(name, 0.0))
        low, high = FEATURE_SCHEMA[name]["range"]
        if value < low or value > high:
            warnings.append(f"{name} out of range: {value}")
    return warnings


def assert_no_label_leakage(feature_inputs: dict, *, context: str = "") -> None:
    lowered_keys = {str(key).strip().lower() for key in feature_inputs.keys()}
    leaked = sorted(lowered_keys & LABEL_FIELDS)
    if leaked:
        prefix = f"{context}: " if context else ""
        raise ValueError(f"{prefix}label leakage detected in feature inputs: {leaked}")
