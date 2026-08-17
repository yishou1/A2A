from __future__ import annotations

import time
from pathlib import Path

from amos_platform.api.app_factory import create_app
from amos_platform.api.dependencies import get_bridge, get_engine
from amos_platform.data.scenario_repository import get_scenario
from amos_platform.domain.policies.visibility import remove_truth_fields
from amos_platform.simulation.asset_motion import WaypointNav
from amos_platform.runtime.platform_runtime import PlatformRuntime


ROOT = Path(__file__).resolve().parents[1]
SCENARIO_ID = "amphibious-landing-joint-operation"


def test_new_runtime_bootstraps_a_consistent_ready_scene() -> None:
    runtime = PlatformRuntime()

    state = runtime.get_engine().get_operator_state()

    assert state["clock"]["lifecycle"] == "ready"
    assert state["clock"]["run_id"] == runtime.context.run_id
    assert len(state["assets"]) == 9
    assert state["fused_tracks"] == []


def test_catalog_exposes_only_the_midterm_scenario_and_no_legacy_routes() -> None:
    app = create_app()
    app.testing = True
    client = app.test_client()

    scenarios = client.get("/api/v1/scenarios").get_json()["data"]["scenarios"]
    assert [item["id"] for item in scenarios] == ["maritime-convoy-air-defense"]
    assert scenarios[0]["name"] == "海上编队护航与要地防空"
    assert all(item["schema_version"] == "amos.scenario.v2" for item in scenarios)
    assert client.get("/api/v1/scenarios/scenario-1").status_code == 404
    assert client.get("/api/v1/backend/capabilities").status_code == 404
    assert client.get("/api/v1/agent/input/FIND").status_code == 404
    assert client.post("/api/v1/sim/maritime-chain/start", json={}).status_code == 404


def test_speed_switch_keeps_simulation_clock_advancing() -> None:
    app = create_app()
    app.testing = True
    client = app.test_client()
    client.post("/api/v1/sim/stop", json={})
    assert client.post("/api/v1/sim/start", json={}).status_code == 200
    before = client.get("/api/v1/sim/state").get_json()["data"]["clock"]["elapsed_sec"]
    switched = client.post("/api/v1/sim/speed", json={"speed": 4}).get_json()["data"]
    time.sleep(0.65)
    after = client.get("/api/v1/sim/state").get_json()["data"]["clock"]["elapsed_sec"]
    client.post("/api/v1/sim/stop", json={})

    assert switched["speed"] == 4
    assert switched["running"] is True
    assert switched["tick_thread_alive"] is True
    assert switched["last_tick_error"] is None
    assert after > before


def test_completed_simulation_cannot_be_started_or_resumed() -> None:
    runtime = PlatformRuntime()
    engine = runtime.get_engine()
    engine.stop()
    engine.clock["lifecycle"] = "completed"
    engine.clock["running"] = False

    engine.resume()
    assert engine.clock["running"] is False
    assert engine.clock["lifecycle"] == "completed"

    engine.start()
    assert engine.clock["running"] is False
    assert engine.clock["lifecycle"] == "completed"


def test_sse_response_uses_wsgi_safe_headers(monkeypatch) -> None:
    from amos_platform.api.routes import sim_routes

    monkeypatch.setattr(
        sim_routes,
        "operator_state_event_stream",
        lambda _get_engine: iter(["event: heartbeat\ndata: {}\n\n"]),
    )
    app = create_app()
    app.testing = True
    response = app.test_client().get("/api/v1/sim/stream", buffered=False)

    assert response.mimetype == "text/event-stream"
    assert response.headers["Cache-Control"] == "no-cache"
    assert "Connection" not in response.headers
    response.close()


def test_reset_returns_backend_and_frontend_to_t_zero() -> None:
    app = create_app()
    app.testing = True
    client = app.test_client()
    client.post("/api/v1/sim/start", json={"scenario_id": SCENARIO_ID})
    client.post("/api/v1/sim/pause", json={})
    with get_engine()._lock:
        get_engine()._tick(180)

    response = client.post("/api/v1/sim/reset", json={"scenario_id": SCENARIO_ID})
    data = response.get_json()["data"]

    assert response.status_code == 200
    assert data["status"] == "reset"
    assert data["state"]["clock"]["elapsed_sec"] == 0
    assert data["state"]["clock"]["running"] is False
    assert data["state"]["clock"]["lifecycle"] == "ready"
    assert len(data["state"]["assets"]) == 9
    assert data["state"]["fused_tracks"] == []


