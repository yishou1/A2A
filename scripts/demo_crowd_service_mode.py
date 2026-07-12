from __future__ import annotations

import argparse
import os
import shutil
import socket
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import requests


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from a2a_protocol.server import A2ABaseAgent  # noqa: E402
from commander_agent.main import CommanderAgent  # noqa: E402


def find_free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def build_supervisor_command(*, host: str, port: int, state_path: str) -> list[str]:
    return [
        sys.executable,
        "-u",
        "commander_agent/main.py",
        "--serve-supervisor",
        "--supervisor-host",
        host,
        "--supervisor-port",
        str(port),
        "--supervisor-path",
        state_path,
    ]


def build_task_pool_command(*, host: str, port: int, state_path: str) -> list[str]:
    return [
        sys.executable,
        "-u",
        "commander_agent/main.py",
        "--serve-task-pool",
        "--task-pool-host",
        host,
        "--task-pool-port",
        str(port),
        "--task-pool-path",
        state_path,
    ]


def build_agent_command(
    *,
    agent_id: str,
    name: str,
    role: str,
    port: int,
    claim_interval: float,
) -> list[str]:
    return [
        sys.executable,
        "-u",
        "scripts/run_demo_agent.py",
        "--agent-id",
        agent_id,
        "--name",
        name,
        "--role",
        role,
        "--port",
        str(port),
        "--claim-interval",
        str(claim_interval),
    ]


def wait_for_health(base_url: str, *, name: str, timeout: float = 10.0) -> dict:
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
    raise TimeoutError(f"{name} health check failed: {last_error}")


def stop_process(process: subprocess.Popen | None) -> None:
    if process is None or process.poll() is not None:
        return
    process.terminate()
    try:
        process.wait(timeout=5)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait(timeout=5)


def start_process(command: list[str], *, env: dict, details: bool):
    output = None if details else subprocess.DEVNULL
    return subprocess.Popen(
        command,
        cwd=PROJECT_ROOT,
        env=env,
        stdout=output,
        stderr=output,
    )


def start_demo_agents(*, claim_interval: float) -> list[A2ABaseAgent]:
    agents = []
    for agent_id, name, role, port in demo_agent_specs():
        agent = A2ABaseAgent(
            name=name,
            description=f"Demo {role} crowd worker",
            role=role,
            port=port,
            agent_id=agent_id,
            crowd_worker_enabled=True,
            crowd_claim_interval=claim_interval,
        )
        agent.register_with_supervisor()
        agent.start_crowd_worker()
        agents.append(agent)
        print(f"[AGENT] {agent_id} role={role} worker=started")
    return agents


def demo_agent_specs() -> list[tuple[str, str, str, int]]:
    return [
        ("crowd-recon-01", "Recon_Agent", "recon", 18112),
        ("crowd-artillery-01", "Artillery_Agent", "artillery", 18113),
        ("crowd-assault-01", "Assault_Agent", "assault", 18115),
    ]


def start_demo_agent_processes(*, env: dict, claim_interval: float, details: bool) -> list[subprocess.Popen]:
    processes = []
    for agent_id, name, role, port in demo_agent_specs():
        process = start_process(
            build_agent_command(
                agent_id=agent_id,
                name=name,
                role=role,
                port=port,
                claim_interval=claim_interval,
            ),
            env=env,
            details=details,
        )
        wait_for_health(f"http://127.0.0.1:{port}", name=f"Agent {agent_id}")
        processes.append(process)
        print(f"[AGENT_PROCESS] {agent_id} role={role} port={port} worker=started")
    return processes


def stop_demo_agents(agents: list[A2ABaseAgent]) -> None:
    for agent in agents:
        agent.stop_crowd_worker()


def run_crowd_workflow(*, state_dir: Path, timeout: float) -> dict:
    commander = CommanderAgent(
        mode="local",
        workflow="bpel",
        workflow_file="quick_strike_workflow",
        workflow_id="demo-crowd-service",
        state_dir=str(state_dir),
        agent_dispatch_mode="crowd",
        crowd_timeout=timeout,
        crowd_poll_interval=0.2,
        max_agent_workers=3,
    )
    return commander.run_bpel_workflow()


