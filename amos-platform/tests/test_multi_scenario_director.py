from __future__ import annotations

import hashlib
from pathlib import Path

from amos_platform.api.app_factory import create_app
from amos_platform.data.scenario_capabilities import (
    FUNCTIONAL_AGENT_CATALOG,
    FUNCTION_POINT_CATALOG,
    MODEL_CATALOG,
)
from amos_platform.data.scenario_repository import get_scenario, list_scenarios
from amos_platform.runtime.platform_runtime import PlatformRuntime
from amos_platform.simulation.director import DirectorService
from amos_platform.simulation.engine import SimEngine


SCENARIO_IDS = [
    "amphibious-landing-joint-operation",
    "border-uav-evacuation",
    "maritime-convoy-air-defense",
]
ROOT = Path(__file__).resolve().parents[1]


def test_midterm_catalog_exposes_maritime_scenario_and_builders_keep_v2_contract() -> None:
    summaries = list_scenarios()
    assert [item["id"] for item in summaries] == ["maritime-convoy-air-defense"]
    assert summaries[0]["name"] == "海上编队护航与要地防空"

    declared_algorithms: set[str] = set()
    declared_functions: set[str] = set()
    for scenario_id in SCENARIO_IDS:
        scenario = get_scenario(scenario_id)
        assert scenario is not None
        assert scenario["schema_version"] == "amos.scenario.v2"
        assert [row["agent_id"] for row in scenario["functional_agents"]] == [
            row["agent_id"] for row in FUNCTIONAL_AGENT_CATALOG
        ]
        assert all(row["agent_id"] != "Commander" for row in scenario["functional_agents"])
        assert scenario["required_agents"]
        assert scenario["demo_checkpoints"]
        assert scenario["expected_branches"]
        declared_algorithms.update(item["algorithm_id"] for item in scenario["algorithm_coverage"])
        declared_functions.update(item["function_id"] for item in scenario["function_point_coverage"])

    assert summaries[0]["default_seed"] == get_scenario("maritime-convoy-air-defense")["default_seed"]
    assert "demo_checkpoints" not in summaries[0]

    assert declared_algorithms == {item["algorithm_id"] for item in MODEL_CATALOG}
    assert declared_functions == {item["function_id"] for item in FUNCTION_POINT_CATALOG}


def test_maritime_scenario_declares_agent_device_compute_mapping() -> None:
    scenario = get_scenario("maritime-convoy-air-defense")
    assert scenario is not None

    devices = {row["device_id"]: row for row in scenario["physical_devices"]}
    nodes = {row["node_id"]: row for row in scenario["compute_nodes"]}
    deployments = scenario["agent_deployments"]

    assert {"ESCORT-01", "UAV-CONFIRM-01", "ASCM-01"}.issubset(devices)
    assert devices["ASCM-01"]["device_type"] == "anti_ship_missile"
    assert nodes["ESCORT-01-COMPUTE"]["host_device_id"] == "ESCORT-01"
    assert nodes["UAV-CONFIRM-01-COMPUTE"]["host_device_id"] == "UAV-CONFIRM-01"
    assert nodes["ASCM-01-COMPUTE"]["host_device_id"] == "ASCM-01"

    escort_agents = {
        row["agent_id"]
        for row in deployments
        if row["compute_node_id"] == "ESCORT-01-COMPUTE"
    }
    assert {"A2", "A3", "A4", "A5", "A6"}.issubset(escort_agents)
    assert any(
        row["agent_id"] == "A6" and row["compute_node_id"] == "ASCM-01-COMPUTE"
        for row in deployments
    )


