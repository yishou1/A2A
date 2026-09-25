from __future__ import annotations

import hashlib
import threading
import time
from pathlib import Path

import pytest

from amos_platform.api.app_factory import create_app
from amos_platform.api.dependencies import get_engine
from amos_platform.data.scenario_capabilities import (
    FUNCTIONAL_AGENT_CATALOG,
    FUNCTION_POINT_CATALOG,
    MODEL_CATALOG,
)
from amos_platform.data.scenario_repository import get_scenario, list_scenarios
from amos_platform.runtime.platform_runtime import PlatformRuntime
from amos_platform.simulation.director import DirectorError, DirectorService
from amos_platform.simulation.engine import SimEngine


SCENARIO_IDS = [
    "maritime-convoy-air-defense",
    "coastal-joint-recon-strike",
    "air-space-sea-carrier-strike",
]
ROOT = Path(__file__).resolve().parents[1]


def test_midterm_catalog_exposes_maritime_scenario_and_builders_keep_v2_contract() -> None:
    summaries = list_scenarios()
    assert [item["id"] for item in summaries] == SCENARIO_IDS
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

    assert declared_algorithms <= {item["algorithm_id"] for item in MODEL_CATALOG}
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
    assert set(models) == {f"M{index:02d}" for index in range(1, 21)} - {"M08"}
    assert {key for key, row in models.items() if row["tier"] == "core"} == (
        {f"M{index:02d}" for index in range(1, 17)} - {"M08"}
    )
    assert {key for key, row in models.items() if row["tier"] == "engineering"} == {
        "M17", "M18", "M19", "M20"
    }
    assert all(row["assigned_agents"] for row in models.values())
    assert {key for key, row in models.items() if not row["function_points"]} == {"M09", "M12"}
    assert all(row["model_id"] is None and row["version"] is None for row in models.values())


