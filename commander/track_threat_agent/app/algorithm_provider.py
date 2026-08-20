"""In-process algorithm execution owned by the Track Threat Agent.

The shared algorithm repository is a source and model distribution boundary.
It is not a runtime workflow dependency: one A2A request is evaluated by the
models already loaded in this Agent process, with local physical fallbacks.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, List

from .algorithm_library_runtime import AlgorithmLibraryError
from .models import Detection, ProtectedAsset, TrackState


@dataclass
class LocalBuiltInAlgorithmProvider:
    tracker: Any
    ranker: Any
    impact_analyzer: Any
    group_detector: Any

    mode: str = "local_builtin"
    trained_st_gnn_runtime: Any | None = None
    algorithm_library_runtime: Any | None = None

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
    """LLM-planned algorithm-library execution with local safe fallback."""

    mode: str = "llm_algolib_hybrid_runtime"

    def begin_request(
        self,
        *,
        request_id: str,
        requested_skills: List[str],
        request_summary: Dict[str, Any],
    ) -> List[Any]:
        if self.algorithm_library_runtime is None:
            return []
        return self.algorithm_library_runtime.begin_request(
            request_id=request_id,
            requested_skills=requested_skills,
            request_summary=request_summary,
        )

    def algorithm_execution_trace(self) -> Dict[str, Any]:
        if self.algorithm_library_runtime is None:
            return {
                "enabled": False,
                "planner_mode": "local_only",
                "planner_fallback_reason": "algorithm_library_runtime_not_configured",
                "planned_algorithms": [],
                "executions": [],
                "local_fallbacks": [],
            }
        return self.algorithm_library_runtime.execution_trace()

    def algorithm_contract(self) -> Dict[str, Any]:
        return {
            "mode": self.mode,
            "execution_strategy": "llm_planned_algorithm_library_with_local_fallback",
            "model_ownership": "algorithm_library_with_agent_local_fallback",
            "network_algorithm_calls": bool(
                self.algorithm_library_runtime is not None
                and self.algorithm_library_runtime.settings.enabled
            ),
            "internal_workflow_engine": False,
            "algorithm_library": (
                self.algorithm_library_runtime.status()
                if self.algorithm_library_runtime is not None
                else {
                    "enabled": False,
                    "base_url": "http://127.0.0.1:8088",
                    "planner_mode": "local_only",
                }
            ),
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
        existing_tracks = [track.model_dump() for track in self.tracker.tracks.values()]
        tracks = super().update_tracks(detections, algorithm_level=algorithm_level)
        self._run_track_library_algorithms(detections, existing_tracks, tracks)
        for track in tracks:
            self._annotate_track_plan_algorithms(track)
        return tracks

    def rank_threats(
        self,
        tracks: List[TrackState],
        scene: Dict[str, Any],
    ) -> List[Any]:
        self._run_feature_fuser(tracks, scene)
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
        relation_output = self._run_remote(
            "graph_relation_reasoner",
            inputs={"tracks": [self._track_for_algolib(track) for track in tracks]},
            params={},
        )
        groups = super().detect_groups(tracks, threats, scene)
        for group in groups:
            group.evidence.append(
                "Group relation graph executed inside Track Threat Agent process."
            )
            if relation_output is not None:
                remote_groups = relation_output.get("groups") or []
                matched = [
                    item
                    for item in remote_groups
                    if set(item.get("member_track_ids") or item.get("members") or [])
                    == set(group.member_track_ids)
                ]
                group.metadata.setdefault("algorithm_library", {})[
                    "graph_relation_reasoner"
                ] = {
                    "used": True,
                    "matched_remote_groups": len(matched),
                    "graph_summary": relation_output.get("graph_summary", {}),
                }
                group.evidence.append(
                    "The zsl algorithm-library graph relation reasoner was executed and cross-checked."
                )
        return groups

    def _run_track_library_algorithms(
        self,
        detections: List[Detection],
        existing_tracks: List[Dict[str, Any]],
        tracks: List[TrackState],
    ) -> None:
        detection_payloads = [detection.model_dump() for detection in detections]
        classification = self._run_remote(
            "target_type_classifier",
            inputs={"detections": detection_payloads},
            params={},
        )
        if classification is not None:
            by_item = {
                str(item.get("item_id")): item
                for item in classification.get("classifications", [])
                if isinstance(item, dict)
            }
            for track in tracks:
                detection_id = str(track.metadata.get("last_detection_id") or "")
                item = by_item.get(detection_id) or by_item.get(track.track_id)
                if item:
                    track.metadata.setdefault("algorithm_library", {})[
                        "target_type_classifier"
                    ] = item

        state_output = self._run_remote(
            "track_state_updater",
            inputs={
                "detections": detection_payloads,
                "existing_tracks": existing_tracks,
            },
            params={},
        )
        if state_output is not None:
            summary = state_output.get("summary", {})
            for track in tracks:
                track.metadata.setdefault("algorithm_library", {})[
                    "track_state_updater"
                ] = {
                    "used": True,
                    "schema_version": state_output.get("schema_version"),
                    "summary": summary,
                    "role": "library_cross_check_local_state_store_remains_authoritative",
                }

        prediction_output = self._run_remote(
            "trajectory_predictor",
            inputs={"tracks": [self._track_for_algolib(track) for track in tracks]},
            params={"horizons_s": [10, 20, 30, 60, 600, 1200]},
        )
        if prediction_output is not None:
            self._merge_remote_predictions(tracks, prediction_output)

    def _run_feature_fuser(self, tracks: List[TrackState], scene: Dict[str, Any]) -> None:
        output = self._run_remote(
            "multimodal_feature_fuser",
            inputs={
                "tracks": [self._track_for_algolib(track) for track in tracks],
                "scene": scene,
                "protected_assets": scene.get("protected_assets", []),
            },
            params={},
        )
        if output is None:
            return
        by_item = {
            str(item.get("item_id")): item
            for item in output.get("feature_vectors", [])
            if isinstance(item, dict)
        }
        for track in tracks:
            item = by_item.get(track.track_id)
            if item:
                track.metadata.setdefault("algorithm_library", {})[
                    "multimodal_feature_fuser"
                ] = item

    def _run_remote(
        self,
        algorithm_id: str,
        *,
        inputs: Dict[str, Any],
        params: Dict[str, Any],
    ) -> Dict[str, Any] | None:
        runtime = self.algorithm_library_runtime
        if runtime is None or not runtime.should_run(algorithm_id):
            return None
        try:
            return runtime.run(algorithm_id, inputs=inputs, params=params)
        except AlgorithmLibraryError as exc:
            runtime.record_local_fallback(algorithm_id, str(exc))
            if runtime.settings.required:
                raise
            return None

    @staticmethod
    def _track_for_algolib(track: TrackState) -> Dict[str, Any]:
        payload = track.model_dump()
        payload["timestamp"] = track.last_update_time
        payload["confidence"] = track.track_quality
        return payload

    @staticmethod
    def _merge_remote_predictions(
        tracks: List[TrackState],
        output: Dict[str, Any],
    ) -> None:
        predictions = {
            str(item.get("track_id")): item
            for item in output.get("predictions", [])
            if isinstance(item, dict) and item.get("track_id")
        }
        for track in tracks:
            prediction = predictions.get(track.track_id)
            if not prediction:
                continue
            remote_points = []
            for raw_point in prediction.get("predicted_path", []):
                if not isinstance(raw_point, dict):
                    continue
                if not {"horizon_s", "lat", "lon"} <= set(raw_point):
                    continue
                point = dict(raw_point)
                point.update(
                    {
                        "model_used": "algorithm_library",
                        "algorithm_id": "trajectory_predictor",
                        "model_version": prediction.get("model_version"),
                        "baseline_model": prediction.get("baseline_model"),
                        "fallback_reason": prediction.get("fallback_reason", ""),
                    }
                )
                remote_points.append(point)
            if not remote_points:
                continue
            remote_horizons = {int(point["horizon_s"]) for point in remote_points}
            retained_local = [
                point
                for point in track.predicted_path
                if PlanAlgorithmProvider._prediction_horizon(point) not in remote_horizons
            ]
            track.predicted_path = sorted(
                [*remote_points, *retained_local],
                key=PlanAlgorithmProvider._prediction_horizon,
            )
            track.metadata.setdefault("algorithm_library", {})[
                "trajectory_predictor"
            ] = {
                "used": True,
                "model_family": prediction.get("model_family"),
                "model_version": prediction.get("model_version"),
                "fallback_used": bool(prediction.get("fallback_used", False)),
                "fallback_reason": prediction.get("fallback_reason", ""),
            }

    @staticmethod
    def _prediction_horizon(point: Dict[str, Any]) -> int:
        return int(
            point.get(
                "horizon_s",
                point.get("dt_s", point.get("time_offset_s", 0)),
            )
        )

    def _annotate_track_plan_algorithms(self, track: TrackState) -> None:
        plan_algorithms = track.metadata.setdefault("plan_algorithms", {})
        trained_runtime_meta = track.metadata.get("st_gnn_runtime", {}) or {}
        remote_runtime_meta = (
            track.metadata.get("algorithm_library", {}).get("trajectory_predictor", {})
            or {}
        )
        remote_model_applied = bool(
            remote_runtime_meta.get("used")
            and not remote_runtime_meta.get("fallback_used")
            and remote_runtime_meta.get("model_family") == "st_gnn"
        )
        trained_model_applied = bool(trained_runtime_meta.get("applied")) or remote_model_applied
        plan_algorithms["trajectory_prediction"] = {
            "algorithm": "ST-GNN",
            "contract": "dynamic_entity_tracking_and_trajectory_prediction",
            "applied": trained_model_applied,
            "runtime_provider": (
                "zsl_algorithm_library"
                if remote_model_applied
                else trained_runtime_meta.get("runtime") if trained_model_applied else None
            ),
            "execution_location": (
                "zsl_algorithm_library" if remote_model_applied else "agent_process"
            ),
            "fallback_provider": "adaptive_multi_model_physics",
            "fallback_algorithm": "adaptive_multi_model_physics",
            "trained_model_loaded": trained_model_applied,
            "model_version": (
                remote_runtime_meta.get("model_version")
                if remote_model_applied
                else trained_runtime_meta.get("model_version")
            ),
            "fallback_reason": remote_runtime_meta.get("fallback_reason") or trained_runtime_meta.get("fallback_reason") or (
                None if trained_model_applied else "trained_model_not_applied"
            ),
        }
        for point in track.predicted_path:
            if point.get("model_used") == "algorithm_library":
                is_trained_model = not bool(point.get("fallback_reason"))
                point["prediction_provenance"] = {
                    "algorithm": "trajectory_predictor",
                    "role": "primary" if is_trained_model else "fallback",
                    "is_trained_model": is_trained_model,
                    "execution_location": "zsl_algorithm_library",
                }
                if is_trained_model:
                    point["st_gnn"] = {
                        "algorithm": "ST-GNN",
                        "contract": "dynamic_entity_tracking_and_trajectory_prediction",
                        "runtime": "zsl_algorithm_library",
                        "runtime_provider": "python_http_service",
                        "execution_location": "zsl_algorithm_library",
                        "trained_model_loaded": True,
                        "model_version": point.get("model_version"),
                        "baseline_model": point.get("baseline_model"),
                        "uncertainty_radius_m": point.get("uncertainty_radius_m"),
                    }
                continue
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
