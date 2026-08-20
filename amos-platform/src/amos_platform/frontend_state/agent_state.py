"""Agent-visible state projection."""

from __future__ import annotations

from copy import deepcopy
from typing import Any

from amos_platform.domain.policies.visibility import (
    observation_from_track,
    sanitize_agent_payload,
    sanitize_fused_track,
)
from amos_platform.frontend_state.operator_state import build_operator_state
from amos_platform.sensors.observation_builder import build_observations_from_tracks


def build_agent_visible_state(internal_state: dict[str, Any]) -> dict[str, Any]:
    """Build the packet-safe state visible to external Agent backends."""
    operator_state = build_operator_state(internal_state)
    observation_batch = deepcopy(internal_state.get("observation_batch") or {})
    observations = list(observation_batch.get("observations") or [])
    media_items = list(observation_batch.get("media_refs") or observation_batch.get("media_items") or [])
    if not observations:
        observations = build_observations_from_tracks(operator_state.get("fused_tracks") or [])
    if not observations:
        observations = [
            observation
            for track in operator_state.get("fused_tracks") or []
            if (observation := observation_from_track(track))
        ]
    if not observation_batch:
        observation_batch = {
            "visibility": "agent-visible",
            "observer_scope": {"type": "own_force"},
            "observations": observations,
            "media_refs": media_items,
        }
    return sanitize_agent_payload({
        "visibility": "agent-visible",
        "clock": operator_state.get("clock", {}),
        "own_asset_poses": operator_state.get("assets", []),
        "observation_batch": observation_batch,
        "observations": observations,
        "fused_tracks": [
            sanitize_fused_track(track)
            for track in internal_state.get("fused_tracks") or []
        ],
        "sensor_frames": [],
        "media": {"items": media_items},
        # The projected story contains only cues and media released at the
        # current simulation time.  Exchange events use it to publish causal
        # media references without exposing the engine's future script.
        "scenario_story": deepcopy(operator_state.get("scenario_story") or {}),
        "coverage": operator_state.get("coverage", {}),
        "network": operator_state.get("network", {}),
        "alerts": operator_state.get("alerts", []),
    }, source_state=internal_state)
