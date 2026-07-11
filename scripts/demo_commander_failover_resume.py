from __future__ import annotations

import argparse
import contextlib
import io
import json
import os
import signal
import socket
import subprocess
import sys
import time
from pathlib import Path

import requests


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from commander_agent.main import CommanderAgent  # noqa: E402
from workflow_state_store import WorkflowStateStore, new_workflow_id  # noqa: E402


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Commander failover and resume demo")
    parser.add_argument("--workflow-id", default=None, help="Workflow checkpoint id to reuse")
    parser.add_argument(
        "--state-dir",
        default=str(PROJECT_ROOT / ".a2a_state" / "workflows"),
        help="Directory used to persist workflow checkpoints",
    )
    parser.add_argument("--seed-max-steps", type=int, default=2, help="How many steps to run before failover")
    parser.add_argument("--heartbeat-interval", type=float, default=5.0, help="Heartbeat polling interval in seconds")
    parser.add_argument("--miss-threshold", type=int, default=2, help="How many missed heartbeats trigger failover")
    parser.add_argument("--crash-after-seconds", type=float, default=8.0, help="When to simulate Commander crash")
    parser.add_argument("--primary-port", type=int, default=0, help="Primary Commander API port; 0 means auto-pick")
    parser.add_argument("--secondary-port", type=int, default=0, help="Failover Commander API port; 0 means auto-pick")
    parser.add_argument("--mock-eval-score", type=int, default=75, help="Mock evaluation score used during resume")
    parser.add_argument(
        "--mock-decision",
        choices=["ASSAULT", "RE-PLAN"],
        default="ASSAULT",
        help="Mock commander decision used during resume",
    )
    parser.add_argument("--details", action="store_true", help="Show full JSON responses.")
    parser.add_argument("--reset", action="store_true", help="Delete any existing checkpoint before starting")
    return parser.parse_args()


def find_free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def pick_port(preferred: int, exclude: set[int] | None = None) -> int:
    exclude = exclude or set()
    if preferred and preferred not in exclude:
        return preferred

    while True:
        candidate = find_free_port()
        if candidate not in exclude:
            return candidate


def wait_for_health(port: int, timeout_seconds: float = 30.0) -> dict:
    url = f"http://127.0.0.1:{port}/health"
    deadline = time.time() + timeout_seconds
    last_error = None
    while time.time() < deadline:
        try:
            response = requests.get(url, timeout=2)
            response.raise_for_status()
            return response.json()
        except Exception as exc:
            last_error = exc
            time.sleep(1)
    raise TimeoutError(f"Health check failed for {url}: {last_error}")


def start_recovery_api(port: int, state_dir: str, details: bool = False) -> subprocess.Popen:
    env = os.environ.copy()
    env["A2A_STATE_DIR"] = state_dir
    command = [
        sys.executable,
        "-u",
        "commander_agent/main.py",
        "--mode",
        "local",
        "--workflow",
        "dynamic",
        "--serve-recovery-api",
        "--recovery-host",
        "127.0.0.1",
        "--recovery-port",
        str(port),
        "--state-dir",
        state_dir,
    ]
    if details:
        command.append("--details")
    return subprocess.Popen(
        command,
        cwd=PROJECT_ROOT,
        env=env,
        stdout=None if details else subprocess.DEVNULL,
        stderr=None if details else subprocess.DEVNULL,
    )


def stop_process(proc: subprocess.Popen | None) -> None:
    if proc is None or proc.poll() is not None:
        return

    proc.terminate()
    try:
        proc.wait(timeout=5)
    except subprocess.TimeoutExpired:
        proc.kill()
        proc.wait(timeout=5)


def seed_checkpoint(
    workflow_id: str,
    state_dir: str,
    mock_eval_score: int,
    mock_decision: str,
    seed_max_steps: int,
    details: bool = False,
) -> dict:
    def run_seed() -> dict:
        commander = CommanderAgent(
            mode="local",
            workflow="dynamic",
            workflow_id=workflow_id,
            state_dir=state_dir,
            resume=False,
            mock_eval_score=mock_eval_score,
            mock_decision=mock_decision,
            details=details,
        )
        return commander.run_dynamic_battle_scenario(max_steps=seed_max_steps)

    if details:
        return run_seed()
    with contextlib.redirect_stdout(io.StringIO()):
        return run_seed()


def probe_health(port: int) -> tuple[bool, str]:
    url = f"http://127.0.0.1:{port}/health"
    try:
        response = requests.get(url, timeout=2)
        response.raise_for_status()
        return True, response.text
    except Exception as exc:
        return False, str(exc)


