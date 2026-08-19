from __future__ import annotations

from types import SimpleNamespace

from amos_platform.agents.a2a.workflow_run_store import get_workflow_run_store
from amos_platform.agents.a2a.workflow_view import (
    VIEW_SCHEMA_VERSION,
    build_submission_snapshot,
    build_workflow_view,
)
from amos_platform.api.app_factory import create_app


def sample_payload() -> dict:
    mission = {
        "simulation_time_sec": 108,
        "metadata": {"run_id": "run-1", "snapshot_sequence": 12, "causal_cutoff_sec": 108},
        "objective": "identify current contacts",
        "contacts": [
            {"contact_id": "TRK-1", "metadata": {"geo": {"lat": 9.5, "lon": 112.9}}},
            {"contact_id": "TRK-2", "metadata": {"geo": {"lat": 9.6, "lon": 113.0}}},
        ],
        "observations": [{"observation_id": "OBS-1"}],
        "perception_frames": [{"task_id": "FRAME-1"}],
        "friendly_platforms": [{"id": "BLUE-1"}],
        "protected_assets": [{"asset_id": "SITE-1"}],
        "evidence": [{"media_id": "MEDIA-1"}],
        "environment": {"jamming_level": 0.2},
    }
    return {
        "workflow": "bpel",
        "workflow_file": "integrated_system/workflows/integrated_demo_workflow.bpel",
        "task_goal": mission["objective"],
        "attachments": [{
            "id": "MEDIA-1",
            "name": "current radar",
            "kind": "radar",
            "mime_type": "image/svg+xml",
            "uri": "http://amos/static/media.svg",
            "checksum": {"algorithm": "sha256", "value": "abc123"},
            "meta": {
                "amos_mission": mission,
                "sensor_id": "SITE-1/RADAR",
                "modality": "radar",
                "captured_at_sim_time": 105,
            },
        }],
    }


def test_submission_snapshot_freezes_exact_commander_input() -> None:
    snapshot = build_submission_snapshot(sample_payload(), scenario_id="SCN-1", accepted=True)

    assert snapshot["simulation_time_sec"] == 108
    assert snapshot["run_id"] == "run-1"
    assert snapshot["snapshot_sequence"] == 12
    assert snapshot["counts"] == {
        "attachments": 1,
        "contacts": 2,
        "observations": 1,
        "perception_frames": 1,
        "events": 0,
        "friendly_platforms": 1,
        "protected_assets": 1,
        "evidence": 1,
    }
    assert snapshot["attachments"][0]["id"] == "MEDIA-1"
    assert snapshot["attachments"][0]["captured_at_sim_time"] == 105
    assert snapshot["contacts"][0]["contact_id"] == "TRK-1"


def test_workflow_view_uses_real_work_list_progress_and_structured_results() -> None:
    status = {
        "workflow_id": "wf-1",
        "status": "running",
        "result": {
            "summary": {"completed": 1, "running": 1},
            "warnings": ["partial output"],
            "activity_results": [{
                "activity_id": "A-1",
                "work_item": "tracking",
                "role": "track_threat",
                "status": "completed",
                "agent": "ThreatAssessmentAgent",
                "metrics": {"duration_ms": 32},
                "output": {"artifact": {"tracks": [{"id": "T-1"}], "track_count": 1}},
            }],
        },
    }
    work = {"work_list": [
        {"activity_id": "A-1", "work_item": "tracking", "status": "completed"},
        {"activity_id": "A-2", "work_item": "assessment", "status": "running"},
    ]}
    view = build_workflow_view(
        status,
        work_list=work,
        trace={"trace": [{"event": "agent_call_completed", "role": "track_threat"}]},
        submission={"scenario_id": "SCN-1"},
    )

    assert view["schema_version"] == VIEW_SCHEMA_VERSION
    assert view["progress_pct"] == 50
    assert view["orchestration"]["counts"] == {"total": 2, "completed": 1, "running": 1, "failed": 0}
    assert view["result"]["cards"][0]["facts"] == [{"key": "track_count", "value": 1}, {"key": "tracks_count", "value": 1}]
    assert view["result"]["warnings"] == ["partial output"]
    assert view["result"]["cards"][0]["execution_mode"] == "unspecified"


