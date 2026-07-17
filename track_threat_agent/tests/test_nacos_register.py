from app.nacos_register import NacosRegistrar, NacosSettings


def test_nacos_metadata_exposes_ready_and_metrics_endpoints(monkeypatch):
    monkeypatch.setenv("SERVICE_IP", "127.0.0.1")
    monkeypatch.setenv("SERVICE_PORT", "8102")

    settings = NacosSettings.from_env()

    assert settings.service_name == "A2A-Agent"
    assert settings.metadata["role"] == "track_threat"
    assert settings.metadata["ready_endpoint"] == "http://127.0.0.1:8102/ready"
    assert settings.metadata["metrics_endpoint"] == "http://127.0.0.1:8102/metrics"


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
