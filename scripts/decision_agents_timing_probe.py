#!/usr/bin/env python3
from __future__ import annotations

import argparse
import contextlib
import io
import json
import os
import sys
import tempfile
import time
from pathlib import Path

import anyio
import httpx

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from a2a_protocol.client import A2AClient  # noqa: E402
from a2a_sdk import SchedulerSDK  # noqa: E402
from commander_agent.main import CommanderAgent  # noqa: E402
from decision_agents.common.a2a_adapter import DecisionAlgorithmA2AAgent  # noqa: E402
from decision_agents.compliance_authorization.agent import ComplianceAuthorizationAgent  # noqa: E402
from decision_agents.common.definitions import AGENT_DEFINITIONS  # noqa: E402
from decision_agents.decision_planning.agent import DecisionPlanningAgent  # noqa: E402
from registry.nacos_manager import NacosRegistry  # noqa: E402


LOCAL_AGENTS = {
    "decision_planning": (
        DecisionPlanningAgent,
        "Decision_Planning_Agent",
        10202,
        "decision_planning_input.json",
    ),
    "compliance_authorization": (
        ComplianceAuthorizationAgent,
        "Compliance_Authorization_Agent",
        10203,
        "compliance_authorization_input.json",
    ),
}


def parse_args():
    parser = argparse.ArgumentParser(description="Measure Project 613 decision-agent A2A timings")
    parser.add_argument("--mode", choices=["local", "remote"], default="local")
    parser.add_argument(
        "--request-timeout",
        type=float,
        default=float(os.environ.get("A2A_REQUEST_TIMEOUT", "120")),
        help="HTTP timeout for remote Agent and Commander calls.",
    )
    parser.add_argument("--json", action="store_true", help="Print machine-readable JSON")
    return parser.parse_args()


def now_ms():
    return time.perf_counter() * 1000.0


def sample_request(sample_name: str) -> dict:
    return json.loads((PROJECT_ROOT / "data" / "samples" / sample_name).read_text())


def task_payload(role: str, sample_name: str, suffix: str) -> dict:
    definition = AGENT_DEFINITIONS[role]
    workflow_id = f"timing-{role}"
    return {
        "schema_version": "1.0",
        "workflow_id": workflow_id,
        "work_item": f"{workflow_id}:{suffix}",
        "command": definition["command"],
        "required_skill": definition["skill_id"],
        "required_skills": [definition["skill_id"]],
        "input": {"agent_request": sample_request(sample_name)},
        "output_hint": definition["output_hint"],
        "work_list": [],
    }