def test_workflow_view_hides_bpel_sequence_container_from_agent_activities() -> None:
    status = {
        "workflow_id": "wf-sequence",
        "status": "completed",
        "result": {"activity_results": [
            {"activity_id": "activatity-1-sequence", "type": "sequence", "status": "completed"},
            {"activity_id": "A-1", "role": "tracking", "agent": "TrackThreatAgent", "status": "completed"},
        ]},
    }
    work = {"work_list": [
        {"activity_id": "activatity-1-sequence", "type": "sequence", "status": "completed"},
        {"activity_id": "A-1", "role": "tracking", "agent": "TrackThreatAgent", "status": "completed"},
    ]}

    view = build_workflow_view(status, work_list=work)

    assert view["orchestration"]["counts"] == {"total": 1, "completed": 1, "running": 0, "failed": 0}
    assert [row["activity_id"] for row in view["orchestration"]["activities"]] == ["A-1"]
    assert view["orchestration"]["activities"][0]["index"] == 1
    assert view["agents"]["counts"]["workflow_activity_count"] == 1


def test_paused_workflow_with_failed_activity_is_terminal_for_director() -> None:
    view = build_workflow_view(
        {"workflow_id": "wf-paused-failed", "status": "paused"},
        work_list={
            "work_list": [
                {"activity_id": "A-1", "status": "completed"},
                {"activity_id": "A-2", "status": "failed", "error": "timeout"},
            ]
        },
    )

    assert view["status"] == "paused"
    assert view["terminal"] is True
    assert view["orchestration"]["counts"]["failed"] == 1


def test_workflow_view_v2_separates_agent_roles_instances_and_stubs() -> None:
    status = {
        "workflow_id": "wf-agents",
        "status": "running",
        "result": {"activity_results": [
            {
                "activity_id": "A-1", "role": "track_threat", "status": "completed",
                "agent": "TrackThreatAgent", "instance_id": "track-01",
                "execution_mode": "remote_agent", "metrics": {"duration_ms": 20},
            },
            {
                "activity_id": "A-2", "role": "track_threat", "status": "running",
                "agent": "TrackThreatAgent", "instance_id": "track-01",
                "execution_mode": "remote_agent",
            },
            {
                "activity_id": "A-3", "role": "simulation", "status": "completed",
                "agent": "SimulationExecutionAgent", "instance_id": "sim-stub-01",
                "execution_mode": "simulation_executor",
            },
            {
                "activity_id": "A-4", "role": "assessment", "status": "pending",
                "agent": "AssessmentAgent",
            },
        ]},
    }
    work = {"work_list": [
        {"activity_id": "A-1", "role": "track_threat", "status": "completed"},
        {"activity_id": "A-2", "role": "track_threat", "status": "running"},
        {"activity_id": "A-3", "role": "simulation", "status": "completed"},
        {"activity_id": "A-4", "role": "assessment", "status": "pending"},
    ]}

    view = build_workflow_view(status, work_list=work)

    assert view["schema_version"] == "amos.workflow-view.v2"
    assert view["agents"]["counts"] == {
        "workflow_activity_count": 4,
        "role_count": 3,
        "planned_role_count": 0,
        "instance_count": 3,
        "real_instance_count": 2,
        "functional_agent_count": 0,
    }
    instances = {row["instance_id"]: row for row in view["agents"]["instances"]}
    assert instances["track-01"]["activity_count"] == 2
    assert instances["track-01"]["real_service"] is True
    assert instances["sim-stub-01"]["is_stub"] is True
    assert instances["sim-stub-01"]["real_service"] is False
    assert instances["runtime:assessmentagent"]["agent"] == "AssessmentAgent"
    assert view["agents"]["heartbeat_available"] is False


def test_workflow_view_v2_verifies_only_algorithms_with_success_evidence() -> None:
    submission = {
        "algorithm_coverage": [
            {"algorithm_id": "track-kalman", "category": "tracking", "model_id": "kf-v2"},
            {"algorithm_id": "risk-ranker", "category": "ranking"},
            {"algorithm_id": "planned-only", "category": "planning"},
            {"algorithm_id": "trace-only", "category": "fusion"},
        ],
    }
    status = {
        "workflow_id": "wf-algorithms",
        "status": "running",
        "result": {"activity_results": [
            {
                "activity_id": "A-1", "status": "completed", "agent": "TrackThreatAgent",
                "output": {"selected_algorithms": ["track-kalman"], "track_count": 2},
                "metrics": {"duration_ms": 31},
            },
            {
                "activity_id": "A-2", "status": "failed", "agent": "AssessmentAgent",
                "output": {"algorithm_id": "risk-ranker", "model_version": "1.2"},
            },
        ]},
    }
    work = {"work_list": [
        {"activity_id": "A-1", "status": "completed"},
        {"activity_id": "A-2", "status": "failed"},
    ]}

    trace = {"trace": [{"event": "algorithm_completed", "algorithm_id": "trace-only"}]}
    view = build_workflow_view(status, work_list=work, trace=trace, submission=submission)
    algorithms = {row["algorithm_id"]: row for row in view["algorithms"]["items"]}

    assert algorithms["track-kalman"]["status"] == "verified"
    assert algorithms["track-kalman"]["duration_ms"] == 31
    assert algorithms["track-kalman"]["result_summary"] == [{"key": "track_count", "value": 2}]
    assert algorithms["risk-ranker"]["status"] == "declared"
    assert algorithms["risk-ranker"]["execution_status"] == "failed"
    assert algorithms["planned-only"]["status"] == "declared"
    assert algorithms["trace-only"]["status"] == "verified"
    assert algorithms["planned-only"]["version"] is None
    assert view["algorithms"]["counts"]["verified"] == 2


