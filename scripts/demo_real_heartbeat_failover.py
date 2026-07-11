from __future__ import annotations

import argparse
import contextlib
import ctypes
import io
import json
import os
import platform
import shutil
import signal
import subprocess
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import requests
import uvicorn


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from a2a_protocol.server import A2ABaseAgent  # noqa: E402
from commander_agent.main import CommanderAgent, load_env_file  # noqa: E402
from registry.nacos_manager import NacosRegistry, get_host_ip  # noqa: E402


SERVICE_NAME = "A2A-Real-Heartbeat-Demo"
PRIMARY_PORT = 18316
BACKUP_PORT = 18317
OUTPUT_ROOT = PROJECT_ROOT / ".a2a_state" / "real_heartbeat_demo"


class PriorityRegistry(NacosRegistry):
    def discover_service(self, service_name, required_tags=None):
        instances = super().discover_service(service_name, required_tags)
        return sorted(
            instances,
            key=lambda item: int(
                (item.get("metadata") or {}).get("demo_priority", 999)
            ),
        )


class LoggedHeartbeatRegistry(NacosRegistry):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.heartbeat_count = 0

    def send_heartbeat(self, *args, **kwargs):
        result = super().send_heartbeat(*args, **kwargs)
        self.heartbeat_count += 1
        print(
            f"[REAL_HEARTBEAT] count={self.heartbeat_count} "
            f"service={args[0]} port={args[2]}",
            flush=True,
        )
        return result


class RealHeartbeatAgent(A2ABaseAgent):
    def __init__(self, name, role, port, primary):
        self.primary = primary
        super().__init__(name, "Agent used for natural Nacos heartbeat timeout.", role, port)

    def execute_task(self, payload):
        if self.primary:
            print(
                f"[TASK_STARTED] work_item={payload.get('work_item')} port={self.port}",
                flush=True,
            )
            time.sleep(60)
        else:
            delay = float(payload.get("input", {}).get("backup_delay_seconds", 0) or 0)
            if delay > 0:
                print(
                    f"[BACKUP_TASK_STARTED] work_item={payload.get('work_item')} "
                    f"port={self.port} delay={delay}",
                    flush=True,
                )
                time.sleep(delay)
        return (
            {"heartbeat_result": f"{self.name} completed the task"},
            f"{self.name} completed",
        )


class AuthHandler(BaseHTTPRequestHandler):
    def _reply(self):
        body = b'{"ok":true,"token":"mock"}'
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        self._reply()

    def do_POST(self):
        self.rfile.read(int(self.headers.get("Content-Length", "0")))
        self._reply()

    def log_message(self, *_args):
        return


