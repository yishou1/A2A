"""Build a causal, stage-scoped transfer manifest for one analysis submission.

The manifest is deliberately descriptive: it tells Gateway/Commander which
part of the current snapshot is relevant at this checkpoint.  It does not
claim that an Agent or algorithm executed, and it never contains future cues,
routes, scenario truth identifiers, or expected conclusions.
"""

from __future__ import annotations

from copy import deepcopy
from typing import Any


PHASE_INPUTS = {
    "FIND": ("sensor_observations", "sensor_geometry", "current_media", "environment"),
    "FIX": ("sensor_observations", "sensor_geometry", "fused_tracks", "current_media"),
    "TRACK": ("sensor_observations", "fused_tracks", "track_history", "current_media", "network_state"),
    "TARGET": ("fused_tracks", "asset_readiness", "network_state", "operational_constraints", "current_media"),
    "ENGAGE": ("verified_workflow_output", "asset_readiness", "task_state", "network_state"),
    "ASSESS": ("baseline_and_current_media", "execution_provenance", "fused_tracks", "asset_readiness"),
}

PHASE_OUTPUTS = {
    "FIND": ("detections", "source_evidence_refs"),
    "FIX": ("contact_associations", "location_confidence", "classification_candidates"),
    "TRACK": ("maintained_tracks", "risk_assessment", "evidence_gaps"),
    "TARGET": ("resource_allocation", "candidate_plans", "constraint_review"),
    "ENGAGE": ("approved_task_state", "execution_monitoring_refs"),
    "ASSESS": ("effect_assessment", "completion_score", "replan_recommendation"),
}


OBSERVATION_FIELDS = {
    "observation_id", "track_id", "sensor_id", "asset_id", "platform_id",
    "sim_time", "modality", "domain_hint", "lat", "lon", "lng", "alt_ft",
    "bearing_deg", "range_nm", "slant_range_nm", "elevation_deg", "elevation_angle_deg",
    "range_rate_kts", "snr_db", "frequency_mhz", "rf_freq_mhz", "power_dbm",
    "confidence", "quality", "covariance",
}
TRACK_FIELDS = {
    "track_id", "id", "lat", "lon", "lng", "alt_ft", "heading", "heading_deg",
    "speed_kts", "confidence", "domain", "domain_hint", "source_observation_ids",
}
ASSET_FIELDS = {
    "id", "asset_id", "platform_id", "role", "domain", "status", "lat", "lon", "lng",
    "alt_ft", "heading", "heading_deg", "speed_kts", "battery_pct", "fuel_pct", "comms_strength",
}
LINK_FIELDS = {
    "from", "to", "source", "target", "quality", "status", "rssi_dbm", "snr_db",
    "latency_ms", "packet_loss_pct", "bandwidth_mbps",
}


def _whitelist_rows(value: Any, allowed: set[str], limit: int) -> list[dict[str, Any]]:
    return [
        {key: deepcopy(raw[key]) for key in allowed if raw.get(key) is not None}
        for raw in value or [] if isinstance(raw, dict)
    ][:limit]


