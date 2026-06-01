from __future__ import annotations

import argparse
import os
import shutil
import socket
import subprocess
import sys
import time
from pathlib import Path

import requests


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from commander_agent.agent_leases import AgentLeaseManager  # noqa: E402
from workflow_state_store import WorkflowStateStore  # noqa: E402


DEFAULT_WORKFLOWS = [
    ("demo-manager-beachhead", "beachhead_workflow", 75),
    ("demo-manager-reinforced", "reinforced_beachhead_workflow", 85),
    ("demo-manager-quick", "quick_strike_workflow", 75),
]


class DemoRegistry:
    def __init__(self):
        self.instances = [
            {
                "ip": "10.0.0.10",
                "port": 8013,
                "metadata": {"role": "artillery", "status": "idle"},
            }
        ]

    def discover_service(self, service_name, required_tags=None):
        return [
            instance
            for instance in self.instances
            if all(
                instance["metadata"].get(key) == value
                for key, value in (required_tags or {}).items()
            )
        ]

    def update_instance_metadata(
        self,
        service_name,
        instance,
        metadata_updates=None,
        remove_keys=None,
    ):
        instance["metadata"].update(metadata_updates or {})
        for key in remove_keys or []:
            instance["metadata"].pop(key, None)
        return instance["metadata"]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Demonstrate resident CommanderWorkflowManager concurrency and Agent leases"
    )
    parser.add_argument(
        "--state-dir",
        default="/tmp/a2a-workflow-manager-demo-state",
        help="Directory used to persist demo checkpoints",
    )
    parser.add_argument(
        "--manager-port",
        type=int,
        default=0,
        help="Manager API port; 0 selects an available local port",
    )
    parser.add_argument(
        "--max-workflows",
        type=int,
        default=2,
        help="Maximum number of concurrently running workflows",
    )
    parser.add_argument(
        "--poll-interval",
        type=float,
        default=0.1,
        help="Workflow status polling interval in seconds",
    )
    parser.add_argument(
        "--timeout",
        type=float,
        default=20.0,
        help="Maximum number of seconds to wait for workflow completion",
    )
    parser.add_argument(
        "--details",
        action="store_true",
        help="Show complete Commander logs from the child Manager process",
    )
    return parser.parse_args()


def find_free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def wait_for_health(base_url: str, timeout: float = 10.0) -> dict:
    deadline = time.time() + timeout
    last_error = None
    while time.time() < deadline:
        try:
            response = requests.get(f"{base_url}/health", timeout=1)
            response.raise_for_status()
            return response.json()
        except Exception as exc:
            last_error = exc
            time.sleep(0.1)
    raise TimeoutError(f"Manager health check failed: {last_error}")


def stop_process(process: subprocess.Popen | None) -> None:
    if process is None or process.poll() is not None:
        return
    process.terminate()
    try:
        process.wait(timeout=5)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait(timeout=5)


def demo_agent_lease() -> None:
    print("=== PHASE 1: AGENT LEASE AND RESOURCE LOCK ===")
    registry = DemoRegistry()
    lease_manager = AgentLeaseManager(registry)
    target = registry.instances[0]

    print(f"[INITIAL] artillery {target['ip']}:{target['port']} status={target['metadata']['status']}")
    first = lease_manager.acquire_one("artillery", "wf-alpha", "wf-alpha:artillery")
    print(
        f"[ACQUIRE] workflow=wf-alpha instance={first.instance_key} "
        f"status={target['metadata']['status']}"
    )

    blocked = lease_manager.acquire_one("artillery", "wf-beta", "wf-beta:artillery")
    print(f"[LOCK] workflow=wf-beta acquire_result={blocked}")
    if blocked is not None:
        raise AssertionError("resource lock failed: wf-beta acquired an already leased Agent")

    lease_manager.release(first)
    print(f"[RELEASE] workflow=wf-alpha status={target['metadata']['status']}")
    replacement = lease_manager.acquire_one("artillery", "wf-beta", "wf-beta:artillery")
    if replacement is None:
        raise AssertionError("released Agent was not available for the next workflow")
    print(
        f"[REACQUIRE] workflow=wf-beta instance={replacement.instance_key} "
        f"status={target['metadata']['status']}"
    )
    lease_manager.release(replacement)
    print(f"[PASS] Agent returned to status={target['metadata']['status']}\n")