def main() -> None:
    args = parse_args()
    workflow_id = args.workflow_id or new_workflow_id("workflow-failover")
    state_dir = args.state_dir
    store = WorkflowStateStore(state_dir)

    if args.reset and store.exists(workflow_id):
        store.delete(workflow_id)
        print(f"[RESET] removed existing checkpoint: {store.state_path(workflow_id)}")

    print("[PHASE 0] Seeding a resumable checkpoint.")
    seed_context = seed_checkpoint(
        workflow_id=workflow_id,
        state_dir=state_dir,
        mock_eval_score=args.mock_eval_score,
        mock_decision=args.mock_decision,
        seed_max_steps=args.seed_max_steps,
        details=args.details,
    )
    print(
        f"[SEED] workflow_id={workflow_id} "
        f"status={seed_context.get('workflow_status')} "
        f"activity={seed_context.get('workflow_activatity')} "
        f"completed_roles={seed_context.get('completed_roles')} "
        f"last_work_item={seed_context.get('last_work_item')} "
        f"checkpoint={store.state_path(workflow_id)}"
    )

    primary_port = pick_port(args.primary_port)
    secondary_port = pick_port(args.secondary_port, exclude={primary_port})

    primary_proc = None
    secondary_proc = None
    try:
        print(f"\n[PHASE 1] Starting primary Commander API on port {primary_port}.")
        primary_proc = start_recovery_api(primary_port, state_dir, details=args.details)
        wait_for_health(primary_port)
        print(f"[HEARTBEAT] primary Commander on port {primary_port} is healthy.")
        print(f"[HEARTBEAT] polling every {args.heartbeat_interval:.1f}s; failover after {args.miss_threshold} misses.")

        started_at = time.time()
        misses = 0
        crashed = False

        while True:
            elapsed = time.time() - started_at
            if not crashed and elapsed >= args.crash_after_seconds:
                print(f"[SIMULATE] Commander on port {primary_port} stopped responding; terminating it now.")
                stop_process(primary_proc)
                crashed = True

            alive, detail = probe_health(primary_port)
            if alive:
                misses = 0
                print(f"[HEARTBEAT] primary Commander port {primary_port} ok")
            else:
                misses += 1
                if args.details:
                    print(f"[HEARTBEAT] primary Commander port {primary_port} missed {misses}/{args.miss_threshold}: {detail}")
                else:
                    print(f"[HEARTBEAT] primary Commander missed {misses}/{args.miss_threshold}")

            if crashed and misses >= args.miss_threshold:
                print(f"[DETECT] primary Commander on port {primary_port} is down; starting failover Commander on port {secondary_port}.")
                break

            time.sleep(args.heartbeat_interval)

        print(f"\n[PHASE 2] Starting failover Commander API on port {secondary_port}.")
        secondary_proc = start_recovery_api(secondary_port, state_dir, details=args.details)
        wait_for_health(secondary_port)
        print(f"[RECOVERY] failover Commander on port {secondary_port} is healthy.")

        resume_payload = {
            "mode": "local",
            "workflow": "dynamic",
            "state_dir": state_dir,
            "max_steps": 10,
            "resume": True,
            "strict": True,
            "mock_eval_score": args.mock_eval_score,
            "mock_decision": args.mock_decision,
            "attachments": [],
        }
        resume_url = f"http://127.0.0.1:{secondary_port}/workflows/{workflow_id}/resume"
        print(f"[RESUME] POST {resume_url}")
        response = requests.post(resume_url, json=resume_payload, timeout=120)
        response.raise_for_status()
        resume_result = response.json()

        print(
            f"\n[RESULT] resume workflow_id={resume_result.get('workflow_id')} "
            f"workflow_status={resume_result.get('workflow_status')} "
            f"state_path={resume_result.get('state_path')}"
        )
        if args.details:
            print(json.dumps(resume_result, ensure_ascii=False, indent=2))

        final_url = f"http://127.0.0.1:{secondary_port}/workflows/{workflow_id}"
        final_state = requests.get(final_url, timeout=10)
        final_state.raise_for_status()
        final_payload = final_state.json()
        context = final_payload.get("context", {})
        print(
            f"[RESULT] final checkpoint workflow_id={final_payload.get('workflow_id')} "
            f"status={final_payload.get('status')} "
            f"workflow_status={context.get('workflow_status')} "
            f"completed_roles={context.get('completed_roles')}"
        )
        if args.details:
            print(json.dumps(final_payload, ensure_ascii=False, indent=2))
    finally:
        stop_process(secondary_proc)
        stop_process(primary_proc)


if __name__ == "__main__":
    main()