def test_workflow_view_joins_safe_activity_input_output_calls_algorithms_and_trace() -> None:
    status = {
        "workflow_id": "wf-inspector",
        "status": "running",
        "result": {"activity_results": [{
            "activity_id": "A-1",
            "work_item": "track",
            "role": "track_threat",
            "agent": "TrackThreatAgent",
            "instance_id": "track-01",
            "status": "completed",
            "output": {
                "track_count": 2,
                "algorithm_id": "motr-neural-kalman",
                "model_version": "1.4",
            },
            "metrics": {"duration_ms": 27, "retry_count": 1},
        }]},
    }
    work = {"work_list": [
        {
            "activity_id": "A-1",
            "work_item": "track",
            "status": "completed",
            "input": {"risk_score": 0.72},
            "depends_on": ["A-0"],
        },
        {"activity_id": "A-2", "work_item": "review", "status": "running"},
    ]}
    trace = {"trace": [
        {
            "event": "agent_call_completed",
            "activity_id": "A-1",
            "agent": "TrackThreatAgent",
            "instance_id": "track-01",
            "timestamp": "2026-08-17T01:02:03Z",
        },
        {"event": "workflow_heartbeat", "timestamp": "2026-08-17T01:02:04Z"},
    ]}

    view = build_workflow_view(status, work_list=work, trace=trace)
    detail = view["activity_details"]["A-1"]

    assert detail["input_summary"] == [{"key": "risk_score", "value": 0.72}]
    assert detail["output_summary"] == [{"key": "track_count", "value": 2}]
    assert detail["input_detail"]["risk_score"]["value"] == 0.72
    assert detail["output_detail"]["track_count"]["value"] == 2
    assert detail["agent_call"]["agent"] == "TrackThreatAgent"
    assert detail["agent_call"]["instances"][0]["instance_id"] == "track-01"
    assert detail["algorithms"][0]["algorithm_id"] == "motr-neural-kalman"
    assert detail["trace_refs"] == ["trace:0"]
    assert detail["trace_events"][0]["event"] == "agent_call_completed"
    assert detail["depends_on"] == ["A-0"]
    assert view["activity_details"]["A-2"]["input_summary"] is None
    assert view["activity_details"]["A-2"]["output_summary"] is None
    assert view["activity_details"]["A-2"]["algorithms"] == []


def test_activity_detail_does_not_show_planned_algorithms_as_runtime_calls() -> None:
    submission = {"algorithm_coverage": [{
        "algorithm_id": "declared-tracker",
        "name": "Declared Tracker",
        "assigned_agents": ["A2"],
    }], "functional_agents": [{
        "agent_id": "A2",
        "backend_roles": ["track_threat"],
    }]}
    work = {"work_list": [{
        "activity_id": "A-2",
        "work_item": "review",
        "role": "track_threat",
        "status": "running",
    }]}

    view = build_workflow_view(
        {"workflow_id": "wf-planned-algorithm", "status": "running"},
        work_list=work,
        submission=submission,
    )
    assert view["activity_details"]["A-2"]["algorithms"] == []


def test_workflow_view_infers_local_mode_instances_and_dereferences_outputs() -> None:
    status = {
        "workflow_id": "wf-local-output",
        "mode": "local",
        "status": "completed",
        "result": {
            "outputs": {
                "task_scheduling_result": {
                    "selected_algorithms": ["marl_ppo_task_scheduler"],
                    "algorithm_calls": [{
                        "algorithm_id": "marl_ppo_task_scheduler",
                        "version": "1.0.0",
                        "execution_mode": "algolib_runtime",
                    }],
                    "scheduled_tasks": [{"id": "TASK-001"}],
                    "resources": [{"id": "ESCORT-01"}],
                }
            },
            "activity_results": [{
                "activity_id": "A-SCHED",
                "work_item": "schedule",
                "role": "task_scheduling",
                "status": "completed",
                "agent": "Local_Task_Scheduling_Agent",
                "output_ref": "outputs.task_scheduling_result",
            }],
        },
    }
    work = {"work_list": [{
        "activity_id": "A-SCHED",
        "work_item": "schedule",
        "role": "task_scheduling",
        "status": "completed",
    }]}

    view = build_workflow_view(status, work_list=work)

    activity = view["orchestration"]["activities"][0]
    assert activity["execution_mode"] == "local_agent"
    instances = {row["instance_id"]: row for row in view["agents"]["instances"]}
    assert instances["local:local_task_scheduling_agent"]["execution_mode"] == "local_agent"
    assert view["activity_details"]["A-SCHED"]["output_summary"] == [
        {"key": "scheduled_tasks_count", "value": 1},
        {"key": "resources_count", "value": 1},
    ]
    algorithms = {row["algorithm_id"]: row for row in view["algorithms"]["items"]}
    assert algorithms["marl_ppo_task_scheduler"]["status"] == "verified"
    assert algorithms["marl_ppo_task_scheduler"]["execution_mode"] == "algolib_runtime"


