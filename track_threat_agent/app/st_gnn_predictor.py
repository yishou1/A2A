"""Lightweight ST-GNN-inspired trajectory prediction refinement.

This module is intentionally demo-safe: it does not claim to be a trained
spatio-temporal graph neural network. It mirrors the idea from the project plan
by treating tracks as graph nodes and nearby/co-moving tracks as weighted edges,
then using neighbor motion as a small correction to short-term predictions.
"""

from __future__ import annotations

from typing import Dict, Iterable, List

from .models import TrackState
from .utils import clamp, haversine_m, heading_difference_deg, project_position


class STGNNInspiredPredictor:
    """Refine `predicted_path` with simple graph-neighbor motion messages."""

    def __init__(self, max_neighbor_distance_m: float = 8_000.0, max_adjustment_ratio: float = 0.18) -> None:
        self.max_neighbor_distance_m = max_neighbor_distance_m
        self.max_adjustment_ratio = max_adjustment_ratio

    def refine(self, tracks: Iterable[TrackState]) -> List[TrackState]:
        track_list = list(tracks)
        if len(track_list) < 2:
            for track in track_list:
                self._mark_no_graph_influence(track)
            return track_list

        neighbors_by_track = {
            track.track_id: self._neighbors(track, track_list)
            for track in track_list
        }

        for track in track_list:
            neighbors = neighbors_by_track[track.track_id]
            if not neighbors:
                self._mark_no_graph_influence(track)
                continue

            weight_sum = sum(weight for _, weight in neighbors)
            avg_vx = sum(neighbor.vx * weight for neighbor, weight in neighbors) / max(weight_sum, 1e-6)
            avg_vy = sum(neighbor.vy * weight for neighbor, weight in neighbors) / max(weight_sum, 1e-6)
            influence = clamp(weight_sum / max(len(neighbors), 1), 0.0, 1.0)
            blend = min(self.max_adjustment_ratio, 0.05 + 0.10 * influence)
            corrected_vx = (1.0 - blend) * track.vx + blend * avg_vx
            corrected_vy = (1.0 - blend) * track.vy + blend * avg_vy

            refined_path = []
            for point in track.predicted_path:
                dt = float(point.get("dt_s", 0.0))
                lat, lon = project_position(track.lat, track.lon, corrected_vx, corrected_vy, dt)
                refined = dict(point)
                refined["lat"] = lat
                refined["lon"] = lon
                refined["st_gnn_inspired"] = True
                refined["graph_neighbor_count"] = len(neighbors)
                refined["graph_influence"] = round(influence, 4)
                refined["graph_blend"] = round(blend, 4)
                base_model = point.get("model_used") or point.get("prediction_model", "adaptive")
                refined["model_used"] = f"{base_model}_graph_refined"
                refined["prediction_model"] = refined["model_used"]
                refined["prediction_confidence"] = round(
                    clamp(float(point.get("prediction_confidence", track.track_quality)) + 0.04 * influence),
                    3,
                )
                refined_path.append(refined)

            track.predicted_path = refined_path
            track.metadata["st_gnn_inspired"] = {
                "enabled": True,
                "neighbor_count": len(neighbors),
                "graph_influence": round(influence, 4),
                "graph_blend": round(blend, 4),
                "neighbor_track_ids": [neighbor.track_id for neighbor, _ in neighbors],
                "note": "Lightweight graph-neighbor refinement for demo trajectory prediction.",
            }
        return track_list

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

    def _mark_no_graph_influence(self, track: TrackState) -> None:
        for point in track.predicted_path:
            point.setdefault("st_gnn_inspired", False)
            point.setdefault("graph_neighbor_count", 0)
            point.setdefault("graph_influence", 0.0)
        track.metadata["st_gnn_inspired"] = {
            "enabled": False,
            "neighbor_count": 0,
            "graph_influence": 0.0,
            "note": "No nearby co-moving neighbors found for graph refinement.",
        }