def print_agent_diagnostics(agents: list[A2ABaseAgent]) -> None:
    for agent in agents:
        metrics = agent.metrics_snapshot()
        print(
            f"[AGENT_METRICS] {agent.agent_id} "
            f"received={metrics.get('tasks_received')} "
            f"completed={metrics.get('tasks_completed')} "
            f"failed={metrics.get('tasks_failed')} "
            f"active={metrics.get('active_tasks')} "
            f"last_error={metrics.get('last_error')}"
        )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Run a full crowd service-mode E2E demo with Supervisor, TaskPool, Agent workers, and Commander."
    )
    parser.add_argument("--work-dir", default=None, help="Directory for demo state files.")
    parser.add_argument("--supervisor-port", type=int, default=0, help="Supervisor port; 0 selects a free port.")
    parser.add_argument("--task-pool-port", type=int, default=0, help="TaskPool port; 0 selects a free port.")
    parser.add_argument("--timeout", type=float, default=20.0, help="Maximum seconds for crowd task waits.")
    parser.add_argument("--claim-interval", type=float, default=0.1, help="Agent worker claim interval.")
    parser.add_argument("--agent-processes", action="store_true", help="Run demo Agents as independent processes.")
    parser.add_argument("--details", action="store_true", help="Show child service logs.")
    parser.add_argument("--keep-state", action="store_true", help="Keep demo state files after completion.")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    created_temp_dir = False
    if args.work_dir:
        work_dir = Path(args.work_dir).expanduser().resolve()
        if work_dir.exists():
            shutil.rmtree(work_dir)
        work_dir.mkdir(parents=True, exist_ok=True)
    else:
        work_dir = Path(tempfile.mkdtemp(prefix="a2a-crowd-demo-"))
        created_temp_dir = True

    supervisor_port = args.supervisor_port or find_free_port()
    task_pool_port = args.task_pool_port or find_free_port()
    supervisor_url = f"http://127.0.0.1:{supervisor_port}"
    task_pool_url = f"http://127.0.0.1:{task_pool_port}"
    supervisor_path = str(work_dir / "supervisor.json")
    task_pool_path = str(work_dir / "task_pool.json")
    workflow_state_dir = work_dir / "workflows"

    base_env = os.environ.copy()
    base_env.update(
        {
            "A2A_SUPERVISOR_URL": supervisor_url,
            "A2A_TASK_POOL_URL": task_pool_url,
            "A2A_SUPERVISOR_REQUIRED": "true",
            "A2A_CROWD_WORKER_ENABLED": "true",
            "A2A_CROWD_CLAIM_INTERVAL": str(args.claim_interval),
        }
    )

    supervisor_process = None
    task_pool_process = None
    agent_processes: list[subprocess.Popen] = []
    agents: list[A2ABaseAgent] = []
    old_env = os.environ.copy()
    try:
        print("=== A2A CROWD SERVICE MODE DEMO ===")
        print(f"[STATE] {work_dir}")

        supervisor_process = start_process(
            build_supervisor_command(
                host="127.0.0.1",
                port=supervisor_port,
                state_path=supervisor_path,
            ),
            env=base_env,
            details=args.details,
        )
        wait_for_health(supervisor_url, name="Supervisor")
        print(f"[SUPERVISOR] {supervisor_url}")

        task_pool_process = start_process(
            build_task_pool_command(
                host="127.0.0.1",
                port=task_pool_port,
                state_path=task_pool_path,
            ),
            env=base_env,
            details=args.details,
        )
        wait_for_health(task_pool_url, name="TaskPool")
        print(f"[TASK_POOL] {task_pool_url}")

        os.environ.clear()
        os.environ.update(base_env)

        if args.agent_processes:
            agent_processes = start_demo_agent_processes(
                env=base_env,
                claim_interval=args.claim_interval,
                details=args.details,
            )
        else:
            agents = start_demo_agents(claim_interval=args.claim_interval)

        context = run_crowd_workflow(state_dir=workflow_state_dir, timeout=args.timeout)
        status = context.get("workflow_status")
        print(f"[COMMANDER] workflow_status={status}")
        print(f"[RESULT] recon_report={context.get('recon_report')}")
        print(f"[RESULT] strike_result={context.get('strike_result')}")
        print(f"[RESULT] assault_result={context.get('assault_result')}")

        if status != "completed":
            print_agent_diagnostics(agents)
            raise AssertionError(f"crowd service workflow did not complete: {status}")
        print_agent_diagnostics(agents)
        print("[PASS] Crowd service-mode E2E completed.")
    finally:
        stop_demo_agents(agents)
        for process in agent_processes:
            stop_process(process)
        stop_process(task_pool_process)
        stop_process(supervisor_process)
        os.environ.clear()
        os.environ.update(old_env)
        if created_temp_dir and args.keep_state:
            print(f"[KEEP] State files left at {work_dir}")
        elif created_temp_dir:
            shutil.rmtree(work_dir, ignore_errors=True)


if __name__ == "__main__":
    main()
