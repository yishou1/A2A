"""Algorithm provider boundary for the Track Threat Agent.

The provider is the seam between A2A/Nacos plumbing and the project-plan
algorithm stack. The plan-facing provider exposes ST-GNN trajectory prediction,
DBN threat assessment, KG+Transformer semantic reasoning, and XAI evidence as
the primary contract. The current in-process runtime now executes local
implementations for those algorithm families while keeping a legacy fallback
path for compatibility and future model replacement.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, List

from .models import Detection, ProtectedAsset, TrackState


@dataclass
class LocalBuiltInAlgorithmProvider:
    tracker: Any
    graph_predictor: Any
    ranker: Any
    impact_analyzer: Any
    group_detector: Any

    mode: str = "local_builtin"

    def update_tracks(self, detections: List[Detection], algorithm_level: str = "medium") -> List[TrackState]:
        tracks = self.tracker.update(detections, algorithm_level=algorithm_level)
        return self.graph_predictor.refine(tracks)

    def rank_threats(self, tracks: List[TrackState], scene: Dict[str, Any]) -> List[Any]:
        return self.ranker.rank(tracks, scene)

    def analyze_asset_impacts(
        self,
        tracks: List[TrackState],
        threats: List[Any],
        protected_assets: List[ProtectedAsset],
    ) -> List[Any]:
        return self.impact_analyzer.assess(tracks, threats, protected_assets)

    def detect_groups(self, tracks: List[TrackState], threats: List[Any], scene: Dict[str, Any]) -> List[Any]:
        return self.group_detector.detect(tracks, threats, scene)

    def reset(self) -> None:
        self.tracker.reset()
        self.ranker.reset()
        self.group_detector.reset()


@dataclass
class PlanAlgorithmProvider(LocalBuiltInAlgorithmProvider):
    """Project-plan algorithm contract backed by local runtime algorithms.

    This class reports the project-plan algorithms as the primary path and
    executes local implementations for ST-GNN-style graph message passing, DBN
    threat-state estimation, KG+Transformer self-attention semantics, and XAI
    evidence. Later trained model weights can replace the local matrices without
    changing the A2A artifact shape.
    """

    mode: str = "plan_algorithm_provider"

    def algorithm_contract(self) -> Dict[str, Any]:
        return {
            "mode": self.mode,
            "primary_algorithms": {
                "trajectory_prediction": "st_gnn_dynamic_entity_tracking",
                "threat_assessment": "dynamic_bayesian_network",
                "semantic_reasoning": "kg_transformer_semantic_sitrep",
                "explainability": "xai_evidence_chain",
                "protected_asset_impact": "asset_track_relation_graph",
            },
            "fallback_providers": {
                "trajectory_prediction": "baseline_motion_provider",
                "threat_assessment": "baseline_dbn_runtime",
                "semantic_reasoning": "metadata_kg_transformer_adapter",
                "explainability": "xai_evidence_runtime",
            },
            "local_runtime_implementations": {
                "trajectory_prediction": "local_numpy_st_gnn_message_passing",
                "threat_assessment": "dbn_with_coa_probability_runtime",
                "semantic_reasoning": "kg_transformer_self_attention_runtime",
                "tracking_filter": "covariance_kalman_cv_filter",
            },
            "training_status": {
                "st_gnn": "local_runtime_available; trained_external_weights_pending",
                "dbn": "runtime_probabilistic_model_available",
                "kg_transformer": "local_self_attention_runtime_available",
            },
        }

    def update_tracks(self, detections: List[Detection], algorithm_level: str = "medium") -> List[TrackState]:
        tracks = super().update_tracks(detections, algorithm_level=algorithm_level)
        for track in tracks:
            self._annotate_track_plan_algorithms(track)
        return tracks

    def rank_threats(self, tracks: List[TrackState], scene: Dict[str, Any]) -> List[Any]:
        threats = super().rank_threats(tracks, scene)
        for threat in threats:
            threat.metadata.setdefault("plan_algorithms", {})
            threat.metadata["plan_algorithms"].update(
                {
                    "threat_assessment": {
                        "algorithm": "DBN",
                        "contract": "dynamic_bayesian_network_threat_state",
                        "runtime_provider": "dbn_with_coa_probability_runtime",
                    },
                    "semantic_reasoning": {
                        "algorithm": "KG+Transformer",
                        "contract": "knowledge_graph_transformer_semantic_sitrep",
                        "runtime_provider": "kg_transformer_self_attention_runtime",
                    },
                }
            )
            if "xai" in threat.metadata:
                threat.metadata["xai"]["algorithm"] = "XAI"
                trace = threat.metadata["xai"].setdefault("model_trace", [])
                for item in ("ST-GNN trajectory prediction", "DBN threat posterior", "KG+Transformer semantic reasoning"):
                    if item not in trace:
                        trace.append(item)
        return threats

    def _annotate_track_plan_algorithms(self, track: TrackState) -> None:
        plan_algorithms = track.metadata.setdefault("plan_algorithms", {})
        graph_meta = track.metadata.get("st_gnn_inspired", {}) or {}
        plan_algorithms["trajectory_prediction"] = {
            "algorithm": "ST-GNN",
            "contract": "dynamic_entity_tracking_and_trajectory_prediction",
            "runtime_provider": graph_meta.get("runtime", "local_numpy_message_passing"),
            "fallback_provider": "baseline_motion_provider",
            "graph_neighbor_count": int(graph_meta.get("neighbor_count", 0) or 0),
            "graph_influence": float(graph_meta.get("graph_influence", 0.0) or 0.0),
            "trained_model_loaded": False,
        }
        for point in track.predicted_path:
            st_gnn = dict(point.get("st_gnn", {}) or {})
            st_gnn.update(
                {
                    "algorithm": "ST-GNN",
                    "contract": "dynamic_entity_tracking_and_trajectory_prediction",
                    "runtime_provider": st_gnn.get("runtime", graph_meta.get("runtime", "local_numpy_message_passing")),
                    "trained_model_loaded": False,
                    "graph_neighbor_count": int(point.get("graph_neighbor_count", graph_meta.get("neighbor_count", 0)) or 0),
                    "graph_influence": float(point.get("graph_influence", graph_meta.get("graph_influence", 0.0)) or 0.0),
                }
            )
            point["st_gnn"] = st_gnn
