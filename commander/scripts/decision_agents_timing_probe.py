#!/usr/bin/env python3
from __future__ import annotations

import argparse
import asyncio
import contextlib
import io
import json
import os
import socket
import statistics
import sys
import tempfile
import time
from pathlib import Path

import anyio
import httpx
import uvicorn

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
from decision_support.config import get_settings  # noqa: E402
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
    parser.add_argument("--mode", choices=["local", "remote", "throughput"], default="local")
    parser.add_argument(
        "--request-timeout",
        type=float,
        default=float(os.environ.get("A2A_REQUEST_TIMEOUT", "120")),
        help="HTTP timeout for remote Agent and Commander calls.",
    )
    parser.add_argument("--requests", type=int, default=30, help="Requests per agent in throughput mode.")
    parser.add_argument("--concurrency", type=int, default=8, help="Maximum concurrent requests in throughput mode.")
    parser.add_argument("--payload-bytes", type=int, default=1024, help="Serialized A2A request size in throughput mode.")
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


def throughput_payload(role: str, suffix: str, payload_bytes: int) -> tuple[dict, int]:
    """Build a small valid task body and pad it to the requested wire size."""
    definition = AGENT_DEFINITIONS[role]
    workflow_id = f"throughput-{role}"
    if role == "decision_planning":
        agent_request = {
            "request_id": f"{workflow_id}-{suffix}",
            "scheduled_tasks": [
                {"id": "TASK-001", "target_id": "TGT-001", "task_type": "monitor"}
            ],
            "resources": [
                {"id": "RES-001", "type": "sensor", "status": "available"}
            ],
        }
    else:
        agent_request = {
            "request_id": f"{workflow_id}-{suffix}",
            "candidate_plans": [
                {
                    "id": "PLAN-001",
                    "name": "Continue monitoring",
                    "actions": ["monitor and report"],
                    "score": 50.0,
                }
            ],
            "authorization": {
                "status": "pending_review",
                "scope": ["simulation-only decision-support"],
            },
            "constraints": ["simulation-only decision-support"],
        }
    payload = {
        "schema_version": "1.0",
        "workflow_id": workflow_id,
        "work_item": f"{workflow_id}:{suffix}",
        "command": definition["command"],
        "required_skill": definition["skill_id"],
        "required_skills": [definition["skill_id"]],
        "input": {"agent_request": agent_request, "benchmark_padding": ""},
        "output_hint": definition["output_hint"],
        "work_list": [],
    }
    encoded_size = len(json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8"))
    if encoded_size > payload_bytes:
        raise ValueError(
            f"minimal {role} request is already {encoded_size} bytes; "
            f"increase --payload-bytes above that size"
        )
    payload["input"]["benchmark_padding"] = "x" * (payload_bytes - encoded_size)
    actual_size = len(json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8"))
    return payload, actual_size


async def measure_local_agent_throughput(
    role: str,
    agent_class,
    name: str,
    port: int,
    *,
    request_count: int,
    concurrency: int,
    payload_bytes: int,
):
    state_root = PROJECT_ROOT / ".a2a_state"
    state_root.mkdir(parents=True, exist_ok=True)
    state_dir = tempfile.TemporaryDirectory(
        prefix=".throughput-", dir=state_root
    )
    agent = DecisionAlgorithmA2AAgent(
        algorithm_agent=agent_class(),
        name=name,
        description=f"Throughput probe for {role}",
        role=role,
        port=port,
        idempotency_db_path=str(Path(state_dir.name) / "idempotency.db"),
        max_concurrent_tasks=concurrency,
    )
    listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    listener.bind(("127.0.0.1", 0))
    listener.listen(128)
    port = listener.getsockname()[1]
    server = uvicorn.Server(
        uvicorn.Config(
            agent.app,
            host="127.0.0.1",
            port=port,
            log_level="critical",
            access_log=False,
            lifespan="off",
        )
    )
    server_task = asyncio.create_task(server.serve(sockets=[listener]))
    try:
        for _ in range(500):
            if server.started:
                break
            if server_task.done():
                await server_task
                raise RuntimeError(f"{role} benchmark server stopped before startup")
            await asyncio.sleep(0.01)
        else:
            raise TimeoutError(f"{role} benchmark server did not start")

        async with httpx.AsyncClient(base_url=f"http://127.0.0.1:{port}") as client:
            warmup_payload, _ = throughput_payload(role, "warmup", payload_bytes)
            warmup = await client.post(
                "/sendMessage",
                json=warmup_payload,
                headers={"Authorization": "Bearer timing-token"},
            )
            warmup.raise_for_status()
            if warmup.json().get("status") != "completed":
                raise RuntimeError(f"{role} warm-up failed: {warmup.json()}")

            semaphore = asyncio.Semaphore(concurrency)
            samples = []

            async def send(index: int):
                payload, actual_size = throughput_payload(role, f"send-{index}-{time.time_ns()}", payload_bytes)
                async with semaphore:
                    started = time.perf_counter()
                    response = await client.post(
                        "/sendMessage",
                        json=payload,
                        headers={"Authorization": "Bearer timing-token"},
                    )
                    elapsed_ms = (time.perf_counter() - started) * 1000.0
                response.raise_for_status()
                body = response.json()
                samples.append({
                    "elapsed_ms": elapsed_ms,
                    "agent_latency_ms": (body.get("metrics") or {}).get("latency_ms"),
                    "idempotency_write_ms": (body.get("metrics") or {}).get("idempotency_write_ms"),
                    "response_bytes": len(response.content),
                    "payload_bytes": actual_size,
                    "status": body.get("status"),
                    "error_code": body.get("error_code"),
                })

            started = time.perf_counter()
            await asyncio.gather(*(send(index) for index in range(request_count)))
            elapsed_seconds = time.perf_counter() - started
    finally:
        server.should_exit = True
        await server_task
        listener.close()
        state_dir.cleanup()

    successful = [sample for sample in samples if sample["status"] == "completed"]
    latencies = sorted(sample["elapsed_ms"] for sample in successful)
    agent_latencies = sorted(
        float(sample["agent_latency_ms"])
        for sample in successful
        if sample["agent_latency_ms"] is not None
    )
    idempotency_latencies = sorted(
        float(sample["idempotency_write_ms"])
        for sample in successful
        if sample["idempotency_write_ms"] is not None
    )
    if not successful:
        raise RuntimeError(f"{role} throughput probe had no successful requests: {samples}")
    p95_index = min(len(latencies) - 1, int(0.95 * len(latencies)))
    return {
        "role": role,
        "phase": "local_send_message_throughput",
        "backend": get_settings().decision_agent_backend,
        "rag_backend": get_settings().rag_backend,
        "requests": request_count,
        "completed": len(successful),
        "failed": len(samples) - len(successful),
        "concurrency": concurrency,
        "payload_bytes": payload_bytes,
        "actual_payload_bytes_min": min(sample["payload_bytes"] for sample in samples),
        "actual_payload_bytes_max": max(sample["payload_bytes"] for sample in samples),
        "elapsed_seconds": round(elapsed_seconds, 4),
        "throughput_msg_s": round(len(successful) / elapsed_seconds, 3),
        "latency_p50_ms": round(statistics.median(latencies), 3),
        "latency_p95_ms": round(latencies[p95_index], 3),
        "latency_max_ms": round(max(latencies), 3),
        "agent_latency_p50_ms": round(statistics.median(agent_latencies), 3) if agent_latencies else None,
        "idempotency_write_p50_ms": round(statistics.median(idempotency_latencies), 3) if idempotency_latencies else None,
        "response_bytes_p50": int(statistics.median(sample["response_bytes"] for sample in successful)),
        "error_codes": sorted({sample["error_code"] for sample in samples if sample["error_code"]}),
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
    if args.requests < 1 or args.concurrency < 1 or args.payload_bytes < 1:
        raise SystemExit("--requests, --concurrency, and --payload-bytes must all be positive")
    rows = []
    if args.mode == "local":
        for role, definition in LOCAL_AGENTS.items():
            rows.append(await measure_local_agent(role, *definition))
        rows.append(measure_local_workflow())
    elif args.mode == "throughput":
        for role, definition in LOCAL_AGENTS.items():
            rows.append(await measure_local_agent_throughput(
                role,
                *definition[:3],
                request_count=args.requests,
                concurrency=args.concurrency,
                payload_bytes=args.payload_bytes,
            ))
    else:
        rows.extend(measure_remote_discovery(args.request_timeout))

    if args.json:
        print(json.dumps(rows, ensure_ascii=False, indent=2))
        return
    for row in rows:
        print(json.dumps(row, ensure_ascii=False))


if __name__ == "__main__":
    anyio.run(main)
