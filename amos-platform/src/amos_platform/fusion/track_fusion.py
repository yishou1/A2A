"""Sensor Fusion Engine - Track correlation and kill chain for simulation platform.

Stripped-down version adapted from amos-autonomous/services/sensor_fusion_engine.py.
No DB persistence, no SocketIO, just core fusion logic.
"""

from __future__ import annotations

import math
import random
import time
import uuid
from copy import deepcopy

from amos_platform.domain.policies.kill_chain import KILL_CHAIN_PHASES, advance_track_phase
from amos_platform.sensors.simulator import SensorSimulator
from amos_platform.sensors.coverage import (
    NM_TO_DEG,
    SENSOR_COVERAGE,
    distance_nm,
    normalize_sensor_name as _normalize_sensor_name,
    offset_position,
)


# Fused track.

class FusedTrack:
    """A correlated track from multiple sensor detections."""

    __slots__ = (
        "id", "lat", "lng", "sources", "confidence", "classification",
        "threat_level", "velocity_lat", "velocity_lng",
        "uncertainty_semi_major", "uncertainty_semi_minor", "uncertainty_angle",
        "last_update", "created", "kill_chain_phase", "kill_chain_times",
        "associated_threat_id", "source_refs",
        "domain_hint",
        "history_path",
        "heading_deg", "agent_assessment", "motion_sim_time",
        "created_sim_time", "last_sim_time",
    )

    def __init__(self, track_id: str, lat: float, lng: float, source_id: str,
                 domain_hint: str | None = None, sim_time: float | None = None):
        now = time.time()
        self.id = track_id
        self.lat = lat
        self.lng = lng
        self.sources: dict[str, float] = {source_id: now}
        self.confidence = 0.5
        self.classification = "UNKNOWN"
        self.threat_level = "UNKNOWN"
        self.velocity_lat = 0.0
        self.velocity_lng = 0.0
        self.uncertainty_semi_major = 0.005
        self.uncertainty_semi_minor = 0.003
        self.uncertainty_angle = 0.0
        self.last_update = now
        self.created = now
        self.kill_chain_phase: str = "FIND"
        self.kill_chain_times: dict[str, float] = {"FIND": now}
        self.associated_threat_id: str | None = None
        self.source_refs: list[dict] = []
        self.domain_hint = domain_hint
        self.history_path: list[dict] = [{
            "lat": lat,
            "lng": lng,
            "sim_time": float(sim_time or 0.0),
            "confidence": self.confidence,
            "heading": None,
            "uncertainty_semi_major_deg": self.uncertainty_semi_major,
            "uncertainty_semi_minor_deg": self.uncertainty_semi_minor,
            "uncertainty_angle_deg": self.uncertainty_angle,
            "source_observation_ids": [],
        }]
        self.heading_deg: float | None = None
        self.agent_assessment: dict = {
            "status": "pending",
            "label": "待后端识别",
            "source": None,
        }
        self.motion_sim_time = sim_time
        self.created_sim_time = float(sim_time or 0.0)
        self.last_sim_time = float(sim_time or 0.0)

    def update(self, lat: float, lng: float, source_id: str,
               classification: str | None = None, threat_level: str | None = None,
               source_ref: dict | None = None, sim_time: float | None = None):
        now = time.time()
        alpha = 0.3 if len(self.sources) > 2 else 0.5
        next_lat = self.lat * (1 - alpha) + lat * alpha
        next_lng = self.lng * (1 - alpha) + lng * alpha
        has_new_motion_sample = (
            sim_time is None
            or self.motion_sim_time is None
            or float(sim_time) > float(self.motion_sim_time)
        )
        if has_new_motion_sample:
            dt = max(
                0.01,
                float(sim_time) - float(self.motion_sim_time)
                if sim_time is not None and self.motion_sim_time is not None
                else now - self.last_update,
            )
            dlat = next_lat - self.lat
            dlng = next_lng - self.lng
            self.velocity_lat = dlat / dt
            self.velocity_lng = dlng / dt
            if abs(dlat) + abs(dlng) > 0.000001:
                measured = (math.degrees(math.atan2(
                    dlng * math.cos(math.radians((self.lat + next_lat) / 2.0)), dlat,
                )) + 360.0) % 360.0
                if self.heading_deg is None:
                    self.heading_deg = measured
                else:
                    delta = (measured - self.heading_deg + 180.0) % 360.0 - 180.0
                    self.heading_deg = (self.heading_deg + 0.35 * delta) % 360.0
            self.motion_sim_time = sim_time
            if sim_time is not None:
                self.last_sim_time = float(sim_time)
        self.lat = next_lat
        self.lng = next_lng
        previous = self.history_path[-1] if self.history_path else None
        point_time = float(sim_time if sim_time is not None else self.motion_sim_time or 0.0)
        point = {
            "lat": round(self.lat, 6),
            "lng": round(self.lng, 6),
            "sim_time": point_time,
        }
        # A fusion cycle can contain many sensor observations for the same
        # target. Keep one settled point per simulation instant instead of
        # drawing every intermediate filter update as target movement.
        if previous and abs(float(previous.get("sim_time", 0.0)) - point_time) < 0.001:
            self.history_path[-1] = point
        elif not previous or abs(previous["lat"] - self.lat) + abs(previous["lng"] - self.lng) > 0.00002:
            self.history_path.append(point)
            self.history_path = self.history_path[-120:]
        self.sources[source_id] = now
        if source_ref:
            self.source_refs.append(dict(source_ref))
            self.source_refs = self.source_refs[-20:]
        self.last_update = now
        self.confidence = min(0.99, 0.3 + len(self.sources) * 0.15)
        n = len(self.sources)
        self.uncertainty_semi_major = max(0.0005, 0.005 / math.sqrt(n))
        self.uncertainty_semi_minor = max(0.0003, 0.003 / math.sqrt(n))
        if self.history_path:
            current_point = self.history_path[-1]
            current_point.update({
                "confidence": round(self.confidence, 4),
                "heading": round(self.heading_deg, 2) if self.heading_deg is not None else None,
                "uncertainty_semi_major_deg": round(self.uncertainty_semi_major, 6),
                "uncertainty_semi_minor_deg": round(self.uncertainty_semi_minor, 6),
                "uncertainty_angle_deg": round(self.uncertainty_angle, 2),
                "source_observation_ids": sorted({
                    str(ref["observation_id"])
                    for ref in self.source_refs
                    if ref.get("observation_id")
                    and (
                        ref.get("sim_time") is None
                        or abs(float(ref["sim_time"]) - point_time) < 0.001
                    )
                }),
            })
        if classification and classification != "UNKNOWN":
            self.classification = classification
        if threat_level and threat_level != "UNKNOWN":
            self.threat_level = threat_level
        self._advance_kill_chain()

    def predict(self, dt_sec: float) -> dict:
        return {
            "lat": round(self.lat + self.velocity_lat * dt_sec, 6),
            "lng": round(self.lng + self.velocity_lng * dt_sec, 6),
            "uncertainty_growth": round(self.uncertainty_semi_major * (1 + dt_sec * 0.01), 4),
        }

    def _advance_kill_chain(self):
        """Advance track through F2T2EA kill chain: FIND -> FIX -> TRACK -> TARGET -> ENGAGE."""
        now = time.time()
        next_phase = advance_track_phase(
            self.kill_chain_phase,
            confidence=self.confidence,
            classification=self.classification,
            threat_level=self.threat_level,
        )
        if next_phase != self.kill_chain_phase:
            self.kill_chain_phase = next_phase
            self.kill_chain_times[next_phase] = now

    @property
    def age_sec(self) -> float:
        return time.time() - self.created

    @property
    def is_active(self) -> bool:
        return (time.time() - self.last_update) < 60

    def is_active_at(self, sim_time: float, timeout_sec: float = 60.0) -> bool:
        return float(sim_time) - self.last_sim_time < timeout_sec

    def to_dict(self, *, current_sim_time: float | None = None) -> dict:
        return {
            "id": self.id,
            "lat": round(self.lat, 6),
            "lng": round(self.lng, 6),
            "confidence": round(self.confidence, 2),
            "classification": self.classification,
            "threat_level": self.threat_level,
            "domain_hint": self.domain_hint,
            "heading": round(self.heading_deg, 1) if self.heading_deg is not None else None,
            "agent_assessment": dict(self.agent_assessment),
            "history_path": list(self.history_path),
            "source_count": len(self.sources),
            "sources": list(self.sources.keys()),
            "source_refs": list(self.source_refs),
            "velocity": {"lat": round(self.velocity_lat, 6), "lng": round(self.velocity_lng, 6)},
            "uncertainty": {
                "semi_major_deg": round(self.uncertainty_semi_major, 6),
                "semi_minor_deg": round(self.uncertainty_semi_minor, 6),
                "angle_deg": round(self.uncertainty_angle, 1),
            },
            "kill_chain": {
                "phase": self.kill_chain_phase,
                "times": {k: round(v, 1) for k, v in self.kill_chain_times.items()},
            },
            "predicted_5min": self.predict(300),
            "age_sec": round(
                max(0.0, float(current_sim_time) - self.created_sim_time)
                if current_sim_time is not None else self.age_sec,
                1,
            ),
        }


