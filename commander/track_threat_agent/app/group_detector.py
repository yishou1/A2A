"""Formation and group detection for simulated tracks."""

from __future__ import annotations

import math
from typing import Any, Dict, Iterable, List, Set
from uuid import uuid4

from .models import ThreatAssessment, TrackGroup, TrackState
from .utils import average_point, bounding_box, clamp, haversine_m, heading_difference_deg, meters_to_lat_lon_delta, risk_level


class GroupDetector:
    """Detects likely simulated formations using distance, heading, and speed similarity."""

    def __init__(
        self,
        max_distance_m: float = 3_500.0,
        max_heading_diff_deg: float = 25.0,
        max_speed_diff_mps: float = 18.0,
        confirmation_hits: int = 2,
        max_missed_frames: int = 2,
    ) -> None:
        self.max_distance_m = max_distance_m
        self.max_heading_diff_deg = max_heading_diff_deg
        self.max_speed_diff_mps = max_speed_diff_mps
        self.confirmation_hits = max(1, int(confirmation_hits))
        self.max_missed_frames = max(0, int(max_missed_frames))
        self.groups: Dict[str, TrackGroup] = {}

    def reset(self) -> None:
        self.groups.clear()

    def detect(
        self,
        tracks: Iterable[TrackState],
        threats: Iterable[ThreatAssessment] | None = None,
        scene_context: Dict[str, float] | None = None,
    ) -> List[TrackGroup]:
        track_list = [t for t in tracks if t.metadata.get("status", "active") != "lost"]
        for track in track_list:
            track.metadata.pop("physical_group_context", None)
        threat_by_track = {threat.track_id: threat for threat in threats or []}
        by_id = {track.track_id: track for track in track_list}
        components = self._complete_link_components(track_list)
        previous_groups = dict(self.groups)
        groups: List[TrackGroup] = []
        reused_group_ids: Set[str] = set()
        for component in components:
            if len(component) < 2:
                continue
            members = [by_id[track_id] for track_id in sorted(component)]
            group_type = self._group_type(members)
            group_id = self._reuse_group_id(component, group_type, reused_group_ids)
            group = self._build_group(members, threat_by_track, scene_context or {}, group_id, group_type)
            self._mark_observed_group(group, previous_groups.get(group_id))
            reused_group_ids.add(group_id)
            groups.append(group)

        for group_id, previous in previous_groups.items():
            if group_id in reused_group_ids:
                continue
            coasting = self._coasting_group(previous)
            if coasting is not None:
                groups.append(coasting)

        self.groups = {group.group_id: group for group in groups}
        self._write_physical_group_context(track_list, groups)
        return groups

    def diagnostics(self) -> Dict[str, Any]:
        lifecycle_counts: Dict[str, int] = {}
        for group in self.groups.values():
            state = str(group.metadata.get("lifecycle_state", "unknown"))
            lifecycle_counts[state] = lifecycle_counts.get(state, 0) + 1
        return {
            "active_group_count": len(self.groups),
            "lifecycle_counts": lifecycle_counts,
            "confirmation_hits": self.confirmation_hits,
            "max_missed_frames": self.max_missed_frames,
        }

    def _related(self, left: TrackState, right: TrackState) -> bool:
        distance_m = haversine_m(left.lat, left.lon, right.lat, right.lon)
        heading_diff = heading_difference_deg(left.heading, right.heading)
        speed_diff = abs(left.speed - right.speed)
        return (
            distance_m <= self.max_distance_m
            and heading_diff <= self.max_heading_diff_deg
            and speed_diff <= self.max_speed_diff_mps
        )

    def _complete_link_components(self, tracks: List[TrackState]) -> List[Set[str]]:
        """Cluster tracks only when every cross-cluster pair satisfies the relation gate."""
        by_id = {track.track_id: track for track in tracks}
        clusters: List[Set[str]] = [{track_id} for track_id in sorted(by_id)]
        while True:
            merge_candidates = []
            for left_index, left_cluster in enumerate(clusters):
                for right_index in range(left_index + 1, len(clusters)):
                    right_cluster = clusters[right_index]
                    cross_pairs = [
                        (by_id[left_id], by_id[right_id])
                        for left_id in left_cluster
                        for right_id in right_cluster
                    ]
                    if not cross_pairs or not all(self._related(left, right) for left, right in cross_pairs):
                        continue
                    similarity = sum(self._pair_similarity(left, right) for left, right in cross_pairs) / len(cross_pairs)
                    identity = tuple(sorted(left_cluster | right_cluster))
                    merge_candidates.append((-similarity, identity, left_index, right_index))
            if not merge_candidates:
                break
            _, _, left_index, right_index = min(merge_candidates)
            clusters[left_index] = clusters[left_index] | clusters[right_index]
            del clusters[right_index]
        return clusters

    def _pair_similarity(self, left: TrackState, right: TrackState) -> float:
        distance_score = 1.0 - haversine_m(left.lat, left.lon, right.lat, right.lon) / self.max_distance_m
        heading_score = 1.0 - heading_difference_deg(left.heading, right.heading) / self.max_heading_diff_deg
        speed_score = 1.0 - abs(left.speed - right.speed) / self.max_speed_diff_mps
        return clamp((distance_score + heading_score + speed_score) / 3.0)

    def _build_group(
        self,
        members: List[TrackState],
        threat_by_track: Dict[str, ThreatAssessment],
        scene_context: Dict[str, float],
        group_id: str,
        group_type: str,
    ) -> TrackGroup:
        current_points = [{"lat": m.lat, "lon": m.lon, "alt": m.alt} for m in members]
        centroid = average_point(current_points)
        prediction_steps = self._centroid_prediction(members)
        envelope = bounding_box(current_points)
        predicted_points = []
        for member in members:
            predicted_points.extend(member.predicted_path or [{"lat": member.lat, "lon": member.lon, "alt": member.alt}])
        predicted_envelope = self._uncertainty_expanded_envelope(predicted_points)
        motion_cohesion = self._motion_cohesion_score(members)
        cohesion_score = motion_cohesion
        group_score, factor_details = self._group_score(
            members, threat_by_track, scene_context, cohesion_score, group_type
        )
        timestamp = max(member.last_update_time for member in members)
        evidence = [
            f"{len(members)} related tracks connected by distance, heading, and speed similarity",
            f"group type inferred as {group_type}",
            f"cohesion score is {cohesion_score:.2f}",
            f"predicted envelope includes up to {predicted_envelope['uncertainty_expansion_m']:.0f} m uncertainty expansion",
            "group score is a demo attention-priority score, not an engagement recommendation",
            f"group factor details: {factor_details}",
        ]
        return TrackGroup(
            group_id=group_id,
            group_type=group_type,
            member_track_ids=[member.track_id for member in members],
            centroid=centroid,
            centroid_prediction=prediction_steps,
            envelope=envelope,
            predicted_envelope=predicted_envelope,
            cohesion_score=round(cohesion_score, 4),
            group_threat_score=round(group_score, 4),
            group_threat_level=risk_level(group_score),
            evidence=evidence,
            timestamp=timestamp,
            metadata={},
        )

    def _mark_observed_group(self, group: TrackGroup, previous: TrackGroup | None) -> None:
        previous_metadata = previous.metadata if previous is not None else {}
        hit_count = int(previous_metadata.get("hit_count", 0)) + 1
        confirmed_once = bool(previous_metadata.get("confirmed_once")) or hit_count >= self.confirmation_hits
        lifecycle_state = "confirmed" if confirmed_once else "tentative"
        previous_members = set(previous.member_track_ids) if previous is not None else set()
        current_members = set(group.member_track_ids)
        group.metadata = {
            "lifecycle_state": lifecycle_state,
            "hit_count": hit_count,
            "consecutive_hit_count": int(previous_metadata.get("consecutive_hit_count", 0)) + 1,
            "missed_count": 0,
            "confirmed_once": confirmed_once,
            "first_observed_time": float(previous_metadata.get("first_observed_time", group.timestamp)),
            "last_observed_time": group.timestamp,
            "member_change": {
                "added": sorted(current_members - previous_members),
                "removed": sorted(previous_members - current_members),
            },
        }
        group.evidence.append(
            f"群组生命周期状态为 {lifecycle_state}，累计命中 {hit_count} 帧"
        )

    def _coasting_group(self, previous: TrackGroup) -> TrackGroup | None:
        metadata = previous.metadata or {}
        if not bool(metadata.get("confirmed_once")):
            return None
        missed_count = int(metadata.get("missed_count", 0)) + 1
        if missed_count > self.max_missed_frames:
            return None
        group = previous.model_copy(deep=True)
        group.metadata.update(
            {
                "lifecycle_state": "coasting",
                "consecutive_hit_count": 0,
                "missed_count": missed_count,
            }
        )
        group.evidence = [
            evidence
            for evidence in group.evidence
            if not evidence.startswith("群组本帧未满足关联门限")
        ]
        group.evidence.append(
            f"群组本帧未满足关联门限，处于短时保持状态（{missed_count}/{self.max_missed_frames} 帧）"
        )
        return group

    @staticmethod
    def _write_physical_group_context(
        tracks: List[TrackState],
        groups: List[TrackGroup],
    ) -> None:
        by_track = {track.track_id: track for track in tracks}
        for group in groups:
            for track_id in group.member_track_ids:
                track = by_track.get(track_id)
                if track is None:
                    continue
                track.metadata["physical_group_context"] = {
                    "group_id": group.group_id,
                    "group_type": group.group_type,
                    "cohesion_score": group.cohesion_score,
                    "lifecycle_state": group.metadata.get("lifecycle_state", "unknown"),
                    "member_count": len(group.member_track_ids),
                }

    def _centroid_prediction(self, members: List[TrackState]) -> List[Dict[str, Any]]:
        predictions = []
        horizons = sorted(
            {
                float(point.get("dt_s", 0.0))
                for member in members
                for point in member.predicted_path
                if float(point.get("dt_s", 0.0)) > 0.0
            }
        )
        for dt in horizons:
            points = []
            timestamps = []
            confidences = []
            uncertainties = []
            model_versions = set()
            models_used = set()
            for member in members:
                point = next((p for p in member.predicted_path if p.get("dt_s") == dt), None)
                if point is not None:
                    points.append(point)
                    timestamps.append(point.get("timestamp", member.last_update_time + dt))
                    confidences.append(float(point.get("prediction_confidence", member.track_quality)))
                    uncertainties.append(max(0.0, float(point.get("uncertainty_radius_m", 0.0))))
                    if point.get("model_version"):
                        model_versions.add(str(point["model_version"]))
                    if point.get("model_used"):
                        models_used.add(str(point["model_used"]))
            if not points:
                continue
            centroid = average_point(points)
            centroid["dt_s"] = dt
            centroid["timestamp"] = sum(timestamps) / len(timestamps) if timestamps else 0.0
            centroid["prediction_confidence"] = round(sum(confidences) / len(confidences), 4)
            centroid["uncertainty_radius_m"] = round(
                math.sqrt(sum(value * value for value in uncertainties) / len(uncertainties)),
                2,
            )
            centroid["model_versions"] = sorted(model_versions)
            centroid["models_used"] = sorted(models_used)
            centroid["member_prediction_count"] = len(points)
            predictions.append(centroid)
        return predictions

    def _uncertainty_expanded_envelope(self, points: List[Dict[str, Any]]) -> Dict[str, float]:
        if not points:
            return {**bounding_box([]), "uncertainty_expansion_m": 0.0}
        expanded_points = []
        maximum_uncertainty = 0.0
        for point in points:
            lat = float(point.get("lat", 0.0))
            lon = float(point.get("lon", 0.0))
            uncertainty_m = max(0.0, float(point.get("uncertainty_radius_m", 0.0)))
            maximum_uncertainty = max(maximum_uncertainty, uncertainty_m)
            delta_lat, delta_lon = meters_to_lat_lon_delta(uncertainty_m, uncertainty_m, lat)
            expanded_points.extend(
                [
                    {"lat": lat - delta_lat, "lon": lon - delta_lon},
                    {"lat": lat + delta_lat, "lon": lon + delta_lon},
                ]
            )
        return {
            **bounding_box(expanded_points),
            "uncertainty_expansion_m": round(maximum_uncertainty, 2),
        }

    def _motion_cohesion_score(self, members: List[TrackState]) -> float:
        if len(members) < 2:
            return 0.0
        pair_scores = []
        for i, left in enumerate(members):
            for right in members[i + 1 :]:
                distance_score = 1.0 - haversine_m(left.lat, left.lon, right.lat, right.lon) / self.max_distance_m
                heading_score = 1.0 - heading_difference_deg(left.heading, right.heading) / self.max_heading_diff_deg
                speed_score = 1.0 - abs(left.speed - right.speed) / self.max_speed_diff_mps
                pair_scores.append(clamp((distance_score + heading_score + speed_score) / 3.0))
        return clamp(sum(pair_scores) / len(pair_scores))

    def _group_type(self, members: List[TrackState]) -> str:
        types = {member.object_type for member in members}
        if types == {"aircraft"}:
            return "air_formation"
        if types == {"ship"}:
            return "surface_group"
        if len(types) > 1:
            return "mixed_group"
        return "unknown_group"

    def _reuse_group_id(
        self,
        component: Set[str],
        group_type: str,
        reserved_group_ids: Set[str] | None = None,
    ) -> str:
        best_id = ""
        best_score = 0.0
        for group_id, group in self.groups.items():
            if group_id in (reserved_group_ids or set()):
                continue
            if group.group_type != group_type:
                continue
            previous = set(group.member_track_ids)
            union_size = len(component | previous)
            if union_size == 0:
                continue
            score = len(component & previous) / union_size
            if score > best_score:
                best_score = score
                best_id = group_id
        if best_score >= 0.5:
            return best_id
        return f"grp-{uuid4().hex[:10]}"

    def _group_score(
        self,
        members: List[TrackState],
        threat_by_track: Dict[str, ThreatAssessment],
        scene_context: Dict[str, float],
        cohesion_score: float,
        group_type: str,
    ) -> tuple[float, Dict[str, float]]:
        member_scores = [threat_by_track.get(member.track_id).score for member in members if member.track_id in threat_by_track]
        max_member_score = max(member_scores, default=max((member.track_quality for member in members), default=0.0) * 0.45)
        size_factor = clamp(len(members) / 5.0)
        closing_factor = self._group_closing_factor(members, scene_context)
        type_mix_factor = 0.75 if group_type == "mixed_group" else 0.55
        if group_type in {"air_formation", "surface_group"}:
            type_mix_factor = 0.65
        group_score = clamp(
            0.30 * max_member_score
            + 0.20 * size_factor
            + 0.20 * closing_factor
            + 0.20 * cohesion_score
            + 0.10 * type_mix_factor
        )
        details = {
            "max_member_score": round(max_member_score, 4),
            "size_factor": round(size_factor, 4),
            "closing_factor": round(closing_factor, 4),
            "cohesion_factor": round(cohesion_score, 4),
            "type_mix_factor": round(type_mix_factor, 4),
        }
        return group_score, details

    def _group_closing_factor(self, members: List[TrackState], scene_context: Dict[str, float]) -> float:
        if not scene_context:
            return 0.5
        zone_lat = float(scene_context.get("protected_zone_lat", 0.0))
        zone_lon = float(scene_context.get("protected_zone_lon", 0.0))
        radius_m = max(float(scene_context.get("protected_radius_m", 20_000.0)), 1.0)
        current = average_point([{"lat": m.lat, "lon": m.lon, "alt": m.alt} for m in members])
        current_distance = haversine_m(current["lat"], current["lon"], zone_lat, zone_lon)
        predictions = self._centroid_prediction(members)
        if not predictions:
            return 0.5
        predicted_distance = min(
            haversine_m(predicted["lat"], predicted["lon"], zone_lat, zone_lon)
            for predicted in predictions
        )
        return clamp(((current_distance - predicted_distance) / radius_m + 0.5) / 1.5)
