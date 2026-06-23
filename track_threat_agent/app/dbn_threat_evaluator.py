"""Dynamic Bayesian Network and COA threat-state evaluation."""

from __future__ import annotations

from typing import Dict

from .models import TrackState
from .utils import clamp


class DBNThreatEvaluator:
    """Maintains low/medium/high state probabilities per track.

    The evaluator maintains low/medium/high posterior probabilities per track.
    It uses a transition prior from the previous frame and an observation
    likelihood from current motion, quality, anomaly, and semantic factors.
    It only produces situation-awareness priority, not engagement decisions.
    """

    STATES = ("low", "medium", "high")

    def __init__(self) -> None:
        self._state_by_track: Dict[str, Dict[str, float]] = {}

    def reset(self) -> None:
        self._state_by_track.clear()

    def update(self, track: TrackState, base_score: float, factors: Dict[str, float]) -> Dict[str, object]:
        prior = self._transition(self._state_by_track.get(track.track_id))
        coa_probabilities = self._coa_probabilities(track, factors)
        observation = self._observation_likelihood(track, base_score, factors)
        posterior = self._normalize({
            state: prior[state] * observation[state]
            for state in self.STATES
        })
        self._state_by_track[track.track_id] = posterior
        state_factor = posterior["medium"] * 0.55 + posterior["high"]
        coa_risk = self._coa_risk_factor(coa_probabilities)
        smoothed_score = clamp(0.62 * base_score + 0.24 * state_factor + 0.14 * coa_risk)
        return {
            "prior": {key: round(value, 4) for key, value in prior.items()},
            "observation": {key: round(value, 4) for key, value in observation.items()},
            "posterior": {key: round(value, 4) for key, value in posterior.items()},
            "coa_probabilities": {key: round(value, 4) for key, value in coa_probabilities.items()},
            "coa_risk_factor": round(coa_risk, 4),
            "state_factor": round(state_factor, 4),
            "smoothed_score": round(smoothed_score, 4),
            "dominant_state": max(posterior, key=posterior.get),
            "dominant_coa": max(coa_probabilities, key=coa_probabilities.get),
        }

    def _transition(self, previous: Dict[str, float] | None) -> Dict[str, float]:
        if previous is None:
            previous = {"low": 0.55, "medium": 0.33, "high": 0.12}
        return self._normalize(
            {
                "low": previous["low"] * 0.74 + previous["medium"] * 0.18 + previous["high"] * 0.04,
                "medium": previous["low"] * 0.22 + previous["medium"] * 0.64 + previous["high"] * 0.24,
                "high": previous["low"] * 0.04 + previous["medium"] * 0.18 + previous["high"] * 0.72,
            }
        )

    def _observation_likelihood(self, track: TrackState, base_score: float, factors: Dict[str, float]) -> Dict[str, float]:
        anomaly = factors.get("anomaly_factor", 0.0)
        closing = factors.get("closing_factor", 0.0)
        distance = factors.get("distance_factor", 0.0)
        graph = (track.metadata.get("st_gnn_inspired", {}) or {}).get("graph_influence", 0.0)
        quality = factors.get("quality_factor", 0.0)
        semantic = factors.get("semantic_factor", 0.0)
        approach = factors.get("intent_asset_approach_prob", 0.0)
        high_signal = clamp(
            0.36 * base_score
            + 0.18 * closing
            + 0.14 * distance
            + 0.10 * anomaly
            + 0.07 * graph
            + 0.08 * semantic
            + 0.07 * approach
        )
        medium_signal = clamp(0.38 + 0.28 * base_score + 0.14 * quality - 0.10 * anomaly)
        low_signal = clamp(1.0 - 0.72 * high_signal - 0.24 * medium_signal)
        return {
            "low": 0.20 + 0.80 * low_signal,
            "medium": 0.20 + 0.80 * medium_signal,
            "high": 0.20 + 0.80 * high_signal,
        }

    def _coa_probabilities(self, track: TrackState, factors: Dict[str, float]) -> Dict[str, float]:
        graph = (track.metadata.get("st_gnn_inspired", {}) or {}).get("graph_influence", 0.0)
        anomaly = factors.get("anomaly_factor", 0.0)
        utilities = {
            "asset_approach": (
                1.20 * factors.get("closing_factor", 0.0)
                + 1.05 * factors.get("distance_factor", 0.0)
                + 0.80 * factors.get("intent_asset_approach_prob", 0.0)
                + 0.35 * factors.get("semantic_factor", 0.0)
            ),
            "surveillance_or_probe": (
                0.70 * factors.get("semantic_factor", 0.0)
                + 0.55 * factors.get("intent_surveillance_prob", 0.0)
                + 0.25 * factors.get("quality_factor", 0.0)
                + 0.15 * float(track.object_type in {"uav", "aircraft"})
            ),
            "formation_coordination": (
                0.95 * graph
                + 0.70 * factors.get("intent_formation_prob", 0.0)
                + 0.28 * float(track.object_type in {"aircraft", "ship"})
            ),
            "anomalous_maneuver": (
                1.00 * anomaly
                + 0.65 * factors.get("intent_anomalous_maneuver_prob", 0.0)
                + 0.20 * (1.0 - factors.get("quality_factor", 0.0))
            ),
            "transit_or_background": (
                0.55 * (1.0 - factors.get("distance_factor", 0.0))
                + 0.45 * (1.0 - factors.get("closing_factor", 0.0))
                + 0.20 * (1.0 - factors.get("semantic_factor", 0.0))
            ),
        }
        return self._softmax(utilities)

    def _coa_risk_factor(self, probabilities: Dict[str, float]) -> float:
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