def test_algorithm_call_duration_and_model_id_are_preserved() -> None:
    status = {
        "workflow_id": "wf-algorithm-fields",
        "status": "completed",
        "result": {"activity_results": [{
            "activity_id": "A-TIA",
            "role": "tactical_intelligence",
            "status": "completed",
            "agent": "Local_Tactical_Intelligence_Agent",
            "output": {
                "algorithm_calls": [{
                    "algorithm_id": "battlefield_rtdetr_detector",
                    "algorithm_name": "battlefield_rtdetr_detector",
                    "model_id": "rt-detr-odconv-detector",
                    "version": "1.0.0",
                    "execution_mode": "algolib_runtime",
                    "duration_ms": 18.75,
                    "result_summary": {"detections": 2},
                }],
            },
        }]},
    }
    work = {"work_list": [{
        "activity_id": "A-TIA",
        "role": "tactical_intelligence",
        "status": "completed",
        "agent": "Local_Tactical_Intelligence_Agent",
        "metrics": {"duration_ms": 0.0},
    }]}

    view = build_workflow_view(status, work_list=work)
    algorithm = {
        row["algorithm_id"]: row
        for row in view["activity_details"]["A-TIA"]["algorithms"]
    }["battlefield_rtdetr_detector"]

    assert algorithm["model_id"] == "rt-detr-odconv-detector"
    assert algorithm["duration_ms"] == 18.75
    assert algorithm["result_summary"] == {"detections": 2}
    assert view["orchestration"]["activities"][0]["duration_ms"] == 18.75
    assert view["activity_details"]["A-TIA"]["agent_call"]["duration_ms"] == 18.75


def test_activity_duration_falls_back_to_start_and_finish_times() -> None:
    view = build_workflow_view(
        {
            "workflow_id": "wf-activity-clock",
            "status": "completed",
            "result": {"activity_results": [{
                "activity_id": "A-SCHED",
                "status": "completed",
                "started_at": "2026-08-17T15:32:39.725970+00:00",
                "finished_at": "2026-08-17T15:32:44.247127+00:00",
                "metrics": {"duration_ms": 0.0},
            }]},
        },
        work_list={"work_list": [{
            "activity_id": "A-SCHED",
            "status": "completed",
            "metrics": {"duration_ms": 0.0},
        }]},
    )

    assert view["orchestration"]["activities"][0]["duration_ms"] == 4521.157
    assert view["activity_details"]["A-SCHED"]["agent_call"]["duration_ms"] == 4521.157


def test_activity_detail_does_not_expose_arbitrary_explicit_summary_objects() -> None:
    view = build_workflow_view(
        {"workflow_id": "wf-safe-summary", "status": "running"},
        work_list={"work_list": [{
            "activity_id": "A-1",
            "status": "running",
            "input_summary": [{"key": "count", "value": 2, "private_truth": "hidden"}],
            "output_summary": [{"private_truth": "hidden"}],
        }]},
    )

    assert view["activity_details"]["A-1"]["input_summary"] == [{"key": "count", "value": 2}]
    assert view["activity_details"]["A-1"]["output_summary"] is None


def test_workflow_view_verifies_agent_algorithm_library_used_records_only() -> None:
    status = {
        "workflow_id": "wf-track-library",
        "status": "completed",
        "result": {"activity_results": [{
            "activity_id": "A-TRACK",
            "status": "completed",
            "agent": "TrackThreatAgent",
            "output": {
                "tracks": [{
                    "metadata": {
                        "algorithm_library": {
                            "track_state_updater": {
                                "used": True,
                                "schema_version": "track_state_updater/v1",
                            },
                            "unused_candidate": {"used": False},
                        }
                    }
                }]
            },
        }]},
    }
    work = {"work_list": [{"activity_id": "A-TRACK", "status": "completed"}]}

    view = build_workflow_view(status, work_list=work)
    algorithms = {row["algorithm_id"]: row for row in view["algorithms"]["items"]}

    assert algorithms["track_state_updater"]["status"] == "verified"
    assert algorithms["track_state_updater"]["category"] == "algorithm_library"
    assert "unused_candidate" not in algorithms


