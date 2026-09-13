from __future__ import annotations

import pytest

from app import main
from app.intelligence_adapter import convert_intelligence_to_detections, has_upstream_tracks


CMS_PACKET = {
    "packet_id": "cms-packet-001",
    "mission_id": "cms-mission-001",
    "created_at": "2026-09-12T08:00:00Z",
    "algorithm_level": "medium",
    "scene": {
        "protected_zone_lat": 30.0,
        "protected_zone_lon": 120.0,
        "protected_radius_m": 20_000.0,
    },
    "tracks": [
        {
            "track_id": "CMS-TRACK-001",
            "track_instance_id": "CMS-TRACK-001-v1",
            "object_type": "aircraft",
            "timestamp": 1_789_200_000.0,
            "lat": 30.10,
            "lon": 120.20,
            "alt": 5_000.0,
            "speed": 230.0,
            "heading": 270.0,
            "confidence": 0.95,
            "history_path": [
                {"timestamp": 1_789_199_990.0, "lat": 30.10, "lon": 120.21, "alt": 5_000.0}
            ],
        }
    ],
}


def test_cms_fused_track_conversion_preserves_stable_source_identity():
    assert has_upstream_tracks({"intelligence_packet": CMS_PACKET})

    observations = convert_intelligence_to_detections(CMS_PACKET)

    assert len(observations) == 1
    assert observations[0].metadata["input_kind"] == "fused_track"
    assert observations[0].metadata["source_identity"] == (
        "TacticalIntelligenceAgent:cms-mission-001:CMS-TRACK-001-v1"
    )


@pytest.mark.anyio
async def test_a2a_cms_fused_tracks_skip_a_second_local_association_pass():
    main.reset_runtime_state()
    body = await main.send_message(
        {
            "workflow_id": "wf-cms-upstream",
            "task_id": "cms-upstream-task",
            "required_skill": "track_threat_situation_analysis",
            "input": {"intelligence_packet": CMS_PACKET},
        },
        token="unit-test",
    )

    artifact = body["artifact"]
    assert body["status"] == "completed"
    assert artifact["trace"]["input_kind"] == "upstream_tracks"
    assert artifact["trace"]["track_input_count"] == 1
    assert artifact["summary"]["track_count"] == 1
    assert artifact["tracks"][0]["metadata"]["upstream_track_id"] == "CMS-TRACK-001"
    assert artifact["tracks"][0]["metadata"]["plan_algorithms"]["trajectory_tracking"] == {
        "algorithm": "upstream_fused_track_sync",
        "runtime_provider": "track_threat_agent",
        "execution_location": "agent_process",
        "local_association_performed": False,
        "local_filter_performed": False,
    }
    assert isinstance(artifact["algorithm_calls"], list)
    assert isinstance(artifact["algorithm_invocations"], list)