def test_document_requirement_ids_and_coverage_tiers_are_not_conflated() -> None:
    scenarios = [get_scenario(scenario_id) for scenario_id in SCENARIO_IDS]
    models = {
        row["requirement_id"]: row
        for scenario in scenarios if scenario is not None
        for row in scenario["algorithm_coverage"]
    }
    assert set(models) == {f"M{index:02d}" for index in range(1, 21)}
    assert {key for key, row in models.items() if row["tier"] == "core"} == {
        f"M{index:02d}" for index in range(1, 17)
    }
    assert {key for key, row in models.items() if row["tier"] == "engineering"} == {
        "M17", "M18", "M19", "M20"
    }
    assert all(row["assigned_agents"] for row in models.values())
    assert all(row["function_points"] for row in models.values())
    assert all(row["model_id"] is None and row["version"] is None for row in models.values())


def test_each_formal_scenario_has_the_documented_model_coverage() -> None:
    expected_core = {
        "amphibious-landing-joint-operation": ({"M08", "M12"}, 14),
        "border-uav-evacuation": (set(), 16),
        "maritime-convoy-air-defense": ({"M08"}, 15),
    }
    for scenario_id, (missing, expected_count) in expected_core.items():
        scenario = get_scenario(scenario_id)
        assert scenario is not None
        requirement_ids = {
            row["requirement_id"] for row in scenario["algorithm_coverage"]
        }
        declared_core = {f"M{index:02d}" for index in range(1, 17)} & requirement_ids
        assert declared_core == {f"M{index:02d}" for index in range(1, 17)} - missing
        assert len(declared_core) == expected_count
        assert {"M17", "M18", "M19", "M20"}.issubset(requirement_ids)
        assert len(scenario["function_point_coverage"]) == 29


def test_planned_agents_do_not_publish_static_runtime_status() -> None:
    scenario = get_scenario("amphibious-landing-joint-operation")
    assert scenario is not None
    a3 = next(row for row in scenario["functional_agents"] if row["agent_id"] == "A3")
    role = next(
        row for row in scenario["required_agents"]
        if "A3" in row.get("functional_agent_ids", [])
    )
    assert "backend_status" not in a3
    assert "backend_status" not in role
    assert next(row for row in scenario["required_agents"] if row["role"] == "Commander")[
        "orchestration_only"
    ] is True


def test_all_scenario_media_exist_and_match_declared_checksums() -> None:
    for scenario_id in SCENARIO_IDS:
        scenario = get_scenario(scenario_id)
        assert scenario is not None
        for item in scenario["media_cues"]:
            path = ROOT / str(item["uri"]).removeprefix("/")
            assert path.is_file(), path
            assert hashlib.sha256(path.read_bytes()).hexdigest() == item["checksum"]


def test_scenario_detail_does_not_publish_future_director_truth() -> None:
    app = create_app()
    app.testing = True
    client = app.test_client()

    response = client.get("/api/v1/scenarios/border-uav-evacuation")
    data = response.get_json()["data"]

    assert response.status_code == 200
    assert data["timeline"] == []
    assert data["media_cues"] == []
    assert "asset_routes" not in data
    assert "asset_route_modes" not in data
    assert "asset_motion_windows" not in data
    assert "threat_observation_windows" not in data
    assert "threats" not in data
    assert "demo_checkpoints" not in data
    assert "fault_injections" not in data
    assert "comm_degraded" not in data


def test_new_scenario_media_routes_enforce_time_and_active_run_boundaries() -> None:
    app = create_app()
    app.testing = True
    client = app.test_client()

    reset = client.post("/api/v1/sim/reset", json={
        "scenario_id": "border-uav-evacuation",
        "seed": 5,
    })
    assert reset.status_code == 200
    assert client.get(
        "/static/assets/scenarios/border-uav-evacuation/00-border-search-overview.png"
    ).status_code == 200
    assert client.get(
        "/static/assets/scenarios/border-uav-evacuation/07-evacuation-progress.png"
    ).status_code == 404
    assert client.get(
        "/static/assets/scenarios/maritime-convoy-air-defense/00-convoy-overview.png"
    ).status_code == 404


