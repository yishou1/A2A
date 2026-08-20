from __future__ import annotations

import pytest
import hashlib
import json

from amos_platform.agents.commander_bridge import CommanderBridge
from amos_platform.agents.a2a.commander_projection import apply_commander_assessments
from amos_platform.agents.a2a.workflow_view import build_submission_snapshot
from amos_platform.api.app_factory import create_app
from amos_platform.api.dependencies import get_engine
from amos_platform.data.scenario_repository import get_scenario
from amos_platform.simulation.engine import SimEngine


SCENARIO_ID = "amphibious-landing-joint-operation"


def _mission_after_all_initial_contact_gates() -> dict:
    scenario = get_scenario(SCENARIO_ID)
    engine = SimEngine()
    engine.load_scenario(scenario)
    engine.clock.update({"run_id": "run-contract", "scenario_id": SCENARIO_ID})
    for dt in (450, 330, 330, 30, 20, 20, 20):
        engine._tick(dt)
    payload = CommanderBridge().build_workflow_payload(scenario, {}, engine)
    return payload["attachments"][0]["meta"]["amos_mission"]


def test_commander_mission_uses_stable_tracks_and_causal_frames() -> None:
    mission = _mission_after_all_initial_contact_gates()

    assert len(mission["contacts"]) == 3
    assert len(mission["observations"]) > len(mission["contacts"])
    assert 5 <= len(mission["perception_frames"]) <= 10
    assert {item["contact_id"] for item in mission["contacts"]} == {
        detection["metadata"]["amos_track_id"]
        for frame in mission["perception_frames"]
        for detection in frame["detections"]
    }
    assert all(
        detection["timestamp"] <= mission["simulation_time_sec"]
        for frame in mission["perception_frames"]
        for detection in frame["detections"]
    )
    frame_times = [frame["detections"][0]["timestamp"] for frame in mission["perception_frames"]]
    assert frame_times == sorted(frame_times)
    assert frame_times[-1] == mission["simulation_time_sec"]
    assert {item["sim_time"] for item in mission["observations"]} == {
        mission["simulation_time_sec"]
    }
    assert mission["metadata"]["run_id"] == "run-contract"
    # Private causal-boundary ticks may add snapshots; the public guarantee is
    # monotonic sequencing, not one sequence number per caller-supplied step.
    assert mission["metadata"]["snapshot_sequence"] >= 7
    assert all(item["classification"] == "unknown" for item in mission["contacts"])
    assert all(item["affiliation"] == "unknown" for item in mission["contacts"])
    assert all("threat_level" not in item for item in mission["contacts"])
    elevated_measurements = [
        item for item in mission["observations"]
        if item.get("elevation_deg") not in (None, 0, 0.0)
    ]
    assert elevated_measurements
    assert all(
        item["slant_range_nm"] >= item["range_nm"]
        for item in elevated_measurements
    )


def test_friendly_platforms_match_integrated_mission_field_names() -> None:
    platforms = _mission_after_all_initial_contact_gates()["friendly_platforms"]

    assert len(platforms) == 9
    assert all(platform["platform_id"] for platform in platforms)
    assert all(platform["platform_type"] for platform in platforms)
    assert all(0 <= platform["readiness"] <= 1 for platform in platforms)
    assert all(isinstance(platform["munitions"], int) for platform in platforms)
    assert all(platform["location"] for platform in platforms)
    ddg = next(platform for platform in platforms if platform["platform_id"] == "DDG-01")
    uav = next(platform for platform in platforms if platform["platform_id"] == "UAV-ISR-01")
    assert ddg["metadata"]["fuel_pct"] is not None
    assert uav["metadata"]["position"]["alt_ft"] == 12_000


