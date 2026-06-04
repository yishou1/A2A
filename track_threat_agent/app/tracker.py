"""Simulation-only multi-target tracking and trajectory prediction."""

from __future__ import annotations

import math
from typing import Dict, Iterable, List, Optional, Set
from uuid import uuid4

import numpy as np

from .models import Detection, TrackState
from .utils import (
    clamp,
    haversine_m,
    heading_difference_deg,
    project_position,
    speed_heading_to_velocity,
    velocity_to_speed_heading,
)


class MultiTargetTracker:
    """Maintains in-memory tracks using simple demo association and filters."""

    def __init__(self, association_gate_m: float = 4_000.0, stale_after_s: float = 300.0) -> None:
        self.tracks: Dict[str, TrackState] = {}
        self.association_gate_m = association_gate_m
        self.stale_after_s = stale_after_s

    def reset(self) -> None:
        self.tracks.clear()

    def update(self, detections: Iterable[Detection], algorithm_level: str = "medium") -> List[TrackState]:
        detections_list = [d if isinstance(d, Detection) else Detection.model_validate(d) for d in detections]
        if algorithm_level not in {"small", "medium", "large"}:
            algorithm_level = "medium"

        now = max((d.timestamp for d in detections_list), default=0.0)
        self._mark_or_remove_stale(now)

        assigned_tracks: Set[str] = set()
        for detection in sorted(detections_list, key=lambda item: item.timestamp):
            track = self._nearest_track(detection, assigned_tracks)
            if track is None:
                track = self._new_track(detection)
            else:
                previous = track.model_copy(deep=True)
                if algorithm_level == "small":
                    track = self._update_alpha_beta(track, detection)
                else:
                    track = self._update_kalman_like(track, detection)
                track.metadata["anomaly"] = self._detect_anomaly(previous, detection)
                if algorithm_level == "large":
                    track.metadata["large_mock"] = True
                    track.metadata["algorithm_note"] = "large is reserved; medium filter used for this demo"

            track.track_quality = self._quality_from_detection(track.track_quality, detection.confidence)
            track.predicted_path = self._predict_path(track)
            self.tracks[track.track_id] = track
            assigned_tracks.add(track.track_id)

        self._age_unassigned_tracks(assigned_tracks, now)
        return list(self.tracks.values())

    def _nearest_track(self, detection: Detection, assigned_tracks: Set[str]) -> Optional[TrackState]:
        candidates = []
        for track in self.tracks.values():
            if track.track_id in assigned_tracks:
                continue
            if track.metadata.get("status") == "lost":
                continue
            dt = max(0.0, detection.timestamp - track.last_update_time)
            predicted_lat, predicted_lon = project_position(track.lat, track.lon, track.vx, track.vy, dt)
            distance = haversine_m(predicted_lat, predicted_lon, detection.lat, detection.lon)
            gate = max(self.association_gate_m, track.speed * max(dt, 1.0) * 2.0 + 1_500.0)
            if track.object_type != detection.object_type:
                gate *= 0.55
            if distance <= gate:
                heading_cost = heading_difference_deg(track.heading, detection.heading) / 180.0
                speed_cost = abs(track.speed - detection.speed) / max(track.speed, detection.speed, 1.0)
                quality_bonus = 1.0 - track.track_quality
                score = distance / gate + 0.35 * heading_cost + 0.25 * speed_cost + 0.10 * quality_bonus
                candidates.append((score, track))
        if not candidates:
            return None
        return min(candidates, key=lambda item: item[0])[1]

    def _new_track(self, detection: Detection) -> TrackState:
        vx, vy = speed_heading_to_velocity(detection.speed, detection.heading)
        point = self._history_point(
            detection.lat,
            detection.lon,
            detection.alt,
            detection.timestamp,
            detection.speed,
            detection.heading,
        )
        anomaly = {"low_confidence": detection.confidence < 0.45}
        if anomaly["low_confidence"]:
            anomaly["reason"] = "initial detection confidence is low"
        track = TrackState(
            track_id=f"trk-{uuid4().hex[:10]}",
            object_type=detection.object_type,
            lat=detection.lat,
            lon=detection.lon,
            alt=detection.alt,
            speed=detection.speed,
            heading=detection.heading,
            vx=vx,
            vy=vy,
            track_quality=clamp(0.45 + 0.5 * detection.confidence),
            last_update_time=detection.timestamp,
            missed_count=0,
            history_path=[point],
            metadata={
                "status": "active",
                "source_agent": detection.source_agent,
                "last_detection_id": detection.detection_id,
                "anomaly": anomaly,
                "filter": "initialized",
                **detection.metadata,
            },
        )
        track.predicted_path = self._predict_path(track)
        return track

    def _update_alpha_beta(self, track: TrackState, detection: Detection) -> TrackState:
        dt = max(1.0, detection.timestamp - track.last_update_time)
        alpha = 0.65
        beta = 0.25
        pred_lat, pred_lon = project_position(track.lat, track.lon, track.vx, track.vy, dt)
        corrected_lat = pred_lat + alpha * (detection.lat - pred_lat)
        corrected_lon = pred_lon + alpha * (detection.lon - pred_lon)
        measured_vx, measured_vy = speed_heading_to_velocity(detection.speed, detection.heading)
        track.vx = (1.0 - beta) * track.vx + beta * measured_vx
        track.vy = (1.0 - beta) * track.vy + beta * measured_vy
        track.lat = corrected_lat
        track.lon = corrected_lon
        track.alt = 0.7 * track.alt + 0.3 * detection.alt
        track.speed, track.heading = velocity_to_speed_heading(track.vx, track.vy)
        return self._finalize_track_update(track, detection, "alpha_beta")

    def _update_kalman_like(self, track: TrackState, detection: Detection) -> TrackState:
        dt = max(1.0, detection.timestamp - track.last_update_time)
        measured_vx, measured_vy = speed_heading_to_velocity(detection.speed, detection.heading)

        state = np.array([track.lat, track.lon, track.vx, track.vy], dtype=float)
        pred_lat, pred_lon = project_position(track.lat, track.lon, track.vx, track.vy, dt)
        predicted = np.array([pred_lat, pred_lon, track.vx, track.vy], dtype=float)
        measurement = np.array([detection.lat, detection.lon, measured_vx, measured_vy], dtype=float)

        confidence_gain = clamp(0.35 + detection.confidence * 0.45, 0.35, 0.8)
        velocity_gain = clamp(0.2 + detection.confidence * 0.35, 0.2, 0.55)
        gain = np.array([confidence_gain, confidence_gain, velocity_gain, velocity_gain], dtype=float)
        updated = predicted + gain * (measurement - predicted)

        track.lat = float(updated[0])
        track.lon = float(updated[1])
        track.vx = float(updated[2])
        track.vy = float(updated[3])
        track.alt = float(0.65 * track.alt + 0.35 * detection.alt)
        track.speed, track.heading = velocity_to_speed_heading(track.vx, track.vy)
        track.metadata["kalman_like_state"] = {
            "prior": state.tolist(),
            "gain": gain.tolist(),
        }
        return self._finalize_track_update(track, detection, "kalman_like")

    def _finalize_track_update(self, track: TrackState, detection: Detection, filter_name: str) -> TrackState:
        track.object_type = detection.object_type
        track.last_update_time = detection.timestamp
        track.missed_count = 0
        track.metadata.update(
            {
                "status": "active",
                "source_agent": detection.source_agent,
                "last_detection_id": detection.detection_id,
                "filter": filter_name,
                **detection.metadata,
            }
        )
        track.history_path.append(
            self._history_point(
                track.lat,
                track.lon,
                track.alt,
                detection.timestamp,
                track.speed,
                track.heading,
            )
        )
        track.history_path = track.history_path[-50:]
        return track

    def _detect_anomaly(self, previous: TrackState, detection: Detection) -> Dict[str, object]:
        heading_jump = heading_difference_deg(previous.heading, detection.heading)
        speed_jump = abs(previous.speed - detection.speed)
        speed_baseline = max(previous.speed, 1.0)
        anomalies = {
            "heading_jump": heading_jump > 45.0,
            "speed_jump": speed_jump > max(20.0, speed_baseline * 0.45),
            "low_confidence": detection.confidence < 0.45,
            "heading_delta_deg": round(heading_jump, 2),
            "speed_delta_mps": round(speed_jump, 2),
        }
        reasons = []
        if anomalies["heading_jump"]:
            reasons.append("heading changed abruptly")
        if anomalies["speed_jump"]:
            reasons.append("speed changed abruptly")
        if anomalies["low_confidence"]:
            reasons.append("detection confidence is low")
        anomalies["reason"] = "; ".join(reasons) if reasons else "none"
        return anomalies

    def _predict_path(self, track: TrackState) -> List[Dict[str, object]]:
        profile = self._prediction_profile(track)
        predictions = []
        for dt in (10.0, 20.0, 30.0):
            lat, lon, speed, heading, alt = self._project_adaptive(track, profile, dt)
            uncertainty = self._prediction_uncertainty(track, profile, dt)
            predictions.append(
                {
                    "dt_s": dt,
                    "timestamp": track.last_update_time + dt,
                    "lat": lat,
                    "lon": lon,
                    "alt": alt,
                    "speed": speed,
                    "heading": heading,
                    "prediction_model": profile["model"],
                    "prediction_confidence": profile["confidence"],
                    "uncertainty_radius_m": uncertainty,
                }
            )
        track.metadata["prediction"] = profile
        return predictions

    def _prediction_profile(self, track: TrackState) -> Dict[str, object]:
        """Estimate a demo-safe adaptive prediction profile from recent track history.

        The output is intentionally transparent rather than mathematically heavy:
        it captures recent acceleration, turn rate, vertical rate, and an estimated
        confidence value that downstream UIs can use for uncertainty display.
        """
        recent = sorted(track.history_path[-6:], key=lambda item: item.get("timestamp", 0.0))
        accel_mps2 = 0.0
        turn_rate_dps = 0.0
        vertical_rate_mps = 0.0

        if len(recent) >= 2:
            last = recent[-1]
            prev = recent[-2]
            dt = max(1.0, last.get("timestamp", track.last_update_time) - prev.get("timestamp", track.last_update_time - 1.0))
            if "speed" in last and "speed" in prev:
                accel_mps2 = (last.get("speed", track.speed) - prev.get("speed", track.speed)) / dt
            if "heading" in last and "heading" in prev:
                turn_rate_dps = self._signed_heading_delta(prev.get("heading", track.heading), last.get("heading", track.heading)) / dt
            vertical_rate_mps = (last.get("alt", track.alt) - prev.get("alt", track.alt)) / dt

        if len(recent) >= 4:
            accel_samples = []
            turn_samples = []
            vertical_samples = []
            for prev, curr in zip(recent[:-1], recent[1:]):
                dt = max(1.0, curr.get("timestamp", 0.0) - prev.get("timestamp", 0.0))
                if "speed" in curr and "speed" in prev:
                    accel_samples.append((curr.get("speed", track.speed) - prev.get("speed", track.speed)) / dt)
                if "heading" in curr and "heading" in prev:
                    turn_samples.append(self._signed_heading_delta(prev.get("heading", track.heading), curr.get("heading", track.heading)) / dt)
                vertical_samples.append((curr.get("alt", track.alt) - prev.get("alt", track.alt)) / dt)
            if accel_samples:
                accel_mps2 = float(np.median(accel_samples))
            if turn_samples:
                turn_rate_dps = float(np.median(turn_samples))
            if vertical_samples:
                vertical_rate_mps = float(np.median(vertical_samples))

        accel_cap, turn_cap, vertical_cap = self._motion_caps(track.object_type)
        accel_mps2 = clamp(accel_mps2, -accel_cap, accel_cap)
        turn_rate_dps = clamp(turn_rate_dps, -turn_cap, turn_cap)
        vertical_rate_mps = clamp(vertical_rate_mps, -vertical_cap, vertical_cap)

        anomaly = track.metadata.get("anomaly", {}) or {}
        anomaly_penalty = 0.18 if any(anomaly.get(key) for key in ("heading_jump", "speed_jump", "low_confidence")) else 0.0
        history_bonus = min(len(recent), 6) / 6.0 * 0.18
        missed_penalty = min(track.missed_count, 5) * 0.05
        confidence = clamp(0.52 + 0.35 * track.track_quality + history_bonus - anomaly_penalty - missed_penalty, 0.15, 0.95)

        if abs(turn_rate_dps) >= 0.08 and track.speed >= 2.0:
            model = "adaptive_ctra_turn"
        elif abs(accel_mps2) >= 0.15:
            model = "adaptive_acceleration"
        else:
            model = "adaptive_constant_velocity"

        return {
            "model": model,
            "accel_mps2": round(accel_mps2, 4),
            "turn_rate_dps": round(turn_rate_dps, 4),
            "vertical_rate_mps": round(vertical_rate_mps, 4),
            "confidence": round(confidence, 3),
            "basis_points": len(recent),
        }

    def _project_adaptive(self, track: TrackState, profile: Dict[str, object], dt_s: float) -> tuple[float, float, float, float, float]:
        lat = track.lat
        lon = track.lon
        speed = track.speed
        heading = track.heading
        alt = track.alt

        accel = float(profile.get("accel_mps2", 0.0))
        turn_rate = float(profile.get("turn_rate_dps", 0.0))
        vertical_rate = float(profile.get("vertical_rate_mps", 0.0))
        remaining = max(0.0, dt_s)

        while remaining > 1e-6:
            step = min(1.0, remaining)
            speed = max(0.0, speed + accel * step)
            heading = (heading + turn_rate * step) % 360.0
            vx, vy = speed_heading_to_velocity(speed, heading)
            lat, lon = project_position(lat, lon, vx, vy, step)
            alt = max(0.0, alt + vertical_rate * step)
            remaining -= step

        return lat, lon, speed, heading, alt

    def _prediction_uncertainty(self, track: TrackState, profile: Dict[str, object], dt_s: float) -> float:
        confidence = float(profile.get("confidence", 0.5))
        maneuver_load = abs(float(profile.get("turn_rate_dps", 0.0))) / max(self._motion_caps(track.object_type)[1], 0.1)
        accel_load = abs(float(profile.get("accel_mps2", 0.0))) / max(self._motion_caps(track.object_type)[0], 0.1)
        base = 40.0 + track.speed * 0.08 * dt_s
        growth = (1.0 - confidence) * (dt_s**1.35) * 14.0
        maneuver = (maneuver_load + accel_load) * dt_s * 22.0
        missed = track.missed_count * dt_s * 8.0
        return round(base + growth + maneuver + missed, 2)

    def _motion_caps(self, object_type: str) -> tuple[float, float, float]:
        if object_type == "ship":
            return 0.8, 0.35, 1.0
        if object_type == "uav":
            return 4.0, 7.0, 8.0
        if object_type == "aircraft":
            return 6.0, 4.0, 20.0
        return 5.0, 5.0, 12.0

    def _signed_heading_delta(self, previous: float, current: float) -> float:
        return (current - previous + 180.0) % 360.0 - 180.0

    def _quality_from_detection(self, current_quality: float, confidence: float) -> float:
        return clamp(0.78 * current_quality + 0.22 * confidence)

    def _age_unassigned_tracks(self, assigned_tracks: Set[str], now: float) -> None:
        for track in self.tracks.values():
            if track.track_id in assigned_tracks or now <= 0:
                continue
            age = max(0.0, now - track.last_update_time)
            if age > 0:
                track.missed_count += 1
                track.track_quality = clamp(track.track_quality * 0.96)
                track.metadata["status"] = "coasting"
                track.predicted_path = self._predict_path(track)

    def _mark_or_remove_stale(self, now: float) -> None:
        stale_ids = []
        for track_id, track in self.tracks.items():
            if now - track.last_update_time > self.stale_after_s:
                stale_ids.append(track_id)
        for track_id in stale_ids:
            self.tracks[track_id].metadata["status"] = "lost"
            del self.tracks[track_id]

    def _history_point(
        self,
        lat: float,
        lon: float,
        alt: float,
        timestamp: float,
        speed: float | None = None,
        heading: float | None = None,
    ) -> Dict[str, float]:
        point = {"timestamp": timestamp, "lat": lat, "lon": lon, "alt": alt}
        if speed is not None:
            point["speed"] = speed
        if heading is not None:
            point["heading"] = heading % 360.0
        return point
