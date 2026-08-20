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


def test_authorization_wait_locks_speed_and_restores_previous_multiplier() -> None:
    runtime = PlatformRuntime()
    director = runtime.get_director()
    engine = runtime.get_engine()
    engine.set_speed(8)
    engine.start()

    director._enter_authorization_wait()

    assert engine.clock["speed"] == 1
    assert engine.clock["running"] is True
    assert engine.clock["lifecycle"] == "running"
    assert engine.clock["speed_locked_reason"] == "awaiting_authorization"
    assert engine.clock["speed_resume_value"] == 8
    operator_clock = engine.get_operator_state()["clock"]
    assert operator_clock["speed"] == 1
    assert operator_clock["speed_resume_value"] == 8
    engine.set_speed(32)
    director._enter_authorization_wait()
    assert engine.clock["speed"] == 1
    assert engine.clock["speed_resume_value"] == 8
    engine.lock_speed_for_confirmation("awaiting_follow_confirmation")

    director._leave_authorization_wait()

    assert engine.clock["speed"] == 1
    assert engine.clock["speed_locked_reason"] == "awaiting_follow_confirmation"
    engine.unlock_speed_for_confirmation("awaiting_follow_confirmation")
    assert engine.clock["speed"] == 8
    assert "speed_locked_reason" not in engine.clock
    assert "speed_resume_value" not in engine.clock
    engine.stop()


def test_speed_endpoint_cannot_override_authorization_lock() -> None:
    app = create_app()
    app.testing = True
    client = app.test_client()
    engine = get_engine()
    engine.set_speed(16)
    engine.lock_speed_for_confirmation("awaiting_authorization")
    try:
        result = client.post("/api/v1/sim/speed", json={"speed": 32}).get_json()["data"]
        assert result["speed"] == 1
        assert result["speed_locked"] is True
        assert result["speed_resume_value"] == 16
    finally:
        engine.unlock_speed_for_confirmation("awaiting_authorization")


def test_weapon_confirmation_keeps_clock_running_at_one_x(monkeypatch) -> None:
    runtime = PlatformRuntime()
    director = runtime.get_director()
    director.configure(
        scenario_id="maritime-convoy-air-defense",
        mode="demonstration",
        branch="standard",
        seed=33031,
    )
    engine = runtime.get_engine()
    engine.set_speed(8)
    engine.pause()
    monkeypatch.setattr(director, "_authorization_stage", lambda: "fire")
    monkeypatch.setattr(
        runtime,
        "record_director_state",
        lambda _state: director._auto_stop.set(),
    )

    director._auto_stop.clear()
    director._auto_monitor()

    assert director.state()["awaiting_authorization"] is True
    assert engine.clock["speed"] == 1
    assert engine.clock["running"] is True
    assert engine.clock["lifecycle"] == "running"
    assert engine.clock["authorization_stage"] == "fire"
    director._leave_authorization_wait()
    engine.stop()


def test_maritime_warning_stage_opens_fire_gate_after_three_hundred_sim_seconds() -> None:
    runtime = PlatformRuntime()
    director = runtime.get_director()
    director.configure(
        scenario_id="maritime-convoy-air-defense",
        mode="demonstration",
        branch="standard",
        seed=33031,
    )
    engine = runtime.get_engine()
    engine.clock["elapsed_sec"] = 4560.0

    assert director._authorization_stage() == "warning"

    engine._engagement_warnings["TRK-TEST"] = {
        "track_id": "TRK-TEST",
        "status": "issued",
        "issued_at_sec": 4560.0,
        "fire_not_before_sec": 4860.0,
        "delay_sec": 300.0,
    }
    assert director._authorization_stage() == "warning_wait"

    engine.clock["elapsed_sec"] = 4859.999
    assert director._authorization_stage() == "warning_wait"
    engine.clock["elapsed_sec"] = 4860.0
    assert director._authorization_stage() == "fire"


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
    assert "smoothTrailPoints" in map_script
    assert "历史航迹" in map_script
    assert "任务执行检查器" in html
    assert "/api/v1/a2a/backend/health" in api_script
    assert "最新观测资料" in html
    assert 'id="btn-director-configure"' not in html
    assert 'id="director-mode-select"' not in html
    assert 'id="director-branch-select"' not in html
    assert 'id="director-seed"' not in html
    assert "btn-director-configure" not in controller
    assert 'data-workspace-tab="agents"' in html
    assert 'data-workspace-tab="algorithms"' in html
    assert 'data-workspace-tab="execution"' in html
    assert 'data-workspace-tab="evidence"' in html
    assert 'id="scenario-functional-agents"' in html
    assert 'id="functional-agent-count"' in html
    assert 'id="agent-deployment-topology"' in html
    assert 'id="agent-deployment-count"' in html
    assert 'id="backend-active-count"' in html
    assert 'id="backend-runnable-count"' in html
    assert 'id="backend-unavailable-count"' in html
    assert 'id="kill-chain-runtime-view"' in html
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


