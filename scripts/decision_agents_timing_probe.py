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
from commander_agent.main import CommanderAgent  # noqa: E402
from decision_agents.a2a_adapter import DecisionAlgorithmA2AAgent  # noqa: E402
from decision_agents.agents import (  # noqa: E402
    ComplianceAuthorizationAgent,
    DecisionPlanningAgent,
    TrackThreatAgent,
)
from registry.nacos_manager import NacosRegistry  # noqa: E402


LOCAL_AGENTS = {
    "track_threat": (TrackThreatAgent, "Track_Threat_Agent", 10201, "track_threat_input.json"),
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
    parser.add_argument("--json", action="store_true", help="Print machine-readable JSON")
    return parser.parse_args()


def now_ms():
    return time.perf_counter() * 1000.0


def sample_request(sample_name: str) -> dict:
    return json.loads((PROJECT_ROOT / "data" / "samples" / sample_name).read_text())


async def measure_local_agent(role: str, agent_class, name: str, port: int, sample_name: str):
    agent = DecisionAlgorithmA2AAgent(
        algorithm_agent=agent_class(),
        name=name,
        description=f"Timing probe for {role}",
        role=role,
        port=port,
    )
    payload = {
        "workflow_id": f"timing-{role}",
        "work_item": f"timing-{role}:send",
        "command": role,
        "input": {"agent_request": sample_request(sample_name)},
        "work_list": [],
    }
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


def measure_remote_discovery():
    registry = NacosRegistry()
    rows = []
    try:
        for role in LOCAL_AGENTS:
            started = now_ms()
            instances = registry.discover_service("A2A-Agent", {"role": role, "status": "idle"})
            elapsed = now_ms() - started
            rows.append(
                {
                    "role": role,
                    "phase": "nacos_discovery",
                    "elapsed_ms": round(elapsed, 3),
                    "instance_count": len(instances),
                }
            )
            if instances:
                target = instances[0]
                client = A2AClient(target["ip"], target["port"])
                payload = {
                    "workflow_id": f"timing-{role}",
                    "work_item": f"timing-{role}:remote-send",
                    "command": role,
                    "input": {
                        "agent_request": sample_request(LOCAL_AGENTS[role][3]),
                    },
                    "work_list": [],
                }
                started = now_ms()
                client.discover()
                client.authenticate()
                response = client.send_message(payload)
                elapsed = now_ms() - started
                rows.append(
                    {
                        "role": role,
                        "phase": "remote_discover_auth_send",
                        "elapsed_ms": round(elapsed, 3),
                        "status": response.get("status"),
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
        rows.extend(measure_remote_discovery())

    if args.json:
        print(json.dumps(rows, ensure_ascii=False, indent=2))
        return
    for row in rows:
        print(json.dumps(row, ensure_ascii=False))


if __name__ == "__main__":
    anyio.run(main)
