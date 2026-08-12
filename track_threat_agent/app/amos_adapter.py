"""Integration event adapter for track, group, asset, and risk artifacts.

The emitted event shapes are intentionally simple JSON dictionaries. They are
usable by a generic A2A Gateway, a message bus, or an AMOS adapter. The
historical function name `build_amos_events` is kept for compatibility with
older tests and bridge code, but the preferred name is `build_integration_events`.
"""

from __future__ import annotations

from typing import Any, Dict, Iterable, List


def build_integration_events(
    tracks: Iterable[Any],
    threats: Iterable[Any],
    groups: Iterable[Any],
    unified_threat_ranking: List[Dict[str, Any]],
    protected_assets: Iterable[Any] | None = None,
    asset_impacts: Iterable[Any] | None = None,
) -> List[Dict[str, Any]]:
    """Convert internal objects into integration event payloads.

    These events intentionally stay simulation-only. The `threat.*` names are
    compatibility labels for attention-priority reports, not weapon or
    engagement instructions.
    """

    track_list = list(tracks)
    threat_list = list(threats)
    group_list = list(groups)
    protected_asset_list = list(protected_assets or [])
    asset_impact_list = list(asset_impacts or [])
    track_by_id = {track.track_id: track for track in track_list}

    events: List[Dict[str, Any]] = []
    events.extend(_protected_asset_updated(asset) for asset in protected_asset_list)
    events.extend(_asset_updated_for_track(track) for track in track_list)
    events.extend(_track_updated(track) for track in track_list)
    events.extend(_threat_updated(threat, track_by_id.get(threat.track_id)) for threat in threat_list)
    events.extend(_asset_updated_for_group(group) for group in group_list)
    events.extend(_asset_relationship_updated(group) for group in group_list)
    events.extend(_track_group_updated(group) for group in group_list)
    events.extend(_threat_group_updated(group) for group in group_list)
    events.extend(_asset_impact_updated(impact) for impact in asset_impact_list)
    events.append(
        {
            "event_type": "threat.ranking.updated",
            "ranking": unified_threat_ranking,
        }
    )
    return events


def build_amos_events(
    tracks: Iterable[Any],
    threats: Iterable[Any],
    groups: Iterable[Any],
    unified_threat_ranking: List[Dict[str, Any]],
    protected_assets: Iterable[Any] | None = None,
    asset_impacts: Iterable[Any] | None = None,
) -> List[Dict[str, Any]]:
    """Backward-compatible alias for AMOS bridge integrations."""

    return build_integration_events(
        tracks,
        threats,
        groups,
        unified_threat_ranking,
        protected_assets,
        asset_impacts,
    )


def _protected_asset_updated(asset: Any) -> Dict[str, Any]:
    return {
        "event_type": "protected.asset.updated",
        "asset_id": asset.asset_id,
        "asset_name": asset.asset_name,
        "asset_type": asset.asset_type,
        "position": {"lat": asset.lat, "lon": asset.lon, "alt": asset.alt},
        "protection_radius_m": asset.protection_radius_m,
        "criticality": asset.criticality,
        "status": asset.status,
        "timestamp": None,
        "metadata": {
            **asset.metadata,
            "owner_agent_id": "track-threat-group-agent-01",
            "safety_note": "protected asset for simulation-only impact analysis",
        },
    }


def _asset_updated_for_track(track: Any) -> Dict[str, Any]:
    return {
        "event_type": "asset.updated",
        "asset_id": track.track_id,
        "asset_type": track.object_type,
        "asset_category": "tracked_object",
        "display_name": f"{track.object_type}:{track.track_id}",
        "status": (track.metadata or {}).get("status", "active"),
        "source": (track.metadata or {}).get("source_agent", "track-threat-group-agent"),
        "position": {
            "lat": track.lat,
            "lon": track.lon,
            "alt": track.alt,
        },
        "geometry": {
            "type": "track",
            "current_position": {
                "lat": track.lat,
                "lon": track.lon,
                "alt": track.alt,
            },
            "history_path": track.history_path,
            "predicted_path": track.predicted_path,
        },
        "timestamp": track.last_update_time,
        "metadata": {
            "track_id": track.track_id,
            "object_type": track.object_type,
            "speed": track.speed,
            "heading": track.heading,
            "track_quality": track.track_quality,
            "anomaly": (track.metadata or {}).get("anomaly", {}),
            "owner_agent_id": "track-threat-group-agent-01",
            "asset_upsert_hint": True,
        },
    }


def _asset_updated_for_group(group: Any) -> Dict[str, Any]:
    lifecycle_state = (getattr(group, "metadata", {}) or {}).get("lifecycle_state", "confirmed")
    return {
        "event_type": "asset.updated",
        "asset_id": group.group_id,
        "asset_type": group.group_type,
        "asset_category": "track_group",
        "display_name": f"{group.group_type}:{group.group_id}",
        "status": lifecycle_state,
        "source": "track-threat-group-agent",
        "position": group.centroid,
        "geometry": {
            "type": "track_group",
            "centroid": group.centroid,
            "envelope": group.envelope,
            "centroid_prediction": group.centroid_prediction,
            "predicted_envelope": group.predicted_envelope,
        },
        "timestamp": group.timestamp,
        "metadata": {
            "group_id": group.group_id,
            "group_type": group.group_type,
            "member_track_ids": group.member_track_ids,
            "cohesion_score": group.cohesion_score,
            "group_threat_score": group.group_threat_score,
            "group_threat_level": group.group_threat_level,
            "lifecycle": getattr(group, "metadata", {}) or {},
            "owner_agent_id": "track-threat-group-agent-01",
            "asset_upsert_hint": True,
        },
    }


