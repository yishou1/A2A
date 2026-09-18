from __future__ import annotations

import json
from pathlib import Path
import socket
import threading
import time

import pytest
from fastapi.testclient import TestClient
import uvicorn

from a2a_protocol.client import A2AClient
from cooperative_execution_agent.agent import CooperativeExecutionAgent
from cooperative_execution_agent.state import ExecutionStateError
from distributed_coordination.execution_dispatch import prepare_distributed_execution
from resource_monitor import ResourceMonitor
from scripts.start_cooperative_execution_agents import load_config


def resource_sample() -> dict:
    return {
        "node_online": True,
        "system": {"cpu_percent": 18.0, "memory_percent": 32.0, "disk_percent": 20.0},
        "process": {"pid": 123, "memory_rss_bytes": 4096},
        "gpu": {"available": False},
        "energy": {"available": False},
        "network": {
            "available": True,
            "bandwidth_mbps": 12.0,
            "link_stability": 0.99,
            "link_up": True,
        },
    }


class DirectExecutionClient:
    def __init__(self, agent: CooperativeExecutionAgent):
        self.agent = agent

    def exchange_execution_coordination(self, message: dict) -> dict:
        return self.agent.execution_coordination(message)

    def send_message(self, payload: dict) -> dict:
        output, message = self.agent.execute_task(payload)
        return {"status": "completed", "output": output, "message": message}


class ExecutionRegistry:
    def __init__(self, agents: dict[str, CooperativeExecutionAgent]):
        self.agents = agents

    def discover_service(self, service_name: str, required_tags=None):
        status = (required_tags or {}).get("status")
        if service_name != "A2A-Agent" or status != "idle":
            return []
        return [
            {
                "ip": "127.0.0.1",
                "port": agent.port,
                "metadata": {
                    "agent_id": agent_id,
                    "role": "cooperative_execution",
                    "status": "idle",
                },
            }
            for agent_id, agent in sorted(self.agents.items())
        ]


def build_agent(
    tmp_path: Path,
    agent_id: str,
    port: int,
    peers=None,
    sources="TRAINING-RANGE",
    max_concurrent_tasks=1,
):
    if peers is None:
        peers = {}
    return CooperativeExecutionAgent(
        agent_id=agent_id,
        port=port,
        state_db=tmp_path / f"{agent_id}.sqlite",
        idempotency_db_path=str(tmp_path / f"{agent_id}.idempotency.sqlite"),
        resource_types=["training_execution"],
        capabilities=["cooperative_execution"],
        allowed_event_sources=sources,
        resource_monitor=ResourceMonitor(sampler=resource_sample),
        max_concurrent_tasks=max_concurrent_tasks,
        execution_client_factory=lambda peer: DirectExecutionClient(peers[peer["agent_id"]]),
    )


def authorization(decision="approved", authorization_id="AUTH-001") -> dict:
    return {
        "decision": decision,
        "authorization_id": authorization_id,
        "expires_at": "2099-01-01T00:00:00Z",
    }


def assignment(agent_id: str, slot: int) -> dict:
    return {
        "coordination_id": "COOP-001",
        "task_id": "TASK-001",
        "slot_id": f"TASK-001#slot-{slot:03d}",
        "winner_agent_id": agent_id,
        "assigned_resources": [agent_id],
        "assignment_source": "deterministic_cbba",
        "assignment_locked": True,
    }


def command(name: str, arguments: dict) -> dict:
    return {"command": name, "input": arguments}


def free_tcp_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as listener:
        listener.bind(("127.0.0.1", 0))
        return int(listener.getsockname()[1])


def authenticated_http_client(target: dict) -> A2AClient:
    client = A2AClient(target.get("ip"), target.get("port"), timeout=3)
    client.discover()
    client.jwt_token = "cooperative-execution-integration-test"
    return client


def reserve(agent: CooperativeExecutionAgent, slot: int) -> dict:
    output, _ = agent.execute_task(
        command(
            "reserve_cooperative_slot",
            {
                "authorization": authorization(),
                "assignment": assignment(agent.agent_id, slot),
                "participant_ids": ["EXEC-A", "EXEC-B"],
                "planned_start_at": "2098-12-31T23:00:00Z",
            },
        )
    )
    return output["cooperative_execution_result"]


def prepare(agent: CooperativeExecutionAgent, slot: int) -> dict:
    output, _ = agent.execute_task(
        command(
            "prepare_cooperative_slot",
            {
                "authorization": authorization(),
                "coordination_id": "COOP-001",
                "slot_id": f"TASK-001#slot-{slot:03d}",
            },
        )
    )
    return output["cooperative_execution_result"]


