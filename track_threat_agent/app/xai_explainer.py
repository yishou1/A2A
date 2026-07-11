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
        dbn_transition = dbn_result.get("state_transition", {}) or {}
        pattern_transition = dbn_result.get("risk_pattern_transition", {}) or {}
        factor_chain = [
            {"factor": key, "contribution": value}
            for key, value in sorted(contributions.items(), key=lambda item: item[1], reverse=True)
        ]
        return {
            "xai": {
                "algorithm": "XAI",
                "contract": "sitrep_explainable_evidence_chain",
                "evidence_chain": self.threat_evidence_chain(track, factors, weighted_score, dbn_result),
                "factor_contributions": contributions,
                "factor_chain": factor_chain,
                "dbn_transition_evidence": {
                    "observation_reliability": dbn_result.get("observation_reliability"),
                    "posterior_entropy": dbn_result.get("posterior_entropy"),
                    "high_delta": dbn_transition.get("high_delta"),
                    "dominant_risk_pattern": dbn_result.get("dominant_risk_pattern"),
                    "risk_pattern_changed": pattern_transition.get("dominant_changed"),
                },
                "safety_chain": [
                    "该结果仅表示仿真态势关注优先级",
                    "不包含武器控制、制导、交战或打击建议",
                    "风险模式概率仅用于态势理解和排序解释",
                ],
                "model_trace": [
                    "prediction_gated_nearest_neighbor_tracking",
                    str((track.metadata.get("prediction") or {}).get("model", "adaptive_motion_prediction")),
                    "ST-GNN trajectory prediction with baseline fallback" if graph_meta.get("enabled") else "ST-GNN trajectory prediction without active graph neighbors",
                    "weighted_multi_factor_attention_score",
                    "DBN threat-state posterior smoothing",
                    "DBN risk-pattern probability calibration",
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
        state_transition = dbn_result.get("state_transition", {}) or {}
        evidence = [
            f"track {track.track_id} is a simulated {track.object_type} with quality {track.track_quality:.2f}",
            f"distance factor {factors.get('distance_factor', 0.0):.2f} and closing factor {factors.get('closing_factor', 0.0):.2f}",
            f"adaptive prediction model: {(track.metadata.get('prediction') or {}).get('model', 'unknown')}",
            f"graph refinement neighbors: {graph_meta.get('neighbor_count', 0)}, influence {graph_meta.get('graph_influence', 0.0)}",
        ]
        evidence.extend(
            [
                f"weighted score before DBN smoothing: {weighted_score:.2f}",
                f"DBN observation reliability: {float(dbn_result.get('observation_reliability', 0.0)):.2f}",
                f"DBN high-state delta: {float(state_transition.get('high_delta', 0.0)):.2f}",
                f"DBN posterior low/medium/high: {posterior.get('low', 0.0):.2f}/{posterior.get('medium', 0.0):.2f}/{posterior.get('high', 0.0):.2f}",
                f"dominant risk pattern: {dbn_result.get('dominant_risk_pattern', 'unknown')}",
                self.SAFETY_NOTE,
            ]
        )
        return evidence
