from scripts.smoke_track_threat_agent import build_smoke_task_payload, validate_artifact


def test_smoke_task_payload_contains_a2a_workflow_and_protected_assets():
    payload = build_smoke_task_payload(frame_index=0, workflow_id="wf-smoke", work_item="wi-smoke")

    assert payload["workflow_id"] == "wf-smoke"
    assert payload["work_item"] == "wi-smoke"
    assert payload["command"] == "analyze_perception_result"
    assert payload["role"] == "track_threat"
    assert payload["payload"]["message_type"] == "perception_result"
    assert payload["payload"]["scene"]["protected_assets"]


def test_validate_artifact_requires_tracks_asset_impacts_and_ranking():
    artifact = {
        "summary": {"track_count": 1, "asset_impact_count": 1},
        "tracks": [{"track_id": "track-1"}],
        "asset_impacts": [{"impact_id": "impact-1"}],
        "unified_threat_ranking": [{"rank": 1, "reason": "demo"}],
    }

    errors = validate_artifact(artifact)

    assert errors == []