def test_rejects_unapproved_or_unassigned_reservation(tmp_path: Path) -> None:
    agent = build_agent(tmp_path, "EXEC-A", 18121)
    payload = {
        "authorization": authorization("pending_review"),
        "assignment": assignment("EXEC-A", 1),
    }
    with pytest.raises(ExecutionStateError, match="approved authorization"):
        agent.execute_task(command("reserve_cooperative_slot", payload))

    payload["authorization"] = authorization()
    payload["assignment"] = assignment("EXEC-B", 1)
    with pytest.raises(ExecutionStateError, match="not assigned"):
        agent.execute_task(command("reserve_cooperative_slot", payload))


def test_prepare_requires_the_same_authorization_used_for_reservation(
    tmp_path: Path,
) -> None:
    agent = build_agent(tmp_path, "EXEC-A", 18121)
    reserve(agent, 1)
    with pytest.raises(ExecutionStateError, match="does not match"):
        agent.execute_task(
            command(
                "prepare_cooperative_slot",
                {
                    "authorization": authorization(authorization_id="AUTH-OTHER"),
                    "coordination_id": "COOP-001",
                    "slot_id": "TASK-001#slot-001",
                },
            )
        )


def test_persisted_session_capacity_blocks_and_then_releases_a_slot(
    tmp_path: Path,
) -> None:
    agent = build_agent(tmp_path, "EXEC-A", 18121, max_concurrent_tasks=1)
    reserve(agent, 1)

    resource_state = agent._coordination_resource_state()
    assert resource_state["active_tasks"] == 1
    assert resource_state["available_task_slots"] == 0
    assert resource_state["available"] is False
    assert agent.heartbeat_metadata()["status"] == "busy"

    with pytest.raises(ExecutionStateError, match="no available task slots"):
        reserve(agent, 2)

    agent.execute_task(
        command(
            "abort_cooperative_slot",
            {
                "authorization": authorization(),
                "coordination_id": "COOP-001",
                "slot_id": "TASK-001#slot-001",
                "reason": "operator cancelled before execution",
            },
        )
    )
    assert reserve(agent, 2)["session"]["status"] == "reserved"


def test_two_independent_agents_exchange_readiness_and_synchronize(tmp_path: Path) -> None:
    peers = {}
    first = build_agent(tmp_path, "EXEC-A", 18121, peers)
    second = build_agent(tmp_path, "EXEC-B", 18122, peers)
    peers.update({"EXEC-A": first, "EXEC-B": second})

    assert reserve(first, 1)["session"]["status"] == "reserved"
    assert reserve(second, 2)["session"]["status"] == "reserved"
    assert prepare(first, 1)["session"]["status"] == "ready"
    assert prepare(second, 2)["session"]["status"] == "ready"

    first_sync = first.execution_coordination(
        {
            "message_type": "synchronize",
            "coordination_id": "COOP-001",
            "local_slot_id": "TASK-001#slot-001",
            "peers": [{"agent_id": "EXEC-B", "ip": "127.0.0.1", "port": 18122}],
        }
    )
    second_sync = second.execution_coordination(
        {
            "message_type": "synchronize",
            "coordination_id": "COOP-001",
            "local_slot_id": "TASK-001#slot-002",
            "peers": [{"agent_id": "EXEC-A", "ip": "127.0.0.1", "port": 18121}],
        }
    )

    assert first_sync["synchronized"] is True
    assert second_sync["synchronized"] is True
    assert first_sync["peer_results"] == [
        {"agent_id": "EXEC-B", "received": True, "ready": True}
    ]
    assert first.execution_store.events("COOP-001", "TASK-001#slot-001") == []
    assert first_sync["session"]["status"] == "synchronized"


def test_missing_peer_prevents_synchronization(tmp_path: Path) -> None:
    agent = build_agent(tmp_path, "EXEC-A", 18121)
    reserve(agent, 1)
    prepare(agent, 1)
    result = agent.execution_coordination(
        {
            "message_type": "synchronize",
            "coordination_id": "COOP-001",
            "local_slot_id": "TASK-001#slot-001",
            "peers": [],
        }
    )
    assert result["all_ready"] is False
    assert result["synchronized"] is False
    assert result["session"]["status"] == "ready"


