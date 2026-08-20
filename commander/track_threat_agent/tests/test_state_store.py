from pathlib import Path

from app.a2a_runtime import A2ARuntimeState
from app.models import TrackState
from app.state_store import FileStateStore


def make_track() -> TrackState:
    return TrackState(
        track_id="trk-persist-001",
        object_type="aircraft",
        lat=31.2,
        lon=121.4,
        alt=3000,
        speed=120,
        heading=90,
        vx=120,
        vy=0,
        track_quality=0.88,
        last_update_time=1000,
        history_path=[{"timestamp": 1000, "lat": 31.2, "lon": 121.4, "alt": 3000}],
        predicted_path=[
            {
                "dt_s": 10.0,
                "timestamp": 1010.0,
                "lat": 31.2,
                "lon": 121.412,
                "alt": 3000,
                "model_used": "adaptive_constant_velocity",
                "prediction_confidence": 0.8,
                "uncertainty_radius_m": 90.0,
                "horizon_type": "short_term",
            }
        ],
        metadata={"status": "active"},
    )


def test_file_state_store_round_trips_tracks_artifact_and_runtime(tmp_path: Path):
    state_path = tmp_path / "state.json"
    store = FileStateStore(state_path)
    runtime = A2ARuntimeState(agent_name="agent-a", role="track_threat")
    runtime.capture_work_list({"workflow_id": "wf-001", "work_list": [{"activity": "track_threat_analysis"}]})
    runtime.set_task_response("work-001", {"status": "Completed", "work_item": "work-001", "cached": False})
    artifact = {"tracks": [{"track_id": "trk-persist-001"}], "summary": {"track_count": 1}}

    store.save(
        tracks={"trk-persist-001": make_track()},
        groups={},
        last_artifact=artifact,
        runtime_state=runtime.export_persistent_state(),
    )
    restored = store.load()

    assert restored is not None
    assert restored.tracks["trk-persist-001"].track_id == "trk-persist-001"
    assert restored.last_artifact["summary"]["track_count"] == 1
    assert restored.runtime_state["task_response_cache"]["work-001"]["status"] == "Completed"


def test_file_state_store_returns_none_for_missing_file(tmp_path: Path):
    store = FileStateStore(tmp_path / "missing.json")

    assert store.load() is None


def test_file_state_store_clear_removes_snapshot(tmp_path: Path):
    state_path = tmp_path / "state.json"
    store = FileStateStore(state_path)
    store.save(tracks={}, groups={}, last_artifact={}, runtime_state={})

    assert state_path.exists()
    store.clear()

    assert not state_path.exists()
