"""Formation and group detection for simulated tracks."""

from __future__ import annotations

from typing import Dict, Iterable, List, Set
from uuid import uuid4

from .models import ThreatAssessment, TrackGroup, TrackState
from .utils import average_point, bounding_box, clamp, haversine_m, heading_difference_deg, risk_level


class GroupDetector:
    """Detects likely simulated formations using distance, heading, and speed similarity."""

    def __init__(
        self,
        max_distance_m: float = 3_500.0,
        max_heading_diff_deg: float = 25.0,
        max_speed_diff_mps: float = 18.0,
    ) -> None:
        self.max_distance_m = max_distance_m
        self.max_heading_diff_deg = max_heading_diff_deg
        self.max_speed_diff_mps = max_speed_diff_mps
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
        threat_by_track = {threat.track_id: threat for threat in threats or []}
        adjacency = {track.track_id: set() for track in track_list}

        for i, left in enumerate(track_list):
            for right in track_list[i + 1 :]:
                if self._related(left, right):
                    adjacency[left.track_id].add(right.track_id)
                    adjacency[right.track_id].add(left.track_id)

        by_id = {track.track_id: track for track in track_list}
        components = self._components(adjacency)
        groups = []
        for component in components:
            if len(component) < 2:
                continue
            members = [by_id[track_id] for track_id in sorted(component)]
            group_type = self._group_type(members)
            group_id = self._reuse_group_id(component, group_type)
            groups.append(self._build_group(members, threat_by_track, scene_context or {}, group_id, group_type))

        self.groups = {group.group_id: group for group in groups}
        return groups

    def _related(self, left: TrackState, right: TrackState) -> bool:
        distance_m = haversine_m(left.lat, left.lon, right.lat, right.lon)
        heading_diff = heading_difference_deg(left.heading, right.heading)
        speed_diff = abs(left.speed - right.speed)
        return (
            distance_m <= self.max_distance_m
            and heading_diff <= self.max_heading_diff_deg
            and speed_diff <= self.max_speed_diff_mps
        )

    def _components(self, adjacency: Dict[str, Set[str]]) -> List[Set[str]]:
        seen: Set[str] = set()
        components = []
        for node in adjacency:
            if node in seen:
                continue
            stack = [node]
            component = set()
            while stack:
                current = stack.pop()
                if current in seen:
                    continue
                seen.add(current)
                component.add(current)
                stack.extend(adjacency[current] - seen)
            components.append(component)
        return components

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
        predicted_envelope = bounding_box(predicted_points)
        motion_cohesion = self._motion_cohesion_score(members)
        semantic_cohesion = self._semantic_cohesion_score(members)
        cohesion_score = clamp(0.82 * motion_cohesion + 0.18 * semantic_cohesion)
        group_score, factor_details = self._group_score(
            members, threat_by_track, scene_context, cohesion_score, group_type, semantic_cohesion
        )
        timestamp = max(member.last_update_time for member in members)
        evidence = [
            f"{len(members)} related tracks connected by distance, heading, and speed similarity",
            f"group type inferred as {group_type}",
            f"cohesion score is {cohesion_score:.2f}",
            f"semantic cohesion score is {semantic_cohesion:.2f}",
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
        )

    def _centroid_prediction(self, members: List[TrackState]) -> List[Dict[str, float]]:
        predictions = []
        for dt in (10.0, 20.0, 30.0, 60.0, 120.0):
            points = []
            timestamps = []
            for member in members:
                point = next((p for p in member.predicted_path if p.get("dt_s") == dt), None)
                if point is not None:
                    points.append(point)
                    timestamps.append(point.get("timestamp", member.last_update_time + dt))
            centroid = average_point(points)
            centroid["dt_s"] = dt
            centroid["timestamp"] = sum(timestamps) / len(timestamps) if timestamps else 0.0
            predictions.append(centroid)
        return predictions

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

    def _semantic_cohesion_score(self, members: List[TrackState]) -> float:
        if len(members) < 2:
            return 0.0
        pair_scores = []
        for i, left in enumerate(members):
            for right in members[i + 1 :]:
                pair_scores.append(self._pair_semantic_score(left, right))
        return clamp(sum(pair_scores) / len(pair_scores))

    def _pair_semantic_score(self, left: TrackState, right: TrackState) -> float:
        score = 0.0
        comparable = 0
        for key, weight in (("affiliation", 0.40), ("label", 0.30), ("threat_level", 0.20), ("source_class", 0.10)):
            left_value = str(left.metadata.get(key, "")).strip().lower()
            right_value = str(right.metadata.get(key, "")).strip().lower()
            if not left_value or not right_value:
                continue
            comparable += 1
            if left_value == right_value:
                score += weight
            elif "unknown" in {left_value, right_value}:
                score += weight * 0.35
        return clamp(score if comparable else 0.0)

    def _group_type(self, members: List[TrackState]) -> str:
        types = {member.object_type for member in members}
        if types == {"aircraft"}:
            return "air_formation"
        if types == {"ship"}:
            return "surface_group"
        if len(types) > 1:
            return "mixed_group"
        return "unknown_group"

    def _reuse_group_id(self, component: Set[str], group_type: str) -> str:
        best_id = ""
        best_score = 0.0
        for group_id, group in self.groups.items():
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
        semantic_cohesion: float,
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
            + 0.16 * cohesion_score
            + 0.10 * type_mix_factor
            + 0.04 * semantic_cohesion
        )
        details = {
            "max_member_score": round(max_member_score, 4),
            "size_factor": round(size_factor, 4),
            "closing_factor": round(closing_factor, 4),
            "cohesion_factor": round(cohesion_score, 4),
            "type_mix_factor": round(type_mix_factor, 4),
            "semantic_cohesion_factor": round(semantic_cohesion, 4),
        }
        return group_score, details

    def _group_closing_factor(self, members: List[TrackState], scene_context: Dict[str, float]) -> float:
        if not scene_context:
            return 0.5
        zone_lat = float(scene_context.get("protected_zone_lat", 0.0))
        zone_lon = float(scene_context.get("protected_zone_lon", 0.0))
        radius_m = max(float(scene_context.get("protected_radius_m", 20_000.0)), 1.0)
        current = average_point([{"lat": m.lat, "lon": m.lon, "alt": m.alt} for m in members])
        predicted = self._centroid_prediction(members)[-1]
        current_distance = haversine_m(current["lat"], current["lon"], zone_lat, zone_lon)
        predicted_distance = haversine_m(predicted["lat"], predicted["lon"], zone_lat, zone_lon)
        return clamp(((current_distance - predicted_distance) / radius_m + 0.5) / 1.5)