def start_manager(port: int, state_dir: Path, max_workflows: int, details: bool):
    command = [
        sys.executable,
        "-u",
        "commander_agent/main.py",
        "--mode",
        "local",
        "--serve-workflow-manager",
        "--manager-host",
        "127.0.0.1",
        "--manager-port",
        str(port),
        "--max-workflows",
        str(max_workflows),
        "--state-dir",
        str(state_dir),
    ]
    output = None if details else subprocess.DEVNULL
    return subprocess.Popen(
        command,
        cwd=PROJECT_ROOT,
        env=os.environ.copy(),
        stdout=output,
        stderr=output,
    )


def compact_statuses(jobs: list[dict]) -> str:
    by_id = {job["workflow_id"]: job["status"] for job in jobs}
    return " | ".join(
        f"{workflow_id}={by_id.get(workflow_id, 'missing')}"
        for workflow_id, _, _ in DEFAULT_WORKFLOWS
    )


def demo_workflow_pool(args: argparse.Namespace) -> None:
    print("=== PHASE 2: RESIDENT WORKFLOW THREAD POOL ===")
    state_dir = Path(args.state_dir).expanduser().resolve()
    if state_dir.exists():
        shutil.rmtree(state_dir)
    state_dir.mkdir(parents=True, exist_ok=True)

    port = args.manager_port or find_free_port()
    base_url = f"http://127.0.0.1:{port}"
    manager_process = None
    try:
        manager_process = start_manager(port, state_dir, args.max_workflows, args.details)
        health = wait_for_health(base_url)
        print(
            f"[MANAGER] url={base_url} mode={health['mode']} "
            f"max_workflows={health['max_workflows']}"
        )

        for workflow_id, workflow_file, mock_eval_score in DEFAULT_WORKFLOWS:
            response = requests.post(
                f"{base_url}/workflows",
                json={
                    "workflow_id": workflow_id,
                    "workflow": "bpel",
                    "workflow_file": workflow_file,
                    "mock_eval_score": mock_eval_score,
                },
                timeout=3,
            )
            response.raise_for_status()
            result = response.json()
            print(
                f"[SUBMIT] workflow_id={workflow_id} bpel={workflow_file} "
                f"status={result['status']}"
            )

        deadline = time.time() + args.timeout
        saw_queued = False
        saw_parallel_running = False
        previous_line = None
        jobs = []
        while time.time() < deadline:
            jobs = requests.get(f"{base_url}/workflows", timeout=3).json()
            statuses = [job["status"] for job in jobs]
            saw_queued = saw_queued or "queued" in statuses
            saw_parallel_running = saw_parallel_running or statuses.count("running") >= 2
            line = compact_statuses(jobs)
            if line != previous_line:
                print(f"[STATUS] {line}")
                previous_line = line
            if jobs and all(status not in {"queued", "running"} for status in statuses):
                break
            time.sleep(args.poll_interval)
        else:
            raise TimeoutError("workflows did not complete before the demo timeout")

        if args.max_workflows == 2 and not saw_queued:
            raise AssertionError("queueing was not observed with three submitted workflows")
        if args.max_workflows >= 2 and not saw_parallel_running:
            raise AssertionError("concurrent workflow execution was not observed")

        print("\n=== PHASE 3: INDEPENDENT CHECKPOINTS ===")
        store = WorkflowStateStore(str(state_dir))
        for workflow_id, _, _ in DEFAULT_WORKFLOWS:
            checkpoint = store.load(workflow_id)
            checkpoint_id = checkpoint["workflow_id"]
            status = checkpoint["status"]
            print(
                f"[CHECKPOINT] workflow_id={workflow_id} saved_id={checkpoint_id} "
                f"status={status} file={store.state_path(workflow_id)}"
            )
            if checkpoint_id != workflow_id:
                raise AssertionError(f"checkpoint mismatch for {workflow_id}")
            if status != "completed":
                raise AssertionError(f"workflow did not complete: {workflow_id} status={status}")

        leases = requests.get(f"{base_url}/leases", timeout=3).json()
        if leases:
            raise AssertionError(f"unexpected remaining leases: {leases}")
        print("\n[PASS] queued state, concurrent execution, and independent checkpoints verified.")
    finally:
        stop_process(manager_process)


def main() -> None:
    args = parse_args()
    if args.max_workflows < 1:
        raise ValueError("--max-workflows must be at least 1")
    demo_agent_lease()
    demo_workflow_pool(args)


if __name__ == "__main__":
    main()