def test_frontend_uses_dynamic_scenarios_without_future_route_renderer() -> None:
    html = (ROOT / "templates/dashboard.html").read_text(encoding="utf-8")
    controller = (ROOT / "static/js/app/platform.js").read_text(encoding="utf-8")
    map_script = (ROOT / "static/js/map/platform-map.js").read_text(encoding="utf-8")
    api_script = (ROOT / "static/js/api/platform-api.js").read_text(encoding="utf-8")

    assert SCENARIO_ID not in controller
    assert "loadScenarios" in controller
    assert 'id="scenario-select"' in html
    assert "maritime-chain" not in html + controller + api_script
    assert "scenario-1" not in html + controller + api_script
    assert "asset_routes" not in map_script
    assert "history_path" in map_script
    assert "历史航迹" in map_script
    assert "工作流执行检查器" in html
    assert "/api/v1/a2a/backend/health" in api_script
    assert "最新观测资料" in html
    assert 'id="btn-director-configure"' in html
    assert 'data-workspace-tab="agents"' in html
    assert 'data-workspace-tab="algorithms"' in html
    assert 'data-workspace-tab="execution"' in html
    assert 'data-workspace-tab="evidence"' in html
    assert 'id="scenario-functional-agents"' in html
    assert 'id="functional-agent-count"' in html
    assert 'id="backend-runnable-count"' in html
    assert 'id="backend-unavailable-count"' in html
    assert 'id="wf-function-points"' in html
    assert 'data-report-format="json"' in html
    assert 'data-report-format="markdown"' in html
    assert 'data-report-format="html"' in html
    assert "runReportUrl" in controller + api_script
    assert "待后端识别" not in html + controller + map_script
    assert "asset-count-badge" in (ROOT / "static/js/panels/platform-panels.js").read_text(encoding="utf-8")
    assert "scenario-select" in html + controller
    assert "URLSearchParams" in controller
    assert "base_surface" in map_script
    assert "btn-toggle-hotspots" not in html + controller + map_script
    assert "btn-story-run-chain" not in html + controller


def test_frontend_keeps_director_stream_and_interpolates_live_markers() -> None:
    controller = (ROOT / "static/js/app/platform.js").read_text(encoding="utf-8")
    map_script = (ROOT / "static/js/map/platform-map.js").read_text(encoding="utf-8")

    assert "moveMarker" in map_script
    assert "durationMs = 450" in map_script
    assert "directorOwnsLiveUpdates" in controller
    assert "!running && !directorOwnsLiveUpdates(currentDirectorState)" in controller
    assert "if (pollTimer || sseAbortController) return" in controller


def test_frontend_authorization_is_non_blocking_and_backend_driven() -> None:
    html = (ROOT / "templates/dashboard.html").read_text(encoding="utf-8")
    controller = (ROOT / "static/js/app/platform.js").read_text(encoding="utf-8")
    panels = (ROOT / "static/js/panels/platform-panels.js").read_text(encoding="utf-8")
    styles = (ROOT / "static/css/platform.css").read_text(encoding="utf-8")

    assert 'id="authorization-dialog"' in html
    assert 'role="dialog"' in html
    assert 'id="authorization-confirm"' in html
    assert "window.confirm" not in controller
    assert "syncAuthorizationDialog" in controller
    assert 'status === "awaiting_authorization"' in controller
    assert "authorizationPromptKey" in controller
    assert "amos:fire-track" not in controller + panels
    assert "data-fire-track-id" not in panels
    assert 'selectWorkspace("execution");' not in controller
    assert 'document.addEventListener("amos:workflow-section"' not in controller
    assert 'eventName !== "sim_state"' in controller
    assert "!Array.isArray(state.assets)" in controller
    assert "!Array.isArray(state.fused_tracks)" in controller
    assert ".authorization-dialog{position:fixed;inset:0" in styles
    assert "padding:24px;background:transparent" in styles
    assert "海面接触 " in panels
    assert "高速攻击艇" in panels
    assert "民用渔船" in panels


def test_live_renderers_do_not_rebuild_unchanged_panels_or_map_layers() -> None:
    controller = (ROOT / "static/js/app/platform.js").read_text(encoding="utf-8")
    panels = (ROOT / "static/js/panels/platform-panels.js").read_text(encoding="utf-8")
    workflow = (ROOT / "static/js/workflow/commander-workflow.js").read_text(encoding="utf-8")
    map_script = (ROOT / "static/js/map/platform-map.js").read_text(encoding="utf-8")

    assert "hero.getAttribute" in controller
    assert "setHtmlIfChanged" in controller
    assert "element.innerHTML === html" in panels
    assert "lastViewSignature" in workflow
    assert "setLatLngs(points)" in map_script
    assert "updateMarkerIcon" in map_script
    assert "_amosGeometrySignature" in map_script
    assert "sensorPoseSignatures" in map_script
    assert "updateMarkerLabel" in map_script
    assert "preferCanvas: false" in map_script
    assert "_amosMotionTarget" in map_script
    assert "顺序流程容器" in workflow