def _asset_relationship_updated(group: Any) -> Dict[str, Any]:
    return {
        "event_type": "asset.relationship.updated",
        "relationship_id": f"{group.group_id}:members",
        "relationship_type": "group_membership",
        "source_asset_id": group.group_id,
        "target_asset_ids": group.member_track_ids,
        "timestamp": group.timestamp,
        "metadata": {
            "group_type": group.group_type,
            "cohesion_score": group.cohesion_score,
            "group_lifecycle_state": (getattr(group, "metadata", {}) or {}).get(
                "lifecycle_state", "confirmed"
            ),
            "owner_agent_id": "track-threat-group-agent-01",
        },
    }


def _track_updated(track: Any) -> Dict[str, Any]:
    track_payload = track.model_dump()
    return {
        "event_type": "track.updated",
        "track_id": track.track_id,
        "object_type": track.object_type,
        "current_position": {
            "lat": track.lat,
            "lon": track.lon,
            "alt": track.alt,
        },
        "speed": track.speed,
        "heading": track.heading,
        "history_path": track.history_path,
        "predicted_path": track.predicted_path,
        "track_quality": track.track_quality,
        "metadata": track.metadata,
        "timestamp": track.last_update_time,
        # Frontend compatibility: the AMOS fields above are canonical.
        "track": track_payload,
    }


def _threat_updated(threat: Any, track: Any | None) -> Dict[str, Any]:
    metadata = {
        "threat_score": threat.score,
        "level": threat.level,
        "rank": threat.rank,
        "evidence": threat.evidence,
        "factors": threat.factors,
        "safety_note": "simulation-only attention priority; no engagement decision",
        **getattr(threat, "metadata", {}),
    }
    return {
        "event_type": "threat.updated",
        "threat_id": threat.threat_id,
        "track_id": threat.track_id,
        "threat_type": track.object_type if track else "unknown",
        "lat": track.lat if track else None,
        "lon": track.lon if track else None,
        "alt": track.alt if track else None,
        "heading": track.heading if track else None,
        "speed": track.speed if track else None,
        "confidence": track.track_quality if track else None,
        "source": (track.metadata or {}).get("source_agent", "track-threat-group-agent") if track else "track-threat-group-agent",
        "timestamp": threat.timestamp,
        "metadata": metadata,
        # Frontend compatibility: the AMOS fields above are canonical.
        "threat": threat.model_dump(),
        "score": threat.score,
        "level": threat.level,
        "rank": threat.rank,
    }


def _track_group_updated(group: Any) -> Dict[str, Any]:
    return {
        "event_type": "track.group.updated",
        "group_id": group.group_id,
        "group_type": group.group_type,
        "members": group.member_track_ids,
        "member_track_ids": group.member_track_ids,
        "centroid": group.centroid,
        "envelope": group.envelope,
        "centroid_prediction": group.centroid_prediction,
        "predicted_envelope": group.predicted_envelope,
        "cohesion_score": group.cohesion_score,
        "evidence": group.evidence,
        "metadata": getattr(group, "metadata", {}) or {},
        "timestamp": group.timestamp,
        # Frontend compatibility: the AMOS fields above are canonical.
        "group": group.model_dump(),
    }


def _threat_group_updated(group: Any) -> Dict[str, Any]:
    return {
        "event_type": "threat.group.updated",
        "group_id": group.group_id,
        "score": group.group_threat_score,
        "level": group.group_threat_level,
        "evidence": group.evidence,
        "metadata": {
            "group_lifecycle_state": (getattr(group, "metadata", {}) or {}).get(
                "lifecycle_state", "confirmed"
            )
        },
        "timestamp": group.timestamp,
        # Frontend compatibility: the AMOS fields above are canonical.
        "group": group.model_dump(),
    }


def _asset_impact_updated(impact: Any) -> Dict[str, Any]:
    return {
        "event_type": "asset.impact.updated",
        "impact_id": impact.impact_id,
        "protected_asset_id": impact.protected_asset_id,
        "protected_asset_name": impact.protected_asset_name,
        "protected_asset_type": impact.protected_asset_type,
        "source_track_id": impact.source_track_id,
        "source_threat_id": impact.source_threat_id,
        "source_object_type": impact.source_object_type,
        "score": impact.score,
        "level": impact.level,
        "rank": impact.rank,
        "closest_distance_m": impact.closest_distance_m,
        "predicted_closest_distance_m": impact.predicted_closest_distance_m,
        "predicted_min_distance_margin_m": impact.predicted_min_distance_margin_m,
        "eta_to_protected_radius_s": impact.eta_to_protected_radius_s,
        "will_enter_protection_radius": impact.will_enter_protection_radius,
        "factors": impact.factors,
        "evidence": impact.evidence,
        "timestamp": impact.timestamp,
        "metadata": {
            "safety_note": "simulation-only protected-asset impact priority; no engagement advice",
        },
        "impact": impact.model_dump(),
    }
