from __future__ import annotations

import json
from typing import Any

from app.algorithm_library_client import (
    AlgorithmLibraryClient,
    AlgorithmLibrarySettings,
    RemoteAlgorithmResult,
)
from app.algorithm_provider import PlanAlgorithmProvider
from app.models import Detection, TrackState


class _FakeTransport:
    def __init__(self, responses: dict[tuple[str, str], dict[str, Any]]) -> None:
        self.responses = responses
        self.calls: list[tuple[str, str, dict[str, Any] | None]] = []

    def __call__(self, method: str, url: str, payload: dict[str, Any] | None, _timeout_s: float) -> dict[str, Any]:
        self.calls.append((method, url, payload))
        return self.responses[(method, url)]


def test_algorithm_library_client_uses_explicit_http_service_endpoint():
    transport = _FakeTransport(
        {
            (
                "POST",
                "http://algorithms:9022/trajectory_predictor/predict",
            ): {
                "ok": True,
                "algorithm_id": "trajectory_predictor",
                "version": "1.0.0",
                "outputs": {"predictions": [{"track_id": "trk-1", "predicted_path": []}]},
                "usage": {"latency_ms": 12.5},
            }
        }
    )
    client = AlgorithmLibraryClient(
        AlgorithmLibrarySettings(enabled=True, base_url="http://algorithms:9022"),
        transport=transport,
    )

    result = client.invoke("trajectory_predictor", {"tracks": [{"track_id": "trk-1"}]})

    assert result.ok is True
    assert result.source == "configured_base_url"
    assert result.outputs["predictions"][0]["track_id"] == "trk-1"
    assert transport.calls[0][1].endswith("/trajectory_predictor/predict")


def test_algorithm_library_client_discovers_nacos_instance_and_filters_owner_scope():
    nacos_url = "http://nacos:8848/nacos/v1/ns/instance/list?serviceName=track-threat-algorithms&groupName=DEFAULT_GROUP"
    transport = _FakeTransport(
        {
            ("GET", nacos_url): {
                "hosts": [
                    {
                        "ip": "10.0.0.8",
                        "port": 9022,
                        "healthy": True,
                        "enabled": True,
                        "metadata": {"owner_scope": "other_agent"},
                    },
                    {
                        "ip": "10.0.0.9",
                        "port": 9022,
                        "healthy": True,
                        "enabled": True,
                        "metadata": {
                            "owner_scope": "track_threat_agent",
                            "base_url": "http://10.0.0.9:9022",
                        },
                    },
                ]
            },
            (
                "POST",
                "http://10.0.0.9:9022/graph_relation_reasoner/predict",
            ): {
                "ok": True,
                "algorithm_id": "graph_relation_reasoner",
                "outputs": {"relations": [], "groups": []},
                "usage": {"latency_ms": 4.0},
            },
        }
    )
    client = AlgorithmLibraryClient(
        AlgorithmLibrarySettings(
            enabled=True,
            nacos_server="nacos:8848",
            service_name="track-threat-algorithms",
        ),
        transport=transport,
    )

    result = client.invoke("graph_relation_reasoner", {"tracks": [{"track_id": "trk-1"}]})

    assert result.ok is True
    assert result.source == "nacos_discovery"
    assert transport.calls[1][1].startswith("http://10.0.0.9:9022")


class _Tracker:
    def __init__(self) -> None:
        self.tracks: dict[str, TrackState] = {}

    def update(self, detections: list[Detection], algorithm_level: str = "medium") -> list[TrackState]:
        detection = detections[0]
        track = TrackState(
            track_id="trk-1",
            object_type=detection.object_type,
            lat=detection.lat,
            lon=detection.lon,
            alt=detection.alt,
            speed=detection.speed,
            heading=detection.heading,
            last_update_time=detection.timestamp,
            history_path=[
                {
                    "timestamp": detection.timestamp,
                    "lat": detection.lat,
                    "lon": detection.lon,
                    "alt": detection.alt,
                    "speed": detection.speed,
                    "heading": detection.heading,
                }
            ],
            predicted_path=[{"dt_s": 10.0, "lat": detection.lat + 0.01, "lon": detection.lon}],
            metadata={"last_detection_id": detection.detection_id},
        )
        self.tracks = {track.track_id: track}
        return [track]

    def reset(self) -> None:
        self.tracks.clear()


