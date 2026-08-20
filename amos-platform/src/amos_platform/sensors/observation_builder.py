"""Build agent-visible observations from platform state."""

from __future__ import annotations

from typing import Any

from amos_platform.domain.policies.visibility import observation_from_track, remove_truth_fields


def build_observations_from_tracks(tracks: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Convert safe fused-track views into Agent observations."""
    observations: list[dict[str, Any]] = []
    for track in tracks:
        observation = observation_from_track(track)
        if observation:
            observations.append(remove_truth_fields(observation))
    return observations