def test_gateway_snapshot_preserves_platform_energy_and_measurement_geometry() -> None:
    scenario = get_scenario(SCENARIO_ID)
    engine = SimEngine()
    engine.load_scenario(scenario)
    engine.clock.update({"run_id": "run-gateway-geometry", "scenario_id": SCENARIO_ID})
    engine._tick(1200)

    snapshot = engine.exchange.build_snapshot(engine.get_agent_visible_state())
    ddg = next(asset for asset in snapshot["assets"] if asset["asset_id"] == "DDG-01")
    elevated_measurements = [
        item for item in snapshot["observations"]
        if item.get("elevation_deg") not in (None, 0, 0.0)
    ]

    assert ddg["fuel_pct"] is not None
    assert "battery_pct" in ddg
    assert elevated_measurements
    assert all(item["slant_range_nm"] >= item["range_nm"] for item in elevated_measurements)


def test_snapshot_and_events_are_continuous_and_truth_safe() -> None:
    app = create_app()
    app.testing = True
    client = app.test_client()
    client.post("/api/v1/sim/reset", json={"scenario_id": SCENARIO_ID})
    engine = get_engine()
    with engine._lock:
        engine._tick(80)

    first = client.get("/api/v1/sim/snapshot").get_json()["data"]
    with engine._lock:
        engine._tick(10)
    second = client.get("/api/v1/sim/snapshot").get_json()["data"]
    events = client.get("/api/v1/sim/events?after_sequence=0").get_json()["data"]

    assert first["schema_version"] == "amos.simulation.snapshot.v1"
    assert second["sequence"] == first["sequence"] + 1
    assert [event["sequence"] for event in events] == list(range(1, second["sequence"] + 1))
    assert all(event["run_id"] == second["run_id"] for event in events)
    assert second["simulation_chains"][0]["chain_id"].endswith(":situation-analysis")
    assert second["provenance"] == {
        "mode": "simulation",
        "generator": "amos_simulation",
        "simulated": True,
    }

    forbidden = {"truth_id", "threat_id", "associated_threat_id", "target_threat_id", "is_hostile", "side"}

    def visit(value):
        if isinstance(value, dict):
            for key, child in value.items():
                assert key not in forbidden
                assert not key.casefold().startswith(("agent", "a2a", "commander"))
                visit(child)
        elif isinstance(value, list):
            for child in value:
                visit(child)

    visit(second)
    visit(events)


def test_exchange_events_are_sampled_and_include_causal_position_summaries() -> None:
    scenario = get_scenario(SCENARIO_ID)
    engine = SimEngine()
    engine.load_scenario(scenario)
    engine.clock.update({
        "run_id": "run-event-sampling",
        "scenario_id": SCENARIO_ID,
        "running": True,
        "lifecycle": "running",
    })

    for _ in range(20):
        engine._tick(0.5)

    events = engine.exchange.events_after(0)

    assert 2 <= len(events) < 20
    assert [event["sequence"] for event in events] == list(range(1, len(events) + 1))
    assert events[-1]["data"]["assets"]
    assert {"asset_id", "position", "heading_deg", "speed_kts", "status"} <= set(
        events[-1]["data"]["assets"][0]
    )
    assert all(event["sim_time_ms"] <= 10_000 for event in events)


def test_exchange_media_references_follow_the_causal_story_cutoff() -> None:
    scenario = get_scenario(SCENARIO_ID)
    engine = SimEngine()
    engine.load_scenario(scenario)
    engine.clock.update({
        "run_id": "run-media-causality",
        "scenario_id": SCENARIO_ID,
        "running": True,
        "lifecycle": "running",
    })

    initial = engine.exchange.build_snapshot(engine.get_agent_visible_state())
    assert [item["media_id"] for item in initial["recent_events"][-1]["media_refs"]] == [
        "AMP-MEDIA-00"
    ]

    engine._tick(205)
    current = engine.exchange.build_snapshot(engine.get_agent_visible_state())
    actual_ids = {
        item["media_id"]
        for event in engine.exchange.events_after(0)
        for item in event["media_refs"]
    }
    expected_ids = {
        item["media_id"]
        for item in engine.get_operator_state()["scenario_story"]["media_cues"]
    }

    assert actual_ids == expected_ids
    assert "AMP-MEDIA-04" not in actual_ids
    assert sum(
        1 for event in engine.exchange.events_after(0)
        for item in event["media_refs"] if item["media_id"] == "AMP-MEDIA-00"
    ) == 1


