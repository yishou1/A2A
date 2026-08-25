from __future__ import annotations

from copy import deepcopy
import json
import sqlite3

from amos_platform.agents.a2a.workflow_run_store import get_workflow_run_store
from amos_platform.api.app_factory import create_app
from amos_platform.data.scenario_repository import get_scenario
from amos_platform.runtime.platform_runtime import PlatformRuntime
from amos_platform.runtime.run_manifest import (
    RUN_MANIFEST_SCHEMA_VERSION,
    RunManifestStore,
    render_html_report,
)


def _context(run_id: str = "run-archive-1") -> dict:
    return {
        "run_id": run_id,
        "scenario_id": "maritime-convoy-air-defense",
        "seed": 771,
        "platform_mode": "online",
        "agent_backend": "gateway",
        "started_at": "2026-08-11T01:02:03Z",
    }


def test_sqlite_manifest_persists_complete_acceptance_evidence(tmp_path) -> None:
    db_path = tmp_path / "archive" / "runs.sqlite3"
    store = RunManifestStore(db_path)
    store.begin_run(
        _context(),
        scenario_name="海上编队护航与要地防空",
        mode="demonstration",
        branch="communication_degraded",
    )
    store.record_director_state("run-archive-1", {
        "mode": "demonstration",
        "branch": "communication_degraded",
        "director_status": "checkpoint_reached",
        "current_checkpoint": {"checkpoint_id": "CP-1"},
        "action_log": [{"action": "advance_checkpoint", "at": "2026-08-11T01:03:00Z"}],
        "reached_checkpoints": [{"checkpoint_id": "CP-1", "reached_at_sec": 75}],
        "requested_faults": [{"fault_id": "FAULT-LINK", "backend_status": "unverified"}],
        "verified_faults": [],
        "last_error": None,
    })
    submission = {
        "scenario_id": "maritime-convoy-air-defense",
        "run_id": "run-archive-1",
        "simulation_time_sec": 75,
        "accepted": True,
        "attachments": [{"id": "MEDIA-1", "checksum": {"algorithm": "sha256", "value": "abc"}}],
    }
    store.record_submission(
        "run-archive-1",
        workflow_id="wf-archive-1",
        submission=submission,
    )
    view = {
        "schema_version": "amos.workflow-view.v2",
        "workflow_id": "wf-archive-1",
        "status": "completed",
        "terminal": True,
        "agents": {"items": [{"role": "track_threat", "instance_id": None}]},
        "algorithms": {"items": [{"algorithm_id": "ALG-01", "status": "verified"}]},
        "function_points": {"items": [{"function_id": "FP-01", "status": "completed"}]},
        "execution_graph": {"nodes": [], "edges": []},
        "metrics": {"duration_ms": 42},
        "provenance": {"transport": "gateway"},
        "result": {"warnings": []},
        "raw_backend_private_field": "must-not-persist",
    }
    store.record_workflow_view("run-archive-1", view)
    store.record_lifecycle("run-archive-1", "completed", alerts=[{
        "level": "WARNING",
        "type": "link_degraded",
        "msg": "链路质量下降",
        "time": "2026-08-11T01:04:00Z",
        "hidden_truth": "must-not-persist",
    }])

    reopened = RunManifestStore(db_path)
    manifest = reopened.get("run-archive-1")
    assert manifest is not None
    assert manifest["schema_version"] == RUN_MANIFEST_SCHEMA_VERSION
    assert manifest["seed"] == 771
    assert manifest["mode"] == "demonstration"
    assert manifest["branch"] == "communication_degraded"
    assert manifest["director"]["actions"][0]["action"] == "advance_checkpoint"
    assert manifest["director"]["checkpoints"][0]["checkpoint_id"] == "CP-1"
    assert manifest["submissions"][0]["snapshot"] == submission
    assert manifest["workflow_ids"] == ["wf-archive-1"]
    assert manifest["workflow_views"][0]["agents"] == view["agents"]
    assert manifest["workflow_views"][0]["metrics"] == {"duration_ms": 42}
    assert "raw_backend_private_field" not in manifest["workflow_views"][0]
    assert manifest["faults"]["requested"][0]["fault_id"] == "FAULT-LINK"
    assert manifest["faults"]["verified"] == []
    assert manifest["lifecycle"]["final_status"] == "completed"
    assert "hidden_truth" not in manifest["alerts"][0]
    assert reopened.list()[0]["workflow_count"] == 1

    with sqlite3.connect(db_path) as connection:
        stored = connection.execute(
            "SELECT manifest_json FROM run_manifests WHERE run_id = ?",
            ("run-archive-1",),
        ).fetchone()
    assert stored is not None
    assert json.loads(stored[0])["run_id"] == "run-archive-1"