async def measure_local_agent(role: str, agent_class, name: str, port: int, sample_name: str):
    agent = DecisionAlgorithmA2AAgent(
        algorithm_agent=agent_class(),
        name=name,
        description=f"Timing probe for {role}",
        role=role,
        port=port,
    )
    payload = task_payload(role, sample_name, f"send-{time.time_ns()}")
    transport = httpx.ASGITransport(app=agent.app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        started = now_ms()
        response = await client.post(
            "/sendMessage",
            json=payload,
            headers={"Authorization": "Bearer timing-token"},
        )
        elapsed = now_ms() - started
    response.raise_for_status()
    body = response.json()
    return {
        "role": role,
        "phase": "local_send_message",
        "elapsed_ms": round(elapsed, 3),
        "status": body.get("status"),
        "selected_algorithms": body.get("selected_algorithms", []),
    }


def measure_local_workflow():
    with tempfile.TemporaryDirectory() as state_dir:
        with contextlib.redirect_stdout(io.StringIO()):
            commander = CommanderAgent(
                mode="local",
                workflow="bpel",
                workflow_file="DecisionSupportWorkflow",
                workflow_id="timing-decision-support",
                state_dir=state_dir,
                initial_context=sample_request("decision_planning_input.json"),
            )
            started = now_ms()
            context = commander.run_bpel_workflow()
            elapsed = now_ms() - started
    return {
        "phase": "local_decision_support_bpel",
        "elapsed_ms": round(elapsed, 3),
        "workflow_status": context.get("workflow_status"),
        "compliance_decision": context.get("compliance_decision"),
    }


def measure_remote_discovery(request_timeout: float):
    registry = NacosRegistry()
    scheduler = SchedulerSDK(registry=registry)
    rows = []
    try:
        for role in LOCAL_AGENTS:
            definition = AGENT_DEFINITIONS[role]
            started = now_ms()
            instances = scheduler.discover_agents(
                role=role,
                required_skill=definition["skill_id"],
            )
            elapsed = now_ms() - started
            rows.append(
                {
                    "role": role,
                    "phase": "nacos_discovery",
                    "elapsed_ms": round(elapsed, 3),
                    "instance_count": len(instances),
                }
            )
            if not instances:
                raise RuntimeError(
                    f"No idle {role} Agent advertises skill {definition['skill_id']}"
                )
            target = instances[0]
            metadata = target.get("metadata", {}) or {}
            required_metadata = {
                "active_tasks",
                "max_concurrent_tasks",
                "available_task_slots",
                "task_execution_status",
            }
            missing_metadata = sorted(required_metadata - set(metadata))
            if missing_metadata:
                raise RuntimeError(f"{role} metadata is missing: {missing_metadata}")

            client = A2AClient(
                target["ip"],
                target["port"],
                timeout=request_timeout,
            )
            payload = task_payload(
                role,
                LOCAL_AGENTS[role][3],
                f"remote-send-{time.time_ns()}",
            )
            started = now_ms()
            card = client.discover()
            advertised_ids = {skill.get("id") for skill in card.get("skills", [])}
            if definition["skill_id"] not in advertised_ids:
                raise RuntimeError(
                    f"{role} Agent Card is missing skill {definition['skill_id']}"
                )
            client.authenticate()
            response = client.send_message(payload)
            elapsed = now_ms() - started
            output_hint_present = definition["output_hint"] in (
                response.get("output") or {}
            )
            if response.get("status") != "completed" or not output_hint_present:
                raise RuntimeError(f"{role} Direct call failed: {response}")
            rows.append(
                {
                    "role": role,
                    "phase": "remote_discover_auth_send",
                    "elapsed_ms": round(elapsed, 3),
                    "status": response.get("status"),
                    "output_hint_present": output_hint_present,
                }
            )

        with tempfile.TemporaryDirectory() as state_dir:
            workflow_id = f"timing-direct-{time.time_ns()}"
            commander = CommanderAgent(
                mode="remote",
                workflow="bpel",
                workflow_file="DecisionSupportWorkflow",
                workflow_id=workflow_id,
                state_dir=state_dir,
                initial_context=sample_request("decision_planning_input.json"),
                request_timeout=request_timeout,
            )
            started = now_ms()
            try:
                context = commander.run_bpel_workflow()
                remaining_leases = commander.lease_manager.list_leases()
            finally:
                commander.lease_manager.close()
                commander.registry.close()
            elapsed = now_ms() - started
            if context.get("workflow_status") != "completed":
                raise RuntimeError(
                    f"Remote DecisionSupportWorkflow failed: {context.get('last_error')}"
                )
            if not context.get("decision_planning_result"):
                raise RuntimeError("Remote workflow did not produce decision_planning_result")
            if not context.get("compliance_authorization_result"):
                raise RuntimeError(
                    "Remote workflow did not produce compliance_authorization_result"
                )
            if remaining_leases:
                raise RuntimeError(f"Remote workflow left active leases: {remaining_leases}")
            rows.append(
                {
                    "phase": "remote_decision_support_bpel",
                    "elapsed_ms": round(elapsed, 3),
                    "workflow_status": context.get("workflow_status"),
                    "compliance_decision": context.get("compliance_decision"),
                    "remaining_leases": len(remaining_leases),
                }
            )
    finally:
        registry.close()
    return rows


async def main():
    args = parse_args()
    rows = []
    if args.mode == "local":
        for role, definition in LOCAL_AGENTS.items():
            rows.append(await measure_local_agent(role, *definition))
        rows.append(measure_local_workflow())
    else:
        rows.extend(measure_remote_discovery(args.request_timeout))

    if args.json:
        print(json.dumps(rows, ensure_ascii=False, indent=2))
        return
    for row in rows:
        print(json.dumps(row, ensure_ascii=False))


if __name__ == "__main__":
    anyio.run(main)
