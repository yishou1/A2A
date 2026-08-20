"""Versioned, causal simulation exchange contract for external orchestrators."""

from __future__ import annotations

import hashlib
import json
import os
from copy import deepcopy
from datetime import datetime, timezone
from threading import RLock
from typing import Any
from urllib.parse import urljoin

from amos_platform.agents.a2a.mission_contract import map_observations


SNAPSHOT_SCHEMA_VERSION = "amos.simulation.snapshot.v1"
EVENT_SCHEMA_VERSION = "amos.simulation.event.v1"
EVENT_SAMPLE_INTERVAL_MS = 2000
PROVENANCE = {
    "mode": "simulation",
    "generator": "amos_simulation",
    "simulated": True,
}


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def chain_id_for(scenario_id: str) -> str:
    return f"{scenario_id}:situation-analysis"


def _fingerprint(value: Any) -> str:
    encoded = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _status(state: dict[str, Any]) -> str:
    clock = state.get("clock") or {}
    lifecycle = str(clock.get("lifecycle") or "")
    if lifecycle in {"ready", "running", "paused", "stopped", "completed", "error"}:
        return lifecycle
    if clock.get("running"):
        return "running"
    elapsed = float(clock.get("elapsed_sec", 0.0) or 0.0)
    story = state.get("scenario_story") or {}
    duration = float((story.get("demo_controls") or {}).get("duration_sec", 0.0) or 0.0)
    if duration and elapsed >= duration:
        return "completed"
    if elapsed > 0:
        return "paused"
    return "ready" if state.get("assets") else "idle"


def _media_refs(state: dict[str, Any]) -> list[dict[str, Any]]:
    story = state.get("scenario_story") or {}
    public_base_url = os.environ.get("AMOS_PUBLIC_BASE_URL", "http://127.0.0.1:5000/")
    refs = []
    for item in story.get("media_cues") or []:
        uri = item.get("uri") or item.get("media_uri")
        checksum = item.get("checksum")
        media_id = item.get("media_id")
        if not uri or not checksum or not media_id:
            continue
        refs.append({
            "media_id": str(media_id),
            # Gateway only normalizes legacy /static paths.  Runtime evidence
            # products use /api paths, so AMOS must publish a directly
            # retrievable absolute reference for both families.
            "uri": urljoin(public_base_url.rstrip("/") + "/", str(uri).lstrip("/")),
            "mime_type": str(item.get("mime_type") or item.get("media_type") or "application/octet-stream"),
            "checksum": str(checksum),
            "source_name": str(item.get("sensor_instance_id") or item.get("title") or media_id),
            "provenance": deepcopy(PROVENANCE),
        })
    return refs


def _assets(state: dict[str, Any]) -> list[dict[str, Any]]:
    rows = []
    for asset in state.get("own_asset_poses") or state.get("assets") or []:
        if not isinstance(asset, dict):
            continue
        position = asset.get("position") if isinstance(asset.get("position"), dict) else asset
        rows.append({
            "asset_id": asset.get("id") or asset.get("asset_id"),
            "asset_type": asset.get("type") or asset.get("role") or "generic",
            "domain": asset.get("domain"),
            "status": asset.get("status"),
            "position": {
                "lat": position.get("lat"),
                "lon": position.get("lng", position.get("lon")),
                "alt_ft": position.get("alt_ft", 0),
            },
            "heading_deg": asset.get("heading", asset.get("heading_deg", 0)),
            "speed_kts": asset.get("speed_kts", 0),
            "sensors": list(asset.get("sensors") or []),
            "battery_pct": asset.get("battery_pct"),
            "fuel_pct": asset.get("fuel_pct"),
            "comms_strength": asset.get("comms_strength"),
            "history_path": deepcopy(asset.get("history_path") or []),
        })
    return rows


