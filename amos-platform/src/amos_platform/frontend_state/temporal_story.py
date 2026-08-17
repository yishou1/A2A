"""Causal projections for scripted scenario narratives.

The simulation engine keeps the complete script. Operator and Agent consumers
must only receive facts and media that exist at the current simulation time.
"""

from __future__ import annotations

from copy import deepcopy
from typing import Any

PUBLIC_CONTROL_KEYS = {"recommended_speed"}
PUBLIC_CUE_KEYS = {"cue_id", "at_sec", "phase", "level", "title", "description", "media_ids"}
PUBLIC_CAPTURE_KEYS = {
    "capture_id", "media_id", "product_type", "at_sec", "captured_at_sim_time",
    "captured_at_tick_id", "run_id", "platform_id", "sensor_instance_id",
    "capability_id", "capture_parameters",
    "observation_ids", "track_ids", "source_capture_ids", "platform_pose",
    "sensor_pose", "sensor_config", "capture_provenance", "uri", "media_uri",
    "dynamic_uri", "dynamic_checksum",
    "mime_type", "media_type", "kind", "modality", "pipeline_role", "text",
    "checksum", "phase", "title", "caption", "source_mode", "provenance",
    "sensor_id", "product_data", "consumer_context",
}


def build_internal_story(scenario: dict[str, Any]) -> dict[str, Any]:
    """Copy the complete scripted narrative into the private engine state."""
    return deepcopy({
        "scenario_id": scenario.get("id"),
        "scenario_type": scenario.get("scenario_type"),
        "title": scenario.get("name"),
        "description": scenario.get("description"),
        "operator_brief": scenario.get("operator_brief"),
        "timeline": scenario.get("timeline") or [],
        "media_cues": scenario.get("media_cues") or [],
        "capture_plans": scenario.get("capture_plans") or [],
        "asset_capabilities": scenario.get("asset_capabilities") or [],
        "asset_task_schedule": scenario.get("asset_task_schedule") or [],
        "captures": [],
        "default_branch": scenario.get("default_branch") or "standard",
        "protected_assets": scenario.get("protected_assets") or [],
        "cover_media_id": scenario.get("cover_media_id"),
        "demo_controls": scenario.get("demo_controls") or {},
        "agent_plan": scenario.get("agent_plan") or {},
        "current_cue_id": None,
    })


def _available_on_branch(item: dict[str, Any], branch: str) -> bool:
    branch_ids = {str(value) for value in item.get("branch_ids") or ["*"] if value}
    return "*" in branch_ids or branch in branch_ids


def _item_released(
    item: dict[str, Any],
    story: dict[str, Any],
    elapsed: float,
    branch: str,
) -> bool:
    if not _available_on_branch(item, branch):
        return False
    captures = {
        str(record.get("media_id")): record
        for record in story.get("captures") or []
        if isinstance(record, dict) and record.get("media_id")
    }
    if captures or "captures" in story:
        capture = captures.get(str(item.get("media_id") or ""))
        if not capture or float(capture.get("captured_at_sim_time", 0) or 0) > elapsed:
            return False
    elif float(item.get("at_sec", 0) or 0) > elapsed:
        return False
    if item.get("pipeline_role") == "rag_and_assessment":
        analysis = story.get("agent_analysis") or {}
        return analysis.get("source") == "commander_workflow"
    return True


def project_story_at_time(
    story: dict[str, Any],
    elapsed_sec: float,
    *,
    branch: str | None = None,
) -> dict[str, Any]:
    """Return the causally released part of a complete internal story."""
    elapsed = max(-1.0, float(elapsed_sec))
    active_branch = str(branch or story.get("default_branch") or "standard")
    all_timeline = list(story.get("timeline") or [])
    all_media = list(story.get("media_cues") or [])
    capture_by_media = {
        str(item.get("media_id")): item
        for item in story.get("captures") or []
        if isinstance(item, dict) and item.get("media_id")
    }
    released_media = []
    for item in all_media:
        if not _item_released(item, story, elapsed, active_branch):
            continue
        capture = capture_by_media.get(str(item.get("media_id") or ""), {})
        released = {
            key: deepcopy(value)
            for key, value in item.items()
            if key in PUBLIC_CAPTURE_KEYS
        }
        released.update({
            key: deepcopy(value)
            for key, value in capture.items()
            if key in PUBLIC_CAPTURE_KEYS
        })
        if capture.get("dynamic_uri"):
            released["uri"] = capture["dynamic_uri"]
            released["media_uri"] = capture["dynamic_uri"]
        if capture.get("dynamic_checksum"):
            released["checksum"] = capture["dynamic_checksum"]
        released_media.append(released)
    released_media_ids = {
        str(item.get("media_id")) for item in released_media if item.get("media_id")
    }
    released_timeline = []
    for raw_cue in all_timeline:
        if not _available_on_branch(raw_cue, active_branch):
            continue
        if float(raw_cue.get("at_sec", 0) or 0) > elapsed:
            continue
        cue = {
            key: deepcopy(value)
            for key, value in raw_cue.items()
            if key in PUBLIC_CUE_KEYS
        }
        cue["media_ids"] = [
            media_id for media_id in cue.get("media_ids") or []
            if str(media_id) in released_media_ids
        ]
        released_timeline.append(cue)

    controls = story.get("demo_controls") or {}
    current_cue_id = released_timeline[-1].get("cue_id") if released_timeline else None
    cover_media_id = story.get("cover_media_id")
    if str(cover_media_id) not in released_media_ids:
        cover_media_id = released_media[-1].get("media_id") if released_media else None

    projected = {
        "scenario_id": story.get("scenario_id"),
        "scenario_type": story.get("scenario_type"),
        "title": story.get("title"),
        "description": story.get("operator_brief") or story.get("description") or "",
        "timeline": released_timeline,
        "media_cues": released_media,
        "protected_assets": deepcopy(story.get("protected_assets") or []),
        "cover_media_id": cover_media_id,
        "demo_controls": {
            key: deepcopy(value)
            for key, value in controls.items()
            if key in PUBLIC_CONTROL_KEYS
        },
        "current_cue_id": current_cue_id,
        "causal_cutoff_sec": max(0.0, elapsed),
        "visibility": "operator-visible:causal-story",
    }
    if story.get("agent_analysis"):
        projected["agent_analysis"] = deepcopy(story["agent_analysis"])
    return projected


def media_is_released(story: dict[str, Any], request_path: str, elapsed_sec: float) -> bool:
    """Return whether a scripted media URI may be fetched at this time."""
    elapsed = float(elapsed_sec)
    return any(
        str(item.get("uri") or item.get("media_uri") or "") == request_path
        and float(item.get("captured_at_sim_time", 0) or 0) <= elapsed
        for item in story.get("captures") or []
        if isinstance(item, dict)
    )


__all__ = [
    "PUBLIC_CAPTURE_KEYS",
    "build_internal_story",
    "media_is_released",
    "project_story_at_time",
]