def _structured_evidence_data(meta: dict[str, Any]) -> dict[str, Any]:
    """Return bounded, truth-free numeric evidence consumable by backend models."""
    product = meta.get("product_data") if isinstance(meta.get("product_data"), dict) else {}
    result: dict[str, Any] = {
        "schema_version": "amos.evidence-data.v1",
        "renderer_type": product.get("renderer_type") or meta.get("renderer_type"),
        "data_source": product.get("data_source") or (meta.get("capture_parameters") or {}).get("data_source"),
        "frozen_at_sim_time": product.get("frozen_at_sim_time", meta.get("captured_at_sim_time")),
    }
    platform_pose = meta.get("platform_pose") if isinstance(meta.get("platform_pose"), dict) else {}
    sensor_pose = meta.get("sensor_pose") if isinstance(meta.get("sensor_pose"), dict) else {}
    sensor_config = meta.get("sensor_config") if isinstance(meta.get("sensor_config"), dict) else {}
    parameters = meta.get("capture_parameters") if isinstance(meta.get("capture_parameters"), dict) else {}
    safe_parameters = {
        key: deepcopy(parameters[key])
        for key in (
            "effective_range_nm", "instrumented_range_nm", "instrumented_range_km",
            "radar_range_nm", "ais_range_nm", "association_gate_nm",
            "horizontal_fov_deg", "vertical_fov_deg", "look_angle_deg",
            "azimuth_coverage_deg", "range_resolution_m", "resolution_px",
            "ground_sample_distance_m", "swath_width_km", "frequency_band_mhz",
            "bearing_error_deg", "observation_window_sec", "input_cutoff_sec",
            "registration_group", "reference_media_id", "frame_role",
        )
        if parameters.get(key) is not None
    }
    geometry = {
        "platform_pose": {
            key: deepcopy(platform_pose[key])
            for key in ("lat", "lon", "lng", "alt_ft", "heading_deg", "speed_kts")
            if platform_pose.get(key) is not None
        },
        "sensor_pose": {
            key: deepcopy(sensor_pose[key])
            for key in ("azimuth_deg", "depression_angle_deg", "planned_depression_angle_deg")
            if sensor_pose.get(key) is not None
        },
        "sensor_config": {
            key: deepcopy(sensor_config[key])
            for key in ("range_nm", "fov_deg", "vertical_fov_deg", "resolution", "width_px", "height_px", "ground_sample_distance_m")
            if sensor_config.get(key) is not None
        },
        "capture_parameters": safe_parameters,
    }
    geometry = {key: value for key, value in geometry.items() if value}
    if geometry:
        result["capture_geometry"] = geometry
    provenance = meta.get("capture_provenance") if isinstance(meta.get("capture_provenance"), dict) else {}
    safe_provenance = {
        key: deepcopy(provenance[key])
        for key in ("source_kind", "capability_source_kind", "simulated", "frozen")
        if provenance.get(key) is not None
    }
    if safe_provenance:
        result["capture_provenance"] = safe_provenance
    window = product.get("observation_window")
    if isinstance(window, dict):
        result["observation_window"] = {
            key: deepcopy(window[key]) for key in ("start_sec", "end_sec", "sample_count")
            if window.get(key) is not None
        }
        if result["observation_window"].get("sample_count") is None and window.get("observation_count") is not None:
            result["observation_window"]["sample_count"] = int(window["observation_count"])
    observations = _whitelist_rows(product.get("observations"), OBSERVATION_FIELDS, 64)
    tracks = _whitelist_rows(product.get("tracks"), TRACK_FIELDS, 32)
    assets = _whitelist_rows(product.get("assets"), ASSET_FIELDS, 32)
    tasks = _whitelist_rows(
        product.get("tasks"),
        {"task_id", "id", "title", "name", "status", "platform_id", "started_at_sec", "completed_at_sec"},
        32,
    )
    if observations:
        result["observations"] = observations
    if tracks:
        result["tracks"] = tracks
    if assets:
        result["assets"] = assets
    if tasks:
        result["tasks"] = tasks
    network = product.get("network") if isinstance(product.get("network"), dict) else {}
    if network:
        safe_network = {
            key: deepcopy(network[key])
            for key in ("monitor_platform_id", "nodes", "links", "degraded_links", "avg_quality", "resilience")
            if network.get(key) is not None and not isinstance(network.get(key), (dict, list))
        }
        current_links = _whitelist_rows(network.get("link_records") or network.get("topology_links"), LINK_FIELDS, 64)
        if current_links:
            safe_network["link_records"] = current_links
        history = []
        for sample in network.get("history_records") or []:
            if not isinstance(sample, dict):
                continue
            history.append({
                "sim_time": sample.get("sim_time"),
                "links": _whitelist_rows(sample.get("links"), LINK_FIELDS, 64),
            })
            if len(history) >= 32:
                break
        if history:
            safe_network["history_records"] = history
        result["network"] = safe_network
    return {key: value for key, value in result.items() if value not in (None, {}, [])}


def _available_on_branch(item: dict[str, Any], branch: str) -> bool:
    branches = {str(value) for value in item.get("branch_ids") or ["*"]}
    return "*" in branches or branch in branches


def _checkpoint_window(
    scenario: dict[str, Any],
    *,
    checkpoint_id: str | None,
    branch: str,
    elapsed: float,
) -> tuple[int | None, float, str | None]:
    checkpoints = [
        item for item in scenario.get("demo_checkpoints") or []
        if isinstance(item, dict) and _available_on_branch(item, branch)
    ]
    index = next(
        (position for position, item in enumerate(checkpoints)
         if str(item.get("checkpoint_id") or "") == str(checkpoint_id or "")),
        None,
    )
    if index is None:
        reached = [
            position for position, item in enumerate(checkpoints)
            if float(item.get("min_elapsed_sec", 0) or 0) <= elapsed
        ]
        index = reached[-1] if reached else None
    if index is None:
        return None, 0.0, None
    previous_cutoff = (
        float(checkpoints[index - 1].get("min_elapsed_sec", 0) or 0)
        if index > 0 else 0.0
    )
    return index, previous_cutoff, str(checkpoints[index].get("checkpoint_id") or "") or None


def _phase_at(scenario: dict[str, Any], elapsed: float, branch: str) -> str:
    cues = [
        item for item in scenario.get("timeline") or []
        if isinstance(item, dict)
        and float(item.get("at_sec", 0) or 0) <= elapsed
        and _available_on_branch(item, branch)
    ]
    return str((cues[-1] if cues else {}).get("phase") or "FIND").upper()