def test_stage_transfer_scopes_media_and_declares_required_inputs() -> None:
    scenario = get_scenario(SCENARIO_ID)
    engine = SimEngine(seed=int(scenario["default_seed"]))
    engine.load_scenario(scenario)
    engine.clock.update({
        "run_id": "run-stage-perception",
        "scenario_id": SCENARIO_ID,
        "scenario_branch": "standard",
        "director_checkpoint_id": "AMP-CP-PERCEPTION",
    })
    for _ in range(27):
        engine._tick(30)

    payload = CommanderBridge().build_workflow_payload(scenario, {}, engine)
    stage = payload["mission_input"]["stage_transfer"]

    assert stage["schema_version"] == "amos.stage-transfer.v1"
    assert stage["checkpoint_id"] == "AMP-CP-PERCEPTION"
    assert stage["phase"] == "FIX"
    assert stage["incremental_media_ids"] == [
        "AMP-MEDIA-00", "AMP-MEDIA-01", "AMP-MEDIA-02",
    ]
    assert stage["context_media_ids"] == []
    assert [item["id"] for item in payload["attachments"]] == stage["incremental_media_ids"]
    assert {item["evidence_role"] for item in stage["evidence_items"]} == {"incremental"}
    assert stage["evidence_items"][0]["uri"].startswith("http://127.0.0.1:5000/")
    assert all(item["status"] == "available" for item in stage["required_inputs"])
    assert "AMP-MEDIA-03" not in json.dumps(payload, ensure_ascii=False)


def test_find_stage_carries_environment_that_gateway_snapshot_v1_lacks() -> None:
    scenario = get_scenario(SCENARIO_ID)
    engine = SimEngine(seed=int(scenario["default_seed"]))
    engine.load_scenario(scenario)
    engine.clock.update({
        "run_id": "run-stage-find",
        "scenario_id": SCENARIO_ID,
        "scenario_branch": "standard",
    })
    for _ in range(5):
        engine._tick(30)

    stage = CommanderBridge(mode="gateway").build_workflow_payload(
        scenario, {}, engine,
    )["mission_input"]["stage_transfer"]

    assert stage["phase"] == "FIND"
    assert stage["supplemental_inputs"]["environment"]
    environment_row = next(
        item for item in stage["required_inputs"] if item["data_group"] == "environment"
    )
    assert environment_row == {
        "data_group": "environment", "required": True, "count": 1, "status": "available",
    }