def _tracks(state: dict[str, Any]) -> list[dict[str, Any]]:
    rows = []
    for track in state.get("fused_tracks") or []:
        if not isinstance(track, dict):
            continue
        track_id = track.get("track_id") or track.get("id")
        if not track_id:
            continue
        rows.append({
            "track_id": str(track_id),
            "lat": track.get("lat"),
            "lon": track.get("lng", track.get("lon")),
            "domain_hint": track.get("domain_hint"),
            "classification": track.get("classification") or "UNKNOWN",
            "confidence": track.get("confidence"),
            "heading_deg": track.get("heading"),
            "history_path": deepcopy(track.get("history_path") or []),
            "source_observation_ids": [
                str(ref["observation_id"])
                for ref in track.get("source_refs") or []
                if isinstance(ref, dict) and ref.get("observation_id")
            ],
            "sensor_sources": list(track.get("sources") or []),
            "uncertainty": deepcopy(track.get("uncertainty") or {}),
            "velocity": deepcopy(track.get("velocity") or {}),
        })
    return rows


def _alerts(state: dict[str, Any]) -> list[dict[str, Any]]:
    allowed = {"level", "msg", "time", "type", "cue_id", "phase"}
    return [
        {key: deepcopy(value) for key, value in item.items() if key in allowed}
        for item in state.get("alerts") or []
        if isinstance(item, dict)
    ]


