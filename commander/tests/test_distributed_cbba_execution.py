from __future__ import annotations

from copy import deepcopy

from a2a_protocol.server import A2ABaseAgent
from decision_support.planning import generate_candidate_plans
from decision_support.schemas import AgentRequest
from distributed_coordination.integration import coordinate_task_schedule
from distributed_coordination.orchestrator import A2ACBBAOrchestrator
from execution_control_agent.execution_control_core import run_execution_control
from resource_monitor import ResourceMonitor
from services.a2a_algorithms_common.distributed_cbba import (
    CBBAParticipant,
    apply_assignments_to_tasks,
    converge_participants,
    run_local_cbba_consensus,
)


def _agents() -> list[dict]:
    return [
        {
            "agent_id": agent_id,
            "available": True,
            "status": "available",
            "resource_types": ["strike"],
            "capabilities": ["target_assignment", "engage"],
            "readiness": 0.9,
            "available_task_slots": 1,
            "max_concurrent_tasks": 1,
        }
        for agent_id in ("AGENT-A", "AGENT-B", "AGENT-C")
    ]


def _tasks() -> list[dict]:
    return [
        {
            "id": "TASK-1",
            "target_id": "TARGET-1",
            "priority": 1,
            "required_resource_types": ["strike"],
            "required_capabilities": ["engage"],
        },
        {
            "id": "TASK-2",
            "target_id": "TARGET-2",
            "priority": 2,
            "required_resource_types": ["strike"],
            "required_capabilities": ["engage"],
        },
    ]


def test_three_agents_compete_for_two_tasks_without_duplicate_winners() -> None:
    result = run_local_cbba_consensus(_tasks(), _agents(), coordination_id="CBBA-TWO")

    assert result["consensus_reached"] is True
    assert len(result["assignments"]) == 2
    assert len({row["slot_id"] for row in result["assignments"]}) == 2
    assert len({row["winner_agent_id"] for row in result["assignments"]}) == 2


def test_conflicting_initial_local_winners_converge_to_one_winner() -> None:
    task = [_tasks()[0]]
    participants = [
        CBBAParticipant(agent["agent_id"], resource_state=agent, tasks=task)
        for agent in _agents()[:2]
    ]
    for participant in participants:
        participant.build_bundle()
    assert {
        participant.winner_table["TASK-1#slot-001"]["winner_agent_id"]
        for participant in participants
    } == {"AGENT-A", "AGENT-B"}

    result = converge_participants(participants, tasks=task, coordination_id="CBBA-CONFLICT")

    assert result["consensus_reached"] is True
    assert result["assignments"] == [
        {
            "task_id": "TASK-1",
            "slot_id": "TASK-1#slot-001",
            "winner_agent_id": "AGENT-A",
            "bid": 158.0,
        }
    ]


def test_agent_missing_required_resources_cannot_win() -> None:
    agents = _agents()[:2]
    agents[0]["resource_types"] = ["sensor"]
    agents[0]["capabilities"] = ["observe"]
    result = run_local_cbba_consensus([_tasks()[0]], agents)

    assert result["assignments"][0]["winner_agent_id"] == "AGENT-B"
    first_state = next(
        state for state in result["participant_states"] if state["agent_id"] == "AGENT-A"
    )
    assert first_state["candidate_bundle"] == []
    assert first_state["local_bids"] == {}


def test_unavailable_agent_releases_unfinished_task_for_reallocation() -> None:
    task = [_tasks()[0]]
    participants = [
        CBBAParticipant(agent["agent_id"], resource_state=agent, tasks=task)
        for agent in _agents()[:2]
    ]
    initial = converge_participants(participants, tasks=task)
    assert initial["assignments"][0]["winner_agent_id"] == "AGENT-A"

    failed_state = deepcopy(_agents()[0])
    failed_state["available"] = False
    failed_state["status"] = "failed"
    participants[0].update_resource_state(failed_state)
    reassigned = converge_participants(
        participants,
        tasks=task,
        coordination_id="CBBA-RECOVERY",
    )

    assert reassigned["consensus_reached"] is True
    assert reassigned["assignments"][0]["winner_agent_id"] == "AGENT-B"


def test_three_slot_parallel_task_uses_three_distinct_agents() -> None:
    task = {
        **_tasks()[0],
        "id": "TASK-PARALLEL",
        "required_slots": 3,
        "distinct_agents": True,
    }
    result = run_local_cbba_consensus([task], _agents())

    assert result["consensus_reached"] is True
    assert len(result["assignments"]) == 3
    assert len({row["winner_agent_id"] for row in result["assignments"]}) == 3
    assert {row["slot_id"] for row in result["assignments"]} == {
        "TASK-PARALLEL#slot-001",
        "TASK-PARALLEL#slot-002",
        "TASK-PARALLEL#slot-003",
    }


def test_same_input_is_deterministic() -> None:
    outputs = [run_local_cbba_consensus(_tasks(), _agents()) for _ in range(5)]
    assert all(output["assignments"] == outputs[0]["assignments"] for output in outputs)
    assert all(output["winner_table"] == outputs[0]["winner_table"] for output in outputs)


