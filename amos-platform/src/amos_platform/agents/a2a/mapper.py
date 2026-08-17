"""A2A task mapping helpers."""

from __future__ import annotations

from copy import deepcopy
import os
import uuid
from typing import Any
from urllib.parse import urljoin

from amos_platform.agents.a2a.mission_contract import (
    build_perception_frames,
    link_evidence,
    map_friendly_platforms,
    map_fused_contacts,
    map_observations,
)
from amos_platform.agents.a2a.stage_transfer import build_stage_transfer_manifest


def build_a2a_perception_task(agent_input: dict[str, Any]) -> dict[str, Any]:
    """Wrap an agent-visible perception packet as an A2A task body."""
    return {
        "task_id": agent_input.get("trace_id") or f"amos-perception-{uuid.uuid4().hex[:12]}",
        "kind": "perception.detect",
        "visibility": "agent-visible",
        "input": agent_input,
        "artifacts": agent_input.get("media", {}).get("items", []),
    }


def sensor_modality(sensor_name: str) -> str:
    """Map a scenario sensor name to the simulated media/telemetry modality."""
    name = str(sensor_name or "").lower()
    if any(token in name for token in ("eo", "ir", "\u5149\u7535", "\u7ea2\u5916", "irst")):
        return "eo_ir"
    if any(token in name for token in ("sar", "\u5408\u6210\u5b54\u5f84")):
        return "sar"
    if any(token in name for token in ("radar", "\u96f7\u8fbe", "aesa", "aew")):
        return "radar"
    if any(token in name for token in ("sonar", "\u58f0\u7eb3", "\u58f0\u5450", "acoustic", "\u58f0\u5b66")):
        return "acoustic"
    return "telemetry"


def scenario_center(scenario: dict[str, Any]) -> dict[str, float]:
    """Return scenario center from explicit theater center or AO bounds."""
    theater = scenario.get("theater") or {}
    center = theater.get("center") or {}
    if center:
        return {
            "lat": float(center.get("lat", 0)),
            "lng": float(center.get("lng", center.get("lon", 0))),
        }
    ao = theater.get("ao") or {}
    if ao:
        north = float(ao.get("north", 0))
        south = float(ao.get("south", 0))
        east = float(ao.get("east", 0))
        west = float(ao.get("west", 0))
        return {"lat": (north + south) / 2, "lng": (east + west) / 2}
    return {"lat": 0.0, "lng": 0.0}


def _area_of_operations(
    scenario: dict[str, Any],
    support_data: dict[str, Any] | None,
) -> dict[str, Any]:
    scenario_ao = (scenario.get("theater") or {}).get("ao")
    support_ao = ((support_data or {}).get("theater") or {}).get("ao")
    return scenario_ao or support_ao or {}


def estimate_jamming_level(snapshot: dict[str, Any]) -> float:
    """Estimate jamming level from visible network resilience/degradation."""
    network = snapshot.get("network") or {}
    resilience = network.get("resilience")
    if isinstance(resilience, (int, float)):
        return round(max(0.0, min(1.0, 1.0 - float(resilience))), 3)
    degraded = network.get("degraded") or network.get("degraded_count") or 0
    try:
        return round(max(0.0, min(1.0, float(degraded) / 10.0)), 3)
    except (TypeError, ValueError):
        return 0.0


def situation_summary(snapshot: dict[str, Any], scenario: dict[str, Any]) -> str:
    """Build a compact text summary for Commander mission attachments."""
    assets = snapshot.get("own_asset_poses") or snapshot.get("assets") or scenario.get("assets", [])
    observations = snapshot.get("observations") or []
    alerts = snapshot.get("alerts") or []
    scenario_name = scenario.get("name") or scenario.get("id", "unknown scenario")
    latest_alerts = "; ".join(str(alert.get("msg", "")) for alert in alerts[-3:] if isinstance(alert, dict))
    suffix = f"; alerts={latest_alerts}" if latest_alerts else ""
    return (
        f"{scenario_name}: assets={len(assets)}, observations={len(observations)}, "
        f"agent_tracks={len(snapshot.get('prior_agent_tracks') or [])}{suffix}"
    )