def _deterministic_slice(engine: SimEngine) -> dict:
    internal = engine.get_state()
    return {
        "assets": [
            {
                "id": item["id"],
                "position": item["position"],
                "heading": item["heading"],
                "battery_pct": item["battery_pct"],
                "comms_strength": item["comms_strength"],
            }
            for item in internal["assets"]
        ],
        "threats": [
            {key: item.get(key) for key in ("id", "position", "heading", "speed_kts", "behavior_phase")}
            for item in internal["threats"]
        ],
        "tracks": [
            {key: item.get(key) for key in ("id", "lat", "lng", "confidence", "history_path")}
            for item in internal["fused_tracks"]
        ],
        "observation_ids": [
            item["observation_id"]
            for item in internal["observation_batch"].get("observations") or []
        ],
    }


def test_equal_seed_and_ticks_produce_equal_simulation_state() -> None:
    scenario = get_scenario("border-uav-evacuation")
    assert scenario is not None
    first = SimEngine()
    second = SimEngine()
    first.load_scenario(scenario, seed=9817)
    second.load_scenario(scenario, seed=9817)

    for _ in range(30):
        first._tick(1.0)
        second._tick(1.0)

    assert first.clock["seed"] == second.clock["seed"] == 9817
    assert _deterministic_slice(first) == _deterministic_slice(second)


def test_loading_a_new_scenario_clears_run_scoped_clock_metadata() -> None:
    engine = SimEngine()
    engine.clock.update({
        "run_id": "run-old",
        "scenario_id": "maritime-convoy-air-defense",
        "director_mode": "demonstration",
        "scenario_branch": "low_score_replan",
        "director_checkpoint_id": "MAR-CP-PLAN",
        "roe_context": {"authorization": "old"},
        "started_at": 123.0,
    })

    scenario = get_scenario("amphibious-landing-joint-operation")
    assert scenario is not None
    engine.load_scenario(scenario, seed=12026)

    for key in (
        "run_id", "scenario_id", "director_mode", "scenario_branch",
        "director_checkpoint_id", "roe_context",
    ):
        assert key not in engine.clock
    assert engine.clock["started_at"] is None


def test_sim_start_and_reset_report_the_effective_seed() -> None:
    app = create_app()
    app.testing = True
    client = app.test_client()

    started = client.post("/api/v1/sim/start", json={
        "scenario_id": "border-uav-evacuation",
        "seed": 77,
    })
    client.post("/api/v1/sim/stop", json={})
    reset = client.post("/api/v1/sim/reset", json={
        "scenario_id": "maritime-convoy-air-defense",
        "seed": 88,
    })

    assert started.status_code == 200
    assert started.get_json()["data"]["seed"] == 77
    assert reset.status_code == 200
    assert reset.get_json()["data"]["seed"] == 88
    assert reset.get_json()["data"]["state"]["clock"]["seed"] == 88
    assert client.post("/api/v1/sim/start", json={"seed": -1}).status_code == 400
    assert client.post("/api/v1/sim/start", json={"seed": 1.5}).status_code == 400


def test_director_reports_unavailable_submission_without_a_backend_callback() -> None:
    runtime = PlatformRuntime()
    director = DirectorService(runtime)
    configured = director.configure(
        scenario_id="amphibious-landing-joint-operation",
        mode="demonstration",
        branch="standard",
        seed=23,
    )

    assert configured["director_status"] == "configured"
    assert configured["current_checkpoint"] is None
    assert "next_checkpoint" not in configured

    stepped = director.action("step_tick", step_sec=2)
    assert stepped["elapsed_sec"] == 2
    assert stepped["simulation_lifecycle"] == "paused"

    reached = director.action("advance_checkpoint")
    assert reached["current_checkpoint"]["checkpoint_id"] == "AMP-CP-PERCEPTION"
    assert reached["current_checkpoint"]["reached_at_sec"] >= 135
    assert reached["current_checkpoint"]["analysis_status"] == "submission_unavailable"
    assert reached["awaiting_analysis"] is False
    assert reached["simulation_lifecycle"] == "paused"


