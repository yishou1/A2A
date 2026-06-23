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
