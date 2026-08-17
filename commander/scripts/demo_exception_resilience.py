from __future__ import annotations

import argparse
import contextlib
import io
import json
import os
import shutil
import subprocess
import sys
import threading
import time
from pathlib import Path

import requests
import uvicorn
from fastapi.responses import JSONResponse


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from a2a_protocol.server import A2ABaseAgent  # noqa: E402
from commander_agent.main import CommanderAgent, load_env_file  # noqa: E402
from registry.nacos_manager import NacosRegistry, get_host_ip  # noqa: E402


SERVICE_NAME = "A2A-Resilience-Demo"
OUTPUT_ROOT = PROJECT_ROOT / ".a2a_state" / "exception_resilience_demo"
AGENT_SPECS = [
    ("retry-primary", "demo_retry", 18212, 0),
    ("failover-primary", "demo_failover", 18213, 0),
    ("failover-backup", "demo_failover", 18214, 1),
    ("circuit-primary", "demo_circuit", 18215, 0),
    ("heartbeat-primary", "demo_heartbeat", 18216, 0),
    ("heartbeat-backup", "demo_heartbeat", 18217, 1),
    ("traceback-primary", "demo_traceback", 18218, 0),
]


class SortedNacosRegistry(NacosRegistry):
    """Keep demo selection deterministic while still using real Nacos discovery."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.forced_stale_ports = set()

    def discover_service(self, service_name, required_tags=None):
        instances = super().discover_service(service_name, required_tags)
        return sorted(
            instances,
            key=lambda item: (
                int((item.get("metadata") or {}).get("demo_priority", 999)),
                int(item.get("port", 0)),
            ),
        )

    def _is_instance_fresh(self, instance):
        metadata = instance.get("metadata", {}) or {}
        if metadata.get("demo") == "exception_resilience":
            return (
                bool(instance.get("healthy", True))
                and int(instance.get("port", 0)) not in self.forced_stale_ports
            )
        return super()._is_instance_fresh(instance)


class ResilienceDemoAgent(A2ABaseAgent):
    def __init__(self, name, role, port, registry, priority):
        self.demo_registry = registry
        self.demo_mode = "success"
        self.demo_failures_remaining = 0
        self.demo_delay_seconds = 2.0
        self.demo_lock = threading.RLock()
        super().__init__(name, "Controllable Agent for resilience demonstrations.", role, port)
        self._install_demo_controls()

    def _install_demo_controls(self):
        @self.app.middleware("http")
        async def inject_http_failure(request, call_next):
            if request.url.path == "/sendMessage":
                with self.demo_lock:
                    should_fail = self.demo_mode == "http_fail_always"
                    if self.demo_mode == "http_fail_n" and self.demo_failures_remaining > 0:
                        self.demo_failures_remaining -= 1
                        should_fail = True
                if should_fail:
                    return JSONResponse(
                        status_code=503,
                        content={"status": "failed", "error": "simulated HTTP 503"},
                    )
            return await call_next(request)

        @self.app.post("/demo/control")
        async def control(payload: dict):
            with self.demo_lock:
                self.demo_mode = str(payload.get("mode", "success"))
                self.demo_failures_remaining = int(payload.get("failures", 0))
                self.demo_delay_seconds = float(payload.get("delay_seconds", 2.0))
                self._task_response_cache.clear()
            return {
                "mode": self.demo_mode,
                "failures_remaining": self.demo_failures_remaining,
                "delay_seconds": self.demo_delay_seconds,
            }

        @self.app.post("/demo/stop-heartbeat")
        async def stop_heartbeat():
            self.demo_registry.close()
            return {"heartbeat": "stopped"}

    def execute_task(self, payload):
        with self.demo_lock:
            mode = self.demo_mode
            delay = self.demo_delay_seconds
        if mode == "slow_success":
            time.sleep(delay)
        if mode == "business_error":
            self._raise_nested_business_error(payload)
        return (
            {payload.get("output_hint", "result"): f"{self.name} completed the demo task"},
            f"{self.name} completed mode={mode}",
        )

    @staticmethod
    def _raise_nested_business_error(payload):
        def validate_demo_payload():
            raise ValueError(
                f"simulated nested business failure for {payload.get('work_item')}"
            )

        validate_demo_payload()


def parse_args():
    load_env_file()
    parser = argparse.ArgumentParser(
        description="Demonstrate retries, failover, leases, heartbeat loss, circuit breaking, and traceback"
    )
    parser.add_argument("--nacos-addr", default=os.environ.get("NACOS_ADDR", "127.0.0.1:8848"))
    parser.add_argument(
        "--auth-server-base",
        default=os.environ.get("A2A_AUTH_SERVER_BASE", "http://127.0.0.1:8080"),
    )
    parser.add_argument("--startup-timeout", type=float, default=30.0)
    parser.add_argument("--details", action="store_true")
    parser.add_argument(
        "--show-nacos-ui",
        action="store_true",
        help="Pause at key Nacos metadata states so the web console can be inspected.",
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
    parser.add_argument("--agent-role", help=argparse.SUPPRESS)
    parser.add_argument("--agent-port", type=int, help=argparse.SUPPRESS)
    parser.add_argument("--agent-priority", type=int, default=0, help=argparse.SUPPRESS)
    return parser.parse_args()


def no_proxy_session():
    session = requests.Session()
    session.trust_env = False
    return session


def nacos_base_url(address):
    address = address.split(",", 1)[0].strip().rstrip("/")
    return address if address.startswith(("http://", "https://")) else f"http://{address}"


def progress(message):
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
    os.environ["A2A_AUTH_SERVER_BASE"] = args.auth_server_base
    os.environ["A2A_HEARTBEAT_INTERVAL"] = "0.2"
    registry = NacosRegistry(server_addresses=args.nacos_addr)
    agent = ResilienceDemoAgent(
        args.agent_name,
        args.agent_role,
        args.agent_port,
        registry,
        args.agent_priority,
    )
    registry.register_service(
        SERVICE_NAME,
        get_host_ip(),
        args.agent_port,
        metadata={
            "role": args.agent_role,
            "status": "idle",
            "demo": "exception_resilience",
            "demo_name": args.agent_name,
            "demo_priority": args.agent_priority,
        },
        heartbeat_interval=0.2,
    )
    uvicorn.run(agent.app, host="0.0.0.0", port=args.agent_port, log_level="warning")


class AgentProcessGroup:
    def __init__(self, args, output_dir):
        self.args = args
        self.output_dir = output_dir
        self.processes = []

    def start(self):
        for name, role, port, priority in AGENT_SPECS:
            log_path = self.output_dir / f"agent_{name}.log"
            log_handle = log_path.open("w", encoding="utf-8")
            command = [
                sys.executable,
                "-u",
                str(Path(__file__).resolve()),
                "--agent",
                "--agent-name",
                name,
                "--agent-role",
                role,
                "--agent-port",
                str(port),
                "--agent-priority",
                str(priority),
                "--nacos-addr",
                self.args.nacos_addr,
                "--auth-server-base",
                self.args.auth_server_base,
            ]
            process = subprocess.Popen(
                command,
                cwd=PROJECT_ROOT,
                stdout=log_handle,
                stderr=subprocess.STDOUT,
            )
            self.processes.append((name, port, process, log_handle, log_path))
        for name, port, _, _, _ in self.processes:
            wait_for_url(f"http://127.0.0.1:{port}/health", self.args.startup_timeout, name)
        self.wait_for_registration()

    def wait_for_registration(self):
        expected = {port for _, _, port, _ in AGENT_SPECS}
        session = no_proxy_session()
        deadline = time.time() + self.args.startup_timeout
        url = f"{nacos_base_url(self.args.nacos_addr)}/nacos/v1/ns/instance/list"
        while time.time() < deadline:
            try:
                response = session.get(url, params={"serviceName": SERVICE_NAME}, timeout=2)
                response.raise_for_status()
                found = {
                    int(host["port"])
                    for host in response.json().get("hosts", [])
                    if (host.get("metadata") or {}).get("demo") == "exception_resilience"
                    and host.get("healthy", True)
                }
                if expected.issubset(found):
                    return
            except Exception:
                pass
            time.sleep(0.2)
        raise RuntimeError("Demo Agents did not register in Nacos")

    def stop(self):
        for _, _, process, _, _ in self.processes:
            if process.poll() is None:
                process.terminate()
        for _, _, process, handle, _ in self.processes:
            with contextlib.suppress(subprocess.TimeoutExpired):
                process.wait(timeout=5)
            if process.poll() is None:
                process.kill()
                process.wait(timeout=5)
            handle.close()
        self._remove_demo_instances()

    def _remove_demo_instances(self):
        session = no_proxy_session()
        url = f"{nacos_base_url(self.args.nacos_addr)}/nacos/v1/ns/instance"
        ip = get_host_ip()
        for _, _, port, _ in AGENT_SPECS:
            with contextlib.suppress(requests.RequestException):
                response = session.delete(
                    url,
                    params={
                        "serviceName": SERVICE_NAME,
                        "ip": ip,
                        "port": port,
                        "clusterName": "DEFAULT",
                        "groupName": "DEFAULT_GROUP",
                        "ephemeral": "true",
                    },
                    timeout=3,
                )
                response.raise_for_status()


def wait_for_url(url, timeout, label):
    session = no_proxy_session()
    deadline = time.time() + timeout
    last_error = None
    while time.time() < deadline:
        try:
            response = session.get(url, timeout=2)
            if response.status_code < 500:
                return
            last_error = RuntimeError(f"HTTP {response.status_code}")
        except Exception as exc:
            last_error = exc
        time.sleep(0.2)
    raise RuntimeError(f"{label} did not become ready: {last_error}")


def check_infrastructure(args):
    wait_for_url(
        f"{nacos_base_url(args.nacos_addr)}/nacos/v1/console/health/readiness",
        args.startup_timeout,
        "Nacos",
    )
    wait_for_url(f"{args.auth_server_base.rstrip('/')}/get", args.startup_timeout, "auth-server")


def control(port, mode, failures=0, delay_seconds=2.0):
    response = no_proxy_session().post(
        f"http://127.0.0.1:{port}/demo/control",
        json={"mode": mode, "failures": failures, "delay_seconds": delay_seconds},
        timeout=3,
    )
    response.raise_for_status()


def task_payload(workflow_id, name):
    return {
        "workflow_id": workflow_id,
        "work_item": f"{workflow_id}:{name}",
        "command": "run_resilience_demo",
        "output_hint": "demo_result",
        "input": {"phase": name},
        "work_list": [],
    }


def new_commander(
    registry,
    state_dir,
    workflow_id,
    *,
    retries=0,
    circuit_threshold=3,
    circuit_timeout=2,
    request_timeout=4,
):
    os.environ["A2A_CIRCUIT_FAILURE_THRESHOLD"] = str(circuit_threshold)
    os.environ["A2A_CIRCUIT_RECOVERY_TIMEOUT"] = str(circuit_timeout)
    commander = CommanderAgent(
        mode="remote",
        workflow_id=workflow_id,
        state_dir=str(state_dir),
        registry=registry,
        max_retries=retries,
        retry_backoff=0.1,
        request_timeout=request_timeout,
    )
    commander.lease_manager.service_name = SERVICE_NAME
    commander.lease_heartbeat_check_interval = 0.1
    return commander


def trace_events(commander, *event_types):
    selected = set(event_types)
    return [event for event in commander.workflow_context["trace"] if event["event_type"] in selected]


def metadata_for_port(registry, port):
    instances = registry.discover_service(SERVICE_NAME)
    for instance in instances:
        if int(instance.get("port", 0)) == port:
            return dict(instance.get("metadata") or {})
    return {}


def wait_for_metadata_status(registry, port, status, timeout=3.0):
    deadline = time.time() + timeout
    latest = {}
    while time.time() < deadline:
        latest = metadata_for_port(registry, port)
        if latest.get("status") == status:
            return latest
        time.sleep(0.05)
    return latest


def run_demo(args):
    if OUTPUT_ROOT.exists():
        shutil.rmtree(OUTPUT_ROOT)
    output_dir = OUTPUT_ROOT / "outputs"
    state_dir = OUTPUT_ROOT / "workflows"
    output_dir.mkdir(parents=True)
    state_dir.mkdir(parents=True)
    os.environ["NACOS_ADDR"] = args.nacos_addr
    os.environ["A2A_AUTH_SERVER_BASE"] = args.auth_server_base

    check_infrastructure(args)
    registry = SortedNacosRegistry(server_addresses=args.nacos_addr)
    agents = AgentProcessGroup(args, output_dir)
    commander_log = io.StringIO()
    summary = {}

    print("=== A2A EXCEPTION AND RESILIENCE DEMO ===")
    print(f"Nacos: {args.nacos_addr}")
    print(f"Auth:  {args.auth_server_base}")
    try:
        agents.start()
        print(f"[READY] {len(AGENT_SPECS)} controllable Agents registered in Nacos")
        hold_nacos_ui(args, "All demo Agents are registered and idle")
        redirect = contextlib.nullcontext() if args.details else contextlib.redirect_stdout(commander_log)
        with redirect:
            print("\n=== 1. AUTOMATIC RETRY ===")
            control(18212, "http_fail_n", failures=1)
            retry = new_commander(registry, state_dir, "demo-retry", retries=1)
            retry_ok = retry.delegate_task("demo_retry", task_payload("demo-retry", "retry"))
            retry_events = trace_events(retry, "agent_call_attempt", "agent_call_failed", "agent_call_completed")
            summary["automatic_retry"] = {
                "success": retry_ok,
                "attempts": len([e for e in retry_events if e["event_type"] == "agent_call_attempt"]),
                "events": retry_events,
            }

            print("\n=== 2. LEASE AND SAME-ROLE FAILOVER ===")
            control(18213, "http_fail_always")
            control(18214, "success")
            failover = new_commander(registry, state_dir, "demo-failover", retries=0)
            failover_ok = failover.delegate_task(
                "demo_failover", task_payload("demo-failover", "same-role-failover")
            )
            failover_events = trace_events(
                failover,
                "agent_call_attempt",
                "agent_call_failed",
                "agent_failover_reassigning",
                "agent_call_completed",
            )
            summary["same_role_failover"] = {
                "success": failover_ok,
                "targets": [e.get("target") for e in failover_events if e["event_type"] == "agent_call_attempt"],
                "primary_metadata": metadata_for_port(registry, 18213),
                "backup_metadata": metadata_for_port(registry, 18214),
                "events": failover_events,
            }
            hold_nacos_ui(
                args,
                "Same-role failover: inspect demo_name=failover-primary port=18213 and failover-backup port=18214",
            )

            print("\n=== 3. CIRCUIT BREAKER AND HALF-OPEN RECOVERY ===")
            control(18215, "http_fail_always")
            circuit = new_commander(
                registry,
                state_dir,
                "demo-circuit",
                retries=0,
                circuit_threshold=2,
                circuit_timeout=1,
            )
            circuit.delegate_task("demo_circuit", task_payload("demo-circuit", "failure-1"))
            circuit.delegate_task("demo_circuit", task_payload("demo-circuit", "failure-2"))
            circuit_instance_key = f"{get_host_ip()}:18215"
            open_circuit_snapshot = circuit.circuit_breaker.snapshot(circuit_instance_key)
            open_metadata = metadata_for_port(registry, 18215)
            before_attempts = len(trace_events(circuit, "agent_call_attempt"))
            blocked = not circuit.delegate_task(
                "demo_circuit", task_payload("demo-circuit", "blocked-while-open")
            )
            after_attempts = len(trace_events(circuit, "agent_call_attempt"))
            hold_nacos_ui(args, "Circuit breaker: inspect demo_name=circuit-primary port=18215, circuit_state=open")
            control(18215, "success")
            time.sleep(1.2)
            recovered = circuit.delegate_task(
                "demo_circuit", task_payload("demo-circuit", "half-open-probe")
            )
            closed_circuit_snapshot = circuit.circuit_breaker.snapshot(circuit_instance_key)
            closed_metadata = metadata_for_port(registry, 18215)
            summary["circuit_breaker"] = {
                "blocked_without_http_attempt": blocked and before_attempts == after_attempts,
                "open_circuit_snapshot": open_circuit_snapshot,
                "open_metadata": open_metadata,
                "half_open_recovered": recovered,
                "closed_circuit_snapshot": closed_circuit_snapshot,
                "closed_metadata": closed_metadata,
                "events": trace_events(
                    circuit,
                    "agent_failure_recorded",
                    "agent_circuit_opened",
                    "agent_circuit_closed",
                ),
            }

            print("\n=== 4. LEASE BUSY STATE, HEARTBEAT LOSS, AND LATE RESPONSE ===")
            if args.show_nacos_ui and args.ui_wait_enter:
                heartbeat_delay_seconds = 3600.0
            elif args.show_nacos_ui:
                heartbeat_delay_seconds = max(2.0, args.ui_hold_seconds + 4.0)
            else:
                heartbeat_delay_seconds = 2.0
            control(18216, "slow_success", delay_seconds=heartbeat_delay_seconds)
            control(18217, "success")
            heartbeat = new_commander(
                registry,
                state_dir,
                "demo-heartbeat",
                retries=0,
                request_timeout=heartbeat_delay_seconds + 2.0,
            )
            heartbeat_result = {}

            no_proxy_session().post(
                "http://127.0.0.1:18216/demo/stop-heartbeat", timeout=3
            ).raise_for_status()

            def invoke_heartbeat_demo():
                heartbeat_result["success"] = heartbeat.delegate_task(
                    "demo_heartbeat", task_payload("demo-heartbeat", "heartbeat-loss")
                )

            worker = threading.Thread(target=invoke_heartbeat_demo, daemon=True)
            worker.start()
            busy_metadata = wait_for_metadata_status(registry, 18216, "busy")
            hold_nacos_ui(args, "Lease state: inspect demo_name=heartbeat-primary port=18216, status=busy")
            registry.forced_stale_ports.add(18216)
            worker.join(timeout=8)
            time.sleep(2.2)
            heartbeat_events = trace_events(
                heartbeat,
                "agent_heartbeat_lost",
                "agent_failover_reassigning",
                "agent_late_response_ignored",
                "agent_call_completed",
            )
            summary["heartbeat_and_lease"] = {
                "success": heartbeat_result.get("success", False),
                "busy_metadata": busy_metadata,
                "events": heartbeat_events,
            }
            hold_nacos_ui(
                args,
                "Heartbeat loss: inspect heartbeat-primary port=18216 unavailable and heartbeat-backup port=18217",
            )

            print("\n=== 5. AGENT BUSINESS ERROR AND TRACEBACK ===")
            control(18218, "business_error")
            traceback_commander = new_commander(
                registry, state_dir, "demo-traceback", retries=0
            )
            traceback_ok = traceback_commander.delegate_task(
                "demo_traceback", task_payload("demo-traceback", "business-error")
            )
            commander_traceback = trace_events(traceback_commander, "agent_call_failed")
            summary["traceback"] = {
                "success_expected_false": not traceback_ok,
                "commander_event": commander_traceback[-1] if commander_traceback else None,
                "agent_log": str(output_dir / "agent_traceback-primary.log"),
            }
    finally:
        agents.stop()
        registry.close()

    agent_trace_log = output_dir / "agent_traceback-primary.log"
    summary["traceback"]["agent_traceback_present"] = (
        "agent_task_failed" in agent_trace_log.read_text(encoding="utf-8")
        and "_raise_nested_business_error" in agent_trace_log.read_text(encoding="utf-8")
    )
    validate_summary(summary)
    summary["all_passed"] = True
    (output_dir / "commander.log").write_text(commander_log.getvalue(), encoding="utf-8")
    (output_dir / "summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    report = build_report(summary, args, output_dir)
    (output_dir / "report.md").write_text(report, encoding="utf-8")

    print_summary(summary)
    print(f"[OUTPUT] {output_dir}")
    print(f"[REPORT] {output_dir / 'report.md'}")


def print_summary(summary):
    print("\n=== RESULT SUMMARY ===")
    print(
        f"[RETRY] success={summary['automatic_retry']['success']} "
        f"attempts={summary['automatic_retry']['attempts']}"
    )
    print(
        f"[FAILOVER] success={summary['same_role_failover']['success']} "
        f"targets={summary['same_role_failover']['targets']}"
    )
    print(
        f"[CIRCUIT] blocked={summary['circuit_breaker']['blocked_without_http_attempt']} "
        f"recovered={summary['circuit_breaker']['half_open_recovered']}"
    )
    print(
        f"[HEARTBEAT] success={summary['heartbeat_and_lease']['success']} "
        f"busy_seen={summary['heartbeat_and_lease']['busy_metadata'].get('status') == 'busy'}"
    )
    print(
        f"[TRACEBACK] commander={bool(summary['traceback']['commander_event'])} "
        f"agent={summary['traceback']['agent_traceback_present']}"
    )
    print("[PASS] All resilience mechanisms were observed")


def validate_summary(summary):
    heartbeat_event_types = {
        event["event_type"] for event in summary["heartbeat_and_lease"]["events"]
    }
    checks = {
        "automatic retry": (
            summary["automatic_retry"]["success"]
            and summary["automatic_retry"]["attempts"] == 2
        ),
        "same-role failover": (
            summary["same_role_failover"]["success"]
            and len(summary["same_role_failover"]["targets"]) == 2
        ),
        "circuit open rejection": summary["circuit_breaker"]["blocked_without_http_attempt"],
        "half-open recovery": summary["circuit_breaker"]["half_open_recovered"],
        "busy lease metadata": (
            summary["heartbeat_and_lease"]["busy_metadata"].get("status") == "busy"
        ),
        "heartbeat failover": {
            "agent_heartbeat_lost",
            "agent_failover_reassigning",
        }.issubset(heartbeat_event_types),
        "Commander traceback": bool(
            (summary["traceback"].get("commander_event") or {}).get("traceback")
        ),
        "Agent traceback": summary["traceback"]["agent_traceback_present"],
    }
    failed = [name for name, passed in checks.items() if not passed]
    if failed:
        raise RuntimeError(f"Resilience demo checks failed: {', '.join(failed)}")


def build_report(summary, args, output_dir):
    return f"""# 异常与弹性机制综合演示结果

