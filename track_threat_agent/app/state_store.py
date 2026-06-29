"""Lightweight JSON snapshot storage for demo Agent restart recovery."""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict

from .models import TrackGroup, TrackState


STATE_SCHEMA_VERSION = 1


@dataclass
class RestoredAgentState:
    tracks: Dict[str, TrackState]
    groups: Dict[str, TrackGroup]
    last_artifact: Dict[str, Any]
    runtime_state: Dict[str, Any]


class FileStateStore:
    """Persist restart-safe Agent state to a local JSON file.

    This is intentionally small and dependency-free. It is not a replacement
    for Redis/PostgreSQL in production, but it makes local demos resilient to a
    process restart and gives future storage providers a clear contract.
    """

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)

    def save(
        self,
        tracks: Dict[str, TrackState],
        groups: Dict[str, TrackGroup],
        last_artifact: Dict[str, Any],
        runtime_state: Dict[str, Any],
    ) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "schema_version": STATE_SCHEMA_VERSION,
            "tracks": {track_id: track.model_dump() for track_id, track in tracks.items()},
            "groups": {group_id: group.model_dump() for group_id, group in groups.items()},
            "last_artifact": last_artifact,
            "runtime_state": runtime_state,
        }
        tmp_path = self.path.with_suffix(f"{self.path.suffix}.tmp")
        tmp_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")
        os.replace(tmp_path, self.path)

    def load(self) -> RestoredAgentState | None:
        if not self.path.exists():
            return None
        payload = json.loads(self.path.read_text(encoding="utf-8"))
        if payload.get("schema_version") != STATE_SCHEMA_VERSION:
            return None
        tracks = {
            track_id: TrackState.model_validate(track_payload)
            for track_id, track_payload in (payload.get("tracks") or {}).items()
        }
        groups = {
            group_id: TrackGroup.model_validate(group_payload)
            for group_id, group_payload in (payload.get("groups") or {}).items()
        }
        return RestoredAgentState(
            tracks=tracks,
            groups=groups,
            last_artifact=payload.get("last_artifact") or {},
            runtime_state=payload.get("runtime_state") or {},
        )

    def clear(self) -> None:
        if self.path.exists():
            self.path.unlink()
