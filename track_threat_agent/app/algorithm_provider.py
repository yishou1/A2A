"""Algorithm provider boundary for the Track Threat Agent.

The provider is the seam between A2A/Nacos plumbing and the project-plan
algorithm stack. The plan-facing provider exposes tracking, ST-GNN trajectory
prediction, DBN risk calibration, protected-asset impact analysis, group
detection, and XAI evidence as the primary contract. Knowledge/RAG/decision
reasoning belongs to downstream agents and consumes this agent's risk output.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, Iterable, List

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

    def update_tracks(self, detections: List[Detection], algorithm_level: str = "medium") -> List[TrackState]:
        tracks = self.tracker.update(detections, algorithm_level=algorithm_level)
        tracks = self.graph_predictor.refine(tracks)
        if self.learned_predictor is not None:
            tracks = self.learned_predictor.refine_tracks(tracks)
        if self.trained_st_gnn_runtime is not None:
            tracks = self.trained_st_gnn_runtime.refine_tracks(tracks)
        return tracks

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
    risk-state calibration, protected-asset impact analysis, and XAI evidence.
    Later trained model weights can replace the local matrices without changing
    the A2A artifact shape.
    """

    mode: str = "plan_algorithm_provider"
    algorithm_library: Any | None = None

    def algorithm_contract(self) -> Dict[str, Any]:
        return {
            "mode": self.mode,
            "execution_strategy": "algorithm_library_preferred_with_local_continuity_fallback",
            "primary_algorithms": {
                "trajectory_prediction": "st_gnn_dynamic_entity_tracking",
                "threat_assessment": "dynamic_bayesian_network",
                "explainability": "xai_evidence_chain",
                "protected_asset_impact": "asset_track_relation_graph",
            },
            "fallback_providers": {
                "trajectory_prediction": "baseline_motion_provider",
                "threat_assessment": "baseline_dbn_runtime",
                "explainability": "xai_evidence_runtime",
            },
            "local_runtime_implementations": {
                "trajectory_prediction": "local_numpy_st_gnn_message_passing",
                "threat_assessment": "dbn_risk_state_calibration_runtime",
                "tracking_filter": "covariance_kalman_cv_filter",
            },
            "training_status": {
                "st_gnn": "local_runtime_available; optional_numpy_sequence_weights_supported",
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
            "algorithm_library": (
                self.algorithm_library.status()
                if self.algorithm_library is not None
                else {"enabled": False, "status": "not_configured"}
            ),
        }

    def update_tracks(self, detections: List[Detection], algorithm_level: str = "medium") -> List[TrackState]:
        prepared_detections = self._library_preprocess_detections(detections)
        remote_track_update = self._library_invoke(
            "track_state_updater",
            {
                "detections": [detection.model_dump() for detection in prepared_detections],
                "existing_tracks": [self._track_payload(track) for track in self.tracker.tracks.values()],
            },
        )
        tracks = super().update_tracks(prepared_detections, algorithm_level=algorithm_level)
        self._annotate_remote_track_update(tracks, remote_track_update)
        self._apply_remote_trajectory_predictions(tracks)
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
                        "runtime_provider": "dbn_risk_state_calibration_runtime",
                    },
                }
            )
            if "xai" in threat.metadata:
                threat.metadata["xai"]["algorithm"] = "XAI"
                trace = threat.metadata["xai"].setdefault("model_trace", [])
                for item in ("ST-GNN trajectory prediction", "DBN risk-state calibration"):
                    if item not in trace:
                        trace.append(item)
        return threats

    def detect_groups(self, tracks: List[TrackState], threats: List[Any], scene: Dict[str, Any]) -> List[Any]:
        remote_graph = self._library_invoke(
            "graph_relation_reasoner",
            {"tracks": [self._track_payload(track) for track in tracks]},
        )
        self._annotate_remote_graph(tracks, remote_graph)
        groups = super().detect_groups(tracks, threats, scene)
        if remote_graph is not None and remote_graph.ok:
            remote_groups = remote_graph.outputs.get("groups") or []
            remote_relations = remote_graph.outputs.get("relations") or []
            for group in groups:
                member_set = set(group.member_track_ids)
                matched = any(member_set == set(item.get("member_track_ids") or item.get("members") or []) for item in remote_groups)
                group.evidence.append(
                    "算法库 M07 图关系推理已参与："
                    f"remote_relations={len(remote_relations)}，remote_group_match={str(matched).lower()}"
                )
        return groups

    def _library_preprocess_detections(self, detections: Iterable[Detection]) -> List[Detection]:
        prepared = [detection.model_copy(deep=True) for detection in detections]
        feature_result = self._library_invoke(
            "multimodal_feature_fuser",
            {"detections": [detection.model_dump() for detection in prepared], "tracks": [], "scene": {}},
        )
        feature_by_id = {
            str(item.get("item_id")): item
            for item in ((feature_result.outputs.get("feature_vectors") or []) if feature_result and feature_result.ok else [])
        }
        type_result = self._library_invoke(
            "target_type_classifier",
            {"detections": [detection.model_dump() for detection in prepared]},
        )
        type_by_id = {
            str(item.get("item_id")): item
            for item in ((type_result.outputs.get("classifications") or []) if type_result and type_result.ok else [])
        }
        for detection in prepared:
            library_metadata = detection.metadata.setdefault("algorithm_library", {})
            feature = feature_by_id.get(detection.detection_id)
            if feature:
                library_metadata["multimodal_feature_fuser"] = {
                    "used": True,
                    "feature_version": feature_result.outputs.get("feature_version"),
                    "numeric_features": feature.get("numeric_features") or {},
                }
            elif feature_result is not None:
                library_metadata["multimodal_feature_fuser"] = self._result_metadata(feature_result)

            classification = type_by_id.get(detection.detection_id)
            if classification:
                object_type = classification.get("object_type")
                if object_type in {"aircraft", "ship", "uav", "unknown"}:
                    detection.object_type = object_type
                confidence = classification.get("confidence")
                if confidence is not None:
                    detection.confidence = min(1.0, max(0.0, float(confidence)))
                library_metadata["target_type_classifier"] = {
                    "used": True,
                    "object_type": detection.object_type,
                    "confidence": detection.confidence,
                }
            elif type_result is not None:
                library_metadata["target_type_classifier"] = self._result_metadata(type_result)
        return prepared

    def _annotate_remote_track_update(self, tracks: List[TrackState], result: Any | None) -> None:
        for track in tracks:
            metadata = track.metadata.setdefault("algorithm_library", {})
            if result is None:
                continue
            metadata["track_state_updater"] = self._result_metadata(result)

    def _apply_remote_trajectory_predictions(self, tracks: List[TrackState]) -> None:
        by_type: Dict[str, List[TrackState]] = {}
        for track in tracks:
            if track.metadata.get("status", "active") == "lost":
                continue
            by_type.setdefault(track.object_type, []).append(track)

        for object_type, type_tracks in by_type.items():
            horizons = [600, 1200] if object_type == "ship" else [10, 20, 30, 60]
            result = self._library_invoke(
                "trajectory_predictor",
                {"tracks": [self._track_payload(track) for track in type_tracks], "horizons_s": horizons},
                {"horizons_s": horizons},
            )
            predictions = {
                str(item.get("track_id")): item
                for item in ((result.outputs.get("predictions") or []) if result and result.ok else [])
            }
            for track in type_tracks:
                metadata = track.metadata.setdefault("algorithm_library", {})
                prediction = predictions.get(track.track_id)
                if prediction and not bool(prediction.get("fallback_used")) and prediction.get("predicted_path"):
                    track.predicted_path = self._remote_prediction_path(track, prediction, result)
                    metadata["trajectory_predictor"] = {
                        **self._result_metadata(result),
                        "used": True,
                        "model_family": prediction.get("model_family"),
                        "model_version": prediction.get("model_version"),
                        "fallback": False,
                    }
                else:
                    details = self._result_metadata(result) if result is not None else {"used": False, "fallback": True}
                    details.update({"used": False, "fallback": True, "fallback_provider": "local_builtin"})
                    if prediction:
                        details["remote_fallback_reason"] = prediction.get("fallback_reason") or "remote_model_fallback"
                    metadata["trajectory_predictor"] = details

    def _annotate_remote_graph(self, tracks: List[TrackState], result: Any | None) -> None:
        for track in tracks:
            metadata = track.metadata.setdefault("algorithm_library", {})
            if result is None:
                continue
            details = self._result_metadata(result)
            if result.ok:
                graph_summary = result.outputs.get("graph_summary") or {}
                details.update(
                    {
                        "relation_count": len(result.outputs.get("relations") or []),
                        "group_count": len(result.outputs.get("groups") or []),
                        "graph_summary": graph_summary,
                    }
                )
            metadata["graph_relation_reasoner"] = details

    def _library_invoke(
        self,
        algorithm_id: str,
        inputs: Dict[str, Any],
        params: Dict[str, Any] | None = None,
    ) -> Any | None:
        if self.algorithm_library is None or not bool(getattr(self.algorithm_library, "enabled", False)):
            return None
        return self.algorithm_library.invoke(algorithm_id, inputs, params)

    @staticmethod
    def _result_metadata(result: Any) -> Dict[str, Any]:
        return {
            "used": bool(result.ok),
            "fallback": not bool(result.ok),
            "source": result.source,
            "endpoint": result.endpoint,
            "latency_ms": float(result.latency_ms or 0.0),
            "error": result.error,
        }

    @staticmethod
    def _track_payload(track: TrackState) -> Dict[str, Any]:
        payload = track.model_dump()
        payload["timestamp"] = track.last_update_time
        payload["confidence"] = track.track_quality
        return payload

    @staticmethod
    def _remote_prediction_path(track: TrackState, prediction: Dict[str, Any], result: Any) -> List[Dict[str, Any]]:
        path = []
        for point in prediction.get("predicted_path") or []:
            horizon = float(point.get("horizon_s", point.get("dt_s", 0.0)) or 0.0)
            if horizon <= 0.0:
                continue
            enriched = dict(point)
            enriched["dt_s"] = horizon
            enriched["timestamp"] = track.last_update_time + horizon
            enriched["model_used"] = "algorithm_library_trajectory_predictor"
            enriched["model_version"] = prediction.get("model_version")
            enriched["baseline_model"] = prediction.get("baseline_model")
            enriched["inference_latency_ms"] = float(prediction.get("inference_latency_ms") or result.latency_ms or 0.0)
            enriched["algorithm_library"] = {
                "algorithm_id": "trajectory_predictor",
                "source": result.source,
                "endpoint": result.endpoint,
            }
            path.append(enriched)
        return path or track.predicted_path

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
            "fallback_provider": "baseline_motion_provider",
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
            point_uses_torchscript = point.get("model_used") == "st_gnn_torchscript"
            point_uses_algorithm_library = point.get("model_used") == "algorithm_library_trajectory_predictor"
            runtime_provider = (
                "torchscript_pytorch"
                if point_uses_torchscript
                else "algorithm_library_python_http_service"
                if point_uses_algorithm_library
                else st_gnn.get("runtime", graph_meta.get("runtime", "local_numpy_message_passing"))
            )
            st_gnn.update(
                {
                    "algorithm": "ST-GNN",
                    "contract": "dynamic_entity_tracking_and_trajectory_prediction",
                    "runtime": runtime_provider,
                    "runtime_provider": runtime_provider,
                    "trained_model_loaded": (
                        point_uses_torchscript
                        or point_uses_algorithm_library
                        or bool(point_learned.get("loaded"))
                        or bool(st_gnn.get("trained_model_loaded"))
                    ),
                    "model_version": point.get("model_version") or st_gnn.get("model_version"),
                    "baseline_model": point.get("baseline_model") or st_gnn.get("baseline_model"),
                    "uncertainty_radius_m": point.get("uncertainty_radius_m"),
                    "graph_neighbor_count": int(point.get("graph_neighbor_count", graph_meta.get("neighbor_count", 0)) or 0),
                    "graph_influence": float(point.get("graph_influence", graph_meta.get("graph_influence", 0.0)) or 0.0),
                }
            )
            point["st_gnn"] = st_gnn