def test_execution_workspace_inspects_current_run_workflows_and_real_activity_details() -> None:
    html = (ROOT / "templates/dashboard.html").read_text(encoding="utf-8")
    api_script = (ROOT / "static/js/api/platform-api.js").read_text(encoding="utf-8")
    workflow = (ROOT / "static/js/workflow/commander-workflow.js").read_text(encoding="utf-8")

    assert 'id="wf-task-history"' in html
    assert 'id="wf-activity-detail"' in html
    assert "观察与识别" in html
    assert "航迹评估" in html
    assert "方案决策" in html
    assert "执行与复核" in html
    assert 'data-activity-tab="input"' in html
    assert 'data-activity-tab="algorithm"' in html
    assert 'data-activity-tab="evidence"' in html
    assert 'data-contract="amos.workflow-view.v2.activity_details"' in html
    assert 'data("/api/v1/runs/" + encodeURIComponent(runId))' in api_script
    assert "runManifest.workflow_ids" in workflow
    assert "view.activity_details" in workflow
    assert 'currentActivity && typeof currentActivity === "object"' in workflow
    assert "后端未上报该活动的算法调用" in workflow
    assert "processActiveView(view)" in workflow
    assert 'String(view.workflow_id || "") !== String(activeWorkflowId || "")' in workflow


def test_algorithm_coverage_uses_runtime_backend_catalog() -> None:
    html = (ROOT / "templates/dashboard.html").read_text(encoding="utf-8")
    api_script = (ROOT / "static/js/api/platform-api.js").read_text(encoding="utf-8")
    panels = (ROOT / "static/js/panels/platform-panels.js").read_text(encoding="utf-8")

    assert "/api/v1/a2a/algorithms" in api_script
    assert "loadRuntimeAlgorithms" in api_script
    assert "runtime_status" in panels
    assert "A2ACapabilityCatalog" not in panels
    assert "后端可运行算法" in html
    assert "静态展示" not in html


def test_private_truth_names_use_neutral_contact_wording() -> None:
    sanitized = remove_truth_fields(
        {"message": "CONTACT-HOSTILE-01 敌方高速攻击艇进入责任区"},
        ["CONTACT-HOSTILE-01", "敌方高速攻击艇"],
    )

    assert sanitized["message"] == "待识别接触 待识别接触进入责任区"
    assert "未识别实体" not in sanitized["message"]


def test_public_scenario_and_media_are_causally_released() -> None:
    app = create_app()
    app.testing = True
    client = app.test_client()
    client.post("/api/v1/sim/stop", json={})
    get_engine().clock.pop("scenario_id", None)

    public = client.get(f"/api/v1/scenarios/{SCENARIO_ID}").get_json()["data"]
    assert public["timeline"] == []
    assert public["media_cues"] == []
    assert "asset_routes" not in public
    assert "agent_plan" not in public
    assert client.get(
        "/static/assets/scenarios/amphibious-landing-joint-operation/09-second-pass-eo.png"
    ).status_code == 404

    client.post("/api/v1/sim/start", json={})
    client.post("/api/v1/sim/pause", json={})
    engine = get_engine()
    with engine._lock:
        engine._tick(40.0)
    story = client.get("/api/v1/sim/state").get_json()["data"]["scenario_story"]
    assert [item["at_sec"] for item in story["media_cues"]] == [0]
    assert [cue["at_sec"] for cue in story["timeline"]] == [0]
    assert client.get(
        "/static/assets/scenarios/amphibious-landing-joint-operation/00-satellite-coast-sar.png"
    ).status_code == 200
    assert client.get(
        "/static/assets/scenarios/amphibious-landing-joint-operation/02-ship-radar-picture.svg"
    ).status_code == 404

    payload = get_bridge().build_workflow_payload(get_scenario(SCENARIO_ID), engine=engine)
    assert [item["id"] for item in payload["attachments"]] == ["AMP-MEDIA-00"]
    client.post("/api/v1/sim/stop", json={})


def test_waypoint_navigation_respects_platform_turn_rate() -> None:
    nav = WaypointNav()
    assets = {
        "SHIP": {
            "domain": "maritime",
            "position": {"lat": 10.0, "lng": 110.0},
            "heading_deg": 0.0,
            "speed_kts": 20.0,
        }
    }
    nav.set_route("SHIP", [{"lat": 10.0, "lng": 111.0, "label": "east"}])
    nav.tick(assets, 1.0)
    assert 0 < assets["SHIP"]["heading_deg"] <= 0.8
    assert assets["SHIP"]["position"]["lat"] > 10.0
    assert assets["SHIP"]["position"]["lng"] > 110.0


def test_coincident_loop_route_consumes_tick_without_spinning() -> None:
    nav = WaypointNav()
    assets = {
        "UAV": {
            "domain": "air",
            "position": {"lat": 22.5, "lng": 120.5},
            "heading_deg": 90.0,
            "speed_kts": 80.0,
        }
    }
    nav.set_route(
        "UAV",
        [{"lat": 22.5, "lng": 120.5, "label": "coincident"}],
        mode="loop",
    )

    events = nav.tick(assets, 30.0)

    assert len(events) == 1
    assert assets["UAV"]["position"] == {"lat": 22.5, "lng": 120.5}
