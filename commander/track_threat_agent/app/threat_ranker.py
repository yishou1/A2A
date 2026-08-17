"""Risk-priority scoring for simulated tracks."""

from __future__ import annotations

import math
from typing import Any, Dict, Iterable, List
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

    def __init__(
        self,
        dbn_evaluator: DBNThreatEvaluator | None = None,
    ) -> None:
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
            prediction_context = self._prediction_risk_context(track, factors)
            extended_factors = {
                **factors,
                "dbn_low_prob": float(dbn_result["posterior"]["low"]),
                "dbn_medium_prob": float(dbn_result["posterior"]["medium"]),
                "dbn_high_prob": float(dbn_result["posterior"]["high"]),
                "dbn_state_factor": float(dbn_result["state_factor"]),
                "pattern_protected_zone_approach_prob": float(
                    dbn_result["risk_pattern_probabilities"]["protected_zone_approach"]
                ),
                "pattern_sustained_presence_prob": float(
                    dbn_result["risk_pattern_probabilities"]["sustained_presence"]
                ),
                "pattern_coordinated_motion_prob": float(
                    dbn_result["risk_pattern_probabilities"]["coordinated_motion"]
                ),
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
                        "prediction_risk_context": prediction_context,
                    },
                )
            )

        assessments.sort(key=lambda item: item.score, reverse=True)
        for index, assessment in enumerate(assessments, start=1):
            assessment.rank = index
        return assessments

    def _factors(
        self,
        track: TrackState,
        zone_lat: float,
        zone_lon: float,
        radius_m: float,
    ) -> Dict[str, float]:
        distance_m = haversine_m(track.lat, track.lon, zone_lat, zone_lon)
        prediction = self._prediction_summary(track, zone_lat, zone_lon, distance_m)

        distance_factor = clamp(1.0 - distance_m / (radius_m * 3.0))
        closing_delta = distance_m - prediction["uncertainty_adjusted_distance_m"]
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
            "predicted_distance_m_30s": prediction["predicted_distance_m_30s"],
            "predicted_min_distance_m": prediction["predicted_min_distance_m"],
            "uncertainty_adjusted_distance_m": prediction["uncertainty_adjusted_distance_m"],
            "predicted_closest_horizon_s": prediction["predicted_closest_horizon_s"],
            "prediction_confidence": prediction["prediction_confidence"],
            "prediction_uncertainty_radius_m": prediction["prediction_uncertainty_radius_m"],
            "prediction_path_used": prediction["prediction_path_used"],
        }

    def _evidence(self, track: TrackState, factors: Dict[str, float], score: float) -> List[str]:
        distance_m = factors["distance_m"]
        closing_delta = distance_m - factors["uncertainty_adjusted_distance_m"]
        context = self._prediction_risk_context(track, factors)
        evidence = [
            f"当前距重点区域 {distance_m:.0f} 米",
            (
                f"{context['model_label']} 预测在 {factors['predicted_closest_horizon_s']:.0f}s 时最接近，"
                f"距离 {factors['predicted_min_distance_m']:.0f} 米，折算后接近量 {closing_delta:.0f} 米"
            ),
            (
                f"预测置信度 {factors['prediction_confidence']:.2f}，"
                f"不确定性半径 {factors['prediction_uncertainty_radius_m']:.0f} 米"
            ),
            f"目标类型 {track.object_type} 的态势关注权重为 {factors['type_factor']:.2f}",
            f"航迹质量因子为 {factors['quality_factor']:.2f}",
        ]
        if factors["anomaly_factor"] > 0:
            evidence.append(f"异常机动将态势关注因子提高到 {factors['anomaly_factor']:.2f}")
        if score >= 0.72:
            evidence.append("多项态势关注因子同时较高，因此排序分数较高")
        elif score >= 0.45:
            evidence.append("部分态势关注因子升高，因此排序分数居中")
        else:
            evidence.append("当前接近程度、趋近趋势或异常因子有限，因此排序分数较低")
        return evidence

    def _prediction_summary(
        self,
        track: TrackState,
        zone_lat: float,
        zone_lon: float,
        current_distance_m: float,
    ) -> Dict[str, float]:
        candidates: List[Dict[str, float]] = []
        for point in track.predicted_path:
            try:
                lat = float(point["lat"])
                lon = float(point["lon"])
                horizon_s = max(0.0, float(point.get("dt_s", 0.0)))
                confidence = clamp(float(point.get("prediction_confidence", track.track_quality)))
                uncertainty_m = max(0.0, float(point.get("uncertainty_radius_m", 0.0)))
            except (KeyError, TypeError, ValueError):
                continue
            if not all(math.isfinite(value) for value in (lat, lon, horizon_s, confidence, uncertainty_m)):
                continue
            raw_distance_m = haversine_m(lat, lon, zone_lat, zone_lon)
            adjusted_distance_m = max(0.0, raw_distance_m - uncertainty_m * (1.0 - confidence))
            candidates.append(
                {
                    "horizon_s": horizon_s,
                    "raw_distance_m": raw_distance_m,
                    "adjusted_distance_m": adjusted_distance_m,
                    "confidence": confidence,
                    "uncertainty_m": uncertainty_m,
                }
            )

        if not candidates:
            predicted_lat, predicted_lon = project_position(track.lat, track.lon, track.vx, track.vy, 30.0)
            raw_distance_m = haversine_m(predicted_lat, predicted_lon, zone_lat, zone_lon)
            candidates = [
                {
                    "horizon_s": 30.0,
                    "raw_distance_m": raw_distance_m,
                    "adjusted_distance_m": raw_distance_m,
                    "confidence": clamp(track.track_quality),
                    "uncertainty_m": 0.0,
                }
            ]
            path_used = 0.0
        else:
            path_used = 1.0

        closest = min(candidates, key=lambda item: (item["adjusted_distance_m"], item["horizon_s"]))
        point_30s = min(candidates, key=lambda item: abs(item["horizon_s"] - 30.0))
        return {
            "predicted_distance_m_30s": point_30s["raw_distance_m"],
            "predicted_min_distance_m": closest["raw_distance_m"],
            "uncertainty_adjusted_distance_m": min(closest["adjusted_distance_m"], current_distance_m),
            "predicted_closest_horizon_s": closest["horizon_s"],
            "prediction_confidence": closest["confidence"],
            "prediction_uncertainty_radius_m": closest["uncertainty_m"],
            "prediction_path_used": path_used,
        }

    def _prediction_risk_context(self, track: TrackState, factors: Dict[str, float]) -> Dict[str, Any]:
        horizon_s = float(factors.get("predicted_closest_horizon_s", 30.0))
        point = min(
            track.predicted_path,
            key=lambda item: abs(float(item.get("dt_s", 0.0)) - horizon_s),
            default={},
        )
        model_used = str(point.get("model_used") or point.get("prediction_model") or "constant_velocity_fallback")
        if "st_gnn" in model_used.lower() or "st-gnn" in model_used.lower():
            model_label = "ST-GNN"
        elif "adaptive_multi_model" in model_used.lower():
            model_label = "自适应物理多假设融合"
        else:
            model_label = model_used
        return {
            "model_used": model_used,
            "model_label": model_label,
            "model_version": point.get("model_version"),
            "closest_horizon_s": horizon_s,
            "prediction_path_used": bool(factors.get("prediction_path_used", 0.0)),
            "uncertainty_policy": "lower_confidence_bound_for_attention_priority",
        }