def test_rejected_external_event_is_not_written_before_state_transition(
    tmp_path: Path,
) -> None:
    agent = build_agent(tmp_path, "EXEC-A", 18121)
    reserve(agent, 1)
    prepare(agent, 1)
    arguments = {
        "authorization": authorization(),
        "coordination_id": "COOP-001",
        "slot_id": "TASK-001#slot-001",
        "event": {
            "event_id": "EVENT-TOO-EARLY",
            "event_type": "started",
            "source": "TRAINING-RANGE",
            "observed_at": "2098-12-31T23:00:01Z",
            "evidence": {"uri": "evidence://event/early", "sha256": "d" * 64},
        },
    }
    with pytest.raises(ExecutionStateError, match="ready -> executing"):
        agent.execute_task(command("record_execution_event", arguments))
    assert agent.execution_store.events("COOP-001", "TASK-001#slot-001") == []


def test_completion_requires_allowlisted_external_evidence(tmp_path: Path) -> None:
    peers = {}
    first = build_agent(tmp_path, "EXEC-A", 18121, peers)
    second = build_agent(tmp_path, "EXEC-B", 18122, peers)
    peers.update({"EXEC-A": first, "EXEC-B": second})
    reserve(first, 1)
    reserve(second, 2)
    prepare(first, 1)
    prepare(second, 2)
    first.execution_coordination(
        {
            "message_type": "synchronize",
            "coordination_id": "COOP-001",
            "local_slot_id": "TASK-001#slot-001",
            "peers": [{"agent_id": "EXEC-B"}],
        }
    )
    base = {
        "authorization": authorization(),
        "coordination_id": "COOP-001",
        "slot_id": "TASK-001#slot-001",
        "event": {
            "event_id": "EVENT-START-001",
            "event_type": "started",
            "source": "UNTRUSTED",
            "observed_at": "2098-12-31T23:00:01Z",
            "evidence": {"uri": "evidence://event/1", "sha256": "a" * 64},
        },
    }
    with pytest.raises(ExecutionStateError, match="not allowlisted"):
        first.execute_task(command("record_execution_event", base))

    base["event"]["source"] = "TRAINING-RANGE"
    output, _ = first.execute_task(command("record_execution_event", base))
    assert output["cooperative_execution_result"]["session"]["status"] == "executing"

    base["event"] = {
        "event_id": "EVENT-COMPLETE-001",
        "event_type": "completed",
        "source": "TRAINING-RANGE",
        "observed_at": "2098-12-31T23:00:10Z",
        "evidence": {"uri": "evidence://event/2", "sha256": "b" * 64},
    }
    output, _ = first.execute_task(command("record_execution_event", base))
    result = output["cooperative_execution_result"]
    assert result["session"]["status"] == "completed"
    assert result["fabricated_effects"] is False
    assert len(result["events"]) == 2


def test_external_events_are_idempotent(tmp_path: Path) -> None:
    peers = {}
    first = build_agent(tmp_path, "EXEC-A", 18121, peers)
    second = build_agent(tmp_path, "EXEC-B", 18122, peers)
    peers.update({"EXEC-A": first, "EXEC-B": second})
    reserve(first, 1)
    reserve(second, 2)
    prepare(first, 1)
    prepare(second, 2)
    first.execution_coordination(
        {
            "message_type": "synchronize",
            "coordination_id": "COOP-001",
            "local_slot_id": "TASK-001#slot-001",
            "peers": [{"agent_id": "EXEC-B"}],
        }
    )
    arguments = {
        "authorization": authorization(),
        "coordination_id": "COOP-001",
        "slot_id": "TASK-001#slot-001",
        "event": {
            "event_id": "EVENT-ONCE",
            "event_type": "started",
            "source": "TRAINING-RANGE",
            "observed_at": "2098-12-31T23:00:01Z",
            "evidence": {"uri": "evidence://event/once", "sha256": "c" * 64},
        },
    }
    first.execute_task(command("record_execution_event", arguments))
    output, _ = first.execute_task(command("record_execution_event", arguments))
    assert output["cooperative_execution_result"]["event_duplicate"] is True
    assert len(first.execution_store.events("COOP-001", "TASK-001#slot-001")) == 1


def test_agent_card_and_heartbeat_publish_real_runtime_boundary(tmp_path: Path) -> None:
    agent = build_agent(tmp_path, "EXEC-A", 18121)
    card = agent.get_agent_card()
    metadata = agent.heartbeat_metadata()
    assert card["executionCoordinationEndpoint"] == "/execution/coordination"
    assert card["actuatorConnected"] is False
    assert metadata["agent_id"] == "EXEC-A"
    assert metadata["resource_cpu_percent"] == 18.0
    assert metadata["resource_memory_percent"] == 32.0
    assert metadata["actuator_connected"] == "false"

    client = TestClient(agent.app)
    assert client.post("/execution/coordination", json={}).status_code == 401
    assert client.get("/execution/sessions").status_code == 401