def test_workflow_view_v2_uses_explicit_function_mapping_and_dag_dependencies() -> None:
    submission = {"function_point_coverage": [
        {"id": "FP-01", "name": "航迹建立", "activity_ids": ["A-1"]},
        {"id": "FP-02", "name": "风险评估", "activity_ids": ["A-2"]},
        {"id": "FP-03", "name": "方案生成"},
        {"id": "FP-04", "name": "仅由 trace 证明"},
    ]}
    status = {
        "workflow_id": "wf-graph",
        "status": "failed",
        "result": {"summary": {"duration_ms": 80}, "activity_results": [
            {"activity_id": "A-1", "status": "completed", "metrics": {"duration_ms": 30}},
            {"activity_id": "A-2", "status": "failed", "metrics": {"duration_ms": 50}},
            {"activity_id": "A-3", "status": "pending"},
        ]},
    }
    work = {"work_list": [
        {"activity_id": "A-1", "status": "completed", "depends_on": []},
        {"activity_id": "A-2", "status": "failed", "depends_on": ["A-1"]},
        {"activity_id": "A-3", "status": "pending"},
    ]}
    trace = {"trace": [
        {"event": "agent_call_attempt", "activity_id": "A-2", "attempt": 1},
        {"event": "agent_call_attempt", "activity_id": "A-2", "attempt": 2},
        {"event": "agent_call_failed", "activity_id": "A-2"},
        {"event": "function_point_completed", "function_point_id": "FP-04"},
    ]}

    view = build_workflow_view(status, work_list=work, trace=trace, submission=submission)
    points = {row["function_point_id"]: row for row in view["function_points"]["items"]}

    assert {"KC-01", "KC-28", "FP-01", "FP-02", "FP-03", "FP-04"}.issubset(points)
    assert points["FP-01"]["status"] == "verified"
    assert points["FP-02"]["status"] == "failed"
    assert points["FP-03"]["status"] == "declared"
    assert points["FP-04"]["status"] == "verified"
    assert points["FP-04"]["evidence_refs"] == ["trace:3"]
    assert view["execution_graph"]["edges"] == [{"source": "A-1", "target": "A-2"}]
    assert not any(edge["target"] == "A-3" for edge in view["execution_graph"]["edges"])
    assert view["metrics"]["workflow_duration_ms"] == 80
    assert view["metrics"]["activity_duration_total_ms"] == 80
    assert view["metrics"]["average_latency_ms"] == 40
    assert view["metrics"]["retry_count"] == 1
    assert view["metrics"]["alert_count"] == 0


def test_submission_snapshot_carries_declared_coverage_without_promoting_it() -> None:
    payload = sample_payload()
    mission = payload["attachments"][0]["meta"]["amos_mission"]
    mission["required_agents"] = [{"role": "track_threat"}]
    mission["functional_agents"] = [{
        "agent_id": "A3",
        "name": "任务调度与资源分配 Agent",
        "backend_status": "not_provided",
        "backend_roles": ["task_scheduling"],
    }]
    mission["algorithm_coverage"] = [{
        "algorithm_id": "declared-algorithm",
        "requirement_id": "M13",
        "tier": "core",
        "assigned_agents": ["A3"],
    }]
    mission["function_point_coverage"] = [{"id": "FP-01"}]

    submission = build_submission_snapshot(payload, scenario_id="SCN-1", accepted=True)
    view = build_workflow_view({"workflow_id": "wf-empty", "status": "queued"}, submission=submission)

    assert submission["required_agents"] == [{"role": "track_threat"}]
    assert submission["functional_agents"][0]["agent_id"] == "A3"
    assert view["agents"]["counts"]["functional_agent_count"] == 1
    assert "backend_status" not in view["agents"]["functional_agents"][0]
    assert view["agents"]["counts"]["planned_role_count"] == 1
    assert view["agents"]["counts"]["instance_count"] == 0
    assert view["algorithms"]["items"][0]["status"] == "declared"
    assert view["algorithms"]["items"][0]["requirement_id"] == "M13"
    assert view["algorithms"]["items"][0]["tier"] == "core"
    assert view["algorithms"]["counts"]["verified"] == 0
    declared = {row["function_point_id"]: row for row in view["function_points"]["items"]}
    assert declared["FP-01"]["status"] == "declared"