def test_stage_context_is_gateway_safe_and_event_media_refs_are_strict_deltas() -> None:
    scenario = get_scenario(SCENARIO_ID)
    engine = SimEngine(seed=int(scenario["default_seed"]))
    engine.load_scenario(scenario)
    engine.clock.update({
        "run_id": "run-stage-gateway",
        "scenario_id": SCENARIO_ID,
        "scenario_branch": "standard",
        "director_checkpoint_id": "AMP-CP-ASSESS",
    })
    for _ in range(49):
        engine._tick(30)

    payload = CommanderBridge(mode="gateway").build_workflow_payload(scenario, {}, engine)
    stage = payload["mission_input"]["stage_transfer"]
    engine.exchange.set_submission_context("run-stage-gateway", stage)
    snapshot = engine.exchange.build_snapshot(engine.get_agent_visible_state())
    events = engine.exchange.events_after(0)

    assert stage["incremental_media_ids"] == ["AMP-MEDIA-03", "AMP-MEDIA-04"]
    assert [item["id"] for item in payload["attachments"]] == stage["incremental_media_ids"]
    assert snapshot["simulation_chains"][0]["submission_context"] == stage
    allowed_media_keys = {
        "media_id", "uri", "mime_type", "checksum", "source_name", "provenance",
    }
    refs = [item for event in events for item in event["media_refs"]]
    assert refs
    assert all(set(item) == allowed_media_keys for item in refs)
    assert all(item["uri"].startswith("http://127.0.0.1:5000/") for item in refs)
    assert len([item for item in refs if item["media_id"] == "AMP-MEDIA-00"]) == 1
    serialized = json.dumps(snapshot, ensure_ascii=False).casefold()
    assert "target_refs" not in serialized
    assert "truth_id" not in serialized
    electronic = next(
        item for item in stage["evidence_items"]
        if item["media_id"] == "AMP-MEDIA-03"
    )
    assert electronic["structured_data"]["schema_version"] == "amos.evidence-data.v1"
    assert electronic["structured_data"]["observation_window"] == {
        "start_sec": 1020.0, "end_sec": 1110.0, "sample_count": 4,
    }
    assert len(electronic["structured_data"]["observations"]) == 4
    assert electronic["consumer_context"] == {
        "planned": True,
        "functional_role_ids": ["A1", "A2"],
        "model_requirement_ids": ["M05", "M07", "M20"],
        "execution_evidence": "backend_trace_required",
    }


def test_assessment_stage_sends_current_frames_and_explicit_baseline_only() -> None:
    scenario = get_scenario(SCENARIO_ID)
    engine = SimEngine(seed=int(scenario["default_seed"]))
    engine.load_scenario(scenario)
    engine.clock.update({
        "run_id": "run-stage-assessment",
        "scenario_id": SCENARIO_ID,
        "scenario_branch": "standard",
        "director_checkpoint_id": "AMP-CP-BDA",
    })
    for _ in range(101):
        engine._tick(30)

    payload = CommanderBridge(mode="gateway").build_workflow_payload(scenario, {}, engine)
    stage = payload["mission_input"]["stage_transfer"]

    assert stage["phase"] == "ASSESS"
    assert stage["incremental_media_ids"] == ["AMP-MEDIA-07", "AMP-MEDIA-08"]
    assert stage["context_media_ids"] == ["AMP-MEDIA-01"]
    assert [item["id"] for item in payload["attachments"]] == [
        "AMP-MEDIA-01", "AMP-MEDIA-07", "AMP-MEDIA-08",
    ]
    assert {item["evidence_role"] for item in stage["evidence_items"]} == {
        "incremental", "context",
    }
    provenance = next(
        item for item in stage["required_inputs"]
        if item["data_group"] == "execution_provenance"
    )
    assert provenance["status"] == "missing"
    assert "workflow_result_ref" not in stage["supplemental_inputs"]
    current_eo = next(
        item for item in stage["evidence_items"]
        if item["media_id"] == "AMP-MEDIA-07"
    )
    geometry = current_eo["structured_data"]["capture_geometry"]
    assert geometry["platform_pose"]["alt_ft"] == 9000.0
    assert geometry["sensor_pose"]["depression_angle_deg"] is not None
    assert geometry["sensor_config"]["fov_deg"] == 35.0
    assert geometry["capture_parameters"]["reference_media_id"] == "AMP-MEDIA-01"


def test_reset_starts_a_new_run_and_event_cursor() -> None:
    app = create_app()
    app.testing = True
    client = app.test_client()

    first_reset = client.post("/api/v1/sim/reset", json={"scenario_id": SCENARIO_ID}).get_json()["data"]
    first_snapshot = client.get("/api/v1/sim/snapshot").get_json()["data"]
    second_reset = client.post("/api/v1/sim/reset", json={"scenario_id": SCENARIO_ID}).get_json()["data"]
    second_snapshot = client.get("/api/v1/sim/snapshot").get_json()["data"]

    assert first_reset["run_id"] != second_reset["run_id"]
    assert first_snapshot["run_id"] == first_reset["run_id"]
    assert second_snapshot["run_id"] == second_reset["run_id"]
    assert second_snapshot["sequence"] == 1


