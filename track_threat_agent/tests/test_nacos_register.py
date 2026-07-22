from app.nacos_register import NacosRegistrar, NacosSettings


def test_nacos_metadata_exposes_ready_and_metrics_endpoints(monkeypatch):
    monkeypatch.setenv("SERVICE_IP", "127.0.0.1")
    monkeypatch.setenv("SERVICE_PORT", "8102")

    settings = NacosSettings.from_env()

    assert settings.service_name == "A2A-Agent"
    assert settings.metadata["role"] == "track_threat"
    assert settings.metadata["ready_endpoint"] == "http://127.0.0.1:8102/ready"
    assert settings.metadata["metrics_endpoint"] == "http://127.0.0.1:8102/metrics"
    assert settings.metadata["state_summary_endpoint"] == "http://127.0.0.1:8102/state/summary"
    assert settings.metadata["input_schema_url"] == "http://127.0.0.1:8102/schema/input"
    assert settings.metadata["output_schema_url"] == "http://127.0.0.1:8102/schema/output"
    assert settings.metadata["capability_version"] == "track_threat_agent_v1"
    assert settings.metadata["model_status"] in {"no_model", "model_loaded"}
    assert settings.metadata["algorithm_profile"] == "kalman_stgnn_dbn_group_asset_xai"
    assert "kg_transformer" not in settings.metadata["algorithm_family"]
    assert "kg_transformer" not in settings.metadata["runtime_providers"]
    assert settings.metadata["object_types"] == "aircraft,ship,uav,unknown"
    assert "tactical_intelligence_result" in settings.metadata["input_message_types"]
    assert "asset_impact" in settings.metadata["ranking_item_types"]
    assert "protected_assets" in settings.metadata["scene_contract"]
    assert settings.metadata["dbn_parameter_schema"] == "dbn_risk_model/v1"
    assert settings.metadata["dbn_parameter_model"] == "dbn-risk-attention-v1"
    assert settings.metadata["group_lifecycle_states"] == "tentative,confirmed,coasting"
    assert "mission_planning" in settings.metadata["downstream_boundary"]
    assert settings.metadata["algorithm_execution_location"] == "agent_process"
    assert settings.metadata["algorithm_library_transport"] == "none"
    assert "track_state_kalman_cv" in settings.metadata["models"]
    assert "trajectory_adaptive_multi_model_physics" in settings.metadata["models_ready"]
    assert "trajectory_imm" not in settings.metadata["models"]
    assert "local_graph_message_passing" not in settings.metadata["models"]
    assert settings.metadata["algorithm_deployment_status"] in {"ready", "partial"}
    assert settings.metadata["max_concurrent_tasks"] == "1"
    assert settings.metadata["active_tasks"] == "0"
    assert settings.metadata["available_task_slots"] == "1"
    assert settings.metadata["task_execution_status"] == "idle"
    assert settings.metadata["quality_success_rate"] == "1.000000"
    assert "resource_cpu_percent" in settings.metadata
    assert "resource_memory_percent" in settings.metadata
    skills = set(settings.metadata["skills"].split(","))
    assert "track_threat_situation_analysis" in skills
    assert "trajectory_prediction" in skills
    assert "threat_ranking" in skills


def test_nacos_metadata_detects_embedded_st_gnn_bundles(monkeypatch):
    monkeypatch.delenv("ST_GNN_MODEL_DIR", raising=False)
    monkeypatch.delenv("ST_GNN_AIRCRAFT_MODEL_DIR", raising=False)
    monkeypatch.delenv("ST_GNN_SHIP_MODEL_DIR", raising=False)

    settings = NacosSettings.from_env()

    assert settings.metadata["st_gnn_aircraft_model_configured"] == "true"
    assert settings.metadata["st_gnn_ship_model_configured"] == "true"


def test_heartbeat_metadata_preserves_commander_busy_lease_state():
    settings = NacosSettings(
        enabled=True,
        service_name="A2A-Agent",
        service_ip="127.0.0.1",
        service_port=8102,
        role="track_threat",
        status="idle",
        metadata={
            "agent_id": "track-threat-group-agent-01",
            "role": "track_threat",
            "status": "idle",
            "heartbeat_ts": "100",
            "heartbeat_at": "old",
        },
    )
    registrar = NacosRegistrar(settings)
    registrar._fetch_current_instance_metadata_http = lambda: {
        "agent_id": "track-threat-group-agent-01",
        "role": "track_threat",
        "status": "busy",
        "lease_workflow_id": "wf-001",
        "lease_work_item": "wf-001:track-threat",
        "heartbeat_ts": "101",
    }

    metadata = registrar._build_heartbeat_metadata()

    assert metadata["status"] == "busy"
    assert metadata["lease_workflow_id"] == "wf-001"
    assert metadata["lease_work_item"] == "wf-001:track-threat"
    assert int(metadata["heartbeat_ts"]) >= 101


