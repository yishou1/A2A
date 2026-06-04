"""Risk-priority scoring for simulated tracks."""

from __future__ import annotations

from typing import Dict, Iterable, List
from uuid import uuid4

from .dbn_threat_evaluator import DBNThreatEvaluator
from .models import ThreatAssessment, TrackState
from .utils import clamp, haversine_m, project_position, risk_level
from .xai_explainer import XAIExplanationBuilder


class ThreatRanker:
    """Ranks tracks by simulated situation-awareness priority."""

    TYPE_WEIGHTS = {
        "aircraft": 0.72,
        "ship": 0.55,
        "uav": 0.62,
        "unknown": 0.78,
    }

    def __init__(self, dbn_evaluator: DBNThreatEvaluator | None = None) -> None:
        self.dbn_evaluator = dbn_evaluator or DBNThreatEvaluator()
        self.explainer = XAIExplanationBuilder()

    def reset(self) -> None:
        self.dbn_evaluator.reset()

    def rank(self, tracks: Iterable[TrackState], scene_context: Dict[str, float] | None = None) -> List[ThreatAssessment]:
        track_list = list(tracks)
        scene = scene_context or {}
        zone_lat = float(scene.get("protected_zone_lat", 0.0))
        zone_lon = float(scene.get("protected_zone_lon", 0.0))
        radius_m = max(float(scene.get("protected_radius_m", 20_000.0)), 1.0)
        assessments = []
        now = max((track.last_update_time for track in track_list), default=0.0)

        for track in track_list:
            factors = self._factors(track, zone_lat, zone_lon, radius_m)
            score = clamp(
                0.28 * factors["distance_factor"]
                + 0.24 * factors["closing_factor"]
                + 0.18 * factors["type_factor"]
                + 0.18 * factors["anomaly_factor"]
                + 0.12 * factors["quality_factor"]
            )
            dbn_result = self.dbn_evaluator.update(track, score, factors)
            final_score = float(dbn_result["smoothed_score"])
            xai_metadata = self.explainer.threat_metadata(track, factors, score, dbn_result)
            extended_factors = {
                **factors,
                "dbn_low_prob": float(dbn_result["posterior"]["low"]),
                "dbn_medium_prob": float(dbn_result["posterior"]["medium"]),
                "dbn_high_prob": float(dbn_result["posterior"]["high"]),
                "dbn_state_factor": float(dbn_result["state_factor"]),
            }
            assessments.append(
                ThreatAssessment(
                    threat_id=f"thr-{uuid4().hex[:10]}",
                    track_id=track.track_id,
                    score=round(final_score, 4),
                    level=risk_level(final_score),
                    rank=0,
                    factors={key: round(value, 4) for key, value in extended_factors.items()},
                    evidence=self._evidence(track, factors, final_score) + xai_metadata["xai"]["evidence_chain"],
                    timestamp=now,
                    metadata={
                        **xai_metadata,
                        "dbn": dbn_result,
                        "weighted_score_before_dbn": round(score, 4),
                    },
                )
            )

        assessments.sort(key=lambda item: item.score, reverse=True)
        for index, assessment in enumerate(assessments, start=1):
            assessment.rank = index
        return assessments

    def _factors(self, track: TrackState, zone_lat: float, zone_lon: float, radius_m: float) -> Dict[str, float]:
        distance_m = haversine_m(track.lat, track.lon, zone_lat, zone_lon)
        predicted_lat, predicted_lon = project_position(track.lat, track.lon, track.vx, track.vy, 30.0)
        predicted_distance_m = haversine_m(predicted_lat, predicted_lon, zone_lat, zone_lon)

        distance_factor = clamp(1.0 - distance_m / (radius_m * 3.0))
        closing_delta = distance_m - predicted_distance_m
        closing_factor = clamp((closing_delta / max(radius_m, 1.0) + 0.5) / 1.5)
        type_factor = self.TYPE_WEIGHTS.get(track.object_type, 0.6)
        anomaly = track.metadata.get("anomaly", {})
        anomaly_factor = 0.0
        if anomaly.get("heading_jump"):
            anomaly_factor += 0.4
        if anomaly.get("speed_jump"):
            anomaly_factor += 0.35
        if anomaly.get("low_confidence"):
            anomaly_factor += 0.25
        quality_factor = clamp(track.track_quality)
        return {
            "distance_factor": distance_factor,
            "closing_factor": closing_factor,
            "type_factor": type_factor,
            "anomaly_factor": clamp(anomaly_factor),
            "quality_factor": quality_factor,
            "distance_m": distance_m,
            "predicted_distance_m_30s": predicted_distance_m,
        }

    def _evidence(self, track: TrackState, factors: Dict[str, float], score: float) -> List[str]:
        distance_m = factors["distance_m"]
        closing_delta = distance_m - factors["predicted_distance_m_30s"]
        evidence = [
            f"distance to protected zone is {distance_m:.0f} m",
            f"30s projection changes distance by {closing_delta:.0f} m",
            f"object type {track.object_type} uses demo weight {factors['type_factor']:.2f}",
            f"track quality contributes {factors['quality_factor']:.2f}",
        ]
        if factors["anomaly_factor"] > 0:
            evidence.append(f"anomaly metadata raises attention factor to {factors['anomaly_factor']:.2f}")
        if score >= 0.72:
            evidence.append("score is high because multiple simulated attention factors are elevated")
        elif score >= 0.45:
            evidence.append("score is medium because some attention factors are elevated")
        else:
            evidence.append("score is low because proximity, closing, or anomaly factors are limited")
        return evidence