def test_events_reject_negative_cursor() -> None:
    app = create_app()
    app.testing = True
    response = app.test_client().get("/api/v1/sim/events?after_sequence=-1")

    assert response.status_code == 400


def test_gateway_is_default_and_submission_uses_run_chain_reference() -> None:
    scenario = get_scenario(SCENARIO_ID)
    engine = SimEngine()
    engine.load_scenario(scenario)
    engine.clock.update({"run_id": "run-gateway", "scenario_id": SCENARIO_ID})
    bridge = CommanderBridge(mode="gateway")
    mission_payload = bridge.build_workflow_payload(scenario, {}, engine)

    payload = bridge.build_backend_submission(
        mission_payload,
        engine=engine,
        overrides={"max_steps": 8},
    )

    assert bridge.mode == "gateway"
    assert payload["schema_version"] == "amos.commander.gateway.submit.v1"
    assert payload["run_id"] == "run-gateway"
    assert payload["chain_id"].endswith(":situation-analysis")
    assert payload["max_steps"] == 8
    assert "attachments" not in payload


def test_llm_gateway_submission_uses_practical_default_timeout(monkeypatch) -> None:
    monkeypatch.delenv("A2A_REQUEST_TIMEOUT", raising=False)
    monkeypatch.setenv("ENABLE_LLM", "true")
    scenario = get_scenario(SCENARIO_ID)
    engine = SimEngine()
    engine.load_scenario(scenario)
    engine.clock.update({"run_id": "run-timeout", "scenario_id": SCENARIO_ID})
    bridge = CommanderBridge(mode="gateway")
    mission = bridge.build_workflow_payload(scenario, {}, engine)

    payload = bridge.build_backend_submission(mission, engine=engine)

    assert payload["request_timeout"] == 180.0


def test_invalid_backend_mode_is_rejected() -> None:
    with pytest.raises(ValueError, match="A2A_BACKEND_MODE"):
        CommanderBridge(mode="automatic")


def test_workflow_file_override_is_rejected() -> None:
    scenario = get_scenario(SCENARIO_ID)
    engine = SimEngine()
    engine.load_scenario(scenario)
    engine.clock.update({"run_id": "run-workflow-file", "scenario_id": SCENARIO_ID})
    bridge = CommanderBridge(mode="gateway")
    mission = bridge.build_workflow_payload(scenario, {}, engine)

    with pytest.raises(ValueError, match="workflow_file"):
        bridge.build_backend_submission(
            mission,
            engine=engine,
            overrides={"workflow_file": "../../untrusted.bpel"},
        )


@pytest.mark.parametrize(
    ("elapsed", "expected"),
    [
        (1470, "observe_workflow.bpel"),
        (2910, "orient_workflow.bpel"),
        (3630, "decide_workflow.bpel"),
        (5610, "act_workflow.bpel"),
    ],
)
def test_backend_selects_workflow_from_current_f2t2ea_phase(elapsed, expected) -> None:
    scenario_id = "maritime-convoy-air-defense"
    scenario = get_scenario(scenario_id)
    engine = SimEngine()
    engine.load_scenario(scenario)
    engine.clock.update({
        "run_id": f"run-phase-{elapsed}",
        "scenario_id": scenario_id,
        "elapsed_sec": elapsed,
    })
    bridge = CommanderBridge(mode="gateway")

    mission = bridge.build_workflow_payload(scenario, {}, engine)
    backend = bridge.build_backend_submission(mission, engine=engine)

    assert mission["workflow_file"].endswith(expected)
    assert backend["workflow_file"].endswith(expected)


