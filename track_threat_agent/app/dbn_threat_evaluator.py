"""Dynamic Bayesian Network risk-state calibration."""

from __future__ import annotations

import math
from typing import Dict

from .models import TrackState
from .utils import clamp


class DBNThreatEvaluator:
    """Maintains low/medium/high state probabilities per track.

    The evaluator maintains low/medium/high posterior probabilities per track.
    It uses a transition prior from the previous frame and an observation
    likelihood from current motion, quality, anomaly, and graph factors. It
    only calibrates situation-awareness priority, not planning or engagement.
    """

    STATES = ("low", "medium", "high")

    def __init__(self) -> None:
        self._state_by_track: Dict[str, Dict[str, float]] = {}
        self._pattern_by_track: Dict[str, Dict[str, float]] = {}

    def reset(self) -> None:
        self._state_by_track.clear()
        self._pattern_by_track.clear()

    def update(self, track: TrackState, base_score: float, factors: Dict[str, float]) -> Dict[str, object]:
        previous_state = self._state_by_track.get(track.track_id)
        previous_pattern = self._pattern_by_track.get(track.track_id)
        transition_matrix = self._transition_matrix(track, factors)
        prior = self._transition(previous_state, transition_matrix)
        risk_pattern_probabilities = self._risk_pattern_probabilities(track, factors)
        observation = self._observation_likelihood(track, base_score, factors)
        raw_posterior = self._normalize({
            state: prior[state] * observation[state]
            for state in self.STATES
        })
        observation_reliability = self._observation_reliability(track, factors)
        posterior = self._normalize(
            {
                state: (1.0 - observation_reliability) * prior[state] + observation_reliability * raw_posterior[state]
                for state in self.STATES
            }
        )
        self._state_by_track[track.track_id] = posterior
        self._pattern_by_track[track.track_id] = risk_pattern_probabilities
        state_factor = posterior["medium"] * 0.55 + posterior["high"]
        pattern_risk = self._risk_pattern_factor(risk_pattern_probabilities)
        smoothed_score = clamp(0.62 * base_score + 0.24 * state_factor + 0.14 * pattern_risk)
        dominant_pattern = max(risk_pattern_probabilities, key=risk_pattern_probabilities.get)
        return {
            "algorithm": "DBN",
            "contract": "dynamic_bayesian_network_risk_state_calibration",
            "prior": {key: round(value, 4) for key, value in prior.items()},
            "observation": {key: round(value, 4) for key, value in observation.items()},
            "posterior": {key: round(value, 4) for key, value in posterior.items()},
            "dbn_posterior": {key: round(value, 4) for key, value in posterior.items()},
            "risk_state_probabilities": {key: round(value, 4) for key, value in posterior.items()},
            "risk_pattern_probabilities": {key: round(value, 4) for key, value in risk_pattern_probabilities.items()},
            "risk_pattern_model": {
                "algorithm": "DBN risk-pattern calibration",
                "contract": "situation_awareness_risk_pattern_probability",
                "dominant_pattern": dominant_pattern,
                "utility_scores": {
                    key: round(value, 4)
                    for key, value in self._risk_pattern_utilities(track, factors).items()
                },
                "utility_basis": "softmax over asset approach, surveillance/probe, formation coordination, anomalous maneuver, and transit likelihoods",
                "safety_note": "Risk-pattern probabilities are situation-awareness estimates only; no planning, engagement, or attack advice is produced.",
            },
            "transition_matrix": {
                row: {col: round(value, 4) for col, value in cols.items()}
                for row, cols in transition_matrix.items()
            },
            "observation_reliability": round(observation_reliability, 4),
            "raw_posterior": {key: round(value, 4) for key, value in raw_posterior.items()},
            "posterior_entropy": round(self._entropy(posterior), 4),
            "state_transition": {
                "previous_high": round((previous_state or {}).get("high", 0.12), 4),
                "prior_high": round(prior["high"], 4),
                "posterior_high": round(posterior["high"], 4),
                "high_delta": round(posterior["high"] - (previous_state or prior).get("high", prior["high"]), 4),
                "dominant_state_changed": (
                    max(previous_state, key=previous_state.get) != max(posterior, key=posterior.get)
                    if previous_state
                    else False
                ),
            },
            "risk_pattern_transition": {
                "previous_dominant_pattern": max(previous_pattern, key=previous_pattern.get) if previous_pattern else None,
                "dominant_pattern": dominant_pattern,
                "dominant_changed": (
                    max(previous_pattern, key=previous_pattern.get) != dominant_pattern
                    if previous_pattern
                    else False
                ),
                "asset_approach_delta": round(
                    risk_pattern_probabilities.get("asset_approach", 0.0)
                    - (previous_pattern or {}).get("asset_approach", risk_pattern_probabilities.get("asset_approach", 0.0)),
                    4,
                ),
            },
            "risk_pattern_factor": round(pattern_risk, 4),
            "state_factor": round(state_factor, 4),
            "smoothed_score": round(smoothed_score, 4),
            "dominant_state": max(posterior, key=posterior.get),
            "dominant_risk_pattern": dominant_pattern,
        }

    def _transition_matrix(self, track: TrackState, factors: Dict[str, float]) -> Dict[str, Dict[str, float]]:
        anomaly = factors.get("anomaly_factor", 0.0)
        closing = factors.get("closing_factor", 0.0)
        distance = factors.get("distance_factor", 0.0)
        graph = (track.metadata.get("st_gnn_inspired", {}) or {}).get("graph_influence", 0.0)
        escalation = clamp(0.34 * anomaly + 0.28 * distance + 0.22 * graph + 0.16 * closing)
        deescalation = clamp(0.22 * (1.0 - closing) + 0.18 * (1.0 - distance) + 0.12 * float(track.missed_count > 0))
        return {
            "low": self._normalize_any(
                {
                    "low": 0.78 - 0.18 * escalation,
                    "medium": 0.18 + 0.12 * escalation,
                    "high": 0.04 + 0.06 * escalation,
                }
            ),
            "medium": self._normalize_any(
                {
                    "low": 0.15 + 0.12 * deescalation,
                    "medium": 0.66 - 0.10 * escalation,
                    "high": 0.19 + 0.18 * escalation,
                }
            ),
            "high": self._normalize_any(
                {
                    "low": 0.03 + 0.08 * deescalation,
                    "medium": 0.18 + 0.10 * deescalation,
                    "high": 0.79 - 0.16 * deescalation + 0.08 * escalation,
                }
            ),
        }

    def _transition(
        self,
        previous: Dict[str, float] | None,
        transition_matrix: Dict[str, Dict[str, float]],
    ) -> Dict[str, float]:
        if previous is None:
            previous = {"low": 0.55, "medium": 0.33, "high": 0.12}
        return self._normalize({
            target: sum(previous[source] * transition_matrix[source][target] for source in self.STATES)
            for target in self.STATES
        })

    def _observation_likelihood(self, track: TrackState, base_score: float, factors: Dict[str, float]) -> Dict[str, float]:
        anomaly = factors.get("anomaly_factor", 0.0)
        closing = factors.get("closing_factor", 0.0)
        distance = factors.get("distance_factor", 0.0)
        graph = (track.metadata.get("st_gnn_inspired", {}) or {}).get("graph_influence", 0.0)
        quality = factors.get("quality_factor", 0.0)
        high_signal = clamp(
            0.36 * base_score
            + 0.18 * closing
            + 0.14 * distance
            + 0.10 * anomaly
            + 0.07 * graph
            + 0.15 * factors.get("type_factor", 0.0)
        )
        medium_signal = clamp(0.38 + 0.28 * base_score + 0.14 * quality - 0.10 * anomaly)
        low_signal = clamp(1.0 - 0.72 * high_signal - 0.24 * medium_signal)
        return {
            "low": 0.20 + 0.80 * low_signal,
            "medium": 0.20 + 0.80 * medium_signal,
            "high": 0.20 + 0.80 * high_signal,
        }

    def _risk_pattern_probabilities(self, track: TrackState, factors: Dict[str, float]) -> Dict[str, float]:
        return self._softmax(self._risk_pattern_utilities(track, factors))

    def _risk_pattern_utilities(self, track: TrackState, factors: Dict[str, float]) -> Dict[str, float]:
        graph = (track.metadata.get("st_gnn_inspired", {}) or {}).get("graph_influence", 0.0)
        anomaly = factors.get("anomaly_factor", 0.0)
        return {
            "asset_approach": (
                1.20 * factors.get("closing_factor", 0.0)
                + 1.05 * factors.get("distance_factor", 0.0)
                + 0.20 * factors.get("type_factor", 0.0)
            ),
            "surveillance_or_probe": (
                0.25 * factors.get("quality_factor", 0.0)
                + 0.15 * float(track.object_type in {"uav", "aircraft"})
                + 0.10 * factors.get("distance_factor", 0.0)
            ),
            "formation_coordination": (
                0.95 * graph
                + 0.28 * float(track.object_type in {"aircraft", "ship"})
            ),
            "anomalous_maneuver": (
                1.00 * anomaly
                + 0.20 * (1.0 - factors.get("quality_factor", 0.0))
            ),
            "transit_or_background": (
                0.55 * (1.0 - factors.get("distance_factor", 0.0))
                + 0.45 * (1.0 - factors.get("closing_factor", 0.0))
            ),
        }

    def _observation_reliability(self, track: TrackState, factors: Dict[str, float]) -> float:
        anomaly = factors.get("anomaly_factor", 0.0)
        quality = factors.get("quality_factor", track.track_quality)
        graph = (track.metadata.get("st_gnn_inspired", {}) or {}).get("graph_influence", 0.0)
        return clamp(
            0.28
            + 0.30 * quality
            + 0.14 * graph
            - 0.08 * anomaly
            - 0.04 * min(track.missed_count, 3)
        )

    def _risk_pattern_factor(self, probabilities: Dict[str, float]) -> float:
        return clamp(
            0.34 * probabilities.get("asset_approach", 0.0)
            + 0.22 * probabilities.get("surveillance_or_probe", 0.0)
            + 0.20 * probabilities.get("formation_coordination", 0.0)
            + 0.18 * probabilities.get("anomalous_maneuver", 0.0)
            + 0.06 * probabilities.get("transit_or_background", 0.0)
        )

    def _softmax(self, values: Dict[str, float]) -> Dict[str, float]:
        max_value = max(values.values(), default=0.0)
        exps = {key: pow(2.718281828459045, value - max_value) for key, value in values.items()}
        total = sum(exps.values()) or 1.0
        return {key: value / total for key, value in exps.items()}

    def _normalize(self, values: Dict[str, float]) -> Dict[str, float]:
        total = sum(max(0.0, value) for value in values.values())
        if total <= 1e-9:
            return {"low": 1.0, "medium": 0.0, "high": 0.0}
        return {key: max(0.0, value) / total for key, value in values.items()}

    def _normalize_any(self, values: Dict[str, float]) -> Dict[str, float]:
        total = sum(max(0.0, value) for value in values.values())
        if total <= 1e-9:
            return {key: 1.0 / max(len(values), 1) for key in values}
        return {key: max(0.0, value) / total for key, value in values.items()}

    def _entropy(self, values: Dict[str, float]) -> float:
        return -sum(value * math.log(max(value, 1e-9), 2) for value in values.values())