def _status_rows(
    required_groups: tuple[str, ...],
    *,
    live_snapshot: dict[str, Any],
    operator_snapshot: dict[str, Any],
    attachments: list[dict[str, Any]],
    incremental_media_ids: set[str],
    supplemental_inputs: dict[str, Any],
) -> list[dict[str, Any]]:
    observations = list(live_snapshot.get("observations") or [])
    tracks = list(live_snapshot.get("fused_tracks") or [])
    assets = list(live_snapshot.get("own_asset_poses") or operator_snapshot.get("assets") or [])
    network = live_snapshot.get("network") if isinstance(live_snapshot.get("network"), dict) else operator_snapshot.get("network") or {}
    current_media = [item for item in attachments if str(item.get("id") or "") in incremental_media_ids]
    all_media_ids = {str(item.get("id") or "") for item in attachments}
    reference_ids = {
        str((item.get("meta") or {}).get("reference_media_id") or "")
        for item in current_media if isinstance(item.get("meta"), dict)
    } - {""}
    counts = {
        "sensor_observations": len(observations),
        "sensor_geometry": sum(
            1 for item in current_media
            if (item.get("meta") or {}).get("platform_id")
            and (item.get("meta") or {}).get("sensor_id")
        ),
        "fused_tracks": len(tracks),
        "track_history": sum(len(item.get("history_path") or []) for item in tracks if isinstance(item, dict)),
        "current_media": len(current_media),
        "environment": 1 if supplemental_inputs.get("environment") else 0,
        "network_state": 1 if network else 0,
        "asset_readiness": len(assets),
        "operational_constraints": 1 if supplemental_inputs.get("operational_constraints") else 0,
        "task_state": len(supplemental_inputs.get("task_state") or []),
        "verified_workflow_output": 1 if supplemental_inputs.get("workflow_result_ref") else 0,
        "execution_provenance": 1 if supplemental_inputs.get("workflow_result_ref") else 0,
        "baseline_and_current_media": len(current_media) + len(reference_ids & all_media_ids),
    }
    return [
        {
            "data_group": group,
            "required": True,
            "count": int(counts.get(group, 0)),
            "status": "available" if counts.get(group, 0) else "missing",
        }
        for group in required_groups
    ]