def _execution_results(tasks: list[dict], decision: str) -> dict:
    return {
        "perception_detection": {"output_data": {"detections": [{"conf": 0.92}]}},
        "threat_evaluation": {
            "output_data": {
                "priority_score": 0.85,
                "ranked_targets": [{"target_id": "TARGET-1", "score": 0.85}],
            }
        },
        "resource_allocation": {
            "output_data": {"readiness": 0.9, "scheduled_tasks": tasks}
        },
        "communication": {"output_data": {"delivery_rate": 0.95}},
        "plan_decision": {
            "output_data": {
                "decision": "STRIKE",
                "recommended_plan": {
                    "id": "PLAN-CBBA",
                    "target_ids": ["TARGET-1"],
                    "actions": ["engage"],
                },
            }
        },
        "compliance_authorization": {
            "output_data": {
                "decision": decision,
                "approved_for_demo_handoff": decision == "approved",
            }
        },
    }


def test_cbba_assignment_cannot_bypass_existing_authorization_and_execution() -> None:
    allocation = run_local_cbba_consensus([_tasks()[0]], _agents())
    scheduled = apply_assignments_to_tasks([_tasks()[0]], allocation)

    blocked = run_execution_control(
        {"phase": "strike", "results": _execution_results(scheduled, "pending_review")}
    )
    assert blocked["output_data"]["commands"] == []
    assert blocked["output_data"]["authorization"]["execution_blocked"] is True
    assert allocation["execution_dispatched"] is False

    approved = run_execution_control(
        {"phase": "strike", "results": _execution_results(scheduled, "approved")}
    )
    command = approved["output_data"]["commands"][0]
    assert command["assigned_resources"] == ["AGENT-A"]
    assert command["assignment_source"] == "deterministic_cbba"
    assert approved["output_data"]["authorization"]["execution_blocked"] is False


def test_local_commander_adapter_writes_locked_assignments_for_planning() -> None:
    schedule = {
        "mission_id": "WF-23A",
        "scheduled_tasks": _tasks(),
        "resources": [
            {
                "id": agent["agent_id"],
                "type": "strike",
                "status": "available",
                "capacity": agent["readiness"],
                "attributes": {
                    "capabilities": agent["capabilities"],
                    "task_slots": 1,
                },
            }
            for agent in _agents()
        ],
    }
    context = {
        "mission_input": {
            "cooperative_execution": {"enabled": True, "max_rounds": 8}
        }
    }
    result = coordinate_task_schedule(schedule, context, mode="local")

    assert result["distributed_allocation"]["consensus_reached"] is True
    assert all(task["assignment_locked"] for task in result["scheduled_tasks"])
    request = AgentRequest(
        scheduled_tasks=result["scheduled_tasks"],
        resources=result["resources"],
        risk_assessments=[
            {
                "target_id": "TARGET-1",
                "priority": 1,
                "risk": "high",
                "threat_score": 85,
                "probability": 0.85,
                "rationale": "test",
            }
        ],
        distributed_allocation=result["distributed_allocation"],
    )
    plans = generate_candidate_plans(request)
    locked = {
        resource
        for task in result["scheduled_tasks"]
        for resource in task["assigned_resources"]
    }
    assert locked
    assert locked.issubset(set(plans[0].assigned_resources))


class _InMemoryClient:
    def __init__(self, target: A2ABaseAgent):
        self.target = target

    def exchange_coordination(self, message: dict) -> dict:
        return self.target.handle_coordination_message(message)


def test_agents_exchange_cbba_tables_over_a2a_coordination_messages(tmp_path) -> None:
    network: dict[str, A2ABaseAgent] = {}

    def stable_resource_sample() -> dict:
        return {
            "node_online": True,
            "system": {"cpu_percent": 20.0, "memory_percent": 30.0},
            "process": {},
            "gpu": {"available": False},
            "energy": {"available": False},
            "network": {"available": True, "link_up": True},
        }

    def client_factory(peer: dict) -> _InMemoryClient:
        return _InMemoryClient(network[str(peer["agent_id"])])

    for index, agent_id in enumerate(("AGENT-A", "AGENT-B"), start=1):
        network[agent_id] = A2ABaseAgent(
            name=agent_id,
            description="CBBA execution participant",
            role="artillery",
            port=9100 + index,
            max_concurrent_tasks=1,
            idempotency_db_path=str(tmp_path / f"{agent_id}.db"),
            resource_monitor=ResourceMonitor(sampler=stable_resource_sample),
            coordination_client_factory=client_factory,
        )
    descriptors = [
        {"agent_id": agent_id, "ip": "memory", "port": agent.port}
        for agent_id, agent in network.items()
    ]
    result = A2ACBBAOrchestrator(
        client_factory=lambda peer: _InMemoryClient(network[str(peer["agent_id"])])
    ).coordinate(
        [
            {
                "id": "TASK-A2A",
                "priority": 1,
                "required_resource_types": ["strike"],
                "required_capabilities": ["target_assignment"],
            }
        ],
        descriptors,
        coordination_id="CBBA-A2A",
    )

    assert result["consensus_reached"] is True
    assert result["transport"] == "a2a_agent_to_agent"
    assert result["assignments"][0]["winner_agent_id"] == "AGENT-A"
    assert all(
        state["local_task_view"]
        and "candidate_bundle" in state
        and "resource_state" in state
        and "winner_table" in state
        for state in result["participant_states"]
    )
    assert network["AGENT-A"].get_agent_card()["coordinationEndpoint"] == "/coordination/cbba"
    assert network["AGENT-A"].metrics_snapshot()["tasks_received"] == 0
