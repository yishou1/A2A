"""Simulation-only multi-target tracking and trajectory prediction."""

from __future__ import annotations

import math
from typing import Dict, Iterable, List, Set, Tuple
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
    """Maintains in-memory tracks using gated global association and filters."""

    _INVALID_ASSOCIATION_COST = 1_000_000.0
    _POSITION_NIS_GATE = 13.815  # 99.9% chi-square gate with two position dimensions.

    def __init__(
        self,
        association_gate_m: float = 4_000.0,
        stale_after_s: float = 300.0,
        confirmation_hits: int = 2,
    ) -> None:
        self.tracks: Dict[str, TrackState] = {}
        self.association_gate_m = association_gate_m
        self.stale_after_s = stale_after_s
        self.confirmation_hits = max(1, confirmation_hits)
        self._latest_detection_time: float | None = None
        self._recent_detection_ids: Dict[str, float] = {}
        self._ignored_duplicate_detection_count = 0
        self._ignored_out_of_order_detection_count = 0

    def reset(self) -> None:
        self.tracks.clear()
        self._latest_detection_time = None
        self._recent_detection_ids.clear()
        self._ignored_duplicate_detection_count = 0
        self._ignored_out_of_order_detection_count = 0

    def restore_tracks(self, tracks: Dict[str, TrackState]) -> None:
        self.tracks = dict(tracks)
        self._latest_detection_time = max(
            (track.last_update_time for track in self.tracks.values()),
            default=None,
        )
        self._recent_detection_ids = {
            str(track.metadata["last_detection_id"]): track.last_update_time
            for track in self.tracks.values()
            if track.metadata.get("last_detection_id")
        }

    def diagnostics(self) -> Dict[str, object]:
        lifecycle_counts: Dict[str, int] = {}
        for track in self.tracks.values():
            state = str(track.metadata.get("lifecycle_state", track.metadata.get("status", "unknown")))
            lifecycle_counts[state] = lifecycle_counts.get(state, 0) + 1
        return {
            "latest_detection_time": self._latest_detection_time,
            "ignored_duplicate_detection_count": self._ignored_duplicate_detection_count,
            "ignored_out_of_order_detection_count": self._ignored_out_of_order_detection_count,
            "lifecycle_counts": lifecycle_counts,
        }

    def update(self, detections: Iterable[Detection], algorithm_level: str = "medium") -> List[TrackState]:
        detections_list = self._prepare_detections(detections)
        if algorithm_level not in {"small", "medium", "large"}:
            algorithm_level = "medium"

        now = max((d.timestamp for d in detections_list), default=0.0)
        self._mark_or_remove_stale(now)

        ordered_detections = sorted(detections_list, key=lambda item: (item.timestamp, item.detection_id))
        associations = self._associate_frame(ordered_detections)
        assigned_tracks: Set[str] = set()
        for detection_index, detection in enumerate(ordered_detections):
            association = associations.get(detection_index)
            if association is None:
                track = self._new_track(detection)
            else:
                track, association_metadata = association
                previous = track.model_copy(deep=True)
                if algorithm_level == "small":
                    track = self._update_alpha_beta(track, detection)
                else:
                    track = self._update_kalman_like(track, detection)
                track.metadata["prediction_eval"] = self._evaluate_previous_prediction(previous, detection)
                track.metadata["anomaly"] = self._detect_anomaly(previous, detection)
                track.metadata["association"] = association_metadata
                if algorithm_level == "large":
                    track.metadata["large_mock"] = True
                    track.metadata["algorithm_note"] = "large is reserved; medium filter used for this demo"

            track.track_quality = self._quality_from_detection(track.track_quality, detection.confidence)
            track.predicted_path = self._predict_path(track)
            self.tracks[track.track_id] = track
            assigned_tracks.add(track.track_id)

        self._age_unassigned_tracks(assigned_tracks, now)
        return list(self.tracks.values())

    def _prepare_detections(self, detections: Iterable[Detection]) -> List[Detection]:
        validated = [d if isinstance(d, Detection) else Detection.model_validate(d) for d in detections]
        unique_by_id: Dict[str, Detection] = {}
        for detection in validated:
            existing = unique_by_id.get(detection.detection_id)
            if existing is not None:
                self._ignored_duplicate_detection_count += 1
                if detection.confidence > existing.confidence:
                    unique_by_id[detection.detection_id] = detection
                continue
            unique_by_id[detection.detection_id] = detection

        accepted = []
        previous_watermark = self._latest_detection_time
        for detection in unique_by_id.values():
            if detection.detection_id in self._recent_detection_ids:
                self._ignored_duplicate_detection_count += 1
                continue
            if previous_watermark is not None and detection.timestamp <= previous_watermark:
                self._ignored_out_of_order_detection_count += 1
                continue
            accepted.append(detection)

        if accepted:
            newest = max(detection.timestamp for detection in accepted)
            self._latest_detection_time = newest if previous_watermark is None else max(previous_watermark, newest)
            for detection in accepted:
                self._recent_detection_ids[detection.detection_id] = detection.timestamp
            cutoff = self._latest_detection_time - self.stale_after_s
            self._recent_detection_ids = {
                detection_id: timestamp
                for detection_id, timestamp in self._recent_detection_ids.items()
                if timestamp >= cutoff
            }
        return accepted

    def _associate_frame(
        self,
        detections: List[Detection],
    ) -> Dict[int, Tuple[TrackState, Dict[str, object]]]:
        active_tracks = sorted(
            (
                track
                for track in self.tracks.values()
                if track.metadata.get("status") != "lost"
            ),
            key=lambda track: track.track_id,
        )
        if not active_tracks or not detections:
            return {}

        cost_matrix = np.full(
            (len(active_tracks), len(detections)),
            self._INVALID_ASSOCIATION_COST,
            dtype=float,
        )
        metrics_by_pair: Dict[tuple[int, int], Dict[str, object]] = {}
        for track_index, track in enumerate(active_tracks):
            for detection_index, detection in enumerate(detections):
                metrics = self._association_metrics(track, detection)
                if metrics is None:
                    continue
                cost_matrix[track_index, detection_index] = float(metrics["cost"])
                metrics_by_pair[(track_index, detection_index)] = metrics

        row_indices, column_indices = self._linear_sum_assignment(cost_matrix)
        associations: Dict[int, Tuple[TrackState, Dict[str, object]]] = {}
        for track_index, detection_index in zip(row_indices, column_indices):
            if cost_matrix[track_index, detection_index] >= self._INVALID_ASSOCIATION_COST:
                continue
            metrics = metrics_by_pair[(track_index, detection_index)]
            associations[detection_index] = (
                active_tracks[track_index],
                {
                    "method": "global_nearest_neighbor",
                    "cost": round(float(metrics["cost"]), 6),
                    "distance_m": round(float(metrics["distance_m"]), 2),
                    "position_nis": round(float(metrics["position_nis"]), 4),
                    "position_nis_gate": self._POSITION_NIS_GATE,
                    "physical_gate_m": round(float(metrics["physical_gate_m"]), 2),
                },
            )
        return associations

    def _association_metrics(self, track: TrackState, detection: Detection) -> Dict[str, float] | None:
        known_types = {"aircraft", "ship", "uav"}
        if track.object_type in known_types and detection.object_type in known_types:
            if track.object_type != detection.object_type:
                return None

        dt = max(0.0, detection.timestamp - track.last_update_time)
        predicted_lat, predicted_lon = project_position(track.lat, track.lon, track.vx, track.vy, dt)
        distance_m = haversine_m(predicted_lat, predicted_lon, detection.lat, detection.lon)
        physical_gate_m = max(
            self.association_gate_m,
            track.speed * max(dt, 1.0) * 2.0 + 1_500.0,
        )
        if distance_m > physical_gate_m:
            return None

        covariance = self._association_position_covariance(track, detection, dt)
        east_m, north_m = self._latlon_to_local_m(
            detection.lat,
            detection.lon,
            predicted_lat,
            predicted_lon,
        )
        innovation = np.array([east_m, north_m], dtype=float)
        position_nis = float(innovation.T @ np.linalg.pinv(covariance) @ innovation)
        if not math.isfinite(position_nis) or position_nis > self._POSITION_NIS_GATE:
            return None

        heading_cost = heading_difference_deg(track.heading, detection.heading) / 180.0
        speed_cost = abs(track.speed - detection.speed) / max(track.speed, detection.speed, 1.0)
        type_penalty = 0.12 if track.object_type != detection.object_type else 0.0
        quality_penalty = (1.0 - track.track_quality) * 0.08
        cost = (
            math.sqrt(max(position_nis, 0.0) / self._POSITION_NIS_GATE)
            + 0.30 * heading_cost
            + 0.20 * speed_cost
            + type_penalty
            + quality_penalty
        )
        return {
            "cost": cost,
            "distance_m": distance_m,
            "position_nis": position_nis,
            "physical_gate_m": physical_gate_m,
        }

    def _association_position_covariance(
        self,
        track: TrackState,
        detection: Detection,
        dt: float,
    ) -> np.ndarray:
        kalman = track.metadata.get("kalman_filter") or {}
        covariance = np.asarray(
            kalman.get("covariance", np.diag([900.0, 900.0, 100.0, 100.0])),
            dtype=float,
        )
        if covariance.shape != (4, 4) or not np.isfinite(covariance).all():
            covariance = np.diag([900.0, 900.0, 100.0, 100.0])
        f = np.array(
            [
                [1.0, 0.0, dt, 0.0],
                [0.0, 1.0, 0.0, dt],
                [0.0, 0.0, 1.0, 0.0],
                [0.0, 0.0, 0.0, 1.0],
            ],
            dtype=float,
        )
        accel_noise = self._process_noise(track.object_type)
        q = accel_noise**2 * np.array(
            [
                [dt**4 / 4.0, 0.0, dt**3 / 2.0, 0.0],
                [0.0, dt**4 / 4.0, 0.0, dt**3 / 2.0],
                [dt**3 / 2.0, 0.0, dt**2, 0.0],
                [0.0, dt**3 / 2.0, 0.0, dt**2],
            ],
            dtype=float,
        )
        predicted_covariance = f @ covariance @ f.T + q
        position_sigma = max(18.0, 260.0 * (1.0 - detection.confidence))
        measurement_covariance = np.diag([position_sigma**2, position_sigma**2])
        return predicted_covariance[:2, :2] + measurement_covariance

    @staticmethod
    def _linear_sum_assignment(cost_matrix: np.ndarray) -> tuple[List[int], List[int]]:
        """Solve rectangular minimum-cost assignment with the Hungarian method."""
        if cost_matrix.ndim != 2 or 0 in cost_matrix.shape:
            return [], []
        original_rows, original_columns = cost_matrix.shape
        transposed = original_rows > original_columns
        matrix = cost_matrix.T if transposed else cost_matrix
        row_count, column_count = matrix.shape

        u = np.zeros(row_count + 1, dtype=float)
        v = np.zeros(column_count + 1, dtype=float)
        matched_row = np.zeros(column_count + 1, dtype=int)
        previous_column = np.zeros(column_count + 1, dtype=int)
        for row in range(1, row_count + 1):
            matched_row[0] = row
            min_value = np.full(column_count + 1, np.inf, dtype=float)
            used = np.zeros(column_count + 1, dtype=bool)
            column = 0
            while True:
                used[column] = True
                current_row = matched_row[column]
                delta = np.inf
                next_column = 0
                for candidate in range(1, column_count + 1):
                    if used[candidate]:
                        continue
                    reduced_cost = matrix[current_row - 1, candidate - 1] - u[current_row] - v[candidate]
                    if reduced_cost < min_value[candidate]:
                        min_value[candidate] = reduced_cost
                        previous_column[candidate] = column
                    if min_value[candidate] < delta:
                        delta = min_value[candidate]
                        next_column = candidate
                for candidate in range(column_count + 1):
                    if used[candidate]:
                        u[matched_row[candidate]] += delta
                        v[candidate] -= delta
                    else:
                        min_value[candidate] -= delta
                column = next_column
                if matched_row[column] == 0:
                    break
            while True:
                previous = previous_column[column]
                matched_row[column] = matched_row[previous]
                column = previous
                if column == 0:
                    break

        pairs = [(matched_row[column] - 1, column - 1) for column in range(1, column_count + 1) if matched_row[column]]
        if transposed:
            pairs = [(column, row) for row, column in pairs]
        pairs.sort()
        return [row for row, _ in pairs], [column for _, column in pairs]

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
                **detection.metadata,
                "status": "active",
                "lifecycle_state": "confirmed" if self.confirmation_hits <= 1 else "tentative",
                "hit_count": 1,
                "consecutive_hit_count": 1,
                "confirmed_once": self.confirmation_hits <= 1,
                "source_agent": detection.source_agent,
                "last_detection_id": detection.detection_id,
                "anomaly": anomaly,
                "filter": "initialized",
                "association": {
                    "method": "new_track",
                    "detection_id": detection.detection_id,
                },
                "kalman_filter": {
                    "reference_lat": detection.lat,
                    "reference_lon": detection.lon,
                    "state": [0.0, 0.0, vx, vy],
                    "covariance": np.diag([900.0, 900.0, 100.0, 100.0]).tolist(),
                    "model": "constant_velocity_xy",
                },
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

        kalman = track.metadata.get("kalman_filter") or {}
        ref_lat = float(kalman.get("reference_lat", track.lat))
        ref_lon = float(kalman.get("reference_lon", track.lon))
        state = np.array(kalman.get("state", self._state_from_track(track, ref_lat, ref_lon)), dtype=float)
        covariance = np.array(kalman.get("covariance", np.diag([900.0, 900.0, 100.0, 100.0])), dtype=float)
        if state.shape != (4,) or covariance.shape != (4, 4):
            state = self._state_from_track(track, ref_lat, ref_lon)
            covariance = np.diag([900.0, 900.0, 100.0, 100.0])

        measurement_x, measurement_y = self._latlon_to_local_m(detection.lat, detection.lon, ref_lat, ref_lon)
        measurement = np.array([measurement_x, measurement_y, measured_vx, measured_vy], dtype=float)
        f = np.array(
            [
                [1.0, 0.0, dt, 0.0],
                [0.0, 1.0, 0.0, dt],
                [0.0, 0.0, 1.0, 0.0],
                [0.0, 0.0, 0.0, 1.0],
            ],
            dtype=float,
        )
        h = np.eye(4, dtype=float)
        accel_noise = self._process_noise(track.object_type)
        q = accel_noise**2 * np.array(
            [
                [dt**4 / 4.0, 0.0, dt**3 / 2.0, 0.0],
                [0.0, dt**4 / 4.0, 0.0, dt**3 / 2.0],
                [dt**3 / 2.0, 0.0, dt**2, 0.0],
                [0.0, dt**3 / 2.0, 0.0, dt**2],
            ],
            dtype=float,
        )
        position_sigma = max(18.0, 260.0 * (1.0 - detection.confidence))
        velocity_sigma = max(2.0, 28.0 * (1.0 - detection.confidence))
        r = np.diag([position_sigma**2, position_sigma**2, velocity_sigma**2, velocity_sigma**2])

        predicted_state = f @ state
        predicted_covariance = f @ covariance @ f.T + q
        innovation = measurement - h @ predicted_state
        innovation_covariance = h @ predicted_covariance @ h.T + r
        kalman_gain = predicted_covariance @ h.T @ np.linalg.pinv(innovation_covariance)
        updated = predicted_state + kalman_gain @ innovation
        identity = np.eye(4, dtype=float)
        updated_covariance = (identity - kalman_gain @ h) @ predicted_covariance

        track.lat, track.lon = self._local_m_to_latlon(float(updated[0]), float(updated[1]), ref_lat, ref_lon)
        track.vx = float(updated[2])
        track.vy = float(updated[3])
        track.alt = float(0.65 * track.alt + 0.35 * detection.alt)
        track.speed, track.heading = velocity_to_speed_heading(track.vx, track.vy)
        track.metadata["kalman_filter"] = {
            "reference_lat": ref_lat,
            "reference_lon": ref_lon,
            "state": updated.tolist(),
            "covariance": updated_covariance.tolist(),
            "innovation": innovation.tolist(),
            "kalman_gain": kalman_gain.tolist(),
            "position_sigma_m": round(position_sigma, 2),
            "velocity_sigma_mps": round(velocity_sigma, 2),
            "model": "constant_velocity_xy",
        }
        return self._finalize_track_update(track, detection, "kalman_cv")

    def _state_from_track(self, track: TrackState, reference_lat: float, reference_lon: float) -> np.ndarray:
        x_m, y_m = self._latlon_to_local_m(track.lat, track.lon, reference_lat, reference_lon)
        return np.array([x_m, y_m, track.vx, track.vy], dtype=float)

    def _latlon_to_local_m(self, lat: float, lon: float, reference_lat: float, reference_lon: float) -> tuple[float, float]:
        cos_lat = max(0.01, math.cos(math.radians(reference_lat)))
        east_m = (lon - reference_lon) * 111_320.0 * cos_lat
        north_m = (lat - reference_lat) * 111_320.0
        return east_m, north_m

    def _local_m_to_latlon(self, east_m: float, north_m: float, reference_lat: float, reference_lon: float) -> tuple[float, float]:
        cos_lat = max(0.01, math.cos(math.radians(reference_lat)))
        lat = reference_lat + north_m / 111_320.0
        lon = reference_lon + east_m / (111_320.0 * cos_lat)
        return lat, lon

    def _process_noise(self, object_type: str) -> float:
        if object_type == "ship":
            return 0.45
        if object_type == "uav":
            return 2.2
        if object_type == "aircraft":
            return 3.2
        return 2.8

    def _finalize_track_update(self, track: TrackState, detection: Detection, filter_name: str) -> TrackState:
        if track.object_type == "unknown" and detection.object_type != "unknown":
            track.object_type = detection.object_type
        track.last_update_time = detection.timestamp
        track.missed_count = 0
        hit_count = int(track.metadata.get("hit_count", 1)) + 1
        consecutive_hit_count = int(track.metadata.get("consecutive_hit_count", 0)) + 1
        confirmed_once = bool(track.metadata.get("confirmed_once")) or consecutive_hit_count >= self.confirmation_hits
        lifecycle_state = "confirmed" if confirmed_once else "tentative"
        track.metadata.update(detection.metadata)
        track.metadata.update(
            {
                "status": "active",
                "lifecycle_state": lifecycle_state,
                "hit_count": hit_count,
                "consecutive_hit_count": consecutive_hit_count,
                "confirmed_once": confirmed_once,
                "source_agent": detection.source_agent,
                "last_detection_id": detection.detection_id,
                "filter": filter_name,
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
        model_probabilities = self._adaptive_model_probabilities(track, profile)
        hypotheses = self._prediction_hypotheses(track, profile, model_probabilities)
        predictions = []
        for dt in (10.0, 20.0, 30.0, 60.0, 120.0):
            candidate_points = [
                next(point for point in hypothesis["points"] if point["dt_s"] == dt)
                for hypothesis in hypotheses
            ]
            lat, lon, speed, heading, alt = self._fuse_hypothesis_points(candidate_points, model_probabilities)
            uncertainty = self._prediction_uncertainty(track, profile, dt)
            horizon_type = "short_term" if dt <= 30.0 else "medium_term"
            confidence = self._horizon_confidence(profile, dt)
            predictions.append(
                {
                    "dt_s": dt,
                    "timestamp": track.last_update_time + dt,
                    "lat": lat,
                    "lon": lon,
                    "alt": alt,
                    "speed": speed,
                    "heading": heading,
                    "model_used": "adaptive_multi_model_fused",
                    "prediction_model": "adaptive_multi_model_fused",
                    "primary_model": profile["model"],
                    "model_probabilities": model_probabilities,
                    "prediction_confidence": confidence,
                    "uncertainty_radius_m": uncertainty,
                    "horizon_type": horizon_type,
                }
            )
        profile["prediction_method"] = "adaptive_multi_model_fused"
        profile["model_probabilities"] = model_probabilities
        profile["prediction_hypotheses"] = hypotheses
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

    def _adaptive_model_probabilities(
        self,
        track: TrackState,
        profile: Dict[str, object],
    ) -> Dict[str, float]:
        accel = abs(float(profile.get("accel_mps2", 0.0)))
        turn_rate = abs(float(profile.get("turn_rate_dps", 0.0)))
        accel_cap, turn_cap, _ = self._motion_caps(track.object_type)
        accel_load = clamp(accel / max(accel_cap, 0.1))
        turn_load = clamp(turn_rate / max(turn_cap, 0.1))
        quality = clamp(track.track_quality)

        cv = 0.58 * (1.0 - 0.55 * accel_load) * (1.0 - 0.60 * turn_load) + 0.08 * quality
        ca = 0.20 + 0.62 * accel_load + 0.08 * (1.0 - quality)
        ct = 0.18 + 0.70 * turn_load

        anomaly = track.metadata.get("anomaly", {}) or {}
        if anomaly.get("heading_jump"):
            ct += 0.18
            cv *= 0.82
        if anomaly.get("speed_jump"):
            ca += 0.14
            cv *= 0.88
        if track.missed_count:
            cv += min(track.missed_count, 3) * 0.04

        raw = {
            "constant_velocity": max(cv, 0.03),
            "constant_acceleration": max(ca, 0.03),
            "coordinated_turn": max(ct, 0.03),
        }
        total = sum(raw.values()) or 1.0
        normalized = {key: value / total for key, value in raw.items()}
        rounded = {key: round(value, 6) for key, value in normalized.items()}
        drift = round(1.0 - sum(rounded.values()), 6)
        rounded["constant_velocity"] = round(rounded["constant_velocity"] + drift, 6)
        return rounded

    def _prediction_hypotheses(
        self,
        track: TrackState,
        profile: Dict[str, object],
        model_probabilities: Dict[str, float],
    ) -> List[Dict[str, object]]:
        hypotheses = []
        for model_name, probability in model_probabilities.items():
            points = []
            for dt in (10.0, 20.0, 30.0, 60.0, 120.0):
                lat, lon, speed, heading, alt = self._project_motion_model(track, profile, dt, model_name)
                points.append(
                    {
                        "dt_s": dt,
                        "timestamp": track.last_update_time + dt,
                        "lat": lat,
                        "lon": lon,
                        "alt": alt,
                        "speed": speed,
                        "heading": heading,
                        "model_used": model_name,
                    }
                )
            hypotheses.append(
                {
                    "hypothesis_id": model_name,
                    "model_used": model_name,
                    "probability": probability,
                    "points": points,
                }
            )
        hypotheses.sort(key=lambda item: item["probability"], reverse=True)
        return hypotheses

    def _fuse_hypothesis_points(
        self,
        points: List[Dict[str, float]],
        model_probabilities: Dict[str, float],
    ) -> tuple[float, float, float, float, float]:
        weights = [model_probabilities.get(str(point.get("model_used", "")), 0.0) for point in points]
        if not any(weights):
            weights = [1.0 / max(len(points), 1)] * len(points)
        total = sum(weights) or 1.0
        weights = [weight / total for weight in weights]
        lat = sum(float(point["lat"]) * weight for point, weight in zip(points, weights))
        lon = sum(float(point["lon"]) * weight for point, weight in zip(points, weights))
        alt = sum(float(point.get("alt", 0.0)) * weight for point, weight in zip(points, weights))
        speed = sum(float(point.get("speed", 0.0)) * weight for point, weight in zip(points, weights))
        sin_sum = sum(math.sin(math.radians(float(point.get("heading", 0.0)))) * weight for point, weight in zip(points, weights))
        cos_sum = sum(math.cos(math.radians(float(point.get("heading", 0.0)))) * weight for point, weight in zip(points, weights))
        heading = math.degrees(math.atan2(sin_sum, cos_sum)) % 360.0 if abs(sin_sum) + abs(cos_sum) > 1e-9 else 0.0
        return lat, lon, speed, heading, alt

    def _project_motion_model(
        self,
        track: TrackState,
        profile: Dict[str, object],
        dt_s: float,
        model_name: str,
    ) -> tuple[float, float, float, float, float]:
        model_profile = dict(profile)
        if model_name == "constant_velocity":
            model_profile["accel_mps2"] = 0.0
            model_profile["turn_rate_dps"] = 0.0
        elif model_name == "constant_acceleration":
            model_profile["turn_rate_dps"] = 0.0
        elif model_name == "coordinated_turn":
            model_profile["accel_mps2"] = 0.0
            if abs(float(model_profile.get("turn_rate_dps", 0.0))) < 0.05:
                model_profile["turn_rate_dps"] = self._default_turn_rate(track)
        return self._project_adaptive(track, model_profile, dt_s)

    def _default_turn_rate(self, track: TrackState) -> float:
        if track.object_type == "ship":
            return 0.12
        if track.object_type == "aircraft":
            return 0.8
        if track.object_type == "uav":
            return 1.4
        return 0.6

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

    def _horizon_confidence(self, profile: Dict[str, object], dt_s: float) -> float:
        base_confidence = float(profile.get("confidence", 0.5))
        if dt_s <= 30.0:
            decay = 0.0
        else:
            decay = min(0.35, (dt_s - 30.0) / 90.0 * 0.28)
        return round(clamp(base_confidence - decay, 0.1, 0.95), 3)

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
                track.metadata["lifecycle_state"] = "coasting"
                track.metadata["consecutive_hit_count"] = 0
                track.predicted_path = self._predict_path(track)

    def _mark_or_remove_stale(self, now: float) -> None:
        stale_ids = []
        for track_id, track in self.tracks.items():
            if now - track.last_update_time > self.stale_after_s:
                track.metadata["lifecycle_state"] = "lost"
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

    def _evaluate_previous_prediction(self, previous: TrackState, detection: Detection) -> Dict[str, float]:
        if not previous.predicted_path:
            return {
                "matched_horizon_s": 0.0,
                "ade_m": 0.0,
                "fde_m": 0.0,
                "sample_count": 0,
            }
        elapsed = max(0.0, detection.timestamp - previous.last_update_time)
        candidates = [
            point for point in previous.predicted_path
            if abs(float(point.get("dt_s", 0.0)) - elapsed) <= max(5.0, elapsed * 0.35)
        ]
        if not candidates:
            candidates = [min(previous.predicted_path, key=lambda point: abs(float(point.get("dt_s", 0.0)) - elapsed))]
        errors = [
            haversine_m(float(point["lat"]), float(point["lon"]), detection.lat, detection.lon)
            for point in candidates
        ]
        return {
            "matched_horizon_s": float(candidates[-1].get("dt_s", 0.0)),
            "ade_m": round(sum(errors) / max(len(errors), 1), 2),
            "fde_m": round(errors[-1], 2),
            "sample_count": len(errors),
        }