class SimulationExchange:
    """Keep a continuous event cursor for one in-process simulation run."""

    def __init__(self) -> None:
        self._lock = RLock()
        self.reset()

    def reset(self) -> None:
        with self._lock:
            self._run_id = ""
            self._last_fingerprint = ""
            self._last_event_signature = ""
            self._last_event_sim_time_ms = -EVENT_SAMPLE_INTERVAL_MS
            self._events: list[dict[str, Any]] = []
            self._last_snapshot: dict[str, Any] = {}
            self._submission_context: dict[str, Any] = {}
            self._last_media_ids: set[str] = set()

    def _ensure_run(self, run_id: str) -> None:
        if self._run_id != run_id:
            self._run_id = run_id
            self._last_fingerprint = ""
            self._last_event_signature = ""
            self._last_event_sim_time_ms = -EVENT_SAMPLE_INTERVAL_MS
            self._events = []
            self._last_snapshot = {}
            self._submission_context = {}
            self._last_media_ids = set()

    def set_submission_context(self, run_id: str, context: dict[str, Any]) -> None:
        """Attach a truth-safe stage manifest to the selected chain.

        Gateway v1 forbids new top-level snapshot fields, but permits chain
        metadata. Keeping the manifest there preserves strict schema
        compatibility while allowing Commander to identify the incremental
        evidence for this submission.
        """
        with self._lock:
            self._ensure_run(str(run_id))
            self._submission_context = deepcopy(context)

    def build_snapshot(self, state: dict[str, Any]) -> dict[str, Any]:
        """Project one causal snapshot and advance the cursor only on change."""
        clock = state.get("clock") or {}
        run_id = str(clock.get("run_id") or "run-not-started")
        scenario_id = str(clock.get("scenario_id") or (state.get("scenario_story") or {}).get("scenario_id") or "unloaded")
        sim_time_ms = max(0, int(round(float(clock.get("elapsed_sec", 0.0) or 0.0) * 1000)))
        media_refs = _media_refs(state)
        assets = _assets(state)
        observations = map_observations(state.get("observations") or [])
        tracks = _tracks(state)
        alerts = _alerts(state)
        network = deepcopy(state.get("network") or {})
        status = _status(state)
        story = state.get("scenario_story") or {}
        phase = str((story.get("timeline") or [{}])[-1].get("phase") or "MONITOR")
        chain_id = chain_id_for(scenario_id)
        chain = {
            "chain_id": chain_id,
            "scenario_id": scenario_id,
            "name": "当前态势综合分析",
            "phase": phase,
            "status": status,
            "objective": "基于当前及历史观测完成识别、跟踪、风险评估和安全处置建议",
        }
        with self._lock:
            self._ensure_run(run_id)
            submission_context = deepcopy(self._submission_context)
        if submission_context:
            chain["submission_context"] = submission_context
        content = {
            "run_id": run_id,
            "scenario_id": scenario_id,
            "sim_time_ms": sim_time_ms,
            "status": status,
            "assets": assets,
            "observations": observations,
            "tracks": tracks,
            "alerts": alerts,
            "network": network,
            "simulation_chains": [chain],
            "media_refs": media_refs,
            "phase": phase,
        }
        fingerprint = _fingerprint(content)
        event_signature = _fingerprint({
            "status": status,
            "phase": phase,
            "media_ids": [item["media_id"] for item in media_refs],
            "track_ids": [item["track_id"] for item in tracks],
            "latest_alert": alerts[-1] if alerts else None,
        })
        with self._lock:
            sample_due = sim_time_ms - self._last_event_sim_time_ms >= EVENT_SAMPLE_INTERVAL_MS
            material_change = event_signature != self._last_event_signature
            if not self._events or sample_due or material_change:
                new_media_refs = [
                    item for item in media_refs
                    if str(item.get("media_id") or "") not in self._last_media_ids
                ]
                sequence = len(self._events) + 1
                self._events.append({
                    "schema_version": EVENT_SCHEMA_VERSION,
                    "event_id": f"{run_id}-event-{sequence:06d}",
                    "run_id": run_id,
                    "sequence": sequence,
                    "sim_time_ms": sim_time_ms,
                    "occurred_at": _now_iso(),
                    "event_type": "simulation.state.updated",
                    "phase": phase,
                    "data": {
                        "scenario_id": scenario_id,
                        "status": status,
                        "asset_count": len(assets),
                        "observation_count": len(observations),
                        "track_count": len(tracks),
                        "alert_count": len(alerts),
                        "assets": [
                            {
                                "asset_id": item.get("asset_id"),
                                "position": deepcopy(item.get("position") or {}),
                                "heading_deg": item.get("heading_deg"),
                                "speed_kts": item.get("speed_kts"),
                                "status": item.get("status"),
                            }
                            for item in assets
                        ],
                        "tracks": [
                            {
                                "track_id": item.get("track_id"),
                                "lat": item.get("lat"),
                                "lon": item.get("lon"),
                                "confidence": item.get("confidence"),
                                "domain_hint": item.get("domain_hint"),
                            }
                            for item in tracks
                        ],
                        "network": deepcopy(network),
                    },
                    # Event media references are deltas. The full history is
                    # reconstructed from the continuous event stream, avoiding
                    # repeated transmission of every earlier image at each tick.
                    "media_refs": deepcopy(new_media_refs),
                    "source": "amos_simulation",
                    "provenance": deepcopy(PROVENANCE),
                })
                self._last_event_signature = event_signature
                self._last_event_sim_time_ms = sim_time_ms
                self._last_media_ids.update(
                    str(item.get("media_id") or "") for item in new_media_refs
                    if item.get("media_id")
                )
            self._last_fingerprint = fingerprint
            snapshot = {
                "schema_version": SNAPSHOT_SCHEMA_VERSION,
                "run_id": run_id,
                "scenario_id": scenario_id,
                "sequence": len(self._events),
                "sim_time_ms": sim_time_ms,
                "status": status,
                "assets": assets,
                "observations": observations,
                "tracks": tracks,
                "alerts": alerts,
                "network": network,
                "simulation_chains": [chain],
                "recent_events": deepcopy(self._events[-20:]),
                "provenance": deepcopy(PROVENANCE),
            }
            self._last_snapshot = deepcopy(snapshot)
            return snapshot

    def events_after(self, after_sequence: int = 0) -> list[dict[str, Any]]:
        with self._lock:
            return [
                deepcopy(event)
                for event in self._events
                if int(event["sequence"]) > int(after_sequence)
            ]


__all__ = [
    "EVENT_SCHEMA_VERSION",
    "EVENT_SAMPLE_INTERVAL_MS",
    "PROVENANCE",
    "SNAPSHOT_SCHEMA_VERSION",
    "SimulationExchange",
    "chain_id_for",
]