def test_direct_commander_mode_remains_explicit_diagnostic_path() -> None:
    scenario = get_scenario(SCENARIO_ID)
    engine = SimEngine()
    engine.load_scenario(scenario)
    engine.clock.update({"run_id": "run-direct", "scenario_id": SCENARIO_ID})
    bridge = CommanderBridge(mode="commander")
    mission_payload = bridge.build_workflow_payload(scenario, {}, engine)

    payload = bridge.build_backend_submission(mission_payload, engine=engine)

    assert bridge.mode == "commander"
    assert payload["attachments"]
    assert "schema_version" not in payload


def test_gateway_health_preserves_degraded_dependency_state() -> None:
    bridge = CommanderBridge(mode="gateway")

    class DegradedGateway:
        def health(self):
            return {"error": True, "code": 503, "status": "degraded"}

    bridge._client = DegradedGateway()

    health = bridge.health()

    assert health["status"] == "degraded"
    assert health["transport"] == "gateway"


def test_gateway_package_is_independently_checksum_verified() -> None:
    package_body = {
        "schema_version": "amos.commander.package.v1",
        "run_id": "run-package",
        "snapshot": {"sequence": 3},
        "events": [],
    }
    checksum = hashlib.sha256(json.dumps(
        package_body,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")).hexdigest()
    bridge = CommanderBridge(mode="gateway")

    class PackageGateway:
        def get_package(self, package_id):
            assert package_id == "pkg-1"
            return package_body

    bridge._client = PackageGateway()

    package, verified = bridge.get_submission_package("pkg-1", checksum)

    assert package == package_body
    assert verified is True
    assert bridge.get_submission_package("pkg-1", "0" * 64)[1] is False


def test_unverified_gateway_package_cannot_write_results_into_simulation() -> None:
    scenario = get_scenario(SCENARIO_ID)
    engine = SimEngine()
    engine.load_scenario(scenario)
    engine.clock["run_id"] = "run-unverified"

    projection = apply_commander_assessments(
        engine,
        {
            "workflow_id": "wf-unverified",
            "status": "completed",
            "result": {"tracks": [{"track_id": "TRK-1", "threats": []}]},
        },
        submission={
            "run_id": "run-unverified",
            "transport": "gateway",
            "package": {"package_id": "pkg-1", "verified": False},
        },
    )

    assert projection["status"] == "integrity_error"
    assert projection["applied_count"] == 0
    assert "agent_analysis" not in engine.scenario_story


def test_gateway_submission_summary_describes_exchange_package_not_direct_attachments() -> None:
    scenario = get_scenario(SCENARIO_ID)
    engine = SimEngine()
    engine.load_scenario(scenario)
    engine.clock.update({"run_id": "run-summary", "scenario_id": SCENARIO_ID})
    engine._tick(205)
    bridge = CommanderBridge(mode="gateway")
    mission = bridge.build_workflow_payload(scenario, {}, engine)
    backend_request = bridge.build_backend_submission(mission, engine=engine)
    exchange = engine.exchange.build_snapshot(engine.get_agent_visible_state())

    summary = build_submission_snapshot(
        mission,
        scenario_id=SCENARIO_ID,
        accepted=True,
        transport="gateway",
        backend_request=backend_request,
        exchange_snapshot=exchange,
        exchange_events=engine.exchange.events_after(0),
    )

    assert summary["counts"]["contacts"] == len(exchange["tracks"])
    assert summary["counts"]["observations"] == len(exchange["observations"])
    assert summary["counts"]["events"] == len(exchange["recent_events"])
    stage_ids = {
        *summary["stage_transfer"]["incremental_media_ids"],
        *summary["stage_transfer"]["context_media_ids"],
    }
    available_ids = {
        item["media_id"]
        for event in exchange["recent_events"]
        for item in event["media_refs"]
    }
    assert summary["counts"]["attachments"] == len(stage_ids & available_ids)
    assert {item["id"] for item in summary["attachments"]} == stage_ids & available_ids