def parse_args():
    load_env_file()
    parser = argparse.ArgumentParser(
        description="Run a natural Nacos heartbeat timeout and Agent failover experiment"
    )
    parser.add_argument("--nacos-addr", default=os.environ.get("NACOS_ADDR", "127.0.0.1:8848"))
    parser.add_argument("--auth-port", type=int, default=18380)
    parser.add_argument("--startup-timeout", type=float, default=45)
    parser.add_argument("--unhealthy-timeout", type=float, default=30)
    parser.add_argument(
        "--backup-delay-seconds",
        type=float,
        default=0.0,
        help="Keep Backup busy for this many seconds after reassignment for Nacos UI inspection.",
    )
    parser.add_argument("--details", action="store_true")
    parser.add_argument(
        "--show-nacos-ui",
        action="store_true",
        help="Pause at key Nacos heartbeat states so the web console can be inspected.",
    )
    parser.add_argument(
        "--ui-hold-seconds",
        type=float,
        default=8.0,
        help="Seconds to pause for each Nacos UI inspection point.",
    )
    parser.add_argument(
        "--ui-wait-enter",
        action="store_true",
        help="Wait for Enter at each Nacos UI inspection point instead of only sleeping.",
    )
    parser.add_argument("--agent", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--agent-name", help=argparse.SUPPRESS)
    parser.add_argument("--agent-port", type=int, help=argparse.SUPPRESS)
    parser.add_argument("--priority", type=int, default=0, help=argparse.SUPPRESS)
    parser.add_argument("--primary", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--auth-base", help=argparse.SUPPRESS)
    return parser.parse_args()


def session():
    result = requests.Session()
    result.trust_env = False
    return result


def nacos_base_url(address):
    address = address.split(",", 1)[0].strip().rstrip("/")
    return address if address.startswith(("http://", "https://")) else f"http://{address}"


def progress(message):
    """Write selected milestones even while Commander stdout is captured."""
    print(message, file=sys.__stdout__, flush=True)


def hold_nacos_ui(args, label):
    if not args.show_nacos_ui:
        return
    progress(f"\n[NACOS UI] {label}")
    progress(f"[NACOS UI] Console: {nacos_base_url(args.nacos_addr)}/nacos/")
    progress(f"[NACOS UI] Service: DEFAULT_GROUP -> {SERVICE_NAME}")
    progress("[NACOS UI] Open service details and refresh the instance list/metadata.")
    if args.ui_wait_enter:
        input("[NACOS UI] Press Enter here after you finish inspecting the frontend...")
    else:
        progress(f"[NACOS UI] Holding {args.ui_hold_seconds:.0f}s for frontend inspection...")
        time.sleep(args.ui_hold_seconds)


def run_agent(args):
    os.environ["NACOS_ADDR"] = args.nacos_addr
    os.environ["A2A_AUTH_SERVER_BASE"] = args.auth_base
    os.environ["A2A_HEARTBEAT_INTERVAL"] = "1"
    registry = LoggedHeartbeatRegistry(server_addresses=args.nacos_addr)
    agent = RealHeartbeatAgent(
        args.agent_name,
        "real_heartbeat",
        args.agent_port,
        args.primary,
    )
    registry.register_service(
        SERVICE_NAME,
        get_host_ip(),
        args.agent_port,
        metadata={
            "role": "real_heartbeat",
            "status": "idle",
            "demo": "real_heartbeat",
            "demo_priority": args.priority,
        },
        heartbeat_interval=1,
        ephemeral=True,
    )
    uvicorn.run(agent.app, host="0.0.0.0", port=args.agent_port, log_level="warning")


def wait_for_url(url, timeout):
    deadline = time.time() + timeout
    last_error = None
    while time.time() < deadline:
        try:
            response = session().get(url, timeout=2)
            if response.status_code < 500:
                return
            last_error = RuntimeError(f"HTTP {response.status_code}")
        except Exception as exc:
            last_error = exc
        time.sleep(0.25)
    raise RuntimeError(f"URL did not become ready: {url}: {last_error}")


def read_instance(nacos_addr, port):
    response = session().get(
        f"{nacos_base_url(nacos_addr)}/nacos/v1/ns/instance/list",
        params={"serviceName": SERVICE_NAME},
        timeout=3,
    )
    response.raise_for_status()
    for host in response.json().get("hosts", []):
        if int(host.get("port", 0)) == port:
            return host
    return None


def wait_for_instance(nacos_addr, port, predicate, timeout):
    deadline = time.time() + timeout
    latest = None
    while time.time() < deadline:
        latest = read_instance(nacos_addr, port)
        if latest and predicate(latest):
            return latest
        time.sleep(0.25)
    raise RuntimeError(f"Nacos instance {port} did not reach expected state; latest={latest}")


def wait_for_log(path, marker, timeout):
    deadline = time.time() + timeout
    while time.time() < deadline:
        if path.exists() and marker in path.read_text(encoding="utf-8"):
            return
        time.sleep(0.2)
    raise RuntimeError(f"Log marker not observed: {marker} in {path}")


def show_heartbeats(path, expected_count, timeout):
    deadline = time.time() + timeout
    shown = 0
    while time.time() < deadline:
        if path.exists():
            heartbeat_lines = [
                line
                for line in path.read_text(encoding="utf-8").splitlines()
                if "[REAL_HEARTBEAT]" in line
            ]
            for line in heartbeat_lines[shown:]:
                progress(f"[HEARTBEAT] Primary -> Nacos | {line.split('] ', 1)[-1]}")
            shown = len(heartbeat_lines)
            if shown >= expected_count:
                return
        time.sleep(0.2)
    raise RuntimeError(f"Only observed {shown}/{expected_count} real heartbeats in {path}")


def wait_for_natural_unhealthy(nacos_addr, port, timeout):
    started_at = time.time()
    deadline = started_at + timeout
    next_status_at = 0.0
    latest = None
    while time.time() < deadline:
        latest = read_instance(nacos_addr, port)
        elapsed = time.time() - started_at
        healthy = latest.get("healthy") if latest else None
        if healthy is False:
            progress(
                f"[NACOS] Primary healthy=false after {elapsed:.3f}s "
                "(natural heartbeat timeout)"
            )
            return latest
        if elapsed >= next_status_at:
            progress(
                f"[NACOS WATCH] elapsed={elapsed:.1f}s "
                f"Primary healthy={healthy}; waiting for natural timeout"
            )
            next_status_at += 2.0
        time.sleep(0.25)
    raise RuntimeError(
        f"Nacos instance {port} remained healthy after {timeout}s; latest={latest}"
    )


def wait_for_trace_event(commander, event_type, timeout, predicate=None):
    deadline = time.time() + timeout
    predicate = predicate or (lambda _event: True)
    while time.time() < deadline:
        for event in commander.workflow_context.get("trace", []):
            if event.get("event_type") == event_type and predicate(event):
                return event
        time.sleep(0.05)
    raise RuntimeError(f"Commander event not observed: {event_type}")


def keep_backup_busy_metadata(args, stop_event: threading.Event):
    registry = NacosRegistry(server_addresses=args.nacos_addr)
    try:
        while not stop_event.is_set():
            latest = read_instance(args.nacos_addr, BACKUP_PORT)
            if latest:
                registry.update_instance_metadata(
                    SERVICE_NAME,
                    latest,
                    metadata_updates={
                        "status": "busy",
                        "lease_workflow_id": "demo-real-heartbeat",
                        "lease_work_item": "demo-real-heartbeat:long-task",
                        "ui_demo_note": "Backup is handling reassigned task",
                    },
                )
            stop_event.wait(0.5)
    finally:
        registry.close()


def start_agent(args, output_dir, *, name, port, priority, primary=False):
    log_path = output_dir / f"{name}.log"
    handle = log_path.open("w", encoding="utf-8")
    command = [
        sys.executable,
        "-u",
        str(Path(__file__).resolve()),
        "--agent",
        "--agent-name",
        name,
        "--agent-port",
        str(port),
        "--priority",
        str(priority),
        "--nacos-addr",
        args.nacos_addr,
        "--auth-base",
        f"http://127.0.0.1:{args.auth_port}",
    ]
    if primary:
        command.append("--primary")
    process = subprocess.Popen(
        command,
        cwd=PROJECT_ROOT,
        stdout=handle,
        stderr=subprocess.STDOUT,
    )
    return process, handle, log_path


def delete_instance(nacos_addr, port):
    with contextlib.suppress(requests.RequestException):
        response = session().delete(
            f"{nacos_base_url(nacos_addr)}/nacos/v1/ns/instance",
            params={
                "serviceName": SERVICE_NAME,
                "ip": get_host_ip(),
                "port": port,
                "clusterName": "DEFAULT",
                "groupName": "DEFAULT_GROUP",
                "ephemeral": "true",
            },
            timeout=3,
        )
        response.raise_for_status()


def suspend_process(process):
    if hasattr(signal, "SIGSTOP"):
        os.kill(process.pid, signal.SIGSTOP)
        return "SIGSTOP"

    if platform.system().lower() == "windows":
        process_suspend_resume = 0x0800
        handle = ctypes.windll.kernel32.OpenProcess(
            process_suspend_resume, False, process.pid
        )
        if not handle:
            raise OSError(f"OpenProcess failed for pid={process.pid}")
        try:
            status = ctypes.windll.ntdll.NtSuspendProcess(handle)
            if status != 0:
                raise OSError(f"NtSuspendProcess failed with status={status}")
        finally:
            ctypes.windll.kernel32.CloseHandle(handle)
        return "NtSuspendProcess"

    raise RuntimeError("No supported process suspend mechanism on this platform")


def stop_process(process, *, killed=False):
    if process.poll() is not None:
        return
    if killed:
        process.kill()
    else:
        process.terminate()
    with contextlib.suppress(subprocess.TimeoutExpired):
        process.wait(timeout=5)
    if process.poll() is None:
        process.kill()
        process.wait(timeout=5)


def run_demo(args):
    if OUTPUT_ROOT.exists():
        shutil.rmtree(OUTPUT_ROOT)
    output_dir = OUTPUT_ROOT / "outputs"
    state_dir = OUTPUT_ROOT / "workflows"
    output_dir.mkdir(parents=True)
    state_dir.mkdir(parents=True)

    wait_for_url(
        f"{nacos_base_url(args.nacos_addr)}/nacos/v1/console/health/readiness",
        args.startup_timeout,
    )
    auth_server = ThreadingHTTPServer(("127.0.0.1", args.auth_port), AuthHandler)
    auth_thread = threading.Thread(target=auth_server.serve_forever, daemon=True)
    auth_thread.start()

    processes = []
    commander_output = io.StringIO()
    frozen_at = None
    unhealthy_at = None
    result = {}
    try:
        primary = start_agent(
            args,
            output_dir,
            name="heartbeat-primary",
            port=PRIMARY_PORT,
            priority=0,
            primary=True,
        )
        backup = start_agent(
            args,
            output_dir,
            name="heartbeat-backup",
            port=BACKUP_PORT,
            priority=1,
        )
        processes = [primary, backup]
        wait_for_url(f"http://127.0.0.1:{PRIMARY_PORT}/health", args.startup_timeout)
        wait_for_url(f"http://127.0.0.1:{BACKUP_PORT}/health", args.startup_timeout)
        wait_for_instance(args.nacos_addr, PRIMARY_PORT, lambda host: host.get("healthy"), 15)
        wait_for_instance(args.nacos_addr, BACKUP_PORT, lambda host: host.get("healthy"), 15)
        progress("=== REAL NACOS HEARTBEAT FAILOVER ===")
        progress(
            f"[REGISTER] Primary={PRIMARY_PORT}, Backup={BACKUP_PORT}, "
            f"service={SERVICE_NAME}"
        )
        show_heartbeats(primary[2], expected_count=3, timeout=10)
        hold_nacos_ui(args, "Primary and Backup are registered, healthy, and idle")

        registry = PriorityRegistry(server_addresses=args.nacos_addr)
        commander = CommanderAgent(
            mode="remote",
            workflow_id="demo-real-heartbeat",
            state_dir=str(state_dir),
            registry=registry,
            max_retries=0,
            request_timeout=40,
        )
        commander.lease_manager.service_name = SERVICE_NAME
        commander.lease_heartbeat_check_interval = 0.5
        payload = {
            "workflow_id": "demo-real-heartbeat",
            "work_item": "demo-real-heartbeat:long-task",
            "command": "run_long_task",
            "output_hint": "heartbeat_result",
            "input": {"backup_delay_seconds": args.backup_delay_seconds},
            "work_list": [],
        }

        def invoke():
            result["success"] = commander.delegate_task("real_heartbeat", payload)

        redirect = (
            contextlib.nullcontext()
            if args.details
            else contextlib.redirect_stdout(commander_output)
        )
        with redirect:
            worker = threading.Thread(target=invoke, name="real-heartbeat-invoke")
            progress(
                f"[DISPATCH] Commander -> Primary:{PRIMARY_PORT} | "
                "command=run_long_task"
            )
            worker.start()
            wait_for_log(primary[2], "[TASK_STARTED]", 10)
            busy = wait_for_instance(
                args.nacos_addr,
                PRIMARY_PORT,
                lambda host: (host.get("metadata") or {}).get("status") == "busy",
                10,
            )
            progress(
                f"[LEASE] Primary:{PRIMARY_PORT} status=busy; long task is running"
            )
            hold_nacos_ui(args, "Primary is busy with lease metadata while long task is running")
            frozen_at = time.time()
            fault_method = suspend_process(primary[0])
            print(f"[FAULT] {fault_method} sent to Primary pid={primary[0].pid}")
            progress(
                f"[FAULT] {fault_method} Primary pid={primary[0].pid}; "
                "HTTP task and heartbeat thread are frozen"
            )
            unhealthy = wait_for_natural_unhealthy(
                args.nacos_addr, PRIMARY_PORT, args.unhealthy_timeout
            )
            unhealthy_at = time.time()
            hold_nacos_ui(args, "Nacos has naturally marked Primary healthy=false after heartbeat timeout")
            print(
                f"[NACOS] Primary became unhealthy after "
                f"{unhealthy_at - frozen_at:.3f}s"
            )
            lost_event = wait_for_trace_event(
                commander, "agent_heartbeat_lost", timeout=5
            )
            progress(f"[COMMANDER] heartbeat lost | target={lost_event.get('target')}")
            reassign_event = wait_for_trace_event(
                commander, "agent_failover_reassigning", timeout=5
            )
            progress(
                f"[FAILOVER] release Primary and find same-role backup | "
                f"failed_target={reassign_event.get('failed_target')}"
            )
            backup_attempt = wait_for_trace_event(
                commander,
                "agent_call_attempt",
                timeout=5,
                predicate=lambda event: str(event.get("target", "")).endswith(
                    f":{BACKUP_PORT}"
                ),
            )
            progress(
                f"[DISPATCH] Commander -> Backup | target={backup_attempt.get('target')}"
            )
            if args.backup_delay_seconds > 0:
                backup_busy_keeper_stop = threading.Event()
                backup_busy_keeper = threading.Thread(
                    target=keep_backup_busy_metadata,
                    args=(args, backup_busy_keeper_stop),
                    daemon=True,
                )
                backup_busy_keeper.start()
                wait_for_instance(
                    args.nacos_addr,
                    BACKUP_PORT,
                    lambda host: (host.get("metadata") or {}).get("status") == "busy",
                    10,
                )
                try:
                    hold_nacos_ui(
                        args,
                        f"Backup is busy after reassignment for {args.backup_delay_seconds:g}s",
                    )
                finally:
                    backup_busy_keeper_stop.set()
                    backup_busy_keeper.join(timeout=2)
            backup_completed = wait_for_trace_event(
                commander,
                "agent_call_completed",
                timeout=5,
                predicate=lambda event: str(event.get("target", "")).endswith(
                    f":{BACKUP_PORT}"
                ),
            )
            progress(
                f"[COMPLETE] Backup finished task | target={backup_completed.get('target')}"
            )
            hold_nacos_ui(args, "Backup completed the reassigned task; inspect final metadata before cleanup")
            worker.join(timeout=20)
            if worker.is_alive():
                raise RuntimeError("Commander did not finish failover after Nacos marked Primary unhealthy")

            # Close the abandoned Primary request while Commander output is still
            # redirected; the failover result has already been obtained from Backup.
            stop_process(primary[0], killed=True)
            time.sleep(0.5)

        events = commander.workflow_context.get("trace", [])
        event_types = {event["event_type"] for event in events}
        completed_targets = [
            event.get("target")
            for event in events
            if event.get("event_type") == "agent_call_completed"
        ]
        checks = {
            "three_real_heartbeats": "[REAL_HEARTBEAT] count=3"
            in primary[2].read_text(encoding="utf-8"),
            "primary_busy_lease": (busy.get("metadata") or {}).get("status") == "busy",
            "nacos_natural_unhealthy": unhealthy.get("healthy") is False,
            "commander_detected_loss": "agent_heartbeat_lost" in event_types,
            "commander_reassigned": "agent_failover_reassigning" in event_types,
            "backup_completed": any(
                str(target).endswith(f":{BACKUP_PORT}") for target in completed_targets
            ),
            "workflow_succeeded": result.get("success") is True,
        }
        failed = [name for name, passed in checks.items() if not passed]
        if failed:
            raise RuntimeError(f"Real heartbeat checks failed: {', '.join(failed)}")

        report = {
            "service_name": SERVICE_NAME,
            "primary_pid": primary[0].pid,
            "primary_port": PRIMARY_PORT,
            "backup_port": BACKUP_PORT,
            "fault": fault_method,
            "nacos_unhealthy_delay_seconds": round(unhealthy_at - frozen_at, 3),
            "checks": checks,
            "events": [
                event
                for event in events
                if event.get("event_type")
                in {
                    "agent_call_attempt",
                    "agent_heartbeat_lost",
                    "agent_failover_reassigning",
                    "agent_call_completed",
                }
            ],
        }
        (output_dir / "summary.json").write_text(
            json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        (output_dir / "commander.log").write_text(
            commander_output.getvalue(), encoding="utf-8"
        )
        (output_dir / "report.md").write_text(
            build_report(report), encoding="utf-8"
        )

        print("[PASS] All natural heartbeat checks passed")
        print(f"[REPORT] {output_dir / 'report.md'}")
    finally:
        if processes:
            stop_process(processes[0][0], killed=True)
            stop_process(processes[1][0])
            for _, handle, _ in processes:
                handle.close()
        delete_instance(args.nacos_addr, PRIMARY_PORT)
        delete_instance(args.nacos_addr, BACKUP_PORT)
        auth_server.shutdown()
        auth_server.server_close()


def build_report(report):
    checks = "\n".join(
        f"| {name} | {passed} |" for name, passed in report["checks"].items()
    )
    return f"""# 真实 Nacos 心跳故障转移实验

| 项目 | 值 |
|---|---|
| Service | `{report['service_name']}` |
| Primary | `{report['primary_port']}` |
| Backup | `{report['backup_port']}` |
| 故障方式 | `{report['fault']}` |
| Nacos 判定 unhealthy 耗时 | `{report['nacos_unhealthy_delay_seconds']}s` |

## 检查结果

| 检查项 | 结果 |
|---|---|
{checks}
"""


def main():
    args = parse_args()
    if args.agent:
        run_agent(args)
        return
    run_demo(args)


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        print(f"[ERROR] {type(exc).__name__}: {exc}", file=sys.stderr)
        raise