def test_heartbeat_metadata_preserves_commander_unavailable_state():
    settings = NacosSettings(
        enabled=True,
        service_name="A2A-Agent",
        service_ip="127.0.0.1",
        service_port=8102,
        role="track_threat",
        status="idle",
        metadata={
            "agent_id": "track-threat-group-agent-01",
            "role": "track_threat",
            "status": "idle",
        },
    )
    registrar = NacosRegistrar(settings)
    registrar._fetch_current_instance_metadata_http = lambda: {
        "agent_id": "track-threat-group-agent-01",
        "role": "track_threat",
        "status": "unavailable",
        "unavailable_reason": "heartbeat lost",
        "unavailable_workflow_id": "wf-002",
    }

    metadata = registrar._build_heartbeat_metadata()

    assert metadata["status"] == "unavailable"
    assert metadata["unavailable_reason"] == "heartbeat lost"
    assert metadata["unavailable_workflow_id"] == "wf-002"


def test_heartbeat_metadata_preserves_commander_circuit_breaker_state():
    settings = NacosSettings(
        enabled=True,
        service_name="A2A-Agent",
        service_ip="127.0.0.1",
        service_port=8102,
        role="track_threat",
        status="idle",
        metadata={
            "agent_id": "track-threat-group-agent-01",
            "role": "track_threat",
            "status": "idle",
            "circuit_state": "closed",
            "circuit_failure_count": "0",
        },
    )
    registrar = NacosRegistrar(settings)
    registrar._fetch_current_instance_metadata_http = lambda: {
        "agent_id": "track-threat-group-agent-01",
        "role": "track_threat",
        "status": "unavailable",
        "circuit_state": "open",
        "circuit_failure_count": "3",
        "circuit_opened_at_ts": "1719000000.0",
        "circuit_open_until_ts": "1719000030.0",
        "unavailable_reason": "agent timeout",
    }

    metadata = registrar._build_heartbeat_metadata()

    assert metadata["status"] == "unavailable"
    assert metadata["circuit_state"] == "open"
    assert metadata["circuit_failure_count"] == "3"
    assert metadata["circuit_opened_at_ts"] == "1719000000.0"
    assert metadata["circuit_open_until_ts"] == "1719000030.0"
    assert metadata["unavailable_reason"] == "agent timeout"


def test_heartbeat_metadata_does_not_replay_stale_busy_lease_after_release():
    settings = NacosSettings(
        enabled=True,
        service_name="A2A-Agent",
        service_ip="127.0.0.1",
        service_port=8102,
        role="track_threat",
        status="busy",
        metadata={
            "agent_id": "track-threat-group-agent-01",
            "role": "track_threat",
            "status": "busy",
            "lease_workflow_id": "wf-stale",
            "lease_work_item": "wf-stale:track-threat",
            "lease_acquired_at": "old",
            "circuit_state": "open",
            "circuit_failure_count": "3",
            "circuit_open_until_ts": "1719000030.0",
        },
    )
    registrar = NacosRegistrar(settings)
    registrar._fetch_current_instance_metadata_http = lambda: {
        "agent_id": "track-threat-group-agent-01",
        "role": "track_threat",
        "status": "idle",
        "circuit_state": "closed",
        "circuit_failure_count": "0",
    }

    metadata = registrar._build_heartbeat_metadata()

    assert metadata["status"] == "idle"
    assert "lease_workflow_id" not in metadata
    assert "lease_work_item" not in metadata
    assert "lease_acquired_at" not in metadata
    assert metadata["circuit_state"] == "closed"
    assert metadata["circuit_failure_count"] == "0"
    assert "circuit_open_until_ts" not in metadata


def test_heartbeat_metadata_preserves_commander_scheduling_decision():
    settings = NacosSettings(
        enabled=True,
        metadata={
            "agent_id": "track-threat-group-agent-01",
            "role": "track_threat",
            "status": "idle",
            "scheduling_score": "old",
        },
    )
    registrar = NacosRegistrar(settings)
    registrar._fetch_current_instance_metadata_http = lambda: {
        "status": "idle",
        "scheduling_score": "82.375",
        "scheduling_reason": "ranked_by_resource_capacity_quality_feedback",
    }

    metadata = registrar._build_heartbeat_metadata()

    assert metadata["scheduling_score"] == "82.375"
    assert metadata["scheduling_reason"] == "ranked_by_resource_capacity_quality_feedback"


def test_agent_status_and_quality_metrics_update_scheduler_capacity():
    settings = NacosSettings.from_env()
    registrar = NacosRegistrar(settings)

    registrar.set_agent_status("busy")
    assert registrar.settings.metadata["active_tasks"] == "1"
    assert registrar.settings.metadata["available_task_slots"] == "0"
    assert registrar.settings.metadata["task_execution_status"] == "saturated"

    registrar.update_runtime_metrics(
        tasks_completed=8,
        tasks_failed=2,
        average_latency_ms=125.5,
        active_tasks=0,
    )
    assert registrar.settings.metadata["quality_tasks_completed"] == "8"
    assert registrar.settings.metadata["quality_tasks_failed"] == "2"
    assert registrar.settings.metadata["quality_success_rate"] == "0.800000"
    assert registrar.settings.metadata["quality_avg_latency_ms"] == "125.500"
    assert registrar.settings.metadata["available_task_slots"] == "1"