def test_launcher_requires_explicit_unique_agent_configuration(tmp_path: Path) -> None:
    valid = tmp_path / "agents.json"
    valid.write_text(
        json.dumps(
            {
                "agents": [
                    {
                        "agent_id": "EXEC-A",
                        "port": 18121,
                        "resource_types": ["training_execution"],
                        "capabilities": ["cooperative_execution"],
                    },
                    {
                        "agent_id": "EXEC-B",
                        "port": 18122,
                        "resource_types": ["training_execution"],
                        "capabilities": ["cooperative_execution"],
                    },
                ]
            }
        ),
        encoding="utf-8",
    )
    assert [row["agent_id"] for row in load_config(valid)] == ["EXEC-A", "EXEC-B"]

    invalid = tmp_path / "duplicate.json"
    invalid.write_text(
        json.dumps(
            {
                "agents": [
                    {
                        "agent_id": "EXEC-A",
                        "port": 18121,
                        "resource_types": ["training_execution"],
                        "capabilities": ["cooperative_execution"],
                    },
                    {
                        "agent_id": "EXEC-A",
                        "port": 18122,
                        "resource_types": ["training_execution"],
                        "capabilities": ["cooperative_execution"],
                    },
                ]
            }
        ),
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="duplicate"):
        load_config(invalid)


def test_dispatcher_uses_locked_cbba_assignments_and_stops_before_actuation(
    tmp_path: Path,
) -> None:
    peers = {}
    first = build_agent(tmp_path, "EXEC-A", 18121, peers)
    second = build_agent(tmp_path, "EXEC-B", 18122, peers)
    peers.update({"EXEC-A": first, "EXEC-B": second})
    registry = ExecutionRegistry(peers)

    result = prepare_distributed_execution(
        {
            "output_data": {
                "authorization": {
                    "decision": "approved",
                    "approved_for_demo_handoff": True,
                    "execution_blocked": False,
                },
                "commands": [
                    {
                        "command_id": "CMD-001",
                        "task_id": "TASK-001",
                        "assigned_resources": ["EXEC-A", "EXEC-B"],
                        "assignment_slot_ids": [
                            "TASK-001#slot-001",
                            "TASK-001#slot-002",
                        ],
                        "assignment_source": "deterministic_cbba",
                        "assignment_locked": True,
                        "coordination_id": "COOP-001",
                    }
                ],
            }
        },
        workflow_id="WF-001",
        registry=registry,
        planned_start_at="2098-12-31T23:00:00Z",
        client_factory=lambda target: DirectExecutionClient(
            peers[target["metadata"]["agent_id"]]
        ),
    )

    assert result["participant_ids"] == ["EXEC-A", "EXEC-B"]
    assert result["ready_for_external_execution"] is True
    assert result["execution_dispatched"] is False
    assert result["actuator_connected"] is False
    assert all(row["synchronized"] for row in result["synchronization"])
    assert first.execution_store.events("COOP-001", "TASK-001#slot-001") == []
    assert second.execution_store.events("COOP-001", "TASK-001#slot-002") == []


def test_two_agent_http_chain_reaches_readiness_consensus_without_actuation(
    tmp_path: Path,
) -> None:
    ports = [free_tcp_port(), free_tcp_port()]
    first = build_agent(tmp_path, "EXEC-A", ports[0])
    second = build_agent(tmp_path, "EXEC-B", ports[1])
    first.execution_client_factory = authenticated_http_client
    second.execution_client_factory = authenticated_http_client
    agents = {"EXEC-A": first, "EXEC-B": second}
    registry = ExecutionRegistry(agents)
    servers = [
        uvicorn.Server(
            uvicorn.Config(agent.app, host="127.0.0.1", port=agent.port, log_level="error")
        )
        for agent in agents.values()
    ]
    threads = [threading.Thread(target=server.run, daemon=True) for server in servers]
    try:
        for thread in threads:
            thread.start()
        deadline = time.monotonic() + 5
        while not all(server.started for server in servers):
            if time.monotonic() >= deadline:
                raise RuntimeError("test execution Agents did not start")
            time.sleep(0.02)

        result = prepare_distributed_execution(
            {
                "output_data": {
                    "authorization": {
                        "decision": "approved",
                        "execution_blocked": False,
                    },
                    "commands": [
                        {
                            "command_id": "CMD-HTTP-001",
                            "task_id": "TASK-001",
                            "assigned_resources": ["EXEC-A", "EXEC-B"],
                            "assignment_slot_ids": [
                                "TASK-001#slot-001",
                                "TASK-001#slot-002",
                            ],
                            "assignment_source": "deterministic_cbba",
                            "assignment_locked": True,
                            "coordination_id": "COOP-001",
                        }
                    ],
                }
            },
            workflow_id="WF-HTTP-001",
            registry=registry,
            planned_start_at="2098-12-31T23:00:00Z",
            client_factory=authenticated_http_client,
        )

        assert result["ready_for_external_execution"] is True
        assert result["execution_dispatched"] is False
        assert result["actuator_connected"] is False
        assert first.execution_store.get(
            "COOP-001", "TASK-001#slot-001"
        )["status"] == "synchronized"
        assert second.execution_store.get(
            "COOP-001", "TASK-001#slot-002"
        )["status"] == "synchronized"
    finally:
        for server in servers:
            server.should_exit = True
        for thread in threads:
            thread.join(timeout=5)