def test_workflow_view_route_joins_commander_endpoints(monkeypatch) -> None:
    from amos_platform.api.routes import a2a_routes

    class FakeCommander:
        def get_workflow(self, workflow_id):
            return {"workflow_id": workflow_id, "status": "running"}

        def get_work_list(self, workflow_id):
            return {"workflow_id": workflow_id, "work_list": [{"activity_id": "A-1", "status": "running"}]}

        def get_workflow_trace(self, workflow_id):
            return {"workflow_id": workflow_id, "trace": [{"event": "agent_call_started"}]}

    fake = FakeCommander()
    runtime = SimpleNamespace(get_workflow_view=lambda workflow_id: build_workflow_view(
        fake.get_workflow(workflow_id),
        work_list=fake.get_work_list(workflow_id),
        trace=fake.get_workflow_trace(workflow_id),
        submission=get_workflow_run_store().get(workflow_id),
    ))
    monkeypatch.setattr(a2a_routes, "get_runtime", lambda: runtime)
    store = get_workflow_run_store()
    store.clear()
    store.put("wf-route", {"scenario_id": "SCN-ROUTE", "simulation_time_sec": 42})

    app = create_app()
    app.testing = True
    response = app.test_client().get("/api/v1/a2a/workflows/wf-route/view")
    data = response.get_json()["data"]

    assert response.status_code == 200
    assert data["schema_version"] == VIEW_SCHEMA_VERSION
    assert data["submission"]["simulation_time_sec"] == 42
    assert data["orchestration"]["activities"][0]["status"] == "running"


def test_submit_route_returns_upstream_http_error_with_rejected_snapshot(monkeypatch) -> None:
    from amos_platform.api.routes import a2a_routes

    class FailingGateway:
        mode = "gateway"

        def build_workflow_payload(self, *_args, **_kwargs):
            return sample_payload()

        def build_backend_submission(self, _mission, *, engine, overrides):
            return {
                "schema_version": "amos.commander.gateway.submit.v1",
                "run_id": engine.clock["run_id"],
                "chain_id": "amphibious-landing-joint-operation:situation-analysis",
                "workflow": overrides.get("workflow", "bpel"),
            }

        def submit_workflow(self, _payload):
            return {
                "error": True,
                "code": 503,
                "detail": {"code": "COMMANDER_UNAVAILABLE", "message": "Commander unavailable"},
            }

    monkeypatch.setattr(a2a_routes, "get_bridge", lambda: FailingGateway())
    app = create_app()
    app.testing = True
    client = app.test_client()
    client.post("/api/v1/sim/reset", json={"scenario_id": "amphibious-landing-joint-operation"})

    response = client.post(
        "/api/v1/a2a/workflows/submit",
        json={"scenario_id": "amphibious-landing-joint-operation", "sim_context": True},
    )
    payload = response.get_json()

    assert response.status_code == 503
    assert payload["error"] == "Commander unavailable"
    assert payload["data"]["amos_submission"]["accepted"] is False
    assert payload["data"]["amos_submission"]["transport"] == "gateway"


def test_transport_failure_is_exposed_as_an_operator_message() -> None:
    from amos_platform.api.routes.a2a_routes import _upstream_error

    status, message = _upstream_error(
        {
            "error": True,
            "code": 503,
            "detail": "<urlopen error [Errno 111] Connection refused>",
        },
        "fallback",
    )

    assert status == 503
    assert message == "分析服务不可达，请检查 Gateway 状态与连接配置"


def test_submit_route_rejects_context_bypass_and_scenario_mismatch() -> None:
    app = create_app()
    app.testing = True
    client = app.test_client()
    client.post("/api/v1/sim/reset", json={"scenario_id": "amphibious-landing-joint-operation"})

    bypass = client.post(
        "/api/v1/a2a/workflows/submit",
        json={"sim_context": False},
    )
    mismatch = client.post(
        "/api/v1/a2a/workflows/submit",
        json={"scenario_id": "different-scenario"},
    )

    assert bypass.status_code == 400
    assert "sim_context=false" in bypass.get_json()["error"]
    assert mismatch.status_code == 409
    assert "active simulation" in mismatch.get_json()["error"]


