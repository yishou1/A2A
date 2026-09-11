"""Operator-visible state projection."""

from __future__ import annotations

from copy import deepcopy
from typing import Any

from amos_platform.domain.policies.visibility import (
    remove_truth_fields,
    sanitize_asset,
    sanitize_coverage,
    sanitize_fused_track,
    sanitize_operator_payload,
    sanitize_stats,
    sanitize_weapon,
    truth_terms_from_state,
)
from amos_platform.frontend_state.temporal_story import project_story_at_time
from amos_platform.domain.mission_phases import build_mission_phase_state


def build_operator_state(
    internal_state: dict[str, Any],
    *,
    include_network_history: bool = False,
) -> dict[str, Any]:
    """Build the map/dashboard state shown to ordinary operators."""
    truth_terms = truth_terms_from_state(internal_state)
    tracks = []
    for index, track in enumerate(internal_state.get("fused_tracks") or [], start=1):
        safe_track = sanitize_fused_track(track)
        domain = str(track.get("domain_hint") or "").casefold()
        prefix = "空中接触" if domain == "air" else ("地面接触" if domain == "ground" else "海面接触")
        safe_track["display_label"] = str(track.get("display_label") or f"{prefix} {index:02d}")
        tracks.append(safe_track)
    clock = deepcopy(internal_state.get("clock") or {})
    story = project_story_at_time(
        internal_state.get("scenario_story") or {},
        float(clock.get("elapsed_sec", 0) or 0),
        branch=str(clock.get("scenario_branch") or "") or None,
    )
    mission_phases = build_mission_phase_state(story, clock)
    network = deepcopy(internal_state.get("network") or {})
    if not include_network_history:
        # The dashboard renders current topology only. Repeating up to ten
        # minutes of link samples in every 2 Hz SSE frame adds substantial
        # transfer and JSON parsing cost without changing the visible UI.
        network.pop("history_records", None)
    return sanitize_operator_payload({
        "visibility": "operator-visible",
        "clock": clock,
        "assets": [
            sanitize_asset(asset)
            for asset in internal_state.get("assets") or []
            if not asset.get("operator_hidden")
        ],
        "weapons": [
            sanitize_weapon(weapon, truth_terms)
            for weapon in internal_state.get("weapons") or []
        ],
        "fused_tracks": tracks,
        "kill_chain": deepcopy(internal_state.get("kill_chain") or {}),
        "scenario_story": remove_truth_fields(story, truth_terms),
        "mission_phases": mission_phases,
        "coverage": sanitize_coverage(internal_state.get("coverage") or {}),
        "network": remove_truth_fields(network, truth_terms),
        "coordination_links": remove_truth_fields(internal_state.get("coordination_links") or [], truth_terms),
        "alerts": remove_truth_fields(internal_state.get("alerts") or [], truth_terms),
        "tasks": remove_truth_fields(internal_state.get("tasks") or [], truth_terms),
        "kill_chain_events": remove_truth_fields(internal_state.get("kill_chain_events") or [], truth_terms),
        "stats": sanitize_stats(internal_state.get("stats") or {}),
    }, source_state=internal_state)