def build_story_attachments(
    scenario: dict[str, Any],
    snapshot: dict[str, Any],
) -> list[dict[str, Any]]:
    """Expose currently available scripted media through the A2A attachment protocol."""
    elapsed = float((snapshot.get("clock") or {}).get("elapsed_sec", 0) or 0)
    public_base_url = os.environ.get("AMOS_PUBLIC_BASE_URL", "http://127.0.0.1:5000/")
    attachments: list[dict[str, Any]] = []
    causal_story = snapshot.get("scenario_story")
    media_source = (
        causal_story.get("media_cues") or []
        if isinstance(causal_story, dict)
        else scenario.get("media_cues") or []
    )
    available_items = [
        item for item in media_source
        if float(item.get("at_sec", 0) or 0) <= elapsed
        and item.get("pipeline_role") != "rag_and_assessment"
    ]
    # Keep every causally released item available to the stage planner.  The
    # former ``latest_visual_only`` shortcut broke evidence provenance by
    # silently discarding baselines that later assessment products reference.
    # ``build_stage_transfer_manifest`` now performs the smaller and explicit
    # incremental/context selection for each submission.

    for item in available_items:
        uri = item.get("uri") or item.get("media_uri")
        checksum = item.get("checksum")
        if not uri or not checksum:
            continue
        platform_id = str(item.get("platform_id") or "") or None
        sensor_instance_id = str(item.get("sensor_instance_id") or item.get("sensor_id") or "") or None
        sensor_id = (
            f"{platform_id}/{sensor_instance_id}"
            if platform_id and sensor_instance_id and "/" not in sensor_instance_id
            else sensor_instance_id
        )
        platform_pose = item.get("platform_pose") if isinstance(item.get("platform_pose"), dict) else {}
        sensor_pose = item.get("sensor_pose") if isinstance(item.get("sensor_pose"), dict) else {}
        sensor_config = item.get("sensor_config") if isinstance(item.get("sensor_config"), dict) else {}
        absolute_uri = urljoin(public_base_url.rstrip("/") + "/", str(uri).lstrip("/"))
        resolution = sensor_config.get("resolution")
        if isinstance(resolution, (list, tuple)) and len(resolution) == 2:
            resolution = f"{resolution[0]}x{resolution[1]}"
        elif resolution is None and sensor_config.get("width_px") and sensor_config.get("height_px"):
            resolution = f"{sensor_config['width_px']}x{sensor_config['height_px']}"
        altitude_ft = platform_pose.get("alt_ft")
        attachments.append({
            "id": item.get("media_id") or f"story-media-{len(attachments) + 1}",
            "kind": item.get("kind") or "image",
            "uri": absolute_uri,
            "mime_type": item.get("mime_type") or "image/png",
            "checksum": {"algorithm": "sha256", "value": str(checksum)},
            "name": item.get("title") or item.get("media_id") or "simulation-frame",
            "meta": {
                "sensor_id": sensor_id,
                "modality": item.get("modality") or "eo_ir",
                "pipeline_role": item.get("pipeline_role") or "visual_detection",
                "text": item.get("text") or item.get("caption") or "",
                "platform_id": platform_id,
                "platform_lat": platform_pose.get("lat"),
                "platform_lon": platform_pose.get("lon"),
                "altitude_m": (
                    round(float(altitude_ft) * 0.3048, 1)
                    if altitude_ft is not None else None
                ),
                "heading_deg": platform_pose.get("heading_deg"),
                "sensor_azimuth_deg": sensor_pose.get("azimuth_deg"),
                "depression_angle_deg": sensor_pose.get("depression_angle_deg"),
                "planned_depression_angle_deg": sensor_pose.get("planned_depression_angle_deg"),
                "resolution": resolution,
                "fov_deg": sensor_config.get("fov_deg"),
                "vertical_fov_deg": sensor_config.get("vertical_fov_deg"),
                "effective_range_nm": sensor_config.get("range_nm"),
                "phase": item.get("phase") or "FIND",
                "capture_id": item.get("capture_id"),
                "product_type": item.get("product_type"),
                "captured_at_sim_time": item.get("captured_at_sim_time", item.get("at_sec", 0)),
                "source_media_id": item.get("media_id"),
                "observation_ids": list(item.get("observation_ids") or []),
                "track_ids": list(item.get("track_ids") or []),
                "source_capture_ids": list(item.get("source_capture_ids") or []),
                "capture_provenance": deepcopy(item.get("capture_provenance") or {}),
                "platform_pose": deepcopy(platform_pose),
                "sensor_pose": deepcopy(sensor_pose),
                "sensor_config": deepcopy(sensor_config),
                "capture_parameters": deepcopy(item.get("capture_parameters") or {}),
                "product_data": deepcopy(item.get("product_data") or {}),
                "consumer_context": deepcopy(item.get("consumer_context") or {}),
                "registration_group": (item.get("capture_parameters") or {}).get("registration_group"),
                "reference_media_id": (item.get("capture_parameters") or {}).get("reference_media_id"),
                "frame_role": (item.get("capture_parameters") or {}).get("frame_role"),
                "subject_asset_ids": list((item.get("capture_parameters") or {}).get("subject_asset_ids") or []),
                "source_mode": item.get("source_mode", "simulation"),
                "simulation_only": True,
            },
        })
    return attachments


