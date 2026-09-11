"""Project completed Commander workflow artifacts onto the live AMOS state."""

from __future__ import annotations

import math
import time
from typing import Any

from amos_platform.domain.policies.visibility import remove_truth_fields

PROTECTED_OBJECT_CLASSES = {
    "fishing_vessel",
    "fishing boat",
    "fishing",
    "civilian",
    "merchant",
    "merchant_vessel",
}


def _first_mapping_with(node: Any, keys: set[str]) -> dict[str, Any]:
    if isinstance(node, dict):
        if keys.intersection(node):
            return node
        for value in node.values():
            found = _first_mapping_with(value, keys)
            if found:
                return found
    elif isinstance(node, list):
        for value in node:
            found = _first_mapping_with(value, keys)
            if found:
                return found
    return {}


def _workflow_artifacts(workflow_status: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
    result = workflow_status.get("result") or {}

    def unwrap(node: Any, output_name: str = "") -> dict[str, Any]:
        if isinstance(node, list):
            for item in reversed(node):
                found = unwrap(item, output_name)
                if found:
                    return found
            return {}
        if not isinstance(node, dict):
            return {}
        if output_name and output_name in node:
            return unwrap(node[output_name], output_name)
        if "value" in node:
            found = unwrap(node["value"], output_name)
            if found:
                return found
        if "output" in node:
            found = unwrap(node["output"], output_name)
            if found:
                return found
        return node["artifact"] if isinstance(node.get("artifact"), dict) else node

    outputs = result.get("outputs") if isinstance(result.get("outputs"), dict) else {}
    tracking = unwrap(outputs.get("tracking_result"), "tracking_result")
    assessment = unwrap(
        outputs.get("threat_assessment_result"),
        "threat_assessment_result",
    )

    # Commander can return tracking and threat assessment in separate activity
    # outputs. Search independently so neither product masks the other.
    if not tracking:
        tracking = unwrap(_first_mapping_with(result, {"tracks"}))
    if not assessment:
        assessment = unwrap(_first_mapping_with(
            result,
            {"threats", "unified_threat_ranking", "ranked_threats"},
        ))
    return tracking, assessment


def _assessment_rows(artifact: dict[str, Any]) -> list[dict[str, Any]]:
    rows = artifact.get("threats") or artifact.get("ranked_threats") or artifact.get("unified_threat_ranking") or []
    if not rows:
        # FIX may produce an identified track before Orient computes a threat
        # ranking. Preserve that backend-owned classification so the next
        # stage receives it instead of reverting the contact to generic SHIP.
        rows = [
            {
                **item,
                "track_id": item.get("track_id") or item.get("id"),
                "score": item.get("track_quality", item.get("confidence")),
                "level": (
                    (item.get("metadata") or {}).get("threat_level")
                    if isinstance(item.get("metadata"), dict)
                    else None
                ),
            }
            for item in artifact.get("tracks") or []
            if isinstance(item, dict)
            and (item.get("track_id") or item.get("id"))
            and isinstance(item.get("metadata"), dict)
            and str((item.get("metadata") or {}).get("source_class") or "").lower()
            not in {"", "unknown", "ship"}
        ]
    normalized = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        score = row.get("score", row.get("priority_score"))
        try:
            numeric_score = float(score) if score is not None else None
        except (TypeError, ValueError):
            numeric_score = None
        level = str(row.get("level") or "").lower()
        if not level and numeric_score is not None:
            level = "high" if numeric_score >= 0.75 else ("medium" if numeric_score >= 0.45 else "low")
        normalized.append({
            **row,
            "track_id": row.get("track_id") or row.get("source_track_id") or row.get("contact_id") or row.get("entity_id") or row.get("item_id"),
            "score": numeric_score,
            "level": level or "unknown",
        })
    return normalized


def _identity_values(item: dict[str, Any]) -> set[str]:
    return {
        str(item.get(key))
        for key in (
            "track_id", "id", "source_track_id", "source_contact_id",
            "contact_id", "entity_id", "item_id",
        )
        if item.get(key)
    }


def _local_source_ids(track: Any) -> set[str]:
    values = {str(getattr(track, "id", ""))}
    for ref in getattr(track, "source_refs", []) or []:
        if not isinstance(ref, dict):
            continue
        for key in ("observation_id", "source_observation_id", "media_id", "source_media_id"):
            if ref.get(key):
                values.add(str(ref[key]))
    return {value for value in values if value}


def _associate_backend_track(
    backend_track: dict[str, Any],
    local_tracks: list[Any],
    assigned_local_ids: set[str],
) -> tuple[Any | None, dict[str, Any]]:
    backend_ids = _identity_values(backend_track)
    backend_id = str(backend_track.get("track_id") or backend_track.get("id") or next(iter(backend_ids), "unidentified"))
    available = [track for track in local_tracks if str(track.id) not in assigned_local_ids]

    exact = [track for track in available if str(track.id) in backend_ids]
    if len(exact) == 1:
        return exact[0], {
            "backend_track_id": backend_id,
            "amos_track_id": exact[0].id,
            "status": "matched",
            "method": "exact_track_id",
            "distance_nm": 0.0,
        }

    source_matches = [
        track for track in available
        if backend_ids.intersection(_local_source_ids(track))
    ]
    if len(source_matches) == 1:
        return source_matches[0], {
            "backend_track_id": backend_id,
            "amos_track_id": source_matches[0].id,
            "status": "matched",
            "method": "source_reference",
            "distance_nm": None,
        }

    lat = backend_track.get("lat")
    lng = backend_track.get("lon", backend_track.get("lng"))
    if lat is None or lng is None:
        return None, {
            "backend_track_id": backend_id,
            "amos_track_id": None,
            "status": "unmatched",
            "method": "missing_reference_and_position",
            "distance_nm": None,
        }
    object_type = str(backend_track.get("object_type") or backend_track.get("entity_type") or "unknown").lower()
    expected_domain = "air" if object_type in {"uav", "drone", "aircraft", "airplane", "air"} else (
        "maritime" if object_type in {"ship", "boat", "vessel", "surface"} else (
            "ground" if object_type in {
                "ground", "ground_installation", "coastal_missile_site",
                "missile_site", "missile_battery",
            } else None
        )
    )
    candidates = [
        track for track in available
        if expected_domain is None or track.domain_hint in {None, "", expected_domain}
    ]
    ranked = sorted(
        ((math.hypot(track.lat - float(lat), track.lng - float(lng)), track) for track in candidates),
        key=lambda item: item[0],
    )
    if not ranked or ranked[0][0] > 0.03:
        return None, {
            "backend_track_id": backend_id,
            "amos_track_id": None,
            "status": "unmatched",
            "method": "outside_match_gate",
            "distance_nm": round(ranked[0][0] * 60.0, 3) if ranked else None,
        }
    if len(ranked) > 1 and ranked[1][0] - ranked[0][0] < 0.01:
        return None, {
            "backend_track_id": backend_id,
            "amos_track_id": None,
            "status": "ambiguous",
            "method": "proximity_tie",
            "distance_nm": round(ranked[0][0] * 60.0, 3),
            "candidate_track_ids": [ranked[0][1].id, ranked[1][1].id],
        }
    return ranked[0][1], {
        "backend_track_id": backend_id,
        "amos_track_id": ranked[0][1].id,
        "status": "matched",
        "method": "gated_proximity",
        "distance_nm": round(ranked[0][0] * 60.0, 3),
    }


def apply_commander_assessments(
    engine: Any,
    workflow_status: dict[str, Any],
    *,
    submission: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Apply only completed backend assessments; unknown contacts remain non-hostile."""
    workflow_id = str(workflow_status.get("workflow_id") or "")
    state = str(workflow_status.get("status") or "").lower()
    if state != "completed":
        return {"workflow_id": workflow_id, "status": state, "applied_count": 0}

    submission = submission if isinstance(submission, dict) else {}
    submitted_run_id = str(submission.get("run_id") or "")
    current_run_id = str(getattr(engine, "clock", {}).get("run_id") or "")
    if submitted_run_id and current_run_id and submitted_run_id != current_run_id:
        return {
            "workflow_id": workflow_id,
            "status": "stale_run",
            "applied_count": 0,
            "reason": "workflow result belongs to a previous simulation run",
            "submitted_run_id": submitted_run_id,
            "current_run_id": current_run_id,
            "analysis": {
                "source": "commander_workflow",
                "status": "stale_run",
                "message": "结果属于已结束的仿真运行，未写入当前态势。",
                "submitted_run_id": submitted_run_id,
                "current_run_id": current_run_id,
            },
        }

    package = submission.get("package") if isinstance(submission.get("package"), dict) else {}
    if submission.get("transport") == "gateway" and not package.get("verified"):
        return {
            "workflow_id": workflow_id,
            "status": "integrity_error",
            "applied_count": 0,
            "reason": "Gateway package checksum or run-chain verification is missing",
            "analysis": {
                "source": "a2a_gateway",
                "status": "integrity_error",
                "message": "Gateway 输入包未通过完整性校验，结果未写入当前态势。",
            },
        }

    story = getattr(engine, "scenario_story", None)
    if isinstance(story, dict) and story.get("projected_workflow_id") == workflow_id:
        analysis = story.get("agent_analysis") or {}
        return {
            "workflow_id": workflow_id,
            "status": state,
            "applied_count": analysis.get("applied_assessment_count", 0),
            "already_applied": True,
        }

    tracking_artifact, assessment_artifact = _workflow_artifacts(workflow_status)
    assessments = _assessment_rows(assessment_artifact or tracking_artifact)
    backend_track_rows = [
        item for item in tracking_artifact.get("tracks") or []
        if isinstance(item, dict) and (item.get("track_id") or item.get("id"))
    ]
    backend_tracks: dict[str, dict[str, Any]] = {}
    for item in backend_track_rows:
        for identity in _identity_values(item):
            backend_tracks[identity] = item
    fusion = getattr(engine, "sensor_fusion", None)
    local_tracks = list(getattr(fusion, "tracks", {}).values()) if fusion else []
    submitted_track_ids = {
        str(item.get("contact_id"))
        for item in submission.get("contacts") or []
        if isinstance(item, dict) and item.get("contact_id")
    }
    if submitted_track_ids:
        local_tracks = [
            track for track in local_tracks
            if str(getattr(track, "id", "")) in submitted_track_ids
        ]
    assigned_local_ids: set[str] = set()
    associations: list[dict[str, Any]] = []
    association_by_backend_id: dict[str, tuple[Any | None, dict[str, Any]]] = {}
    for backend_track in backend_track_rows:
        local_track, association = _associate_backend_track(
            backend_track,
            local_tracks,
            assigned_local_ids,
        )
        if local_track is not None:
            assigned_local_ids.add(str(local_track.id))
        associations.append(association)
        for identity in _identity_values(backend_track):
            association_by_backend_id[identity] = (local_track, association)

    applied = 0
    for assessment in assessments:
        backend_id = str(assessment.get("track_id") or "")
        backend_track = backend_tracks.get(backend_id, {})
        associated = association_by_backend_id.get(backend_id)
        if associated is None:
            local_track, association = _associate_backend_track(
                {**assessment, **backend_track},
                local_tracks,
                assigned_local_ids,
            )
            if local_track is not None:
                assigned_local_ids.add(str(local_track.id))
            associations.append(association)
            associated = (local_track, association)
            for identity in _identity_values(assessment):
                association_by_backend_id[identity] = associated
        track, association = associated
        metadata = backend_track.get("metadata") if isinstance(backend_track.get("metadata"), dict) else {}
        source_class = str(metadata.get("source_class") or "").lower()
        object_type = str(
            source_class
            if source_class not in {"", "unknown"}
            else backend_track.get("object_type") or assessment.get("entity_type") or "unknown"
        ).lower()
        if track is None or association.get("status") != "matched":
            continue
        level = str(assessment.get("level", "unknown")).lower()
        semantic_label = str(metadata.get("label") or "").lower()
        semantic_threat_level = str(metadata.get("threat_level") or "").lower()
        existing_class = str(getattr(track, "classification", "") or "").lower()
        protected = (
            object_type in PROTECTED_OBJECT_CLASSES
            or any(value in object_type for value in {"fishing", "civilian", "merchant"})
            or existing_class in {value.upper().lower() for value in PROTECTED_OBJECT_CLASSES}
            or any(value in existing_class for value in {"fishing", "civilian", "merchant"})
        )
        if protected:
            status = "cleared"
            level = "low"
        elif semantic_label == "hostile" and str(metadata.get("affiliation") or "").lower() == "red":
            status = "confirmed"
            level = semantic_threat_level if semantic_threat_level in {"high", "critical"} else "high"
        elif semantic_label in {"neutral", "friendly"}:
            status = "cleared"
            level = semantic_threat_level if semantic_threat_level in {"none", "low"} else "low"
        else:
            status = "confirmed" if level == "high" else ("watch" if level == "medium" else "cleared")
        if object_type != "unknown":
            track.classification = object_type.upper()
        track.threat_level = level.upper()
        previous_phase = None
        while previous_phase != track.kill_chain_phase:
            previous_phase = track.kill_chain_phase
            track._advance_kill_chain()
        assessment_record = {
            "status": status,
            "label": {"high": "高风险", "medium": "关注", "low": "低风险"}.get(level, "已评估"),
            "score": assessment.get("score"),
            "level": str(level).upper(),
            "source": "A2A 后端工作流",
            "workflow_id": workflow_id,
            "backend_track_id": backend_id,
            "association_method": association.get("method"),
        }
        if submitted_run_id:
            assessment_record["source_run_id"] = submitted_run_id
        if submission.get("snapshot_sequence") is not None:
            assessment_record["source_snapshot_sequence"] = submission["snapshot_sequence"]
        if submission.get("simulation_time_sec") is not None:
            assessment_record["source_simulation_time_sec"] = submission["simulation_time_sec"]
        track.agent_assessment = assessment_record
        applied += 1

    analysis = remove_truth_fields({
        "source": "commander_workflow",
        "status": state,
        "workflow_id": workflow_id,
        "message": "Commander 综合工作流已完成，结果已投影到 AMOS 仿真态势。",
        "summary": (workflow_status.get("result") or {}).get("summary") or {},
        "tracks": tracking_artifact.get("tracks") or [],
        "groups": tracking_artifact.get("groups") or [],
        "protected_assets": tracking_artifact.get("protected_assets") or [],
        "asset_impacts": tracking_artifact.get("asset_impacts") or [],
        "attention_ranking": assessments,
        "associations": associations,
        "unmatched_output_count": sum(1 for item in associations if item.get("status") != "matched"),
        "applied_assessment_count": applied,
        "source_run_id": submitted_run_id or None,
        "source_snapshot_sequence": submission.get("snapshot_sequence"),
        "source_simulation_time_sec": submission.get("simulation_time_sec"),
    })
    if isinstance(story, dict):
        story["agent_analysis"] = analysis
        story["projected_workflow_id"] = workflow_id
    if applied and hasattr(engine, "events"):
        engine.events.append({
            "type": "commander_assessment_applied",
            "source": "A2A 后端工作流",
            "track_count": applied,
            "workflow_id": workflow_id,
            "timestamp": time.time(),
        })
    return {
        "workflow_id": workflow_id,
        "status": state,
        "applied_count": applied,
        "analysis": analysis,
    }


__all__ = ["apply_commander_assessments"]