def test_submit_route_summarizes_verified_gateway_package(monkeypatch) -> None:
    from amos_platform.api.routes import a2a_routes

    package = {
        "run_id": None,
        "chain_id": "amphibious-landing-joint-operation:situation-analysis",
        "snapshot": {
            "run_id": None,
            "sequence": 2,
            "sim_time_ms": 42_000,
            "tracks": [{
                "track_id": "TRK-PKG",
                "lat": 9.5,
                "lon": 112.9,
                "source_observation_ids": ["OBS-PKG"],
            }],
            "observations": [{"observation_id": "OBS-PKG"}],
            "assets": [{"asset_id": "BLUE-PKG"}],
            "recent_events": [],
        },
        "events": [
            {"media_refs": []},
            {"media_refs": [{
                "media_id": "MEDIA-PKG",
                "uri": "/static/pkg.png",
                "mime_type": "image/png",
                "checksum": "abc123",
                "source_name": "PKG-SENSOR",
            }]},
        ],
    }

    class SuccessfulGateway:
        mode = "gateway"

        def build_workflow_payload(self, *_args, **_kwargs):
            return sample_payload()

        def build_backend_submission(self, _mission, *, engine, overrides):
            package["run_id"] = engine.clock["run_id"]
            package["snapshot"]["run_id"] = engine.clock["run_id"]
            return {
                "schema_version": "amos.commander.gateway.submit.v1",
                "run_id": engine.clock["run_id"],
                "chain_id": "amphibious-landing-joint-operation:situation-analysis",
                "workflow": overrides.get("workflow", "bpel"),
            }

        def submit_workflow(self, _payload):
            return {
                "workflow_id": "wf-package-route",
                "status": "queued",
                "package_id": "pkg-route",
                "package_checksum": "f" * 64,
                "event_cursor": 2,
            }

        def get_submission_package(self, package_id, checksum):
            assert package_id == "pkg-route"
            assert checksum == "f" * 64
            return package, True

    monkeypatch.setattr(a2a_routes, "get_bridge", lambda: SuccessfulGateway())
    app = create_app()
    app.testing = True
    client = app.test_client()
    client.post("/api/v1/sim/reset", json={"scenario_id": "amphibious-landing-joint-operation"})

    response = client.post(
        "/api/v1/a2a/workflows/submit",
        json={"scenario_id": "amphibious-landing-joint-operation", "sim_context": True},
    )
    submission = response.get_json()["data"]["amos_submission"]

    assert response.status_code == 200
    assert submission["run_id"] == package["run_id"]
    assert submission["simulation_time_sec"] == 42
    assert submission["counts"]["contacts"] == 1
    assert submission["counts"]["events"] == 2
    assert submission["attachments"][0]["id"] == "MEDIA-PKG"
    assert submission["package"]["verified"] is True


def test_ambiguous_backend_track_is_not_silently_applied() -> None:
    from amos_platform.agents.a2a.commander_projection import apply_commander_assessments
    from amos_platform.fusion.track_fusion import FusedTrack

    first = FusedTrack("LOCAL-1", 9.50, 112.95, "RADAR", domain_hint="air", sim_time=0)
    second = FusedTrack("LOCAL-2", 9.50, 112.97, "RADAR", domain_hint="air", sim_time=0)
    engine = SimpleNamespace(
        sensor_fusion=SimpleNamespace(tracks={first.id: first, second.id: second}),
        events=[],
        scenario_story={},
    )
    workflow = {
        "workflow_id": "wf-ambiguous",
        "status": "completed",
        "result": {"outputs": {"tracking_result": {"artifact": {
            "tracks": [{"track_id": "BACKEND-1", "object_type": "uav", "lat": 9.50, "lon": 112.96}],
            "threats": [{"track_id": "BACKEND-1", "score": 0.9, "level": "high"}],
        }}}},
    }

    projection = apply_commander_assessments(engine, workflow)

    assert projection["applied_count"] == 0
    assert projection["analysis"]["unmatched_output_count"] == 1
    assert projection["analysis"]["associations"][0]["status"] == "ambiguous"
    assert first.agent_assessment["status"] == "pending"
    assert second.agent_assessment["status"] == "pending"


def test_named_threat_output_takes_precedence_over_empty_tracking_threats() -> None:
    from amos_platform.agents.a2a.commander_projection import apply_commander_assessments
    from amos_platform.fusion.track_fusion import FusedTrack

    track = FusedTrack("TRK-AMOS-01", 22.1, 121.6, "RADAR", domain_hint="maritime", sim_time=0)
    engine = SimpleNamespace(
        sensor_fusion=SimpleNamespace(tracks={track.id: track}),
        events=[],
        scenario_story={},
    )
    workflow = {
        "workflow_id": "wf-named-outputs",
        "status": "completed",
        "result": {
            "outputs": {
                "tracking_result": [{
                    "value": {
                        "tracks": [{
                            "track_id": "TRK-AMOS-01",
                            "object_type": "ship",
                            "metadata": {
                                "source_class": "fast_attack_craft",
                                "label": "hostile",
                                "affiliation": "red",
                                "threat_level": "high",
                            },
                            "lat": 22.1,
                            "lon": 121.6,
                        }],
                        "threats": [],
                    },
                }],
                "threat_assessment_result": [{
                    "value": {
                        "threats": [{
                            "track_id": "TRK-AMOS-01",
                            "score": 0.91,
                            "level": "high",
                        }],
                    },
                }],
            },
        },
    }

    projection = apply_commander_assessments(engine, workflow)

    assert projection["applied_count"] == 1
    assert track.agent_assessment["status"] == "confirmed"
    assert track.agent_assessment["backend_track_id"] == "TRK-AMOS-01"
    assert track.classification == "FAST_ATTACK_CRAFT"
    assert track.threat_level == "HIGH"


