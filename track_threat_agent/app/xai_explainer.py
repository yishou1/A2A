"""Explainability helpers for simulation-only risk-priority outputs."""

from __future__ import annotations

from typing import Dict, List

from .models import TrackState


class XAIExplanationBuilder:
    """Build compact evidence chains and model traces for reports/events."""

    SAFETY_NOTE = "simulation-only attention priority; no weapon control, targeting, or engagement advice"

    def threat_metadata(
        self,
        track: TrackState,
        factors: Dict[str, float],
        weighted_score: float,
        dbn_result: Dict[str, object],
    ) -> Dict[str, object]:
        contributions = {
            "distance": round(0.28 * factors.get("distance_factor", 0.0), 4),
            "closing": round(0.24 * factors.get("closing_factor", 0.0), 4),
            "type": round(0.18 * factors.get("type_factor", 0.0), 4),
            "anomaly": round(0.18 * factors.get("anomaly_factor", 0.0), 4),
            "quality": round(0.12 * factors.get("quality_factor", 0.0), 4),
            "dbn_state": round(0.28 * float(dbn_result.get("state_factor", 0.0)), 4),
        }
        graph_meta = track.metadata.get("st_gnn_inspired", {}) or {}
        return {
            "xai": {
                "evidence_chain": self.threat_evidence_chain(track, factors, weighted_score, dbn_result),
                "factor_contributions": contributions,
                "model_trace": [
                    "prediction_gated_nearest_neighbor_tracking",
                    str((track.metadata.get("prediction") or {}).get("model", "adaptive_motion_prediction")),
                    "ST-GNN trajectory prediction with baseline fallback" if graph_meta.get("enabled") else "ST-GNN trajectory prediction without active graph neighbors",
                    "weighted_multi_factor_attention_score",
                    "DBN threat-state posterior smoothing",
                ],
                "safety_note": self.SAFETY_NOTE,
            }
        }

    def threat_evidence_chain(
        self,
        track: TrackState,
        factors: Dict[str, float],
        weighted_score: float,
        dbn_result: Dict[str, object],
    ) -> List[str]:
        graph_meta = track.metadata.get("st_gnn_inspired", {}) or {}
        posterior = dbn_result.get("posterior", {}) or {}
        return [
            f"track {track.track_id} is a simulated {track.object_type} with quality {track.track_quality:.2f}",
            f"distance factor {factors.get('distance_factor', 0.0):.2f} and closing factor {factors.get('closing_factor', 0.0):.2f}",
            f"adaptive prediction model: {(track.metadata.get('prediction') or {}).get('model', 'unknown')}",
            f"graph refinement neighbors: {graph_meta.get('neighbor_count', 0)}, influence {graph_meta.get('graph_influence', 0.0)}",
            f"weighted score before DBN smoothing: {weighted_score:.2f}",
            f"DBN posterior low/medium/high: {posterior.get('low', 0.0):.2f}/{posterior.get('medium', 0.0):.2f}/{posterior.get('high', 0.0):.2f}",
            self.SAFETY_NOTE,
        ]
