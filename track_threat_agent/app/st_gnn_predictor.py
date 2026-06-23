"""Local ST-GNN trajectory prediction runtime.

The implementation keeps the project-plan structure in-process: tracks become
dynamic graph nodes, related tracks become weighted edges, a NumPy message
passing block produces node embeddings, and a decoder corrects short-horizon
trajectory hypotheses. Trained weights can replace the fixed matrices later,
but the runtime path is a real graph-temporal inference step rather than a
metadata-only adapter.
"""

from __future__ import annotations

import math
from typing import Dict, Iterable, List

import numpy as np

from .models import TrackState
from .utils import clamp, haversine_m, heading_difference_deg, project_position


class STGNNTrajectoryPredictor:
    """Refine `predicted_path` with local spatio-temporal graph inference."""

    feature_dim = 8

    def __init__(self, max_neighbor_distance_m: float = 8_000.0, max_adjustment_ratio: float = 0.24) -> None:
        self.max_neighbor_distance_m = max_neighbor_distance_m
        self.max_adjustment_ratio = max_adjustment_ratio
        self._w_self = np.array(
            [
                [0.82, 0.04, 0.00, 0.02, 0.05, 0.02, 0.03, 0.00],
                [0.02, 0.80, 0.03, 0.00, 0.04, 0.05, 0.00, 0.03],
                [0.00, 0.04, 0.78, 0.06, 0.04, 0.00, 0.05, 0.02],
                [0.03, 0.00, 0.06, 0.77, 0.00, 0.04, 0.02, 0.05],
                [0.05, 0.03, 0.02, 0.00, 0.76, 0.07, 0.04, 0.01],
                [0.02, 0.06, 0.00, 0.05, 0.06, 0.75, 0.03, 0.04],
                [0.03, 0.00, 0.05, 0.02, 0.04, 0.03, 0.74, 0.07],
                [0.00, 0.03, 0.02, 0.05, 0.01, 0.04, 0.07, 0.78],
            ],
            dtype=float,
        )
        self._w_msg = np.array(
            [
                [0.34, 0.02, 0.00, 0.03, 0.08, 0.02, 0.06, 0.00],
                [0.02, 0.33, 0.03, 0.00, 0.07, 0.06, 0.00, 0.05],
                [0.00, 0.05, 0.31, 0.08, 0.03, 0.00, 0.07, 0.02],
                [0.04, 0.00, 0.07, 0.30, 0.00, 0.04, 0.03, 0.07],
                [0.08, 0.04, 0.03, 0.00, 0.28, 0.06, 0.08, 0.02],
                [0.02, 0.07, 0.00, 0.04, 0.06, 0.28, 0.04, 0.08],
                [0.06, 0.00, 0.08, 0.03, 0.07, 0.04, 0.27, 0.08],
                [0.00, 0.05, 0.02, 0.07, 0.02, 0.08, 0.07, 0.29],
            ],
            dtype=float,
        )

    def refine(self, tracks: Iterable[TrackState]) -> List[TrackState]:
        track_list = list(tracks)
        if len(track_list) < 2:
            for track in track_list:
                self._mark_no_graph_influence(track)
            return track_list

        neighbors_by_track = {track.track_id: self._neighbors(track, track_list) for track in track_list}
        embeddings = self._message_passing(track_list, neighbors_by_track)

        for track in track_list:
            neighbors = neighbors_by_track[track.track_id]
            embedding = embeddings[track.track_id]
            if not neighbors:
                self._mark_no_graph_influence(track, embedding)
                continue

            weight_sum = sum(weight for _, weight in neighbors)
            avg_vx = sum(neighbor.vx * weight for neighbor, weight in neighbors) / max(weight_sum, 1e-6)
            avg_vy = sum(neighbor.vy * weight for neighbor, weight in neighbors) / max(weight_sum, 1e-6)
            influence = clamp(weight_sum / max(len(neighbors), 1), 0.0, 1.0)
            temporal = self._temporal_features(track)
            decoder = self._decode_motion_correction(track, embedding, avg_vx, avg_vy, influence, temporal)
            corrected_vx = decoder["corrected_vx"]
            corrected_vy = decoder["corrected_vy"]

            refined_path = []
            for point in track.predicted_path:
                dt = float(point.get("dt_s", 0.0))
                vx = corrected_vx + decoder["accel_x"] * dt
                vy = corrected_vy + decoder["accel_y"] * dt
                lat, lon = project_position(track.lat, track.lon, vx, vy, dt)
                refined = dict(point)
                refined["lat"] = lat
                refined["lon"] = lon
                refined["st_gnn_inspired"] = True
                refined["st_gnn"] = {
                    "algorithm": "ST-GNN",
                    "runtime": "local_numpy_message_passing",
                    "message_passing_layers": 2,
                    "node_embedding": [round(float(value), 4) for value in embedding[:6]],
                    "edge_attention": self._edge_attention(neighbors),
                    "decoder_adjustment": {
                        "vx_delta_mps": round(decoder["vx_delta"], 4),
                        "vy_delta_mps": round(decoder["vy_delta"], 4),
                        "accel_x_mps2": round(decoder["accel_x"], 4),
                        "accel_y_mps2": round(decoder["accel_y"], 4),
                    },
                    "trained_model_loaded": False,
                }
                refined["graph_neighbor_count"] = len(neighbors)
                refined["graph_influence"] = round(influence, 4)
                refined["graph_blend"] = round(decoder["blend"], 4)
                base_model = point.get("model_used") or point.get("prediction_model", "adaptive")
                refined["model_used"] = f"{base_model}_graph_refined"
                refined["prediction_model"] = refined["model_used"]
                refined["prediction_confidence"] = round(
                    clamp(float(point.get("prediction_confidence", track.track_quality)) + 0.04 * influence),
                    3,
                )
                refined_path.append(refined)

            track.predicted_path = refined_path
            final_model = refined_path[0].get("model_used", "graph_refined") if refined_path else "graph_refined"
            prediction_meta = dict(track.metadata.get("prediction", {}) or {})
            prediction_meta["final_model_used"] = final_model
            prediction_meta["graph_refined"] = True
            track.metadata["prediction"] = prediction_meta
            track.metadata["st_gnn_inspired"] = {
                "enabled": True,
                "runtime": "local_numpy_message_passing",
                "message_passing_layers": 2,
                "neighbor_count": len(neighbors),
                "graph_influence": round(influence, 4),
                "graph_blend": round(decoder["blend"], 4),
                "node_embedding": [round(float(value), 4) for value in embedding[:6]],
                "decoder_adjustment": {
                    "vx_delta_mps": round(decoder["vx_delta"], 4),
                    "vy_delta_mps": round(decoder["vy_delta"], 4),
                    "accel_x_mps2": round(decoder["accel_x"], 4),
                    "accel_y_mps2": round(decoder["accel_y"], 4),
                },
                "edge_attention": self._edge_attention(neighbors),
                "neighbor_track_ids": [neighbor.track_id for neighbor, _ in neighbors],
                "note": "Local ST-GNN message passing over co-moving track graph.",
            }
        return track_list

    def _message_passing(
        self,
        tracks: List[TrackState],
        neighbors_by_track: Dict[str, List[tuple[TrackState, float]]],
    ) -> Dict[str, np.ndarray]:
        features = {track.track_id: self._node_features(track) for track in tracks}
        hidden = dict(features)
        for _ in range(2):
            next_hidden: Dict[str, np.ndarray] = {}
            for track in tracks:
                neighbors = neighbors_by_track[track.track_id]
                if neighbors:
                    total = sum(weight for _, weight in neighbors) or 1.0
                    message = sum(hidden[neighbor.track_id] * weight for neighbor, weight in neighbors) / total
                else:
                    message = np.zeros(self.feature_dim, dtype=float)
                updated = np.tanh(hidden[track.track_id] @ self._w_self + message @ self._w_msg)
                next_hidden[track.track_id] = updated
            hidden = next_hidden
        return hidden

    def _node_features(self, track: TrackState) -> np.ndarray:
        temporal = self._temporal_features(track)
        heading_rad = math.radians(track.heading)
        anomaly = track.metadata.get("anomaly", {}) or {}
        return np.array(
            [
                clamp(track.vx / 180.0, -1.0, 1.0),
                clamp(track.vy / 180.0, -1.0, 1.0),
                math.sin(heading_rad),
                math.cos(heading_rad),
                clamp(track.speed / 180.0),
                clamp(track.track_quality),
                clamp(abs(temporal["turn_rate_dps"]) / 8.0),
                clamp(float(any(anomaly.get(key) for key in ("heading_jump", "speed_jump", "low_confidence")))),
            ],
            dtype=float,
        )

    def _temporal_features(self, track: TrackState) -> Dict[str, float]:
        recent = sorted(track.history_path[-4:], key=lambda item: item.get("timestamp", 0.0))
        if len(recent) < 2:
            return {"accel_mps2": 0.0, "turn_rate_dps": 0.0}
        prev = recent[-2]
        curr = recent[-1]
        dt = max(1.0, float(curr.get("timestamp", track.last_update_time)) - float(prev.get("timestamp", track.last_update_time - 1.0)))
        accel = (float(curr.get("speed", track.speed)) - float(prev.get("speed", track.speed))) / dt
        turn = (float(curr.get("heading", track.heading)) - float(prev.get("heading", track.heading)) + 180.0) % 360.0 - 180.0
        return {"accel_mps2": clamp(accel, -6.0, 6.0), "turn_rate_dps": clamp(turn / dt, -8.0, 8.0)}

    def _decode_motion_correction(
        self,
        track: TrackState,
        embedding: np.ndarray,
        avg_vx: float,
        avg_vy: float,
        influence: float,
        temporal: Dict[str, float],
    ) -> Dict[str, float]:
        blend = min(self.max_adjustment_ratio, 0.07 + 0.15 * influence + 0.02 * max(float(embedding[6]), 0.0))
        vx_delta = blend * (avg_vx - track.vx) + 2.5 * float(embedding[0])
        vy_delta = blend * (avg_vy - track.vy) + 2.5 * float(embedding[1])
        heading_rad = math.radians(track.heading)
        accel = float(temporal["accel_mps2"]) * (0.45 + 0.25 * influence)
        accel_x = accel * math.sin(heading_rad)
        accel_y = accel * math.cos(heading_rad)
        return {
            "blend": blend,
            "vx_delta": vx_delta,
            "vy_delta": vy_delta,
            "accel_x": accel_x,
            "accel_y": accel_y,
            "corrected_vx": track.vx + vx_delta,
            "corrected_vy": track.vy + vy_delta,
        }

    def _edge_attention(self, neighbors: List[tuple[TrackState, float]]) -> List[Dict[str, float | str]]:
        total = sum(weight for _, weight in neighbors) or 1.0
        return [
            {"track_id": neighbor.track_id, "attention_weight": round(float(weight / total), 4)}
            for neighbor, weight in neighbors
        ]

    def _neighbors(self, track: TrackState, tracks: List[TrackState]) -> List[tuple[TrackState, float]]:
        neighbors: List[tuple[TrackState, float]] = []
        for other in tracks:
            if other.track_id == track.track_id:
                continue
            distance_m = haversine_m(track.lat, track.lon, other.lat, other.lon)
            if distance_m > self.max_neighbor_distance_m:
                continue
            distance_score = clamp(1.0 - distance_m / self.max_neighbor_distance_m)
            heading_score = clamp(1.0 - heading_difference_deg(track.heading, other.heading) / 60.0)
            speed_delta = abs(track.speed - other.speed)
            speed_score = clamp(1.0 - speed_delta / max(track.speed, other.speed, 1.0))
            type_score = 1.0 if track.object_type == other.object_type else 0.7
            weight = clamp(0.40 * distance_score + 0.25 * heading_score + 0.25 * speed_score + 0.10 * type_score)
            if weight >= 0.25:
                neighbors.append((other, weight))
        neighbors.sort(key=lambda item: item[1], reverse=True)
        return neighbors[:5]

    def _mark_no_graph_influence(self, track: TrackState, embedding: np.ndarray | None = None) -> None:
        embedding = embedding if embedding is not None else self._node_features(track)
        for point in track.predicted_path:
            point.setdefault("st_gnn_inspired", False)
            point.setdefault(
                "st_gnn",
                {
                    "algorithm": "ST-GNN",
                    "runtime": "local_numpy_message_passing",
                    "message_passing_layers": 2,
                    "node_embedding": [round(float(value), 4) for value in embedding[:6]],
                    "edge_attention": [],
                    "trained_model_loaded": False,
                },
            )
            point.setdefault("graph_neighbor_count", 0)
            point.setdefault("graph_influence", 0.0)
        track.metadata["st_gnn_inspired"] = {
            "enabled": False,
            "runtime": "local_numpy_message_passing",
            "message_passing_layers": 2,
            "neighbor_count": 0,
            "graph_influence": 0.0,
            "node_embedding": [round(float(value), 4) for value in embedding[:6]],
            "edge_attention": [],
            "note": "No nearby co-moving neighbors found for ST-GNN edge message passing.",
        }


STGNNInspiredPredictor = STGNNTrajectoryPredictor