## 环境

| 项目 | 值 |
|---|---|
| Nacos | `{args.nacos_addr}` |
| Auth Server | `{args.auth_server_base}` |
| Agent 数量 | {len(AGENT_SPECS)} |

## 结果

| 能力 | 结果 | 关键数据 |
|---|---|---|
| 自动重试 | {summary['automatic_retry']['success']} | attempts={summary['automatic_retry']['attempts']} |
| 同类 Agent 故障转移 | {summary['same_role_failover']['success']} | targets={summary['same_role_failover']['targets']} |
| 熔断期间拒绝请求 | {summary['circuit_breaker']['blocked_without_http_attempt']} | state={summary['circuit_breaker']['open_circuit_snapshot'].get('state')} |
| 半开探测恢复 | {summary['circuit_breaker']['half_open_recovered']} | state={summary['circuit_breaker']['closed_circuit_snapshot'].get('state')} |
| 租约 busy 可见 | {summary['heartbeat_and_lease']['busy_metadata'].get('status') == 'busy'} | lease_work_item={summary['heartbeat_and_lease']['busy_metadata'].get('lease_work_item')} |
| 心跳丢失后转移 | {summary['heartbeat_and_lease']['success']} | events={len(summary['heartbeat_and_lease']['events'])} |
| Commander traceback | {bool(summary['traceback']['commander_event'])} | agent_call_failed |
| Agent traceback | {summary['traceback']['agent_traceback_present']} | agent_task_failed |

## 输出文件

- `{output_dir / 'summary.json'}`
- `{output_dir / 'commander.log'}`
- `{output_dir / 'agent_traceback-primary.log'}`
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