def test_each_scenario_can_reach_all_unconditional_declared_checkpoints() -> None:
    for scenario_id in SCENARIO_IDS:
        runtime = PlatformRuntime()
        director = DirectorService(runtime)
        scenario = get_scenario(scenario_id)
        assert scenario is not None
        director.configure(
            scenario_id=scenario_id,
            mode="demonstration",
            branch="standard",
            seed=scenario["default_seed"],
        )
        expected = [
            item for item in scenario["demo_checkpoints"]
            if not item.get("requires_operator_action")
            and (
                "*" in set(item.get("branch_ids") or ["*"])
                or "standard" in set(item.get("branch_ids") or [])
            )
        ]
        reached = []
        for _ in expected:
            checkpoint_id = director.action("advance_checkpoint")["current_checkpoint"]["checkpoint_id"]
            reached.append(checkpoint_id)
            if scenario_id == "maritime-convoy-air-defense" and checkpoint_id == "MAR-CP-PLAN":
                engine = runtime.get_engine()
                hostile = next(
                    track for track in engine.sensor_fusion.tracks.values()
                    if engine._truth_target_for_track(track) == "CONTACT-HOSTILE-01"
                )
                hostile.classification = "FAST_ATTACK_CRAFT"
                hostile.threat_level = "HIGH"
                hostile.kill_chain_phase = "TARGET"
                hostile.agent_assessment = {
                    "status": "confirmed",
                    "label": "高风险",
                    "source": "test checkpoint workflow",
                }
                engine._tick(1.0)
                prompt = engine.get_operator_state()["follow_launch_prompt"]
                authorized = engine.authorize_follow_asset(
                    str(prompt["asset_id"]),
                    str(prompt["track_id"]),
                    authorized=True,
                )
                assert authorized["status"] == "authorized"
                warning = engine.issue_warning_at_track(hostile.id, authorized=True)
                assert warning["status"] == "issued"
                engine._tick(float(warning["delay_sec"]))
                launched = engine.fire_weapon_at_track(
                    hostile.id,
                    asset_id="ESCORT-01",
                    weapon_name="舰载反舰导弹",
                    authorized=True,
                )
                assert launched["status"] == "launched"
        assert reached == [item["checkpoint_id"] for item in expected]


def test_border_checkpoint_and_timeline_are_filtered_by_branch() -> None:
    scenario = get_scenario("border-uav-evacuation")
    assert scenario is not None

    runtime = PlatformRuntime()
    director = DirectorService(runtime)
    director.configure(
        scenario_id=scenario["id"],
        mode="demonstration",
        branch="standard",
        seed=scenario["default_seed"],
    )
    standard_ids = [
        director.action("advance_checkpoint")["current_checkpoint"]["checkpoint_id"]
        for _ in range(4)
    ]
    assert standard_ids == ["BOR-CP-DETECT", "BOR-CP-TRACK", "BOR-CP-PLAN", "BOR-CP-CLOSE"]
    standard_story = runtime.get_engine().get_operator_state()["scenario_story"]
    assert "BOR-CUE-05" not in {item["cue_id"] for item in standard_story["timeline"]}
    assert "BOR-MEDIA-04" not in {item["media_id"] for item in standard_story["media_cues"]}

    degraded_runtime = PlatformRuntime()
    degraded = DirectorService(degraded_runtime)
    degraded.configure(
        scenario_id=scenario["id"],
        mode="demonstration",
        branch="communication_degraded",
        seed=scenario["default_seed"],
    )
    degraded_ids = [
        degraded.action("advance_checkpoint")["current_checkpoint"]["checkpoint_id"]
        for _ in range(5)
    ]
    assert degraded_ids == [
        "BOR-CP-DETECT", "BOR-CP-TRACK", "BOR-CP-LINK", "BOR-CP-PLAN", "BOR-CP-CLOSE",
    ]
    degraded_story = degraded_runtime.get_engine().get_operator_state()["scenario_story"]
    assert "BOR-CUE-05" in {item["cue_id"] for item in degraded_story["timeline"]}
    assert "BOR-MEDIA-04" in {item["media_id"] for item in degraded_story["media_cues"]}


