"""Formation and group detection for simulated tracks."""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any, Dict, Iterable, List, Set
from uuid import uuid4

from .models import ThreatAssessment, TrackGroup, TrackState
from .utils import average_point, bounding_box, clamp, haversine_m, heading_difference_deg, meters_to_lat_lon_delta, risk_level


@dataclass(frozen=True)
class AssociationProfile:
    """Configurable association gates for one homogeneous target type."""

    max_distance_m: float
    max_heading_diff_deg: float
    max_speed_diff_mps: float


# These are simulation association gates, not operational formation standards.
DEFAULT_ASSOCIATION_PROFILES: Dict[str, AssociationProfile] = {
    "aircraft": AssociationProfile(3_500.0, 25.0, 18.0),
    "uav": AssociationProfile(1_200.0, 30.0, 10.0),
    "ship": AssociationProfile(8_000.0, 20.0, 5.0),
    "unknown": AssociationProfile(3_500.0, 25.0, 18.0),
}


class GroupDetector:
    """Detects likely simulated formations using distance, heading, and speed similarity."""

    def __init__(
        self,
        max_distance_m: float | None = None,
        max_heading_diff_deg: float | None = None,
        max_speed_diff_mps: float | None = None,
        confirmation_hits: int = 2,
        max_missed_frames: int = 2,
        member_exit_misses: int = 2,
        association_profiles: Dict[str, AssociationProfile | Dict[str, float]] | None = None,
    ) -> None:
        self.association_profiles = self._build_association_profiles(
            max_distance_m=max_distance_m,
            max_heading_diff_deg=max_heading_diff_deg,
            max_speed_diff_mps=max_speed_diff_mps,
            overrides=association_profiles,
        )
        # Keep these attributes for compatibility with callers that configure a global profile.
        default_profile = self.association_profiles["unknown"]
        self.max_distance_m = default_profile.max_distance_m
        self.max_heading_diff_deg = default_profile.max_heading_diff_deg
        self.max_speed_diff_mps = default_profile.max_speed_diff_mps
        self.confirmation_hits = max(1, int(confirmation_hits))
        self.max_missed_frames = max(0, int(max_missed_frames))
        self.member_exit_misses = max(1, int(member_exit_misses))
        self.groups: Dict[str, TrackGroup] = {}
        self._last_input_track_count = 0
        self._last_eligible_track_count = 0
        self._last_excluded_non_active_count = 0
        self._last_excluded_duplicate_source_count = 0

    def reset(self) -> None:
        self.groups.clear()
        self._last_input_track_count = 0
        self._last_eligible_track_count = 0
        self._last_excluded_non_active_count = 0
        self._last_excluded_duplicate_source_count = 0

    def detect(
        self,
        tracks: Iterable[TrackState],
        threats: Iterable[ThreatAssessment] | None = None,
        scene_context: Dict[str, float] | None = None,
    ) -> List[TrackGroup]:
        track_list = self._eligible_tracks(list(tracks))
        for track in track_list:
            track.metadata.pop("physical_group_context", None)
        threat_by_track = {threat.track_id: threat for threat in threats or []}
        by_id = {track.track_id: track for track in track_list}
        components = self._complete_link_components(track_list)
        previous_groups = dict(self.groups)
        candidate_groups: List[tuple[str, str, List[TrackState]]] = []
        reused_group_ids: Set[str] = set()
        for component in components:
            if len(component) < 2:
                continue
            members = [by_id[track_id] for track_id in sorted(component)]
            group_type = self._group_type(members)
            group_id = self._reuse_group_id(component, group_type, reused_group_ids)
            reused_group_ids.add(group_id)
            candidate_groups.append((group_id, group_type, members))

        observed_member_ids = {
            member.track_id
            for _, _, members in candidate_groups
            for member in members
        }
        groups: List[TrackGroup] = []
        for group_id, group_type, members in candidate_groups:
            previous = previous_groups.get(group_id)
            resolved_members, held_member_ids, member_relation_misses = self._apply_member_exit_hysteresis(
                members,
                previous,
                by_id,
                observed_member_ids,
            )
            group = self._build_group(
                resolved_members,
                threat_by_track,
                scene_context or {},
                group_id,
                self._group_type(resolved_members),
            )
            self._mark_observed_group(
                group,
                previous,
                held_member_ids=held_member_ids,
                member_relation_misses=member_relation_misses,
            )
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
            "member_exit_misses": self.member_exit_misses,
            "last_input_track_count": self._last_input_track_count,
            "last_eligible_track_count": self._last_eligible_track_count,
            "last_excluded_non_active_count": self._last_excluded_non_active_count,
            "last_excluded_duplicate_source_count": self._last_excluded_duplicate_source_count,
            "association_profiles": {
                object_type: {
                    "max_distance_m": profile.max_distance_m,
                    "max_heading_diff_deg": profile.max_heading_diff_deg,
                    "max_speed_diff_mps": profile.max_speed_diff_mps,
                }
                for object_type, profile in self.association_profiles.items()
            },
        }

    def adopt_remote_groups(
        self,
        candidates: Iterable[Dict[str, Any]],
        tracks: Iterable[TrackState],
        threats: Iterable[ThreatAssessment] | None = None,
        scene_context: Dict[str, float] | None = None,
    ) -> List[TrackGroup]:
        """Apply graph-relation groups while retaining stable IDs and envelopes locally.

        The graph relation algorithm is authoritative for membership.  This
        method intentionally does not run the local complete-link inference;
        it only enriches valid remote candidates with lifecycle, prediction
        envelope and group-risk fields required by downstream consumers.
        """
        track_list = self._eligible_tracks(list(tracks))
        by_id = {track.track_id: track for track in track_list}
        threat_by_track = {threat.track_id: threat for threat in threats or []}
        previous_groups = dict(self.groups)
        groups: List[TrackGroup] = []
        observed_group_ids: Set[str] = set()

        for candidate in candidates:
            if not isinstance(candidate, dict):
                continue
            member_ids = candidate.get("member_track_ids") or candidate.get("members") or []
            members = [by_id[str(track_id)] for track_id in member_ids if str(track_id) in by_id]
            if len(members) < 2:
                continue
            requested_type = str(candidate.get("group_type") or self._group_type(members))
            group_type = requested_type if requested_type in {
                "air_formation", "surface_group", "mixed_group", "unknown_group"
            } else self._group_type(members)
            group_id = str(candidate.get("group_id") or self._reuse_group_id(
                {member.track_id for member in members}, group_type, observed_group_ids
            ))
            if group_id in observed_group_ids:
                continue
            group = self._build_group(members, threat_by_track, scene_context or {}, group_id, group_type)
            remote_cohesion = candidate.get("cohesion_score")
            if isinstance(remote_cohesion, (int, float)):
                group.cohesion_score = round(clamp(float(remote_cohesion)), 4)
                score, details = self._group_score(
                    members, threat_by_track, scene_context or {}, group.cohesion_score, group_type
                )
                group.group_threat_score = round(score, 4)
                group.group_threat_level = risk_level(score)
                group.metadata["group_score_factors"] = details
            group.evidence = [
                *(str(item) for item in candidate.get("evidence", []) if item),
                "成员关系由算法库图关系推理器给出；本 Agent 负责生命周期、包络和态势关注汇总。",
            ]
            self._mark_observed_group(group, previous_groups.get(group_id))
            group.metadata["formation_inference"] = {
                "algorithm": "graph_relation_reasoner",
                "membership_source": "algorithm_library",
                "remote_group_id": str(candidate.get("group_id") or group_id),
            }
            groups.append(group)
            observed_group_ids.add(group_id)

        for group_id, previous in previous_groups.items():
            if group_id not in observed_group_ids:
                coasting = self._coasting_group(previous)
                if coasting is not None:
                    groups.append(coasting)
        self.groups = {group.group_id: group for group in groups}
        self._write_physical_group_context(track_list, groups)
        return groups

    def _eligible_tracks(self, tracks: List[TrackState]) -> List[TrackState]:
        """Use active tracks for new groups and enforce one candidate per trusted source identity."""
        self._last_input_track_count = len(tracks)
        active = [
            track
            for track in tracks
            if track.metadata.get("status", "active") == "active"
            and track.metadata.get("lifecycle_state", "tentative") in {"tentative", "confirmed"}
        ]
        self._last_excluded_non_active_count = len(tracks) - len(active)
        by_identity: Dict[str, TrackState] = {}
        unscoped: List[TrackState] = []
        duplicate_count = 0
        for track in active:
            identity = str(track.metadata.get("source_identity") or "").strip()
            if not identity:
                source_object_id = str(track.metadata.get("source_object_id") or "").strip()
                source_agent = str(track.metadata.get("source_agent") or "").strip()
                if source_object_id and source_agent:
                    identity = f"{source_agent}:{source_object_id}"
            if not identity:
                unscoped.append(track)
                continue
            previous = by_identity.get(identity)
            if previous is None:
                by_identity[identity] = track
                continue
            duplicate_count += 1
            by_identity[identity] = max(
                (previous, track),
                key=lambda item: (
                    item.track_quality,
                    item.last_update_time,
                    int(item.metadata.get("hit_count", 0)),
                    item.track_id,
                ),
            )
        eligible = sorted([*by_identity.values(), *unscoped], key=lambda track: track.track_id)
        self._last_excluded_duplicate_source_count = duplicate_count
        self._last_eligible_track_count = len(eligible)
        return eligible

    def _related(self, left: TrackState, right: TrackState) -> bool:
        profile = self._pair_profile(left, right)
        distance_m = haversine_m(left.lat, left.lon, right.lat, right.lon)
        heading_diff = heading_difference_deg(left.heading, right.heading)
        speed_diff = abs(left.speed - right.speed)
        return (
            distance_m <= profile.max_distance_m
            and heading_diff <= profile.max_heading_diff_deg
            and speed_diff <= profile.max_speed_diff_mps
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
        profile = self._pair_profile(left, right)
        distance_score = 1.0 - haversine_m(left.lat, left.lon, right.lat, right.lon) / profile.max_distance_m
        heading_score = 1.0 - heading_difference_deg(left.heading, right.heading) / profile.max_heading_diff_deg
        speed_score = 1.0 - abs(left.speed - right.speed) / profile.max_speed_diff_mps
        return clamp((distance_score + heading_score + speed_score) / 3.0)

    @staticmethod
    def _build_association_profiles(
        max_distance_m: float | None,
        max_heading_diff_deg: float | None,
        max_speed_diff_mps: float | None,
        overrides: Dict[str, AssociationProfile | Dict[str, float]] | None,
    ) -> Dict[str, AssociationProfile]:
        profiles = dict(DEFAULT_ASSOCIATION_PROFILES)
        if max_distance_m is not None or max_heading_diff_deg is not None or max_speed_diff_mps is not None:
            # Explicit legacy thresholds mean the caller intentionally wants one shared profile.
            profiles = {
                object_type: AssociationProfile(
                    max_distance_m=float(max_distance_m if max_distance_m is not None else profile.max_distance_m),
                    max_heading_diff_deg=float(
                        max_heading_diff_deg if max_heading_diff_deg is not None else profile.max_heading_diff_deg
                    ),
                    max_speed_diff_mps=float(
                        max_speed_diff_mps if max_speed_diff_mps is not None else profile.max_speed_diff_mps
                    ),
                )
                for object_type, profile in profiles.items()
            }
        for object_type, override in (overrides or {}).items():
            base = profiles.get(object_type, profiles["unknown"])
            if isinstance(override, AssociationProfile):
                profiles[object_type] = override
                continue
            profiles[object_type] = AssociationProfile(
                max_distance_m=float(override.get("max_distance_m", base.max_distance_m)),
                max_heading_diff_deg=float(
                    override.get("max_heading_diff_deg", base.max_heading_diff_deg)
                ),
                max_speed_diff_mps=float(override.get("max_speed_diff_mps", base.max_speed_diff_mps)),
            )
        return profiles

    def _pair_profile(self, left: TrackState, right: TrackState) -> AssociationProfile:
        if left.object_type == right.object_type:
            return self.association_profiles.get(left.object_type, self.association_profiles["unknown"])
        # Preserve the previous mixed-target behavior instead of imposing an unvalidated cross-domain gate.
        return self.association_profiles["unknown"]

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

    def _mark_observed_group(
        self,
        group: TrackGroup,
        previous: TrackGroup | None,
        held_member_ids: List[str] | None = None,
        member_relation_misses: Dict[str, int] | None = None,
    ) -> None:
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
            "held_member_ids": sorted(held_member_ids or []),
            "member_relation_misses": dict(member_relation_misses or {}),
        }
        group.evidence.append(
            f"群组生命周期状态为 {lifecycle_state}，累计命中 {hit_count} 帧"
        )
        if held_member_ids:
            group.evidence.append(
                "成员短时未满足关联门限，按离组滞回机制暂时保留："
                + ", ".join(sorted(held_member_ids))
            )

    def _apply_member_exit_hysteresis(
        self,
        observed_members: List[TrackState],
        previous: TrackGroup | None,
        tracks_by_id: Dict[str, TrackState],
        observed_member_ids: Set[str],
    ) -> tuple[List[TrackState], List[str], Dict[str, int]]:
        """Retain a confirmed member for brief relation breaks, never across another observed group."""
        members_by_id = {member.track_id: member for member in observed_members}
        if previous is None or not bool((previous.metadata or {}).get("confirmed_once")):
            return list(members_by_id.values()), [], {}

        prior_misses = {
            str(track_id): int(miss_count)
            for track_id, miss_count in (previous.metadata or {}).get("member_relation_misses", {}).items()
        }
        held_member_ids: List[str] = []
        member_relation_misses: Dict[str, int] = {}
        for track_id in previous.member_track_ids:
            if track_id in members_by_id:
                continue
            if track_id not in tracks_by_id or track_id in observed_member_ids:
                continue
            miss_count = prior_misses.get(track_id, 0) + 1
            if miss_count >= self.member_exit_misses:
                continue
            members_by_id[track_id] = tracks_by_id[track_id]
            held_member_ids.append(track_id)
            member_relation_misses[track_id] = miss_count
        return [members_by_id[track_id] for track_id in sorted(members_by_id)], held_member_ids, member_relation_misses

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
                profile = self._pair_profile(left, right)
                distance_score = 1.0 - haversine_m(left.lat, left.lon, right.lat, right.lon) / profile.max_distance_m
                heading_score = 1.0 - heading_difference_deg(left.heading, right.heading) / profile.max_heading_diff_deg
                speed_score = 1.0 - abs(left.speed - right.speed) / profile.max_speed_diff_mps
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
            intersection_size = len(component & previous)
            jaccard = intersection_size / union_size
            smaller_retention = intersection_size / max(1, min(len(component), len(previous)))
            score = max(jaccard, smaller_retention)
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