def build_stage_transfer_manifest(
    scenario: dict[str, Any],
    snapshot: dict[str, Any],
    live_snapshot: dict[str, Any],
    attachments: list[dict[str, Any]],
) -> dict[str, Any]:
    """Return the exact stage slice to be consumed from the full snapshot."""
    clock = live_snapshot.get("clock") or snapshot.get("clock") or {}
    elapsed = float(clock.get("elapsed_sec", 0) or 0)
    branch = str(clock.get("scenario_branch") or scenario.get("default_branch") or "standard")
    checkpoint_id = str(clock.get("director_checkpoint_id") or "") or None
    checkpoint_index, start_sec, resolved_checkpoint_id = _checkpoint_window(
        scenario,
        checkpoint_id=checkpoint_id,
        branch=branch,
        elapsed=elapsed,
    )
    phase = _phase_at(scenario, elapsed, branch)
    first_window = checkpoint_index in (None, 0)

    def in_window(at_sec: Any) -> bool:
        value = float(at_sec or 0)
        return (value >= start_sec if first_window else value > start_sec) and value <= elapsed

    stage_cues = [
        item for item in scenario.get("timeline") or []
        if isinstance(item, dict) and _available_on_branch(item, branch)
        and in_window(item.get("at_sec"))
    ]
    incremental_attachments = [
        item for item in attachments
        if in_window((item.get("meta") or {}).get("captured_at_sim_time"))
    ]
    incremental_media_ids = {str(item.get("id") or "") for item in incremental_attachments}
    context_media_ids = {
        str((item.get("meta") or {}).get("reference_media_id") or "")
        for item in incremental_attachments if isinstance(item.get("meta"), dict)
    } - {""}

    agent_ids = sorted({
        str(value) for cue in stage_cues
        for value in cue.get("functional_agent_ids") or [] if value
    })
    model_ids = sorted({
        str(value) for cue in stage_cues
        for value in cue.get("model_requirement_ids") or [] if value
    })
    function_ids = sorted({
        str(item.get("function_id"))
        for item in scenario.get("function_point_coverage") or []
        if isinstance(item, dict) and str(item.get("phase") or "").upper() == phase
        and item.get("function_id")
    })
    algorithm_rows = [
        item for item in scenario.get("algorithm_coverage") or []
        if isinstance(item, dict) and str(item.get("requirement_id") or "") in set(model_ids)
    ]
    required_groups = PHASE_INPUTS.get(phase, PHASE_INPUTS["FIND"])
    story = snapshot.get("scenario_story") if isinstance(snapshot.get("scenario_story"), dict) else {}
    analysis = story.get("agent_analysis") if isinstance(story.get("agent_analysis"), dict) else {}
    workflow_ready = bool(
        analysis.get("source") == "commander_workflow"
        and str(analysis.get("status") or "").casefold() == "completed"
    )
    supplemental_inputs: dict[str, Any] = {}
    if "environment" in required_groups:
        supplemental_inputs["environment"] = {
            **deepcopy(scenario.get("environment") or {}),
            "area_of_operations": deepcopy((scenario.get("theater") or {}).get("ao") or {}),
        }
    if "operational_constraints" in required_groups:
        supplemental_inputs["operational_constraints"] = {
            "no_real_execution": True,
            "current_time_only": True,
            "do_not_infer_future_events": True,
        }
    if "task_state" in required_groups:
        allowed_task_keys = {"task_id", "id", "title", "name", "status", "platform_id", "started_at_sec", "completed_at_sec"}
        supplemental_inputs["task_state"] = [
            {key: deepcopy(value) for key, value in item.items() if key in allowed_task_keys}
            for item in snapshot.get("tasks") or []
            if isinstance(item, dict)
        ]
    if workflow_ready and ({"verified_workflow_output", "execution_provenance"} & set(required_groups)):
        allowed_result_keys = {
            "source", "status", "workflow_id", "source_run_id", "transport",
            "package_id", "package_verified", "completed_at",
        }
        supplemental_inputs["workflow_result_ref"] = {
            key: deepcopy(value)
            for key, value in analysis.items()
            if key in allowed_result_keys
        }
    input_rows = _status_rows(
        required_groups,
        live_snapshot=live_snapshot,
        operator_snapshot=snapshot,
        attachments=attachments,
        incremental_media_ids=incremental_media_ids,
        supplemental_inputs=supplemental_inputs,
    )
    attachment_by_id = {
        str(item.get("id") or ""): item
        for item in attachments
        if item.get("id")
    }
    scoped_attachments = [
        ("incremental", item)
        for item in incremental_attachments
    ] + [
        ("context", attachment_by_id[media_id])
        for media_id in sorted(context_media_ids)
        if media_id in attachment_by_id and media_id not in incremental_media_ids
    ]
    evidence_items = []
    for evidence_role, item in scoped_attachments:
        meta = item.get("meta") if isinstance(item.get("meta"), dict) else {}
        checksum = item.get("checksum") if isinstance(item.get("checksum"), dict) else {}
        evidence_items.append({
            "media_id": item.get("id"),
            "evidence_role": evidence_role,
            "uri": item.get("uri"),
            "mime_type": item.get("mime_type"),
            "source_name": item.get("name"),
            "capture_id": meta.get("capture_id"),
            "product_type": meta.get("product_type"),
            "captured_at_sim_time": meta.get("captured_at_sim_time"),
            "platform_id": meta.get("platform_id"),
            "sensor_id": meta.get("sensor_id"),
            "observation_ids": list(meta.get("observation_ids") or []),
            "track_ids": list(meta.get("track_ids") or []),
            "checksum": deepcopy(checksum),
            "consumer_context": deepcopy(meta.get("consumer_context") or {}),
            "structured_data_schema": "amos.evidence-data.v1",
            "structured_data": _structured_evidence_data(meta),
        })

    return {
        "schema_version": "amos.stage-transfer.v1",
        "checkpoint_id": resolved_checkpoint_id,
        "submission_ordinal": checkpoint_index + 1 if checkpoint_index is not None else None,
        "phase": phase,
        "branch": branch,
        "window": {"start_exclusive_sec": None if first_window else start_sec, "start_sec": start_sec, "end_sec": elapsed},
        "incremental_media_ids": sorted(incremental_media_ids),
        "context_media_ids": sorted(context_media_ids),
        "evidence_items": evidence_items,
        "requested_agent_ids": agent_ids,
        "requested_algorithm_ids": model_ids,
        "requested_function_point_ids": function_ids,
        "algorithm_plan": [
            {
                "requirement_id": item.get("requirement_id"),
                "algorithm_id": item.get("algorithm_id"),
                "assigned_agents": list(item.get("assigned_agents") or []),
            }
            for item in algorithm_rows
        ],
        "supplemental_inputs": supplemental_inputs,
        "required_inputs": input_rows,
        "expected_outputs": list(PHASE_OUTPUTS.get(phase, ())),
        "transfer_policy": {
            "current_snapshot": "required",
            "media": "incremental_plus_explicit_context",
            "event_history": "gateway_v1_continuous_from_sequence_1",
            "future_data": "prohibited",
            "private_scenario_state": "prohibited",
        },
    }


__all__ = ["build_stage_transfer_manifest"]