def build_commander_workflow_payload(
    scenario: dict[str, Any],
    snapshot: dict[str, Any],
    live_snapshot: dict[str, Any],
    *,
    workflow_mode: str,
    workflow_file: str,
    support_data: dict[str, Any] | None = None,
    options: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Build a Commander WorkflowSubmitRequest from agent-visible state."""
    scenario_options = dict(((scenario.get("agent_plan") or {}).get("commander_options") or {}))
    scenario_options.update(options or {})
    options = scenario_options
    support = support_data or {}
    scenario_id = scenario.get("id", "amphibious-landing-joint-operation")

    own_assets = live_snapshot.get("own_asset_poses") or []
    tracks = live_snapshot.get("fused_tracks") or []
    observations = map_observations(live_snapshot.get("observations") or [])
    contacts = map_fused_contacts(tracks)
    friendly_platforms = map_friendly_platforms(own_assets)

    attachments = build_story_attachments(scenario, snapshot)
    supplied_attachments = options.get("attachments") or []
    if supplied_attachments:
        attachments.extend(supplied_attachments)
    stage_transfer = build_stage_transfer_manifest(
        scenario,
        snapshot,
        live_snapshot,
        attachments,
    )
    transfer_media_ids = {
        *[str(value) for value in stage_transfer.get("incremental_media_ids") or []],
        *[str(value) for value in stage_transfer.get("context_media_ids") or []],
    }
    attachments = [
        item for item in attachments
        if str(item.get("id") or "") in transfer_media_ids
    ]
    evidence = link_evidence(attachments, tracks)
    elapsed = float((snapshot.get("clock") or {}).get("elapsed_sec", 0) or 0)
    jamming_level = options.get("jamming_level")
    if jamming_level is None:
        active_levels = [
            float(item.get("level", 0) or 0)
            for item in options.get("jamming_schedule") or []
            if float(item.get("at_sec", 0) or 0) <= elapsed
        ]
        jamming_level = active_levels[-1] if active_levels else estimate_jamming_level(live_snapshot)
    area_of_operations = dict(_area_of_operations(scenario, support))
    area_of_operations.setdefault("center", scenario_center(scenario))
    protected_assets = scenario.get("protected_assets") or []
    batch = live_snapshot.get("observation_batch") or {}
    active_clock = live_snapshot.get("clock") or snapshot.get("clock") or {}
    run_id = str((live_snapshot.get("clock") or {}).get("run_id") or batch.get("run_id") or "")
    snapshot_sequence = int(batch.get("tick_id", 0) or 0)
    perception_frames = build_perception_frames(
        tracks,
        scenario_id=scenario_id,
        protected_assets=protected_assets,
        cutoff_sec=elapsed,
    )
    primary_asset = protected_assets[0] if protected_assets else {}
    scene = {
        "scenario_id": scenario_id,
        "protected_zone_lat": primary_asset.get("lat"),
        "protected_zone_lon": primary_asset.get("lon", primary_asset.get("lng")),
        "protected_radius_m": primary_asset.get("protection_radius_m"),
        "protected_assets": protected_assets,
    }
    mission_input = {
        "scenario_id": scenario_id,
        "scenario_name": scenario.get("name") or scenario_id,
        "mission_type": "multimodal_tracking_and_assessment",
        "objective": options.get("task_goal") or "识别模拟接触、更新航迹、评估风险并生成安全处置建议",
        "intelligence_text": situation_summary(live_snapshot, scenario),
        "contacts": contacts,
        "friendly_platforms": friendly_platforms,
        "protected_assets": protected_assets,
        "perception_frames": perception_frames,
        "observations": observations,
        "evidence": evidence,
        "stage_transfer": stage_transfer,
        "constraints": {
            "no_real_execution": True,
            "current_time_only": True,
            "do_not_infer_future_events": True,
        },
        "scene": scene,
        "analysis_guidance": (
            "仅依据当前仿真时刻已经产生的观测和附件进行关联、分类与风险评估；"
            "不得假设后续事件、未来航路或未观测目标。"
        ),
        "knowledge_base": options.get("knowledge_base") or [],
        "environment": {
            **dict(scenario.get("environment") or {}),
            "jamming_level": jamming_level,
            "area_of_operations": area_of_operations,
            "network": dict(live_snapshot.get("network") or {}),
        },
        "simulation_time_sec": elapsed,
        "simulation_mode": "safe",
        "metadata": {
            "run_id": run_id,
            "snapshot_sequence": snapshot_sequence,
            "causal_cutoff_sec": elapsed,
            "source_system": "AMOS",
            "source_contract": "amos.commander.mission.v2",
            "director_mode": active_clock.get("director_mode"),
            "director_checkpoint_id": active_clock.get("director_checkpoint_id"),
            "scenario_branch": active_clock.get("scenario_branch"),
            "simulation_seed": active_clock.get("seed"),
        },
        # Planned coverage is scenario metadata, not proof of execution.  The
        # workflow view only promotes records to verified when trace evidence
        # exists for this run.
        "required_agents": deepcopy(scenario.get("required_agents") or []),
        "functional_agents": deepcopy(scenario.get("functional_agents") or []),
        "algorithm_coverage": deepcopy(scenario.get("algorithm_coverage") or []),
        "function_point_coverage": deepcopy(scenario.get("function_point_coverage") or []),
    }
    if attachments:
        first_meta = attachments[0].setdefault("meta", {})
        first_meta["amos_mission"] = mission_input

    payload: dict[str, Any] = {
        "workflow": options.get("workflow") or options.get("workflow_mode") or workflow_mode,
        "workflow_file": options.get("workflow_file") or workflow_file,
        "max_steps": options.get("max_steps", 10),
        "max_workers": options.get("max_workers", 4),
        "max_retries": options.get("max_retries", 1),
        "retry_backoff": options.get("retry_backoff", 0.2),
        "request_timeout": options.get(
            "request_timeout",
            float(
                os.environ.get(
                    "A2A_REQUEST_TIMEOUT",
                    "180"
                    if str(os.environ.get("ENABLE_LLM", "false")).lower()
                    in {"1", "true", "yes", "on"}
                    else "60",
                )
            ),
        ),
    }

    if options.get("workflow_id"):
        payload["workflow_id"] = options["workflow_id"]
    if options.get("resume"):
        payload["resume"] = True
    if options.get("mock_eval_score") is not None:
        payload["mock_eval_score"] = options["mock_eval_score"]
    if options.get("mock_decision"):
        payload["mock_decision"] = options["mock_decision"]

    payload["attachments"] = attachments
    # This internal copy makes audit summaries robust when a stage has no
    # media attachment. Direct Commander transport still receives the
    # compatible attachment form; Gateway receives the context in the
    # selected simulation chain.
    payload["mission_input"] = mission_input
    payload["task_goal"] = mission_input["objective"]
    payload["required_agents"] = deepcopy(mission_input["required_agents"])
    payload["functional_agents"] = deepcopy(mission_input["functional_agents"])
    payload["algorithm_coverage"] = deepcopy(mission_input["algorithm_coverage"])
    payload["function_point_coverage"] = deepcopy(mission_input["function_point_coverage"])
    return payload


__all__ = [
    "build_a2a_perception_task",
    "build_commander_workflow_payload",
    "build_story_attachments",
    "estimate_jamming_level",
    "scenario_center",
    "sensor_modality",
    "situation_summary",
]