def test_dispatcher_fails_closed_when_assigned_agent_is_not_registered(
    tmp_path: Path,
) -> None:
    first = build_agent(tmp_path, "EXEC-A", 18121)
    registry = ExecutionRegistry({"EXEC-A": first})
    with pytest.raises(RuntimeError, match="EXEC-B"):
        prepare_distributed_execution(
            {
                "output_data": {
                    "authorization": {
                        "decision": "approved",
                        "execution_blocked": False,
                    },
                    "commands": [
                        {
                            "command_id": "CMD-001",
                            "task_id": "TASK-001",
                            "assigned_resources": ["EXEC-A", "EXEC-B"],
                            "assignment_slot_ids": [
                                "TASK-001#slot-001",
                                "TASK-001#slot-002",
                            ],
                            "assignment_source": "deterministic_cbba",
                            "assignment_locked": True,
                            "coordination_id": "COOP-001",
                        }
                    ],
                }
            },
            workflow_id="WF-001",
            registry=registry,
            planned_start_at="2098-12-31T23:00:00Z",
            client_factory=lambda target: DirectExecutionClient(first),
        )


def test_dispatcher_rejects_assignment_that_is_not_consensus_locked(
    tmp_path: Path,
) -> None:
    first = build_agent(tmp_path, "EXEC-A", 18121)
    registry = ExecutionRegistry({"EXEC-A": first})
    with pytest.raises(ValueError, match="consensus-locked"):
        prepare_distributed_execution(
            {
                "output_data": {
                    "authorization": {"decision": "approved", "execution_blocked": False},
                    "commands": [
                        {
                            "command_id": "CMD-001",
                            "task_id": "TASK-001",
                            "assigned_resources": ["EXEC-A"],
                            "assignment_slot_ids": ["TASK-001#slot-001"],
                            "assignment_source": "deterministic_cbba",
                            "assignment_locked": False,
                            "coordination_id": "COOP-001",
                        }
                    ],
                }
            },
            workflow_id="WF-001",
            registry=registry,
            planned_start_at="2098-12-31T23:00:00Z",
        )


def test_dispatcher_synchronizes_every_slot_when_one_agent_has_multiple_slots(
    tmp_path: Path,
) -> None:
    first = build_agent(tmp_path, "EXEC-A", 18121, max_concurrent_tasks=2)
    registry = ExecutionRegistry({"EXEC-A": first})
    result = prepare_distributed_execution(
        {
            "output_data": {
                "authorization": {"decision": "approved", "execution_blocked": False},
                "commands": [
                    {
                        "command_id": "CMD-001",
                        "task_id": "TASK-001",
                        "assigned_resources": ["EXEC-A", "EXEC-A"],
                        "assignment_slot_ids": [
                            "TASK-001#slot-001",
                            "TASK-001#slot-002",
                        ],
                        "assignment_source": "deterministic_cbba",
                        "assignment_locked": True,
                        "coordination_id": "COOP-001",
                    }
                ],
            }
        },
        workflow_id="WF-001",
        registry=registry,
        planned_start_at="2098-12-31T23:00:00Z",
        client_factory=lambda target: DirectExecutionClient(first),
    )
    assert len(result["synchronization"]) == 2
    assert {
        row["session"]["slot_id"] for row in result["synchronization"]
    } == {"TASK-001#slot-001", "TASK-001#slot-002"}