def test_fault_branch_injects_simulation_condition_without_claiming_backend_recovery() -> None:
    runtime = PlatformRuntime()
    director = DirectorService(runtime)
    director.configure(
        scenario_id="border-uav-evacuation",
        mode="demonstration",
        branch="communication_degraded",
        seed=55,
    )
    director.action("advance_checkpoint")
    director.action("advance_checkpoint")
    state = director.action("advance_checkpoint")

    assert state["requested_faults"] == [{
        "fault_id": "BOR-FAULT-LINK",
        "type": "communication_degradation",
        "target": "RELAY-UAV-01",
        "requested_at_sec": 1110.0,
        "simulation_status": "injected",
        "backend_status": "unverified",
    }]
    assert state["verified_faults"] == []
    assert runtime.get_engine().assets["RELAY-UAV-01"]["health"]["comms_strength"] <= 24.0


def test_director_supports_all_manual_and_automatic_control_actions() -> None:
    runtime = PlatformRuntime()
    director = DirectorService(runtime)
    director.configure(
        scenario_id="amphibious-landing-joint-operation",
        mode="demonstration",
        branch="standard",
        seed=99,
    )

    assert director.action("start")["director_status"] == "running"
    assert director.action("pause")["director_status"] == "paused"
    assert director.action("step_tick", step_sec=1)["elapsed_sec"] >= 1
    assert director.action("start_auto")["director_status"] == "auto_running"
    stopped = director.action("stop_auto")
    assert stopped["director_status"] == "paused"
    assert stopped["auto_running"] is False


def test_checkpoint_callback_is_the_only_source_of_submitted_status() -> None:
    calls: list[dict] = []

    def submit(context: dict) -> dict:
        calls.append(context)
        return {"workflow_id": "wf-real-boundary"}

    runtime = PlatformRuntime()
    director = DirectorService(runtime, checkpoint_callback=submit)
    director.configure(
        scenario_id="amphibious-landing-joint-operation",
        mode="demonstration",
        branch="standard",
        seed=101,
    )
    state = director.action("advance_checkpoint")

    assert calls[0]["run_id"] == state["run_id"]
    assert calls[0]["checkpoint_id"] == "AMP-CP-PERCEPTION"
    assert state["current_checkpoint"]["analysis_status"] == "submitted"
    assert state["current_checkpoint"]["submission"]["workflow_id"] == "wf-real-boundary"
    assert state["awaiting_analysis"] is True
    assert state["director_status"] == "awaiting_analysis"


def test_completed_backend_analysis_is_required_before_checkpoint_can_finish() -> None:
    views = [{
        "status": "completed",
        "terminal": True,
        "run": {"current": True},
        "orchestration": {"counts": {"total": 3, "completed": 3, "failed": 0}},
        "result": {"projection_status": "completed"},
    }]

    runtime = PlatformRuntime()
    director = DirectorService(
        runtime,
        checkpoint_callback=lambda context: {"workflow_id": "wf-observe"},
        workflow_state_callback=lambda workflow_id: views[0],
    )
    director.configure(
        scenario_id="amphibious-landing-joint-operation",
        mode="demonstration",
        branch="standard",
        seed=102,
    )
    submitted = director.action("advance_checkpoint")

    assert submitted["current_checkpoint"]["analysis_status"] == "submitted"
    completed = director.action("refresh_analysis")
    assert completed["analysis_poll_result"] == "completed"
    assert completed["current_checkpoint"]["analysis_status"] == "completed"
    assert completed["director_status"] == "paused"
    assert completed["awaiting_analysis"] is False


