"""DBN-inspired dynamic threat-state smoothing for demo attention ranking."""

from __future__ import annotations

from typing import Dict

from .models import TrackState
from .utils import clamp


class DBNThreatEvaluator:
    """Maintains low/medium/high state probabilities per track.

    This is a lightweight dynamic Bayesian network inspired layer. It uses a
    transition prior from the previous frame and a simple observation likelihood
    from current factors. It is designed for explainable demo behavior, not for
    real tactical decision making.
    """

    STATES = ("low", "medium", "high")

    def __init__(self) -> None:
        self._state_by_track: Dict[str, Dict[str, float]] = {}

    def reset(self) -> None:
        self._state_by_track.clear()

    def update(self, track: TrackState, base_score: float, factors: Dict[str, float]) -> Dict[str, object]:
        prior = self._transition(self._state_by_track.get(track.track_id))
        observation = self._observation_likelihood(track, base_score, factors)
        posterior = self._normalize({
            state: prior[state] * observation[state]
            for state in self.STATES
        })
        self._state_by_track[track.track_id] = posterior
        state_factor = posterior["medium"] * 0.55 + posterior["high"]
        smoothed_score = clamp(0.72 * base_score + 0.28 * state_factor)
        return {
            "prior": {key: round(value, 4) for key, value in prior.items()},
            "observation": {key: round(value, 4) for key, value in observation.items()},
            "posterior": {key: round(value, 4) for key, value in posterior.items()},
            "state_factor": round(state_factor, 4),
            "smoothed_score": round(smoothed_score, 4),
            "dominant_state": max(posterior, key=posterior.get),
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
        high_signal = clamp(0.45 * base_score + 0.22 * closing + 0.18 * distance + 0.10 * anomaly + 0.05 * graph)
        medium_signal = clamp(0.38 + 0.28 * base_score + 0.14 * quality - 0.10 * anomaly)
        low_signal = clamp(1.0 - 0.72 * high_signal - 0.24 * medium_signal)
        return {
            "low": 0.20 + 0.80 * low_signal,
            "medium": 0.20 + 0.80 * medium_signal,
            "high": 0.20 + 0.80 * high_signal,
        }

    def _normalize(self, values: Dict[str, float]) -> Dict[str, float]:
        total = sum(max(0.0, value) for value in values.values())
        if total <= 1e-9:
            return {"low": 1.0, "medium": 0.0, "high": 0.0}
        return {key: max(0.0, value) / total for key, value in values.items()}