def test_evidence_workspace_embeds_the_knowledge_graph_as_one_static_asset() -> None:
    html = (ROOT / "templates/dashboard.html").read_text(encoding="utf-8")
    controller = (ROOT / "static/js/app/platform.js").read_text(encoding="utf-8")
    styles = (ROOT / "static/css/platform.css").read_text(encoding="utf-8")
    graph_path = ROOT / "static/knowledge-graph/roe-knowledge-graph.html"

    assert 'id="btn-open-knowledge-graph"' in html
    assert 'id="knowledge-graph-dialog"' in html
    assert 'role="dialog"' in html
    assert 'data-src="/static/knowledge-graph/roe-knowledge-graph.html"' in html
    assert "openKnowledgeGraph" in controller
    assert "closeKnowledgeGraph" in controller
    assert ".knowledge-graph-dialog{position:fixed;inset:0" in styles
    assert "width:100vw;height:100vh" in styles
    assert "查看规则实体与语料分块的主题关系" in html
    assert "实体" in html
    assert "语料" in html
    report_position = html.index('class="card report-card"')
    graph_position = html.index('class="card knowledge-graph-entry"')
    snapshot_position = html.index('id="wf-input-snapshot"')
    assert report_position < graph_position < snapshot_position
    assert graph_path.is_file()
    graph_html = graph_path.read_text(encoding="utf-8")
    assert "交战规则知识图谱" in graph_html
    assert '"visible_nodes":1522' in graph_html
    assert '"visible_edges":19459' in graph_html
    assert "节点类型" in graph_html
    assert "语料（文档分块）" in graph_html
    assert '"label":"语料 001"' in graph_html
    assert '"color":"#4DB68B"' in graph_html
    assert '"color":"#D67366"' in graph_html


def test_frontend_keeps_director_stream_and_interpolates_live_markers() -> None:
    controller = (ROOT / "static/js/app/platform.js").read_text(encoding="utf-8")
    map_script = (ROOT / "static/js/map/platform-map.js").read_text(encoding="utf-8")

    assert "moveMarker" in map_script
    assert "durationMs = 450" in map_script
    assert "_amosMotionUpdatedAt" in map_script
    assert "updateCadence * 1.04" in map_script
    assert "directorOwnsLiveUpdates" in controller
    assert "!running && !directorOwnsLiveUpdates(currentDirectorState)" in controller
    assert "if (pollTimer || sseAbortController) return" in controller


def test_frontend_authorization_is_non_blocking_and_backend_driven() -> None:
    html = (ROOT / "templates/dashboard.html").read_text(encoding="utf-8")
    controller = (ROOT / "static/js/app/platform.js").read_text(encoding="utf-8")
    panels = (ROOT / "static/js/panels/platform-panels.js").read_text(encoding="utf-8")
    map_script = (ROOT / "static/js/map/platform-map.js").read_text(encoding="utf-8")
    styles = (ROOT / "static/css/platform.css").read_text(encoding="utf-8")

    assert 'id="authorization-dialog"' in html
    assert 'role="dialog"' in html
    assert 'id="authorization-confirm"' in html
    assert "window.confirm" not in controller
    assert "syncAuthorizationDialog" in controller
    assert 'status === "awaiting_authorization"' in controller
    assert "authorizationPromptKey" in controller
    assert 'command_type: "warn"' in controller
    assert 'mode: "warn"' in controller
    assert "authorization_stage" in controller
    assert 'if (stage !== "warning" && stage !== "fire") return;' in controller
    assert "无线电警告确认" in controller
    assert "目标未回应警告，是否授权实施武器打击？" in controller
    assert "警告发出已满" not in controller
    assert "warningDelaySeconds" in controller
    assert "interactive: false" in map_script
    assert "sensorCapabilityHtml" in map_script
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
    assert "showWeaponImpact" in map_script
    assert "impact_sim_time" in map_script
    assert 'return "civilian"' in map_script
    assert 'return "destroyed"' in map_script
    assert "updateDestroyedImpactMarkers" in map_script
    assert "destroyedImpactMarkers" in map_script
    assert 'return "impact"' in map_script
    assert "damage_assessment_confirmed" in map_script


def test_story_animation_is_incremental_and_speed_ui_tracks_backend_state() -> None:
    controller = (ROOT / "static/js/app/platform.js").read_text(encoding="utf-8")
    styles = (ROOT / "static/css/platform.css").read_text(encoding="utf-8")

    assert "syncStoryMedia" in controller
    assert "syncStoryTimeline" in controller
    assert "updateStoryHero" in controller
    assert "scrollIntoView" in controller
    assert "prefers-reduced-motion" in controller + styles
    assert "speedRequestQueue" in controller
    assert "syncSpeedFromClock" in controller
    assert "Boolean(clock.speed_locked_reason)" in controller
    assert "speed_resume_value" in controller
    assert ".speed-group.speed-locked" in styles
    assert ".leaflet-overlay-pane path.leaflet-interactive:focus{outline:none}" in styles
    assert "@keyframes weapon-impact-ring" in styles
    assert ".map-resource-label.civilian-label" in styles
    assert ".map-resource-label.destroyed-label" in styles
    assert ".map-resource-label.impact-label" in styles


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
    assert 'detail[scope + "_fields"]' in workflow
    assert 'data-detail-scope="' in workflow
    assert "后端未返回可展示的 JSON 字段" in workflow
    assert "semanticIoSpec" not in workflow
    assert "上一阶段形成的稳定航迹" not in workflow
    assert "当前新增传感器观测" not in workflow
    assert "当前传感器观测" not in workflow
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
    assert "算法与功能" in html
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