def test_each_formal_scenario_has_the_documented_model_coverage() -> None:
    expected_core = {
        "maritime-convoy-air-defense": ({"M08"}, 15),
        "coastal-joint-recon-strike": ({"M08"}, 15),
        "air-space-sea-carrier-strike": ({"M08"}, 15),
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
        assert len(scenario["function_point_coverage"]) == 28


def test_planned_agents_do_not_publish_static_runtime_status() -> None:
    scenario = get_scenario("maritime-convoy-air-defense")
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

    response = client.get("/api/v1/scenarios/maritime-convoy-air-defense")
    data = response.get_json()["data"]

    assert response.status_code == 200
    assert all(float(item.get("at_sec", 0)) <= 0 for item in data["timeline"])
    assert all(float(item.get("at_sec", 0)) <= 0 for item in data["media_cues"])
    assert "asset_routes" not in data
    assert "asset_route_modes" not in data
    assert "asset_motion_windows" not in data
    assert "threat_observation_windows" not in data
    assert "threats" not in data
    assert "demo_checkpoints" not in data
    assert "fault_injections" not in data
    assert "comm_degraded" not in data


def test_active_scenario_detail_reuses_engine_story_media() -> None:
    app = create_app()
    app.testing = True
    client = app.test_client()
    scenario_id = "coastal-joint-recon-strike"
    client.post("/api/v1/sim/reset", json={"scenario_id": scenario_id})
    engine = get_engine()
    with engine._lock:
        engine._tick(945.0)

    detail = client.get(f"/api/v1/scenarios/{scenario_id}").get_json()["data"]
    live = client.get("/api/v1/sim/state").get_json()["data"]["scenario_story"]

    assert detail["media_cues"]
    assert [item["media_id"] for item in detail["media_cues"]] == [
        item["media_id"] for item in live["media_cues"]
    ]


def test_new_scenario_media_routes_enforce_time_and_active_run_boundaries() -> None:
    app = create_app()
    app.testing = True
    client = app.test_client()

    reset = client.post("/api/v1/sim/reset", json={
        "scenario_id": "maritime-convoy-air-defense",
        "seed": 5,
    })
    assert reset.status_code == 200
    assert client.get(
        "/static/assets/scenarios/maritime-convoy-air-defense/00-convoy-overview.png"
    ).status_code == 200
    assert client.get(
        "/static/assets/scenarios/maritime-convoy-air-defense/07-post-maneuver-observation.png"
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
    scenario = get_scenario("maritime-convoy-air-defense")
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

    scenario = get_scenario("maritime-convoy-air-defense")
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
        "scenario_id": "maritime-convoy-air-defense",
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
        scenario_id="maritime-convoy-air-defense",
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
    assert reached["current_checkpoint"]["checkpoint_id"] == "MAR-CP-PERCEPTION"
    assert reached["current_checkpoint"]["reached_at_sec"] >= 1470
    assert reached["current_checkpoint"]["analysis_status"] == "submission_unavailable"
    assert reached["awaiting_analysis"] is False
    assert reached["simulation_lifecycle"] == "paused"


def test_failed_checkpoint_cannot_be_advanced_or_restarted() -> None:
    runtime = PlatformRuntime()
    director = DirectorService(runtime)
    director.configure(
        scenario_id="maritime-convoy-air-defense",
        mode="demonstration",
        branch="standard",
        seed=23,
    )
    failed = director.action("advance_checkpoint")
    elapsed = failed["elapsed_sec"]

    for action in ("start", "step_tick", "advance_checkpoint", "start_auto"):
        with pytest.raises(DirectorError, match="重置场景后重试"):
            director.action(action)

    assert runtime.get_engine().clock["elapsed_sec"] == elapsed
    assert runtime.get_engine().clock["running"] is False


def test_each_scenario_can_reach_leading_unconditional_declared_checkpoints() -> None:
    for scenario_id in SCENARIO_IDS:
        runtime = PlatformRuntime()
        director = DirectorService(
            runtime,
            checkpoint_callback=lambda context: {"workflow_id": f"wf-{context['checkpoint_id']}"},
            workflow_state_callback=lambda workflow_id: {
                "status": "completed",
                "terminal": True,
                "run": {"current": True},
                "orchestration": {"counts": {"total": 1, "completed": 1, "failed": 0}},
                "result": {"projection_status": "completed"},
            },
        )
        scenario = get_scenario(scenario_id)
        assert scenario is not None
        director.configure(
            scenario_id=scenario_id,
            mode="demonstration",
            branch="standard",
            seed=scenario["default_seed"],
        )
        expected = []
        for item in scenario["demo_checkpoints"]:
            if item.get("requires_operator_action"):
                break
            if (
                "*" in set(item.get("branch_ids") or ["*"])
                or "standard" in set(item.get("branch_ids") or [])
            ):
                expected.append(item)
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
            director.action("refresh_analysis")
        assert reached == [item["checkpoint_id"] for item in expected]


def test_director_supports_all_manual_and_automatic_control_actions() -> None:
    runtime = PlatformRuntime()
    director = DirectorService(runtime)
    director.configure(
        scenario_id="maritime-convoy-air-defense",
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


def test_authorization_wait_starts_only_at_reached_operator_checkpoint_and_is_wave_scoped() -> None:
    runtime = PlatformRuntime()
    director = DirectorService(runtime)
    director.configure(
        scenario_id="air-space-sea-carrier-strike",
        mode="demonstration",
        branch="standard",
        seed=76091,
    )
    engine = runtime.get_engine()

    engine.clock["elapsed_sec"] = 3360
    director._state["current_checkpoint"] = {
        "checkpoint_id": "ASC-CP-PLAN",
        "reached_at_sec": 2730,
        "requires_operator_action": False,
    }
    assert director._authorization_stage() is None

    director._state["current_checkpoint"] = {
        "checkpoint_id": "ASC-CP-WAVE1",
        "reached_at_sec": 3390,
        "requires_operator_action": True,
    }
    engine.clock["elapsed_sec"] = 3390
    assert director._authorization_stage() == "fire"

    engine.events.append({
        "type": "authorized_fire_command",
        "command_source": "operator",
        "sim_time": 3391,
    })
    assert director._authorization_stage() is None

    director._state["current_checkpoint"] = {
        "checkpoint_id": "ASC-CP-WAVE2",
        "reached_at_sec": 4380,
        "requires_operator_action": True,
    }
    engine.clock["elapsed_sec"] = 4380
    assert director._authorization_stage() == "fire"


def test_carrier_wave_two_and_close_require_target_specific_bda() -> None:
    runtime = PlatformRuntime()
    director = DirectorService(runtime)
    director.configure(
        scenario_id="air-space-sea-carrier-strike",
        mode="demonstration",
        branch="standard",
        seed=76091,
    )
    engine = runtime.get_engine()
    scenario = get_scenario("air-space-sea-carrier-strike")
    assert scenario is not None
    wave_two = next(
        item for item in scenario["demo_checkpoints"]
        if item["checkpoint_id"] == "ASC-CP-WAVE2"
    )
    close = next(
        item for item in scenario["demo_checkpoints"]
        if item["checkpoint_id"] == "ASC-CP-CLOSE"
    )

    engine.clock["elapsed_sec"] = 4560
    engine.media_capture._captures["ASC-MEDIA-08"] = {}
    assert director._checkpoint_satisfied(wave_two) is False

    engine.events.extend([
        {
            "type": "weapon_hit",
            "target_threat_id": "COASTAL-AIRFIELD-01",
            "sim_time": 3940,
        },
        {
            "type": "damage_assessment_confirmed",
            "target_threat_id": "CIVILIAN-PORT-01",
            "sim_time": 4380,
        },
    ])
    assert director._checkpoint_satisfied(wave_two) is False
    engine.events.append({
        "type": "damage_assessment_confirmed",
        "target_threat_id": "COASTAL-AIRFIELD-01",
        "sim_time": 4380,
    })
    assert director._checkpoint_satisfied(wave_two) is True
    engine.clock["elapsed_sec"] = 5760
    for media_id in ("ASC-MEDIA-08", "ASC-MEDIA-10", "ASC-MEDIA-09"):
        engine.media_capture._captures[media_id] = {}
    assert director._checkpoint_satisfied(close) is False
    engine.events.append({"type": "weapon_hit", "target_threat_id": "MOBILE-COASTAL-AD-01"})
    assert director._checkpoint_satisfied(close) is False
    engine.events.append({
        "type": "damage_assessment_confirmed",
        "target_threat_id": "MOBILE-COASTAL-AD-01",
        "sim_time": 5400,
    })
    assert director._checkpoint_satisfied(close) is True
    director._state["current_checkpoint"] = {
        "checkpoint_id": close["checkpoint_id"],
        "reached_at_sec": 5760,
        "requires_operator_action": False,
    }
    assert director._authorization_stage() is None


def test_operator_checkpoint_keeps_fire_gate_after_story_phase_boundary() -> None:
    runtime = PlatformRuntime()
    director = DirectorService(runtime)
    director.configure(
        scenario_id="coastal-joint-recon-strike",
        mode="demonstration",
        branch="standard",
        seed=61023,
    )
    engine = runtime.get_engine()
    director._state["current_checkpoint"] = {
        "checkpoint_id": "CJR-CP-ENGAGE",
        "reached_at_sec": 3330,
        "requires_operator_action": True,
    }

    # A slow backend response must not turn the first ASSESS timestamp into
    # implicit approval.  The checkpoint remains the authority boundary.
    engine.clock["elapsed_sec"] = 4500
    assert director._authorization_stage() == "fire"

    engine.events.append({
        "type": "authorized_fire_command",
        "command_source": "operator",
        "sim_time": 4501,
    })
    assert director._authorization_stage() is None


def test_maritime_engage_checkpoint_preserves_warning_and_fire_across_assess_boundary() -> None:
    runtime = PlatformRuntime()
    director = DirectorService(runtime)
    director.configure(
        scenario_id="maritime-convoy-air-defense",
        mode="demonstration",
        branch="standard",
        seed=33031,
    )
    engine = runtime.get_engine()
    engine._tick(4600)
    hostile = next(
        track for track in engine.sensor_fusion.tracks.values()
        if engine._truth_target_for_track(track) == "CONTACT-HOSTILE-01"
    )
    hostile.classification = "FAST_ATTACK_CRAFT"
    hostile.threat_level = "HIGH"
    hostile.kill_chain_phase = "ASSESS"
    hostile.agent_assessment = {
        "status": "confirmed", "label": "高风险", "source": "test",
    }
    director._state["current_checkpoint"] = {
        "checkpoint_id": "MAR-CP-ENGAGE",
        "reached_at_sec": 3930,
        "requires_operator_action": True,
        "operator_action_type": "fire",
    }

    assert director._authorization_stage() == "warning"
    public_track = next(
        item for item in engine.get_operator_state()["fused_tracks"]
        if item["id"] == hostile.id
    )
    assert public_track["engagement_eligible"] is True

    warning = engine.issue_warning_at_track(hostile.id, authorized=True)
    assert warning["status"] == "issued"
    assert director._authorization_stage() == "warning_wait"
    engine._tick(float(warning["delay_sec"]))
    hostile.kill_chain_phase = "ASSESS"
    assert director._authorization_stage() == "fire"
    public_track = next(
        item for item in engine.get_operator_state()["fused_tracks"]
        if item["id"] == hostile.id
    )
    assert public_track["engagement_eligible"] is True


def test_authorization_at_gate_instant_counts_despite_rounding() -> None:
    """授权事件与检查点时刻的舍入精度差（3位 vs 2位）不得让导演永远等下去。"""
    runtime = PlatformRuntime()
    director = DirectorService(runtime)
    director.configure(
        scenario_id="coastal-joint-recon-strike",
        mode="demonstration",
        branch="standard",
        seed=106,
    )
    engine = runtime.get_engine()
    gate_elapsed = 3333.9909896850586
    engine.events.append({
        "type": "authorized_fire_command",
        "command_source": "operator",
        "sim_time": round(gate_elapsed, 2),  # 事件侧两位精度：3333.99
    })
    assert director._has_authorized_engagement(
        since_sec=round(gate_elapsed, 3),  # 检查点侧三位精度：3333.991
    ) is True


def test_operator_gate_submission_is_deferred() -> None:
    """操作员门检查点的分析提交走后台线程：先出弹窗状态，提交结果稍后补齐。"""
    started = threading.Event()
    release = threading.Event()

    def slow_callback(context: dict) -> dict:
        started.set()
        release.wait(timeout=10)
        return {"workflow_id": "wf-deferred"}

    runtime = PlatformRuntime()
    director = DirectorService(runtime, checkpoint_callback=slow_callback)
    director.configure(
        scenario_id="maritime-convoy-air-defense",
        mode="demonstration",
        branch="standard",
        seed=105,
    )
    checkpoint = director._next_checkpoint()
    reached = director._reach_checkpoint(checkpoint, defer_submission=True)
    assert reached["analysis_status"] == "submitting"
    assert director.state()["current_checkpoint"]["analysis_status"] == "submitting"

    assert started.wait(timeout=5)
    release.set()
    deadline = time.time() + 5
    while time.time() < deadline:
        current = director.state()["current_checkpoint"]
        if current["analysis_status"] == "submitted":
            break
        time.sleep(0.05)
    current = director.state()["current_checkpoint"]
    assert current["analysis_status"] == "submitted"
    assert current["submission"]["workflow_id"] == "wf-deferred"


def test_stale_checkpoint_analysis_is_finalized_by_sweep() -> None:
    """推进到下一检查点后，旧检查点的终态由补漏轮询收尾。"""
    views = {
        "wf-old": {
            "workflow_id": "wf-old", "status": "completed", "terminal": True,
            "run": {"current": True},
            "orchestration": {"counts": {"total": 2, "completed": 2, "failed": 0}},
            "result": {"projection_status": "completed"},
        },
        "wf-new": {
            "workflow_id": "wf-new", "status": "running", "terminal": False,
        },
    }
    runtime = PlatformRuntime()
    director = DirectorService(
        runtime,
        checkpoint_callback=lambda context: {
            "workflow_id": "wf-old" if context.get("checkpoint_id") == "MAR-CP-PERCEPTION" else "wf-new",
        },
        workflow_state_callback=lambda workflow_id, light=True: views[str(workflow_id)],
    )
    director.configure(
        scenario_id="maritime-convoy-air-defense",
        mode="demonstration",
        branch="standard",
        seed=107,
    )
    director.action("advance_checkpoint")
    director.action("advance_checkpoint")
    assert len(director._reached) == 2
    director._reached[0]["analysis_status"] = "running"  # 模拟：推进后旧检查点仍显示运行中
    status_before = director.state()["director_status"]
    director._poll_stale_checkpoints(str(director.state()["run_id"]))
    refreshed = director.state()["reached_checkpoints"]
    assert refreshed[0]["analysis_status"] == "completed"
    assert refreshed[1]["analysis_status"] != "completed"  # 当前检查点不被补漏碰
    # 补漏只记账：不得篡改当前导演状态
    assert director.state()["director_status"] == status_before


def test_nonblocking_checkpoint_analysis_is_polled_without_waiting() -> None:
    """非阻塞检查点的分析仍要被轮询投影，但不得把导演挂进等待态。"""
    views = [
        {"workflow_id": "wf-nonblocking", "status": "running", "terminal": False},
        {
            "workflow_id": "wf-nonblocking", "status": "completed", "terminal": True,
            "run": {"current": True},
            "orchestration": {"counts": {"total": 3, "completed": 3, "failed": 0}},
            "result": {"projection_status": "completed"},
        },
    ]

    def workflow_view(workflow_id: str, light: bool = True) -> dict:
        return views.pop(0)

    runtime = PlatformRuntime()
    director = DirectorService(
        runtime,
        checkpoint_callback=lambda context: {"workflow_id": "wf-nonblocking"},
        workflow_state_callback=workflow_view,
    )
    director.configure(
        scenario_id="maritime-convoy-air-defense",
        mode="demonstration",
        branch="standard",
        seed=104,
    )
    submitted = director.action("advance_checkpoint")
    assert submitted["current_checkpoint"]["analysis_blocking"] is False

    assert director._poll_current_analysis(resume_on_success=True) == "pending"
    pending = director.state()
    assert pending["awaiting_analysis"] is False
    assert pending["director_status"] != "awaiting_analysis"

    assert director._poll_current_analysis(resume_on_success=True) == "completed"
    completed = director.state()
    assert completed["reached_checkpoints"][0]["analysis_status"] == "completed"
    assert completed["awaiting_analysis"] is False


def test_maritime_close_checkpoint_is_review_only_and_requires_real_effects() -> None:
    scenario = get_scenario("maritime-convoy-air-defense")
    assert scenario is not None
    checkpoints = {
        item["checkpoint_id"]: item for item in scenario["demo_checkpoints"]
    }
    assert checkpoints["MAR-CP-ENGAGE"]["operator_action_type"] == "fire"
    assert checkpoints["MAR-CP-ENGAGE"]["conditions"] == {
        "media_ids_released": ["MAR-MEDIA-05"],
    }
    assert checkpoints["MAR-CP-CLOSE"]["operator_action_type"] == "review"
    assert checkpoints["MAR-CP-CLOSE"]["conditions"]["event_types_emitted"] == [
        "weapon_hit", "damage_assessment_confirmed",
    ]


def test_checkpoint_callback_is_the_only_source_of_submitted_status() -> None:
    calls: list[dict] = []

    def submit(context: dict) -> dict:
        calls.append(context)
        return {"workflow_id": "wf-real-boundary"}

    runtime = PlatformRuntime()
    director = DirectorService(runtime, checkpoint_callback=submit)
    director.configure(
        scenario_id="maritime-convoy-air-defense",
        mode="demonstration",
        branch="standard",
        seed=101,
    )
    state = director.action("advance_checkpoint")

    assert calls[0]["run_id"] == state["run_id"]
    assert calls[0]["checkpoint_id"] == "MAR-CP-PERCEPTION"
    assert state["current_checkpoint"]["analysis_status"] == "submitted"
    assert state["current_checkpoint"]["submission"]["workflow_id"] == "wf-real-boundary"
    # MAR-CP-PERCEPTION is a nonblocking analysis checkpoint: the real
    # callback still owns the submitted status, but the director keeps
    # flowing instead of waiting for the backend workflow.
    assert state["current_checkpoint"]["analysis_blocking"] is False
    assert state["awaiting_analysis"] is False
    assert state["director_status"] == "checkpoint_reached"


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
        scenario_id="maritime-convoy-air-defense",
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
    scenario = get_scenario("maritime-convoy-air-defense")
    assert scenario is not None
    director._checkpoint_index = len(scenario["demo_checkpoints"]) - 1
    director._state["current_checkpoint"] = {
        "checkpoint_id": "MAR-CP-CLOSE",
        "analysis_status": "running",
        "submission": {"workflow_id": "wf-act"},
    }
    engine = runtime.get_engine()
    engine.clock["elapsed_sec"] = 5400.0
    engine.clock["running"] = False
    engine.clock["lifecycle"] = "completed"

    assert director._poll_current_analysis(resume_on_success=True) == "completed"
    assert engine.clock["running"] is False
    assert engine.clock["lifecycle"] == "completed"

    director._auto_stop.clear()
    director._auto_monitor()
    assert director.state()["director_status"] == "completed"


def test_slow_analysis_result_cannot_mutate_a_reconfigured_run() -> None:
    runtime = PlatformRuntime()
    director = DirectorService(runtime)
    director.configure(
        scenario_id="maritime-convoy-air-defense",
        mode="demonstration",
        branch="standard",
        seed=33031,
    )
    old_run_id = director.state()["run_id"]
    director._state["current_checkpoint"] = {
        "checkpoint_id": "MAR-CP-PERCEPTION",
        "analysis_status": "running",
        "submission": {"workflow_id": "wf-old-run"},
    }

    def reconfigure_while_polling(_workflow_id: str, **_kwargs) -> dict:
        director.configure(
            scenario_id="coastal-joint-recon-strike",
            mode="demonstration",
            branch="standard",
            seed=44021,
        )
        return {
            "status": "completed",
            "terminal": True,
            "run": {"current": True},
            "orchestration": {"counts": {"total": 1, "completed": 1, "failed": 0}},
            "result": {"projection_status": "completed"},
        }

    director.workflow_state_callback = reconfigure_while_polling
    assert director._poll_current_analysis(resume_on_success=True) == "stale_run"
    state = director.state()
    assert state["run_id"] != old_run_id
    assert state["scenario_id"] == "coastal-joint-recon-strike"
    assert state["current_checkpoint"] is None
    assert state["director_status"] == "configured"


def test_authorization_checkpoint_fails_explainably_without_an_eligible_target(
    monkeypatch,
) -> None:
    runtime = PlatformRuntime()
    director = runtime.get_director()
    director.configure(
        scenario_id="air-space-sea-carrier-strike",
        mode="demonstration",
        branch="standard",
        seed=76091,
    )
    director._state["current_checkpoint"] = {
        "checkpoint_id": "ASC-CP-WAVE1",
        "analysis_status": "completed",
        "requires_operator_action": True,
        "operator_action_type": "fire",
        "engagement_wave": 1,
    }
    monkeypatch.setattr(director, "_authorization_stage", lambda: "fire")

    director._auto_stop.clear()
    director._auto_monitor()

    state = director.state()
    assert state["director_status"] == "error"
    assert "没有满足识别、证据与交战规则的目标" in state["last_error"]
    assert state["awaiting_authorization"] is False


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
        scenario_id="maritime-convoy-air-defense",
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
        "scenario_id": "maritime-convoy-air-defense",
        "mode": "demonstration",
        "branch": "not-a-branch",
    })
    assert invalid.status_code == 400

    configured = client.post("/api/v1/director/configure", json={
        "scenario_id": "maritime-convoy-air-defense",
        "mode": "demo",
        "branch": "communication_degraded",
        "seed": 991,
    }).get_json()["data"]
    assert configured["mode"] == "demonstration"
    assert configured["branch"] == "communication_degraded"
    assert configured["seed"] == 991
    assert "conditions" not in str(configured)
    assert client.post("/api/v1/director/action", json={"action": "unknown"}).status_code == 400