def test_fix_tracking_semantics_are_applied_without_threat_ranking() -> None:
    from amos_platform.agents.a2a.commander_projection import apply_commander_assessments
    from amos_platform.fusion.track_fusion import FusedTrack

    fishing = FusedTrack(
        "TRK-FISHING", 22.1, 121.4, "RADAR", domain_hint="maritime", sim_time=0
    )
    hostile = FusedTrack(
        "TRK-HOSTILE", 22.2, 121.5, "RADAR", domain_hint="maritime", sim_time=0
    )
    engine = SimpleNamespace(
        sensor_fusion=SimpleNamespace(
            tracks={fishing.id: fishing, hostile.id: hostile}
        ),
        events=[],
        scenario_story={},
    )
    workflow = {
        "workflow_id": "wf-fix",
        "status": "completed",
        "result": {
            "outputs": {
                "tracking_result": {
                    "tracks": [
                        {
                            "track_id": "TRK-FISHING",
                            "object_type": "ship",
                            "track_quality": 0.9,
                            "metadata": {
                                "source_class": "fishing_vessel",
                                "label": "neutral",
                                "affiliation": "unknown",
                                "threat_level": "low",
                            },
                        },
                        {
                            "track_id": "TRK-HOSTILE",
                            "object_type": "ship",
                            "track_quality": 0.95,
                            "metadata": {
                                "source_class": "fast_attack_craft",
                                "label": "hostile",
                                "affiliation": "red",
                                "threat_level": "high",
                            },
                        },
                    ]
                }
            }
        },
    }

    projection = apply_commander_assessments(engine, workflow)

    assert projection["applied_count"] == 2
    assert fishing.classification == "FISHING_VESSEL"
    assert fishing.agent_assessment["status"] == "cleared"
    assert hostile.classification == "FAST_ATTACK_CRAFT"
    assert hostile.agent_assessment["status"] == "confirmed"


def test_protected_fishing_track_stays_low_risk_even_if_ranking_overstates_it() -> None:
    from amos_platform.agents.a2a.commander_projection import apply_commander_assessments
    from amos_platform.fusion.track_fusion import FusedTrack

    fishing = FusedTrack(
        "TRK-FISHING", 22.1, 121.4, "RADAR", domain_hint="maritime", sim_time=0
    )
    engine = SimpleNamespace(
        sensor_fusion=SimpleNamespace(tracks={fishing.id: fishing}),
        events=[],
        scenario_story={},
    )
    workflow = {
        "workflow_id": "wf-fishing-protected",
        "status": "completed",
        "result": {
            "outputs": {
                "tracking_result": {
                    "tracks": [{
                        "track_id": "TRK-FISHING",
                        "object_type": "ship",
                        "metadata": {
                            "source_class": "fishing_vessel",
                            "label": "neutral",
                            "threat_level": "low",
                        },
                    }]
                },
                "threat_assessment_result": {
                    "threats": [{
                        "track_id": "TRK-FISHING",
                        "score": 0.95,
                        "level": "high",
                    }]
                },
            }
        },
    }

    projection = apply_commander_assessments(engine, workflow)

    assert projection["applied_count"] == 1
    assert fishing.classification == "FISHING_VESSEL"
    assert fishing.threat_level == "LOW"
    assert fishing.agent_assessment["status"] == "cleared"
    assert fishing.agent_assessment["level"] == "LOW"


def test_previous_run_result_is_not_applied_to_current_tracks() -> None:
    from amos_platform.agents.a2a.commander_projection import apply_commander_assessments
    from amos_platform.fusion.track_fusion import FusedTrack

    track = FusedTrack("LOCAL-1", 9.5, 112.9, "RADAR", domain_hint="air", sim_time=0)
    engine = SimpleNamespace(
        clock={"run_id": "run-current"},
        sensor_fusion=SimpleNamespace(tracks={track.id: track}),
        events=[],
        scenario_story={},
    )
    workflow = {
        "workflow_id": "wf-old",
        "status": "completed",
        "result": {"artifact": {
            "tracks": [{"track_id": "LOCAL-1", "object_type": "uav", "lat": 9.5, "lon": 112.9}],
            "threats": [{"track_id": "LOCAL-1", "score": 0.9, "level": "high"}],
        }},
    }

    projection = apply_commander_assessments(
        engine,
        workflow,
        submission={"run_id": "run-previous", "contacts": [{"contact_id": "LOCAL-1"}]},
    )

    assert projection["status"] == "stale_run"
    assert projection["applied_count"] == 0
    assert track.agent_assessment["status"] == "pending"