def test_html_report_escapes_every_dynamic_value_and_keeps_unknowns_explicit(tmp_path) -> None:
    store = RunManifestStore(tmp_path / "runs.sqlite3")
    context = _context("run-html")
    store.begin_run(context, scenario_name='<script>alert("x")</script>')
    manifest = store.get("run-html")
    assert manifest is not None

    html = render_html_report(manifest)
    assert '<script>alert("x")</script>' not in html
    assert "&lt;script&gt;alert(&quot;x&quot;)&lt;/script&gt;" in html
    assert "未上报" in html


def test_run_archive_routes_export_json_markdown_and_safe_html(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("AMOS_RUN_DB", str(tmp_path / "api-runs.sqlite3"))
    runtime = PlatformRuntime()
    store = runtime.get_run_manifest_store()
    store.begin_run(_context("run-api"), scenario_name="验收场景")
    store.record_lifecycle("run-api", "stopped", alerts=[])

    from amos_platform.api.routes import run_routes

    monkeypatch.setattr(run_routes, "get_runtime", lambda: runtime)
    app = create_app()
    app.testing = True
    client = app.test_client()

    listing = client.get("/api/v1/runs")
    detail = client.get("/api/v1/runs/run-api")
    json_report = client.get("/api/v1/runs/run-api/report?format=json")
    markdown_report = client.get("/api/v1/runs/run-api/report?format=markdown")
    html_report = client.get("/api/v1/runs/run-api/report?format=html")

    assert listing.status_code == 200
    assert listing.get_json()["data"]["runs"][0]["run_id"] == "run-api"
    assert detail.get_json()["data"]["lifecycle"]["final_status"] == "stopped"
    assert json_report.get_json()["schema_version"] == RUN_MANIFEST_SCHEMA_VERSION
    assert 'filename="amos-run-api-report.json"' in json_report.headers["Content-Disposition"]
    assert markdown_report.mimetype == "text/markdown"
    assert "# AMOS 运行验收报告：run-api" in markdown_report.get_data(as_text=True)
    assert html_report.mimetype == "text/html"
    assert html_report.headers["X-Content-Type-Options"] == "nosniff"
    assert "frame-ancestors 'none'" in html_report.headers["Content-Security-Policy"]
    assert client.get("/api/v1/runs/missing").status_code == 404
    assert client.get("/api/v1/runs/run-api/report?format=pdf").status_code == 400
    assert client.get("/api/v1/runs?limit=101").status_code == 400


def test_runtime_director_submission_and_view_update_one_manifest(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("AMOS_RUN_DB", str(tmp_path / "integrated.sqlite3"))
    runtime = PlatformRuntime()
    engine = runtime.get_engine()
    engine.stop()
    context = runtime.begin_run("maritime-convoy-air-defense", seed=812)
    scenario = get_scenario("maritime-convoy-air-defense")
    assert scenario is not None
    engine.load_scenario(scenario, seed=812)
    engine.clock.update({"run_id": context.run_id, "scenario_id": context.scenario_id})
    engine.start()
    engine.stop()

    manifest = runtime.get_run_manifest_store().get(context.run_id)
    assert manifest is not None
    assert manifest["lifecycle"]["final_status"] == "stopped"
    assert manifest["alerts"][-1]["msg"] == "仿真已停止"

    completion_context = runtime.begin_run("maritime-convoy-air-defense", seed=812)
    short_scenario = deepcopy(scenario)
    short_scenario["demo_controls"]["duration_sec"] = 1
    engine.load_scenario(short_scenario, seed=812)
    engine.clock.update({
        "run_id": completion_context.run_id,
        "scenario_id": completion_context.scenario_id,
        "running": True,
        "lifecycle": "running",
    })
    engine._tick(1)
    completed = runtime.get_run_manifest_store().get(completion_context.run_id)
    assert completed is not None
    assert completed["lifecycle"]["final_status"] == "completed"
    assert completed["alerts"][-1]["type"] == "scenario_complete"

    director = runtime.get_director()
    state = director.configure(
        scenario_id="maritime-convoy-air-defense",
        mode="demonstration",
        branch="standard",
        seed=813,
    )
    state = director.action("step_tick", step_sec=1)
    directed = runtime.get_run_manifest_store().get(state["run_id"])
    assert directed is not None
    assert directed["mode"] == "demonstration"
    assert [item["action"] for item in directed["director"]["actions"]][-1] == "step_tick"


def test_workflow_routes_archive_frozen_submission_and_v2_view(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("AMOS_RUN_DB", str(tmp_path / "workflow.sqlite3"))
    get_workflow_run_store().clear()

    from amos_platform.api.routes import a2a_routes
    from amos_platform.runtime.platform_runtime import get_platform_runtime

    class ArchiveBackend:
        mode = "commander"

        def build_workflow_payload(self, _scenario, _support, engine, *, options):
            run_id = str(engine.clock["run_id"])
            mission = {
                "simulation_time_sec": float(engine.clock.get("elapsed_sec", 0)),
                "metadata": {"run_id": run_id, "snapshot_sequence": 1},
                "objective": "分析当前接触",
                "contacts": [],
            }
            return {
                "workflow": "bpel",
                "task_goal": mission["objective"],
                "attachments": [{"id": "MISSION", "meta": {"amos_mission": mission}}],
            }

        def build_backend_submission(self, _mission, *, engine, overrides):
            return {
                "run_id": engine.clock["run_id"],
                "chain_id": "archive-chain",
                "workflow": overrides.get("workflow", "bpel"),
            }

        def submit_workflow(self, _payload):
            return {"workflow_id": "wf-archive-route", "status": "queued"}

        def get_workflow(self, workflow_id):
            return {"workflow_id": workflow_id, "status": "running"}

        def get_work_list(self, _workflow_id):
            return {"work_list": [{
                "activity_id": "A-1",
                "work_item": "track_threat",
                "role": "track_threat",
                "status": "running",
                "agent": "ThreatAssessmentAgent",
            }]}

        def get_workflow_trace(self, _workflow_id):
            return {"trace": []}

    backend = ArchiveBackend()
    monkeypatch.setattr(a2a_routes, "get_bridge", lambda: backend)
    runtime = get_platform_runtime()
    monkeypatch.setattr(runtime, "_bridge", backend)
    runtime._run_manifest_store = None

    app = create_app()
    app.testing = True
    client = app.test_client()
    reset = client.post("/api/v1/sim/reset", json={
        "scenario_id": "maritime-convoy-air-defense",
        "seed": 814,
    }).get_json()["data"]
    submitted = client.post("/api/v1/a2a/workflows/submit", json={
        "scenario_id": "maritime-convoy-air-defense",
        "sim_context": True,
    })
    viewed = client.get("/api/v1/a2a/workflows/wf-archive-route/view")

    assert submitted.status_code == 200
    assert viewed.status_code == 200
    manifest = runtime.get_run_manifest_store().get(reset["run_id"])
    assert manifest is not None
    assert manifest["workflow_ids"] == ["wf-archive-route"]
    assert manifest["submissions"][0]["snapshot"]["run_id"] == reset["run_id"]
    assert manifest["workflow_views"][0]["schema_version"] == "amos.workflow-view.v2"
    assert manifest["workflow_views"][0]["agents"] == viewed.get_json()["data"]["agents"]
    assert manifest["workflow_views"][0]["algorithms"] == viewed.get_json()["data"]["algorithms"]
    assert manifest["workflow_views"][0]["function_points"] == viewed.get_json()["data"]["function_points"]
    assert manifest["workflow_views"][0]["metrics"] == viewed.get_json()["data"]["metrics"]
    assert manifest["workflow_views"][0]["provenance"] == viewed.get_json()["data"]["provenance"]

    get_workflow_run_store().clear()
    backend.get_workflow = lambda workflow_id: {
        "workflow_id": workflow_id,
        "run_id": reset["run_id"],
        "status": "running",
    }
    restored = client.get("/api/v1/a2a/workflows/wf-archive-route/view").get_json()["data"]
    assert restored["submission"]["run_id"] == reset["run_id"]
    assert restored["provenance"]["run_id"] == reset["run_id"]
