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
    ranker: Any
    impact_analyzer: Any
    group_detector: Any

    mode: str = "local_builtin"
    trained_st_gnn_runtime: Any | None = None

    def update_tracks(
        self,
        detections: List[Detection],
        algorithm_level: str = "medium",
    ) -> List[TrackState]:
        tracks = self.tracker.update(detections, algorithm_level=algorithm_level)
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
                "group_detection": "physical_relation_complete_link_clustering",
                "protected_asset_impact": "predicted_path_asset_proximity",
            },
            "fallback_providers": {
                "trajectory_prediction": "adaptive_cv_ca_ct_physics",
                "threat_assessment": "weighted_risk_factor_score",
                "explainability": "local_xai_evidence_runtime",
            },
            "fallback_details": {
                "trajectory_prediction": "Agent-local adaptive CV/CA/CT physical hypothesis fusion",
            },
            "local_runtime_implementations": {
                "trajectory_prediction": "torchscript_st_gnn_with_physics_fallback",
                "threat_assessment": "dbn_risk_state_calibration_runtime",
                "tracking_filter": "covariance_kalman_cv_filter",
                "group_detection": "physical_relation_complete_link_runtime",
                "protected_asset_impact": "local_asset_impact_runtime",
            },
            "training_status": {
                "st_gnn": "Only verified TorchScript bundles are treated as ST-GNN; physical fallback is labeled separately",
                "dbn": {
                    "status": "runtime_probabilistic_model_available",
                    "parameter_schema": self.ranker.dbn_evaluator.parameters["schema_version"],
                    "parameter_version": self.ranker.dbn_evaluator.parameters["model_version"],
                    "parameter_sha256": self.ranker.dbn_evaluator.parameter_sha256,
                },
                "trained_st_gnn_runtime": (
                    self.trained_st_gnn_runtime.status()
                    if self.trained_st_gnn_runtime is not None
                    else {"overall": "degraded", "ready": True, "models": {}}
                ),
            },
            "algorithm_boundary": {
                "sensor_detection": "upstream_perception_agent",
                "multimodal_fusion": "upstream_fusion_agent",
                "intent_inference": "downstream_agent",
                "knowledge_graph_reasoning": "downstream_agent",
                "weapon_or_engagement_decision": "out_of_scope",
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
        trained_runtime_meta = track.metadata.get("st_gnn_runtime", {}) or {}
        trained_model_applied = bool(trained_runtime_meta.get("applied"))
        plan_algorithms["trajectory_prediction"] = {
            "algorithm": "ST-GNN",
            "contract": "dynamic_entity_tracking_and_trajectory_prediction",
            "applied": trained_model_applied,
            "runtime_provider": trained_runtime_meta.get("runtime") if trained_model_applied else None,
            "execution_location": "agent_process",
            "fallback_provider": "adaptive_multi_model_physics",
            "fallback_algorithm": "adaptive_multi_model_physics",
            "trained_model_loaded": trained_model_applied,
            "model_version": trained_runtime_meta.get("model_version"),
            "fallback_reason": trained_runtime_meta.get("fallback_reason") or (
                None if trained_model_applied else "trained_model_not_applied"
            ),
        }
        for point in track.predicted_path:
            uses_torchscript = point.get("model_used") == "st_gnn_torchscript"
            if not uses_torchscript:
                point.pop("st_gnn", None)
                point["prediction_provenance"] = {
                    "algorithm": "adaptive_multi_model_physics",
                    "role": "fallback",
                    "is_trained_model": False,
                }
                continue
            point["st_gnn"] = {
                "algorithm": "ST-GNN",
                "contract": "dynamic_entity_tracking_and_trajectory_prediction",
                "runtime": "torchscript_pytorch",
                "runtime_provider": "torchscript_pytorch",
                "execution_location": "agent_process",
                "trained_model_loaded": True,
                "model_version": point.get("model_version"),
                "baseline_model": point.get("baseline_model"),
                "uncertainty_radius_m": point.get("uncertainty_radius_m"),
            }