# Sensor fusion engine.

class SensorFusionEngine:
    """Multi-sensor track correlation engine."""

    def __init__(self, *, rng: random.Random | None = None):
        self._rng = rng or random.Random()
        self.tracks: dict[str, FusedTrack] = {}
        self.events: list[dict] = []
        self.last_coverage: dict[str, list[dict]] = {}
        self.coverage_gaps: list[dict] = []
        self.admin_coverage_gaps: list[dict] = []
        self.truth_associations: list[dict] = []
        self.last_observation_batch: dict = {}
        self.observation_history: list[dict] = []
        self.truth_association_history: list[dict] = []
        self._tick_count = 0
        self.current_sim_time = 0.0

    def _record_observation_history(
        self,
        batch: dict,
        associations: list[dict] | None,
    ) -> None:
        """Retain a bounded private history for time-window evidence products."""
        sim_time = float(batch.get("sim_time", self.current_sim_time) or 0.0)
        for item in batch.get("observations") or []:
            if isinstance(item, dict) and item.get("observation_id"):
                self.observation_history.append(deepcopy(item))
        for item in associations or []:
            if isinstance(item, dict) and item.get("observation_id"):
                record = deepcopy(item)
                record["sim_time"] = sim_time
                self.truth_association_history.append(record)
        cutoff = sim_time - 900.0
        self.observation_history = [
            item for item in self.observation_history[-12000:]
            if float(item.get("sim_time", 0) or 0) >= cutoff
        ]
        self.truth_association_history = [
            item for item in self.truth_association_history[-12000:]
            if float(item.get("sim_time", 0) or 0) >= cutoff
        ]

    def sample_observations(
        self,
        assets: dict,
        threats: dict,
        *,
        sim_time: float = 0.0,
        run_id: str = "",
        scenario_id: str = "",
    ) -> dict:
        """Prime a current sensor batch without creating fused tracks."""
        simulator = SensorSimulator(rng=self._rng)
        generated = simulator.generate_observations(
            assets,
            threats,
            sim_time=sim_time,
            tick_id=self._tick_count,
            run_id=run_id,
            scenario_id=scenario_id,
        )
        self.truth_associations = generated.get("truth_associations", [])
        self.last_observation_batch = deepcopy(generated.get("batch") or {})
        self._record_observation_history(
            self.last_observation_batch,
            self.truth_associations,
        )
        return deepcopy(self.last_observation_batch)

    def tick(
        self,
        assets: dict,
        threats: dict,
        dt: float = 1.0,
        *,
        sim_time: float | None = None,
        run_id: str = "",
        scenario_id: str = "",
    ) -> list[dict]:
        """Compatibility wrapper: generate observations, then fuse observations."""
        self._tick_count += 1
        self.current_sim_time = float(sim_time) if sim_time is not None else self._tick_count * dt
        simulator = SensorSimulator(rng=self._rng)
        generated = simulator.generate_observations(
            assets,
            threats,
            sim_time=float(sim_time) if sim_time is not None else self._tick_count * dt,
            tick_id=self._tick_count,
            run_id=run_id,
            scenario_id=scenario_id,
        )
        self.truth_associations = generated.get("truth_associations", [])
        self._record_observation_history(
            generated.get("batch") or {},
            self.truth_associations,
        )
        events = self.tick_observations(generated["batch"], dt=dt)
        self._update_coverage(assets, threats)
        return events

    def tick_observations(
        self,
        observation_batch: dict,
        perception_results: list[dict] | None = None,
        dt: float = 1.0,
    ) -> list[dict]:
        """One fusion cycle from agent-visible observations."""
        self.last_observation_batch = deepcopy(observation_batch or {})
        if observation_batch.get("sim_time") is not None:
            self.current_sim_time = float(observation_batch["sim_time"])
        events: list[dict] = []

        for observation in observation_batch.get("observations") or []:
            event = self.update_from_observation(observation, observation_batch)
            if event:
                events.append(event)

        for result in perception_results or []:
            self.update_from_platform_perception_result(result)

        stale_ids = [
            tid for tid, t in self.tracks.items()
            if not t.is_active_at(self.current_sim_time)
            and self.current_sim_time - t.created_sim_time > 120.0
        ]
        for tid in stale_ids:
            events.append({
                "type": "track_lost",
                "track_id": tid,
                "timestamp": time.time(),
            })
            self.tracks.pop(tid, None)

        self.events.extend(events)
        if len(self.events) > 200:
            self.events = self.events[-100:]

        return events

    def _observation_position(self, observation: dict, observation_batch: dict) -> tuple[float, float] | None:
        position = observation.get("position")
        if isinstance(position, dict) and position.get("lat") is not None and position.get("lng") is not None:
            return float(position["lat"]), float(position["lng"])

        asset_id = observation.get("asset_id")
        asset = None
        for candidate in observation_batch.get("own_asset_poses") or []:
            if candidate.get("id") == asset_id or candidate.get("asset_id") == asset_id:
                asset = candidate
                break
        if not asset:
            return None
        apos = asset.get("position", asset)
        alat = float(apos.get("lat", 0.0))
        alng = float(apos.get("lng", apos.get("lon", 0.0)))
        bearing = math.radians(float(observation.get("bearing_deg", 0.0)))
        range_nm = float(observation.get("range_nm", 0.0))
        return offset_position(alat, alng, math.degrees(bearing), range_nm)

    def update_from_observation(self, observation: dict, observation_batch: dict | None = None) -> dict | None:
        observation_batch = observation_batch or {}
        position = self._observation_position(observation, observation_batch)
        if not position:
            return None
        lat, lng = position
        track = self.associate_observation_to_track(observation, lat, lng)
        source_id = str(observation.get("sensor_id") or observation.get("modality") or "sensor")
        source_ref = {
            "observation_id": observation.get("observation_id"),
            "sensor_id": observation.get("sensor_id"),
            "asset_id": observation.get("asset_id"),
            "sim_time": observation.get("sim_time"),
        }
        for media in observation_batch.get("media_refs") or observation_batch.get("media_items") or []:
            metadata = media.get("metadata") or {}
            if metadata.get("source_observation_id") == observation.get("observation_id"):
                source_ref["media_id"] = media.get("media_id")
                break
        if track:
            if not track.domain_hint and observation.get("domain_hint"):
                track.domain_hint = str(observation["domain_hint"])
            track.update(
                lat,
                lng,
                source_id,
                source_ref=source_ref,
                sim_time=observation.get("sim_time"),
            )
            return None

        new_track = FusedTrack(
            f"TRK-{uuid.UUID(int=self._rng.getrandbits(128)).hex[:6]}", lat, lng, source_id,
            domain_hint=str(observation.get("domain_hint") or "") or None,
            sim_time=observation.get("sim_time"),
        )
        new_track.confidence = float(observation.get("confidence") or new_track.confidence)
        new_track.source_refs.append(source_ref)
        new_track.history_path[-1].update({
            "confidence": new_track.confidence,
            "source_observation_ids": [str(observation.get("observation_id"))]
            if observation.get("observation_id") else [],
        })
        self.tracks[new_track.id] = new_track
        return {
            "type": "track_created",
            "track_id": new_track.id,
            "observation_id": observation.get("observation_id"),
            "source": source_id,
            "confidence": round(new_track.confidence, 2),
            "timestamp": time.time(),
        }

    def update_from_platform_perception_result(self, result: dict) -> None:
        observation_id = result.get("source_observation_id")
        source_media_id = result.get("source_media_id")
        for track in self.tracks.values():
            for ref in track.source_refs:
                if observation_id and ref.get("observation_id") == observation_id:
                    self._apply_perception_result(track, result)
                    return
                if source_media_id and ref.get("media_id") == source_media_id:
                    self._apply_perception_result(track, result)
                    return

    def _apply_perception_result(self, track: FusedTrack, result: dict) -> None:
        label = result.get("label")
        if label:
            track.classification = str(label)
        confidence = result.get("confidence")
        if confidence is not None:
            track.confidence = max(track.confidence, min(0.99, float(confidence)))
        level = result.get("threat_level") or result.get("threat_assessment")
        if level:
            track.threat_level = str(level)

    def associate_observation_to_track(
        self,
        observation: dict,
        lat: float | None = None,
        lng: float | None = None,
    ) -> FusedTrack | None:
        if lat is None or lng is None:
            return None
        best: FusedTrack | None = None
        best_dist = float("inf")
        observation_domain = str(observation.get("domain_hint") or "")
        for track in self.tracks.values():
            if not track.is_active_at(self.current_sim_time):
                continue
            if observation_domain and track.domain_hint and observation_domain != track.domain_hint:
                continue
            dist = distance_nm(track.lat, track.lng, lat, lng)
            if dist < best_dist:
                best = track
                best_dist = dist
        return best if best is not None and best_dist <= 3.0 else None

    def _find_track_for_threat(self, threat_id: str) -> FusedTrack | None:
        for t in self.tracks.values():
            if t.associated_threat_id == threat_id and t.is_active_at(self.current_sim_time):
                return t
        return None

    def _update_coverage(self, assets: dict, threats: dict):
        """Compute sensor coverage footprints and gaps."""
        self.last_coverage = {}
        for aid, asset in assets.items():
            apos = asset.get("position", asset)
            lat, lng = apos.get("lat", 0), apos.get("lng", 0)
            heading = asset.get("heading_deg", asset.get("heading", 0))

            footprints = []
            for sname in asset.get("sensors", []):
                sname_norm = _normalize_sensor_name(sname)
                spec = SENSOR_COVERAGE.get(sname_norm)
                if not spec or spec["range_nm"] == 0:
                    continue
                footprints.append({
                    "sensor": sname,
                    "center_lat": lat, "center_lng": lng,
                    "range_nm": spec["range_nm"],
                    "range_deg": round(spec["range_nm"] * NM_TO_DEG, 4),
                    "fov_deg": spec["fov_deg"],
                    "detect_air": spec["detect_air"],
                    "detect_ground": spec["detect_ground"],
                    "detect_maritime": spec["detect_maritime"],
                })
            self.last_coverage[aid] = footprints

        # Admin-only truth coverage gaps plus ordinary aggregate summaries.
        self.coverage_gaps = []
        self.admin_coverage_gaps = []
        uncovered_count = 0
        for tid, threat in threats.items():
            if threat.get("neutralized"):
                continue
            tlat, tlng = threat.get("lat", 0), threat.get("lng", 0)
            covered = False
            for aid, a in assets.items():
                if a.get("status") not in ("operational", "active"):
                    continue
                apos = a.get("position", a)
                alat, alng = apos.get("lat", 0), apos.get("lng", 0)
                dist = distance_nm(alat, alng, tlat, tlng)
                for fp in self.last_coverage.get(aid, []):
                    if dist <= fp["range_nm"]:
                        covered = True
                        break
                if covered:
                    break
            if not covered:
                uncovered_count += 1
                self.admin_coverage_gaps.append({
                    "threat_id": tid,
                    "lat": tlat, "lng": tlng,
                    "type": threat.get("type", threat.get("threat_type", "unknown")),
                    "visibility": "admin-only",
                })
        if uncovered_count:
            self.coverage_gaps.append({
                "uncovered_area_count": uncovered_count,
                "sensor_type": "multi_sensor",
                "reason": "coverage_gap",
                "visibility": "operator-visible",
            })

    def get_tracks(self, *, include_stale: bool = False) -> dict[str, dict]:
        """Return fused tracks, optionally retaining the final paused-state picture."""
        return {
            tid: t.to_dict(current_sim_time=self.current_sim_time)
            for tid, t in self.tracks.items()
            if include_stale or t.is_active_at(self.current_sim_time)
        }

    def get_coverage(self) -> dict[str, list[dict]]:
        return dict(self.last_coverage)

    def get_coverage_gaps(self) -> list[dict]:
        return list(self.coverage_gaps)

    def get_admin_coverage_gaps(self) -> list[dict]:
        return list(self.admin_coverage_gaps)

    def get_kill_chain_summary(self, *, include_stale: bool = False) -> dict:
        counts = {p: 0 for p in KILL_CHAIN_PHASES}
        for t in self.tracks.values():
            if include_stale or t.is_active_at(self.current_sim_time):
                p = t.kill_chain_phase
                if p in counts:
                    counts[p] += 1
        return {
            "phase_counts": counts,
            "total_tracks": sum(counts.values()),
            "phases": KILL_CHAIN_PHASES,
        }


__all__ = [
    "FusedTrack",
    "KILL_CHAIN_PHASES",
    "NM_TO_DEG",
    "SENSOR_COVERAGE",
    "SensorFusionEngine",
    "_dist_deg",
    "_normalize_sensor_name",
]
