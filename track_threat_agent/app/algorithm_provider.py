"""In-process algorithm execution owned by the Track Threat Agent.

The shared algorithm repository is a source and model distribution boundary.
It is not a runtime workflow dependency: one A2A request is evaluated by the
models already loaded in this Agent process, with local physical fallbacks.
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
    learned_predictor: Any | None = None
    trained_st_gnn_runtime: Any | None = None

    def update_tracks(
        self,
        detections: List[Detection],
        algorithm_level: str = "medium",
    ) -> List[TrackState]:
        tracks = self.tracker.update(detections, algorithm_level=algorithm_level)
        tracks = self.graph_predictor.refine(tracks)
        if self.learned_predictor is not None:
            tracks = self.learned_predictor.refine_tracks(tracks)
        if self.trained_st_gnn_runtime is not None:
            tracks = self.trained_st_gnn_runtime.refine_tracks(tracks)
        return tracks

    def rank_threats(
        self,
        tracks: List[TrackState],
        scene: Dict[str, Any],
    ) -> List[Any]:
        return self.ranker.rank(tracks, scene)

    def analyze_asset_impacts(
        self,
        tracks: List[TrackState],
        threats: List[Any],
        protected_assets: List[ProtectedAsset],
    ) -> List[Any]:
        return self.impact_analyzer.assess(tracks, threats, protected_assets)

    def detect_groups(
        self,
        tracks: List[TrackState],
        threats: List[Any],
        scene: Dict[str, Any],
    ) -> List[Any]:
        return self.group_detector.detect(tracks, threats, scene)

    def reset(self) -> None:
        self.tracker.reset()
        self.ranker.reset()
        self.group_detector.reset()


@dataclass
class PlanAlgorithmProvider(LocalBuiltInAlgorithmProvider):
    """Project-plan algorithms executed directly inside the Agent process."""

    mode: str = "agent_local_model_runtime"

    def algorithm_contract(self) -> Dict[str, Any]:
        return {
            "mode": self.mode,
            "execution_strategy": "in_process_model_execution",
            "model_ownership": "track_threat_agent",
            "network_algorithm_calls": False,
            "internal_workflow_engine": False,
            "primary_algorithms": {
                "trajectory_tracking": "covariance_kalman_cv_filter",
                "trajectory_prediction": "st_gnn_dynamic_entity_tracking",
                "threat_assessment": "dynamic_bayesian_network",
                "explainability": "xai_evidence_chain",
                "group_detection": "track_relation_connected_components",
                "protected_asset_impact": "asset_track_relation_graph",
            },
            "fallback_providers": {
                "trajectory_prediction": "baseline_motion_provider",
                "threat_assessment": "weighted_risk_factor_score",
                "explainability": "local_xai_evidence_runtime",
            },
            "fallback_details": {
                "trajectory_prediction": "Agent-local IMM or constant-velocity physics",
            },
            "local_runtime_implementations": {
                "trajectory_prediction": "torchscript_st_gnn_with_local_graph_fallback",
                "threat_assessment": "dbn_risk_state_calibration_runtime",
                "tracking_filter": "covariance_kalman_cv_filter",
                "group_detection": "local_relation_graph_runtime",
                "protected_asset_impact": "local_asset_impact_runtime",
            },
            "training_status": {
                "st_gnn": "TorchScript bundles are loaded by the Agent; physical fallback remains local",
                "dbn": "runtime_probabilistic_model_available",
                "learned_trajectory_predictor": (
                    self.learned_predictor.status()
                    if self.learned_predictor is not None
                    else {"loaded": False, "model_path": None, "model_type": None}
                ),
                "trained_st_gnn_runtime": (
                    self.trained_st_gnn_runtime.status()
                    if self.trained_st_gnn_runtime is not None
                    else {"overall": "degraded", "ready": True, "models": {}}
                ),
            },
        }

    def update_tracks(
        self,
        detections: List[Detection],
        algorithm_level: str = "medium",
    ) -> List[TrackState]:
        tracks = super().update_tracks(detections, algorithm_level=algorithm_level)
        for track in tracks:
            self._annotate_track_plan_algorithms(track)
        return tracks

    def rank_threats(
        self,
        tracks: List[TrackState],
        scene: Dict[str, Any],
    ) -> List[Any]:
        threats = super().rank_threats(tracks, scene)
        for threat in threats:
            threat.metadata.setdefault("plan_algorithms", {})
            threat.metadata["plan_algorithms"]["threat_assessment"] = {
                "algorithm": "DBN",
                "contract": "dynamic_bayesian_network_threat_state",
                "runtime_provider": "dbn_risk_state_calibration_runtime",
                "execution_location": "agent_process",
            }
            if "xai" in threat.metadata:
                threat.metadata["xai"]["algorithm"] = "XAI"
                trace = threat.metadata["xai"].setdefault("model_trace", [])
                for item in (
                    "ST-GNN trajectory prediction",
                    "DBN risk-state calibration",
                ):
                    if item not in trace:
                        trace.append(item)
        return threats

    def detect_groups(
        self,
        tracks: List[TrackState],
        threats: List[Any],
        scene: Dict[str, Any],
    ) -> List[Any]:
        groups = super().detect_groups(tracks, threats, scene)
        for group in groups:
            group.evidence.append(
                "Group relation graph executed inside Track Threat Agent process."
            )
        return groups

    def _annotate_track_plan_algorithms(self, track: TrackState) -> None:
        plan_algorithms = track.metadata.setdefault("plan_algorithms", {})
        graph_meta = track.metadata.get("st_gnn_inspired", {}) or {}
        learned_meta = track.metadata.get("learned_predictor", {}) or {}
        trained_runtime_meta = track.metadata.get("st_gnn_runtime", {}) or {}
        trained_model_loaded = bool(learned_meta.get("applied")) or bool(
            trained_runtime_meta.get("applied")
        )
        plan_algorithms["trajectory_prediction"] = {
            "algorithm": "ST-GNN",
            "contract": "dynamic_entity_tracking_and_trajectory_prediction",
            "runtime_provider": trained_runtime_meta.get(
                "runtime",
                graph_meta.get("runtime", "local_numpy_message_passing"),
            ),
            "execution_location": "agent_process",
            "fallback_provider": "imm_or_constant_velocity_physics",
            "graph_neighbor_count": int(graph_meta.get("neighbor_count", 0) or 0),
            "graph_influence": float(graph_meta.get("graph_influence", 0.0) or 0.0),
            "trained_model_loaded": trained_model_loaded,
            "learned_model_provider": learned_meta.get("model_type"),
            "model_version": trained_runtime_meta.get("model_version"),
            "fallback_reason": trained_runtime_meta.get("fallback_reason"),
        }
        for point in track.predicted_path:
            st_gnn = dict(point.get("st_gnn", {}) or {})
            point_learned = dict(point.get("learned_model", {}) or {})
            uses_torchscript = point.get("model_used") == "st_gnn_torchscript"
            runtime_provider = (
                "torchscript_pytorch"
                if uses_torchscript
                else st_gnn.get(
                    "runtime",
                    graph_meta.get("runtime", "local_numpy_message_passing"),
                )
            )
            st_gnn.update(
                {
                    "algorithm": "ST-GNN",
                    "contract": "dynamic_entity_tracking_and_trajectory_prediction",
                    "runtime": runtime_provider,
                    "runtime_provider": runtime_provider,
                    "execution_location": "agent_process",
                    "trained_model_loaded": (
                        uses_torchscript
                        or bool(point_learned.get("loaded"))
                        or bool(st_gnn.get("trained_model_loaded"))
                    ),
                    "model_version": point.get("model_version")
                    or st_gnn.get("model_version"),
                    "baseline_model": point.get("baseline_model")
                    or st_gnn.get("baseline_model"),
                    "uncertainty_radius_m": point.get("uncertainty_radius_m"),
                    "graph_neighbor_count": int(
                        point.get(
                            "graph_neighbor_count",
                            graph_meta.get("neighbor_count", 0),
                        )
                        or 0
                    ),
                    "graph_influence": float(
                        point.get(
                            "graph_influence",
                            graph_meta.get("graph_influence", 0.0),
                        )
                        or 0.0
                    ),
                }
            )
            point["st_gnn"] = st_gnn
