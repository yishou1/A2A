from __future__ import annotations

from app.algorithm_library_runtime import AlgorithmLibrarySettings, TrackThreatAlgorithmRuntime
from app.algorithm_provider import PlanAlgorithmProvider
from app.group_detector import GroupDetector
from app.models import TrackState


def tracks(*, far_apart=False):
    return [TrackState(track_id=str(i), object_type="aircraft", lat=31 + i * (1 if far_apart else .001),
                       lon=121, speed=220, heading=90, last_update_time=60,
                       metadata={"source_class": "fighter"},
                       history_path=[{"lat": 31 + i * .001, "lon": 121 + j * .001,
                                      "timestamp": j * 10} for j in range(6)]) for i in range(2)]


def provider(output):
    item = PlanAlgorithmProvider(tracker=None, ranker=None, impact_analyzer=None,
                                 group_detector=GroupDetector())
    calls = []
    def remote(algorithm_id, *, inputs, params):
        calls.append((algorithm_id, inputs, params))
        return output
    item._run_remote = remote
    return item, calls


def output(*, groups=None, declined=False):
    return {"model": {"model_version": "2.0.0"}, "groups": groups or [],
            "routing": [{"track_id": str(i), "domain": None if declined else "aircraft",
                         "reason": "insufficient_history_points" if declined else None} for i in range(2)],
            "graph_summary": {"model_variant": "m20_fused_onnx_v6"}}


def test_model_membership_is_used_instead_of_recomputed_by_local_distance_rules():
    candidates = [{"member_track_ids": ["0", "1"]}]
    item, calls = provider(output(groups=candidates))
    result = item.detect_groups(tracks(far_apart=True), [], {})
    assert len(result) == 1 and result[0].member_track_ids == ["0", "1"]
    metadata = result[0].metadata["algorithm_library"]["graph_relation_reasoner"]
    assert metadata["used"] and metadata["membership_source"] == "temporal_gru_gnn"
    assert calls[0][2] == {"model_variant": "m20_fused_onnx_v6"}
    assert len(calls[0][1]["tracks"][0]["history_path"]) == 6
    assert calls[0][1]["tracks"][0]["metadata"]["source_class"] == "fighter"


def test_negative_model_result_does_not_get_overridden_by_local_rules():
    item, _ = provider(output())
    assert item.detect_groups(tracks(), [], {}) == []


def test_remote_group_domain_and_model_confidence_are_preserved():
    item, _ = provider(output(groups=[{"member_track_ids": ["0", "1"],
                                      "group_type": "ground_group", "cohesion_score": .91}]))
    result = item.detect_groups(tracks(), [], {})
    assert result[0].group_type == "ground_group" and result[0].cohesion_score == .91


def test_declined_tracks_can_use_explicit_local_fallback():
    item, _ = provider(output(declined=True))
    result = item.detect_groups(tracks(), [], {})
    assert result and not result[0].metadata["algorithm_library"]["graph_relation_reasoner"]["used"]
    assert result[0].metadata["algorithm_library"]["graph_relation_reasoner"]["membership_source"] == "local_physical_fallback"


def test_unavailable_service_retains_existing_local_fallback():
    item, _ = provider(None)
    assert item.detect_groups(tracks(), [], {})


def test_catalog_selects_highest_version_independent_of_response_order():
    runtime = TrackThreatAlgorithmRuntime(AlgorithmLibrarySettings.from_env())
    for versions in (["2.0.0", "1.0.0"], ["1.0.0", "2.0.0"]):
        catalog = [{"algorithm_id": "graph_relation_reasoner", "version": version,
                    "backend_type": "python_http_service"} for version in versions]
        assert runtime._validated_catalog(catalog)["graph_relation_reasoner"]["version"] == "2.0.0"
    assert runtime._validated_catalog([{"algorithm_id": "graph_relation_reasoner", "version": "1.0.0"}]) == {}