def test_analysis_can_advance_within_phase_without_crossing_next_phase() -> None:
    runtime = PlatformRuntime()
    director = DirectorService(
        runtime,
        checkpoint_callback=lambda context: {"workflow_id": "wf-observe"},
        workflow_state_callback=lambda workflow_id: {
            "status": "running",
            "terminal": False,
        },
    )
    director.configure(
        scenario_id="maritime-convoy-air-defense",
        mode="demonstration",
        branch="standard",
        seed=33031,
    )
    submitted = director.action("advance_checkpoint")
    engine = runtime.get_engine()

    assert submitted["current_checkpoint"]["analysis_status"] == "submitted"
    assert director._analysis_progress_enabled({"submit_analysis": True}) is True
    director._arm_analysis_motion_limit()
    assert 2159.9 < float(engine._director_motion_limit_sec or 0) < 2160.0

    with engine._lock:
        engine._tick(1200.0)
    state = engine.get_operator_state()

    assert state["clock"]["elapsed_sec"] < 2160.0
    assert state["mission_phases"]["f2t2ea"]["current"] == "FIX"

    director._clear_analysis_motion_limit()
    assert engine._director_motion_limit_sec is None


def test_final_analysis_completion_does_not_restart_completed_clock() -> None:
    runtime = PlatformRuntime()
    director = DirectorService(
        runtime,
        workflow_state_callback=lambda workflow_id: {
            "status": "completed",
            "terminal": True,
            "run": {"current": True},
            "orchestration": {"counts": {"total": 3, "completed": 3, "failed": 0}},
            "result": {"projection_status": "completed"},
        },
    )
    director.configure(
        scenario_id="maritime-convoy-air-defense",
        mode="demonstration",
        branch="standard",
        seed=33031,
    )
    director._checkpoint_index = 3
    director._state["current_checkpoint"] = {
        "checkpoint_id": "MAR-CP-CLOSE",
        "analysis_status": "running",
        "submission": {"workflow_id": "wf-act"},
    }
    engine = runtime.get_engine()
    engine.clock["elapsed_sec"] = 6000.0
    engine.clock["running"] = False
    engine.clock["lifecycle"] = "completed"

    assert director._poll_current_analysis(resume_on_success=True) == "completed"
    assert engine.clock["running"] is False
    assert engine.clock["lifecycle"] == "completed"

    director._auto_stop.clear()
    director._auto_monitor()
    assert director.state()["director_status"] == "completed"


def test_failed_activity_keeps_director_paused_at_checkpoint() -> None:
    runtime = PlatformRuntime()
    director = DirectorService(
        runtime,
        checkpoint_callback=lambda context: {"workflow_id": "wf-failed"},
        workflow_state_callback=lambda workflow_id: {
            "status": "completed",
            "terminal": True,
            "run": {"current": True},
            "orchestration": {"counts": {"total": 3, "completed": 2, "failed": 1}},
            "result": {"projection_status": "completed"},
            "recovery": {"reason": "one activity failed"},
        },
    )
    director.configure(
        scenario_id="amphibious-landing-joint-operation",
        mode="demonstration",
        branch="standard",
        seed=103,
    )
    director.action("advance_checkpoint")

    assert director._poll_current_analysis(resume_on_success=True) == "failed"
    failed = director.state()
    assert failed["director_status"] == "error"
    assert failed["current_checkpoint"]["analysis_status"] == "failed"
    assert failed["simulation_lifecycle"] == "paused"


def test_director_routes_validate_configuration_and_do_not_expose_future_conditions() -> None:
    app = create_app()
    app.testing = True
    client = app.test_client()

    invalid = client.post("/api/v1/director/configure", json={
        "scenario_id": "border-uav-evacuation",
        "mode": "demonstration",
        "branch": "not-a-branch",
    })
    assert invalid.status_code == 400

    configured = client.post("/api/v1/director/configure", json={
        "scenario_id": "border-uav-evacuation",
        "mode": "demo",
        "branch": "communication_degraded",
        "seed": 991,
    }).get_json()["data"]
    assert configured["mode"] == "demonstration"
    assert configured["branch"] == "communication_degraded"
    assert configured["seed"] == 991
    assert "conditions" not in str(configured)
    assert client.post("/api/v1/director/action", json={"action": "unknown"}).status_code == 400