class _PassThrough:
    def refine(self, tracks: list[TrackState]) -> list[TrackState]:
        return tracks


class _Resettable:
    def reset(self) -> None:
        return None


class _RemoteLibrary:
    enabled = True

    def __init__(self) -> None:
        self.calls: list[str] = []

    def invoke(self, algorithm_id: str, _inputs: dict[str, Any], _params: dict[str, Any] | None = None) -> RemoteAlgorithmResult:
        self.calls.append(algorithm_id)
        outputs: dict[str, Any] = {}
        if algorithm_id == "target_type_classifier":
            outputs = {"classifications": [{"item_id": "det-1", "object_type": "aircraft", "confidence": 0.91}]}
        elif algorithm_id == "track_state_updater":
            outputs = {"updates": [{"track_id": "trk-remote", "update_type": "created"}]}
        elif algorithm_id == "trajectory_predictor":
            outputs = {
                "predictions": [
                    {
                        "track_id": "trk-1",
                        "model_family": "st_gnn",
                        "model_version": "remote-st-gnn-v1",
                        "baseline_model": "IMM",
                        "inference_latency_ms": 7.0,
                        "fallback_used": False,
                        "predicted_path": [
                            {
                                "horizon_s": 10,
                                "lat": 31.21,
                                "lon": 121.51,
                                "alt": 1000.0,
                                "prediction_confidence": 0.9,
                                "uncertainty_radius_m": 80.0,
                            }
                        ],
                    }
                ]
            }
        elif algorithm_id == "graph_relation_reasoner":
            outputs = {"relations": [], "groups": [], "graph_summary": {"node_count": 1, "edge_count": 0}}
        return RemoteAlgorithmResult.ok_result(algorithm_id, outputs, source="configured_base_url")

    def status(self) -> dict[str, Any]:
        return {"enabled": True, "status": "ready"}


def test_plan_provider_uses_remote_prediction_and_keeps_authoritative_local_track_store():
    remote = _RemoteLibrary()
    provider = PlanAlgorithmProvider(
        _Tracker(),
        _PassThrough(),
        _Resettable(),
        _Resettable(),
        _Resettable(),
        algorithm_library=remote,
    )
    detection = Detection(
        detection_id="det-1",
        object_type="unknown",
        timestamp=1_700_000_000.0,
        lat=31.2,
        lon=121.5,
        alt=1000.0,
        speed=210.0,
        heading=90.0,
        confidence=0.6,
    )

    tracks = provider.update_tracks([detection])

    assert tracks[0].track_id == "trk-1"
    assert tracks[0].object_type == "aircraft"
    assert tracks[0].predicted_path[0]["model_version"] == "remote-st-gnn-v1"
    assert tracks[0].predicted_path[0]["dt_s"] == 10.0
    assert tracks[0].predicted_path[0]["st_gnn"]["runtime_provider"] == "algorithm_library_python_http_service"
    assert tracks[0].metadata["algorithm_library"]["track_state_updater"]["used"] is True
    assert {"multimodal_feature_fuser", "target_type_classifier", "track_state_updater", "trajectory_predictor"}.issubset(remote.calls)


def test_plan_provider_keeps_local_prediction_when_remote_predictor_fails():
    class _FailingRemote(_RemoteLibrary):
        def invoke(self, algorithm_id: str, inputs: dict[str, Any], params: dict[str, Any] | None = None) -> RemoteAlgorithmResult:
            if algorithm_id == "trajectory_predictor":
                return RemoteAlgorithmResult.failure(algorithm_id, "timeout")
            return super().invoke(algorithm_id, inputs, params)

    provider = PlanAlgorithmProvider(
        _Tracker(),
        _PassThrough(),
        _Resettable(),
        _Resettable(),
        _Resettable(),
        algorithm_library=_FailingRemote(),
    )
    tracks = provider.update_tracks(
        [
            Detection(
                detection_id="det-1",
                timestamp=1_700_000_000.0,
                lat=31.2,
                lon=121.5,
                speed=210.0,
                heading=90.0,
                confidence=0.8,
            )
        ]
    )

    assert tracks[0].predicted_path[0]["lat"] == 31.21
    assert tracks[0].metadata["algorithm_library"]["trajectory_predictor"]["fallback"] is True
