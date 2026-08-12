"""Protected-asset impact analysis for simulation-only situation awareness."""

from __future__ import annotations

from typing import Dict, Iterable, List
from uuid import uuid4

from .models import AssetImpactAssessment, ProtectedAsset, ThreatAssessment, TrackState
from .utils import clamp, haversine_m, risk_level


class AssetImpactAnalyzer:
    """Scores how tracked objects may affect protected assets in the demo.

    This module does not recommend actions. It only estimates attention priority
    for protected assets based on proximity, projected proximity, movement
    toward the asset, track anomaly metadata, and asset criticality.
    """

    TYPE_FACTORS = {
        "aircraft": 0.70,
        "ship": 0.55,
        "uav": 0.65,
        "unknown": 0.78,
    }

    def assess(
        self,
        tracks: Iterable[TrackState],
        threats: Iterable[ThreatAssessment],
        protected_assets: Iterable[ProtectedAsset],
    ) -> List[AssetImpactAssessment]:
        track_list = list(tracks)
        asset_list = list(protected_assets)
        threat_by_track = {threat.track_id: threat for threat in threats}
        now = max((track.last_update_time for track in track_list), default=0.0)
        impacts: List[AssetImpactAssessment] = []

        for asset in asset_list:
            for track in track_list:
                if track.metadata.get("status") == "lost":
                    continue
                factors = self._factors(track, asset, threat_by_track.get(track.track_id))
                score = clamp(
                    0.22 * factors["current_proximity_factor"]
                    + 0.21 * factors["predicted_proximity_factor"]
                    + 0.16 * factors["closing_factor"]
                    + 0.08 * factors["asset_criticality_factor"]
                    + 0.08 * factors["asset_priority_factor"]
                    + 0.06 * factors["asset_vulnerability_factor"]
                    + 0.10 * factors["track_attention_factor"]
                    + 0.07 * factors["anomaly_factor"]
                )
                if score < 0.18:
                    continue
                threat = threat_by_track.get(track.track_id)
                impacts.append(
                    AssetImpactAssessment(
                        impact_id=f"impact-{uuid4().hex[:10]}",
                        protected_asset_id=asset.asset_id,
                        protected_asset_name=asset.asset_name,
                        protected_asset_type=asset.asset_type,
                        source_track_id=track.track_id,
                        source_threat_id=threat.threat_id if threat else None,
                        source_object_type=track.object_type,
                        score=round(score, 4),
                        level=risk_level(score),
                        rank=0,
                        closest_distance_m=round(factors["current_distance_m"], 2),
                        predicted_closest_distance_m=round(factors["predicted_min_distance_m"], 2),
                        predicted_min_distance_margin_m=round(factors["predicted_min_distance_margin_m"], 2),
                        closest_time_s=round(factors["predicted_closest_time_s"], 2),
                        eta_to_protected_radius_s=(
                            round(factors["eta_to_protected_radius_s"], 2)
                            if factors["eta_to_protected_radius_s"] >= 0.0
                            else None
                        ),
                        will_enter_protection_radius=bool(factors["will_enter_protection_radius"]),
                        factors={key: round(value, 4) for key, value in factors.items()},
                        evidence=self._evidence(asset, track, factors, score),
                        timestamp=now,
                        metadata={
                            "asset_radius_m": asset.protection_radius_m,
                            "asset_priority": asset.priority,
                            "asset_vulnerability": asset.vulnerability,
                            "safety_boundary": "simulation-only protected asset impact; no engagement advice",
                        },
                    )
                )

        impacts.sort(key=lambda item: item.score, reverse=True)
        for rank, impact in enumerate(impacts, start=1):
            impact.rank = rank
        return impacts

    def _factors(
        self,
        track: TrackState,
        asset: ProtectedAsset,
        threat: ThreatAssessment | None,
    ) -> Dict[str, float]:
        radius = max(asset.protection_radius_m, 1.0)
        current_distance = haversine_m(track.lat, track.lon, asset.lat, asset.lon)
        predicted_candidates = [
            (
                haversine_m(point["lat"], point["lon"], asset.lat, asset.lon),
                float(point.get("dt_s", 0.0)),
            )
            for point in track.predicted_path
        ] or [(current_distance, 0.0)]
        predicted_min, predicted_closest_time_s = min(predicted_candidates, key=lambda item: item[0])
        radius_entry_times = [
            predicted_time
            for predicted_distance, predicted_time in predicted_candidates
            if predicted_distance <= radius
        ]
        currently_inside_radius = current_distance <= radius
        eta_to_radius = 0.0 if currently_inside_radius else (min(radius_entry_times) if radius_entry_times else -1.0)
        will_enter_radius = currently_inside_radius or bool(radius_entry_times)
        current_proximity = clamp(1.0 - current_distance / (radius * 4.0))
        predicted_proximity = clamp(1.0 - predicted_min / (radius * 4.0))
        closing_delta = current_distance - predicted_min
        closing_factor = clamp((closing_delta / radius + 0.5) / 1.5)
        anomaly = track.metadata.get("anomaly", {})
        anomaly_factor = 0.0
        if anomaly.get("heading_jump"):
            anomaly_factor += 0.40
        if anomaly.get("speed_jump"):
            anomaly_factor += 0.35
        if anomaly.get("low_confidence"):
            anomaly_factor += 0.25
        return {
            "current_distance_m": current_distance,
            "predicted_min_distance_m": predicted_min,
            "predicted_min_distance_margin_m": predicted_min - radius,
            "predicted_closest_time_s": predicted_closest_time_s,
            "eta_to_protected_radius_s": eta_to_radius,
            "will_enter_protection_radius": 1.0 if will_enter_radius else 0.0,
            "closing_delta_m": closing_delta,
            "current_proximity_factor": current_proximity,
            "predicted_proximity_factor": predicted_proximity,
            "closing_factor": closing_factor,
            "asset_criticality_factor": clamp(asset.criticality),
            "asset_priority_factor": clamp(asset.priority if asset.priority is not None else asset.criticality),
            "asset_vulnerability_factor": clamp(asset.vulnerability),
            "track_attention_factor": threat.score if threat else self.TYPE_FACTORS.get(track.object_type, 0.6),
            "type_factor": self.TYPE_FACTORS.get(track.object_type, 0.6),
            "anomaly_factor": clamp(anomaly_factor),
        }

    def _evidence(
        self,
        asset: ProtectedAsset,
        track: TrackState,
        factors: Dict[str, float],
        score: float,
    ) -> List[str]:
        evidence = [
            f"保护资产 {asset.asset_name} 与目标 {track.track_id} 当前距离约 {factors['current_distance_m']:.0f} 米",
            f"预测最近距离约 {factors['predicted_min_distance_m']:.0f} 米，最近时间约 T+{factors['predicted_closest_time_s']:.0f} 秒",
            f"保护资产重要度因子 {asset.criticality:.2f}，优先级因子 {factors['asset_priority_factor']:.2f}，脆弱性因子 {factors['asset_vulnerability_factor']:.2f}",
            f"目标类型 {track.object_type}，态势关注因子 {factors['track_attention_factor']:.2f}",
        ]
        if factors["closing_delta_m"] > 0:
            evidence.append(f"预测轨迹正在接近该资产，距离变化约 {factors['closing_delta_m']:.0f} 米")
        else:
            evidence.append("预测轨迹未显示持续接近该资产")
        if factors.get("will_enter_protection_radius", 0.0) >= 1.0:
            evidence.append(
                f"预测轨迹预计在 T+{factors['eta_to_protected_radius_s']:.0f} 秒进入保护半径，"
                f"最小距离裕度约 {factors['predicted_min_distance_margin_m']:.0f} 米"
            )
        else:
            evidence.append(
                f"预测轨迹未进入保护半径，最小距离裕度约 {factors['predicted_min_distance_margin_m']:.0f} 米"
            )
        if factors["anomaly_factor"] > 0:
            evidence.append(f"异常机动/低置信度使资产影响关注因子增加到 {factors['anomaly_factor']:.2f}")
        if score >= 0.72:
            evidence.append("综合分数为 high：建议在态势图中重点关注该资产周边情况，不代表交战建议")
        elif score >= 0.45:
            evidence.append("综合分数为 medium：建议持续观察该资产附近态势，不代表交战建议")
        else:
            evidence.append("综合分数为 low：当前对该资产的模拟影响关注较低")
        return evidence
