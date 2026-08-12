"""Versioned Dynamic Bayesian Network situation-attention calibration."""

from __future__ import annotations

import hashlib
import json
import math
from pathlib import Path
from typing import Any, Dict, Mapping

from .models import TrackState
from .utils import clamp


DEFAULT_PARAMETER_PATH = Path(__file__).resolve().parents[1] / "config" / "dbn_risk_model_v1.json"


class DBNThreatEvaluator:
    """Maintain low/medium/high attention-state posteriors per track.

    The DBN consumes only observable motion, prediction, quality and anomaly
    factors. It calibrates situation-awareness priority and does not perform
    mission planning, target selection or engagement decisions.
    """

    STATES = ("low", "medium", "high")
    PATTERNS = (
        "protected_zone_approach",
        "sustained_presence",
        "coordinated_motion",
        "anomalous_motion",
        "non_closing_motion",
    )

    def __init__(self, parameter_path: str | Path | None = None) -> None:
        self.parameter_path = Path(parameter_path or DEFAULT_PARAMETER_PATH).resolve()
        self.parameters, self.parameter_sha256 = self._load_parameters(self.parameter_path)
        self._state_by_track: Dict[str, Dict[str, float]] = {}
        self._pattern_by_track: Dict[str, Dict[str, float]] = {}

    def reset(self) -> None:
        self._state_by_track.clear()
        self._pattern_by_track.clear()

    def update(self, track: TrackState, base_score: float, factors: Dict[str, float]) -> Dict[str, object]:
        features = self._feature_vector(track, base_score, factors)
        previous_state = self._state_by_track.get(track.track_id)
        previous_pattern = self._pattern_by_track.get(track.track_id)
        transition_matrix = self._transition_matrix(features)
        prior = self._transition(previous_state, transition_matrix)
        utility_scores = self._risk_pattern_utilities(features)
        risk_pattern_probabilities = self._softmax(utility_scores)
        observation = self._observation_likelihood(features)
        raw_posterior = self._normalize(
            {state: prior[state] * observation[state] for state in self.STATES}
        )
        observation_reliability = self._observation_reliability(features)
        posterior = self._normalize(
            {
                state: (1.0 - observation_reliability) * prior[state]
                + observation_reliability * raw_posterior[state]
                for state in self.STATES
            }
        )
        self._state_by_track[track.track_id] = posterior
        self._pattern_by_track[track.track_id] = risk_pattern_probabilities

        state_values = self.parameters["state_values"]
        state_factor = sum(posterior[state] * float(state_values[state]) for state in self.STATES)
        pattern_risk = self._risk_pattern_factor(risk_pattern_probabilities)
        fusion = self.parameters["score_fusion"]
        smoothed_score = clamp(
            float(fusion["base_score"]) * base_score
            + float(fusion["state_factor"]) * state_factor
            + float(fusion["risk_pattern_factor"]) * pattern_risk
        )
        dominant_pattern = max(risk_pattern_probabilities, key=risk_pattern_probabilities.get)
        approach_key = "protected_zone_approach"
        parameter_model = {
            "schema_version": self.parameters["schema_version"],
            "model_version": self.parameters["model_version"],
            "sha256": self.parameter_sha256,
            "source": str(self.parameter_path),
        }
        return {
            "algorithm": "DBN",
            "contract": "dynamic_bayesian_network_situation_attention_calibration",
            "parameter_model": parameter_model,
            "prior": self._rounded(prior),
            "observation": self._rounded(observation),
            "posterior": self._rounded(posterior),
            "dbn_posterior": self._rounded(posterior),
            "risk_state_probabilities": self._rounded(posterior),
            "risk_pattern_probabilities": self._rounded(risk_pattern_probabilities),
            "risk_pattern_model": {
                "algorithm": "DBN observable-pattern calibration",
                "contract": "situation_awareness_observable_pattern_probability",
                "dominant_pattern": dominant_pattern,
                "utility_scores": self._rounded(utility_scores),
                "utility_basis": (
                    "softmax over protected-zone approach, sustained presence, coordinated motion, "
                    "anomalous motion and non-closing motion"
                ),
                "parameter_model": parameter_model,
                "safety_note": (
                    "These probabilities describe observable situation patterns only; no planning, "
                    "targeting, engagement or attack advice is produced."
                ),
            },
            "transition_matrix": {
                row: self._rounded(columns) for row, columns in transition_matrix.items()
            },
            "observation_reliability": round(observation_reliability, 4),
            "raw_posterior": self._rounded(raw_posterior),
            "posterior_entropy": round(self._entropy(posterior), 4),
            "state_transition": {
                "previous_high": round((previous_state or self.parameters["initial_state"])["high"], 4),
                "prior_high": round(prior["high"], 4),
                "posterior_high": round(posterior["high"], 4),
                "high_delta": round(
                    posterior["high"] - (previous_state or prior).get("high", prior["high"]),
                    4,
                ),
                "dominant_state_changed": (
                    max(previous_state, key=previous_state.get) != max(posterior, key=posterior.get)
                    if previous_state
                    else False
                ),
            },
            "risk_pattern_transition": {
                "previous_dominant_pattern": (
                    max(previous_pattern, key=previous_pattern.get) if previous_pattern else None
                ),
                "dominant_pattern": dominant_pattern,
                "dominant_changed": (
                    max(previous_pattern, key=previous_pattern.get) != dominant_pattern
                    if previous_pattern
                    else False
                ),
                "protected_zone_approach_delta": round(
                    risk_pattern_probabilities[approach_key]
                    - (previous_pattern or {}).get(approach_key, risk_pattern_probabilities[approach_key]),
                    4,
                ),
            },
            "risk_pattern_factor": round(pattern_risk, 4),
            "state_factor": round(state_factor, 4),
            "smoothed_score": round(smoothed_score, 4),
            "dominant_state": max(posterior, key=posterior.get),
            "dominant_risk_pattern": dominant_pattern,
        }

    def _feature_vector(
        self,
        track: TrackState,
        base_score: float,
        factors: Mapping[str, float],
    ) -> Dict[str, float]:
        anomaly = clamp(float(factors.get("anomaly_factor", 0.0)))
        quality = clamp(float(factors.get("quality_factor", track.track_quality)))
        closing = clamp(float(factors.get("closing_factor", 0.0)))
        distance = clamp(float(factors.get("distance_factor", 0.0)))
        physical_context = track.metadata.get("physical_group_context", {}) or {}
        coordinated_context = clamp(float(physical_context.get("cohesion_score", 0.0)))
        return {
            "base_score": clamp(float(base_score)),
            "distance_factor": distance,
            "closing_factor": closing,
            "type_factor": clamp(float(factors.get("type_factor", 0.0))),
            "anomaly_factor": anomaly,
            "quality_factor": quality,
            "inverse_distance": 1.0 - distance,
            "inverse_closing": 1.0 - closing,
            "inverse_quality": 1.0 - quality,
            "stable_motion": 1.0 - anomaly,
            "missed_indicator": float(track.missed_count > 0),
            "missed_count_capped": float(min(track.missed_count, 3)),
            "aircraft_or_uav": float(track.object_type in {"aircraft", "uav"}),
            "aircraft_or_ship": float(track.object_type in {"aircraft", "ship"}),
            "coordinated_context": coordinated_context,
        }

    def _transition_matrix(self, features: Mapping[str, float]) -> Dict[str, Dict[str, float]]:
        transition_features = self.parameters["transition_features"]
        escalation = clamp(self._linear(transition_features["escalation"], features))
        deescalation = clamp(self._linear(transition_features["deescalation"], features))
        total = escalation + deescalation
        if total > 1.0:
            escalation /= total
            deescalation /= total
        neutral = 1.0 - escalation - deescalation
        matrices = self.parameters["transition_matrices"]
        return {
            source: self._normalize_any(
                {
                    target: (
                        neutral * float(matrices["base"][source][target])
                        + escalation * float(matrices["escalated"][source][target])
                        + deescalation * float(matrices["deescalated"][source][target])
                    )
                    for target in self.STATES
                }
            )
            for source in self.STATES
        }

    def _transition(
        self,
        previous: Dict[str, float] | None,
        transition_matrix: Dict[str, Dict[str, float]],
    ) -> Dict[str, float]:
        source_probability = previous or self.parameters["initial_state"]
        return self._normalize(
            {
                target: sum(
                    float(source_probability[source]) * transition_matrix[source][target]
                    for source in self.STATES
                )
                for target in self.STATES
            }
        )

    def _observation_likelihood(self, features: Mapping[str, float]) -> Dict[str, float]:
        logits = {
            state: self._linear(self.parameters["observation_logits"][state], features)
            for state in self.STATES
        }
        return self._softmax(logits)

    def _risk_pattern_utilities(self, features: Mapping[str, float]) -> Dict[str, float]:
        return {
            pattern: self._linear(self.parameters["risk_pattern_utilities"][pattern], features)
            for pattern in self.PATTERNS
        }

    def _observation_reliability(self, features: Mapping[str, float]) -> float:
        return clamp(self._linear(self.parameters["observation_reliability"], features))

    def _risk_pattern_factor(self, probabilities: Mapping[str, float]) -> float:
        weights = self.parameters["risk_pattern_weights"]
        return clamp(sum(float(weights[key]) * probabilities[key] for key in self.PATTERNS))

    @staticmethod
    def _linear(weights: Mapping[str, float], features: Mapping[str, float]) -> float:
        return float(weights.get("bias", 0.0)) + sum(
            float(weight) * float(features.get(name, 0.0))
            for name, weight in weights.items()
            if name != "bias"
        )

    @staticmethod
    def _softmax(values: Mapping[str, float]) -> Dict[str, float]:
        max_value = max(values.values(), default=0.0)
        exps = {key: math.exp(value - max_value) for key, value in values.items()}
        total = sum(exps.values()) or 1.0
        return {key: value / total for key, value in exps.items()}

    def _normalize(self, values: Mapping[str, float]) -> Dict[str, float]:
        total = sum(max(0.0, float(value)) for value in values.values())
        if total <= 1e-9:
            return {"low": 1.0, "medium": 0.0, "high": 0.0}
        return {key: max(0.0, float(value)) / total for key, value in values.items()}

    @staticmethod
    def _normalize_any(values: Mapping[str, float]) -> Dict[str, float]:
        total = sum(max(0.0, float(value)) for value in values.values())
        if total <= 1e-9:
            return {key: 1.0 / max(len(values), 1) for key in values}
        return {key: max(0.0, float(value)) / total for key, value in values.items()}

    @staticmethod
    def _entropy(values: Mapping[str, float]) -> float:
        return -sum(value * math.log(max(value, 1e-9), 2) for value in values.values())

    @staticmethod
    def _rounded(values: Mapping[str, float]) -> Dict[str, float]:
        return {key: round(float(value), 4) for key, value in values.items()}

    @classmethod
    def _load_parameters(cls, path: Path) -> tuple[Dict[str, Any], str]:
        try:
            raw = path.read_bytes()
            parameters = json.loads(raw.decode("utf-8"))
            cls._validate_parameters(parameters)
        except (OSError, UnicodeError, json.JSONDecodeError, KeyError, TypeError, ValueError) as exc:
            raise ValueError(f"DBN parameter file is invalid: {path}: {exc}") from exc
        return parameters, hashlib.sha256(raw).hexdigest()

    @classmethod
    def _validate_parameters(cls, parameters: Mapping[str, Any]) -> None:
        if parameters.get("schema_version") != "dbn_risk_model/v1":
            raise ValueError("DBN parameter schema_version must be dbn_risk_model/v1")
        if tuple(parameters.get("states", ())) != cls.STATES:
            raise ValueError(f"DBN parameter states must be {cls.STATES}")
        if not str(parameters.get("model_version", "")).strip():
            raise ValueError("DBN parameter model_version is required")

        cls._validate_probability_row("initial_state", parameters["initial_state"], cls.STATES)
        matrices = parameters["transition_matrices"]
        for matrix_name in ("base", "escalated", "deescalated"):
            matrix = matrices[matrix_name]
            if set(matrix) != set(cls.STATES):
                raise ValueError(f"DBN parameter {matrix_name} transition rows are invalid")
            for source in cls.STATES:
                cls._validate_probability_row(
                    f"transition_matrices.{matrix_name}.{source}",
                    matrix[source],
                    cls.STATES,
                )

        if set(parameters["observation_logits"]) != set(cls.STATES):
            raise ValueError("DBN parameter observation states are invalid")
        if set(parameters["risk_pattern_utilities"]) != set(cls.PATTERNS):
            raise ValueError("DBN parameter observable patterns are invalid")
        if set(parameters["risk_pattern_weights"]) != set(cls.PATTERNS):
            raise ValueError("DBN parameter observable-pattern weights are invalid")
        if set(parameters["state_values"]) != set(cls.STATES):
            raise ValueError("DBN parameter state values are invalid")

        for section_name in (
            "transition_features",
            "observation_logits",
            "observation_reliability",
            "risk_pattern_utilities",
            "risk_pattern_weights",
            "state_values",
            "score_fusion",
        ):
            cls._validate_finite_tree(section_name, parameters[section_name])
        cls._validate_weight_sum("risk_pattern_weights", parameters["risk_pattern_weights"])
        cls._validate_weight_sum("score_fusion", parameters["score_fusion"])

    @staticmethod
    def _validate_probability_row(name: str, row: Mapping[str, Any], expected: tuple[str, ...]) -> None:
        if set(row) != set(expected):
            raise ValueError(f"DBN parameter {name} keys are invalid")
        values = [float(row[key]) for key in expected]
        if any(not math.isfinite(value) or value < 0.0 for value in values):
            raise ValueError(f"DBN parameter {name} contains an invalid probability")
        if not math.isclose(sum(values), 1.0, abs_tol=1e-6):
            raise ValueError(f"DBN parameter {name} probabilities must sum to 1")

    @classmethod
    def _validate_finite_tree(cls, name: str, value: Any) -> None:
        if isinstance(value, Mapping):
            for child_name, child_value in value.items():
                cls._validate_finite_tree(f"{name}.{child_name}", child_value)
            return
        numeric = float(value)
        if not math.isfinite(numeric):
            raise ValueError(f"DBN parameter {name} must be finite")

    @staticmethod
    def _validate_weight_sum(name: str, weights: Mapping[str, Any]) -> None:
        values = [float(value) for value in weights.values()]
        if any(value < 0.0 for value in values):
            raise ValueError(f"DBN parameter {name} cannot contain negative weights")
        if not math.isclose(sum(values), 1.0, abs_tol=1e-6):
            raise ValueError(f"DBN parameter {name} must sum to 1")
