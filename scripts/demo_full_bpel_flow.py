from __future__ import annotations

import argparse
import contextlib
import io
import json
import os
import shutil
import subprocess
import sys
import textwrap
import time
from pathlib import Path

import requests


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from commander_agent.main import CommanderAgent, load_env_file  # noqa: E402


DEMO_BPEL = """<?xml version="1.0" encoding="UTF-8"?>
<process name="FullA2ADemoWorkflow">
  <sequence name="RootSequence">
    <flow name="ReconAndEvaluateFlow">
      <invoke name="ReconNorth" partnerLink="ReconAgent" operation="scanBeachDefenses" inputVariable="Sector_A" outputVariable="ReconReport"/>
      <invoke name="ReconSouth" partnerLink="ReconAgent" operation="scanBeachDefenses" inputVariable="Sector_A" outputVariable="ReconReport"/>
      <invoke name="EvaluateAllRecon" partnerLink="EvaluatorAgent" operation="evaluateStrike" inputVariable="ReconReport" outputVariable="EvalScore" dependsOn="ReconNorth,ReconSouth"/>
    </flow>
    <switch name="DecisionSwitch">
      <case name="AssaultCase" condition="getVariableData('EvalScore') &gt;= 60">
        <invoke name="AssaultAfterEval" partnerLink="AssaultAgent" operation="captureBeachhead" inputVariable="EvalScore" outputVariable="AssaultResult"/>
      </case>
      <otherwise name="ReplanCase">
        <assign name="ReplanAssign">
          <copy>
            <from>RE-PLAN because strike effect is below threshold.</from>
            <to variable="ReplanResult"/>
          </copy>
        </assign>
      </otherwise>
    </switch>
  </sequence>
</process>
"""


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Demonstrate BPEL loading, DAG scheduling, result collections, and checkpoint recovery"
    )
    parser.add_argument(
        "--mode",
        choices=["remote", "local"],
        default="remote",
        help="remote uses Nacos and real HTTP Agents; local uses in-process fake responses",
    )
    parser.add_argument(
        "--nacos-addr",
        default=os.environ.get("NACOS_ADDR", "127.0.0.1:8848"),
        help="Nacos server address used by Commander and demo Agents",
    )
    parser.add_argument(
        "--auth-server-base",
        default=os.environ.get("A2A_AUTH_SERVER_BASE", "http://127.0.0.1:8080"),
        help="Authentication mock base URL embedded in Agent Cards",
    )
    parser.add_argument(
        "--no-start-infrastructure",
        action="store_true",
        help="Do not automatically run docker compose when Nacos or auth-server is unavailable",
    )
    parser.add_argument(
        "--startup-timeout",
        type=float,
        default=60.0,
        help="Maximum seconds to wait for Nacos, auth-server, and Agent processes",
    )
    parser.add_argument(
        "--state-dir",
        default=str(PROJECT_ROOT / ".a2a_state" / "full_bpel_demo"),
        help="Directory used to persist demo checkpoints and output files",
    )
    parser.add_argument(
        "--mock-eval-score",
        type=int,
        default=86,
        help="Evaluation score returned by the fake evaluator",
    )
    parser.add_argument(
        "--max-activity-workers",
        type=int,
        default=3,
        help="Maximum concurrent BPEL flow activities",
    )
    parser.add_argument(
        "--details",
        action="store_true",
        help="Print complete Commander logs to the terminal instead of only writing them to files",
    )
    return parser.parse_args()


def reset_dir(path: Path) -> None:
    if path.exists():
        shutil.rmtree(path)
    path.mkdir(parents=True, exist_ok=True)


def write_demo_bpel(state_dir: Path) -> Path:
    workflow_path = state_dir / "full_a2a_demo_workflow.bpel"
    workflow_path.write_text(DEMO_BPEL, encoding="utf-8")
    return workflow_path


def output_dir(state_dir: Path) -> Path:
    path = state_dir / "demo_outputs"
    path.mkdir(parents=True, exist_ok=True)
    return path


def http_session() -> requests.Session:
    session = requests.Session()
    session.trust_env = False
    return session


def nacos_base_url(address: str) -> str:
    address = address.split(",", 1)[0].strip().rstrip("/")
    if not address.startswith(("http://", "https://")):
        address = f"http://{address}"
    return address


def nacos_host_port(address: str) -> tuple[str, int]:
    normalized = nacos_base_url(address).split("://", 1)[1]
    host_port = normalized.split("/", 1)[0]
    host, port = host_port.rsplit(":", 1)
    return host, int(port)


def wait_for_http(url: str, timeout: float, label: str) -> None:
    session = http_session()
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
        time.sleep(0.5)
    raise RuntimeError(f"{label} did not become ready: {last_error}")


def ensure_infrastructure(args: argparse.Namespace, files_dir: Path) -> None:
    nacos_url = f"{nacos_base_url(args.nacos_addr)}/nacos/v1/console/health/readiness"
    auth_url = f"{args.auth_server_base.rstrip('/')}/get"
    session = http_session()

    def available(url: str) -> bool:
        try:
            return session.get(url, timeout=2).status_code < 500
        except Exception:
            return False

    if available(nacos_url) and available(auth_url):
        print(f"[INFRA] Nacos ready at {args.nacos_addr}; auth-server ready at {args.auth_server_base}")
        return

    if args.no_start_infrastructure:
        raise RuntimeError(
            "Nacos or auth-server is unavailable. Start them with "
            "'docker compose up -d nacos auth-server' or omit --no-start-infrastructure."
        )

    _, nacos_port = nacos_host_port(args.nacos_addr)
    compose_log = files_dir / "docker_compose.log"
    env = os.environ.copy()
    env["NACOS_PORT"] = str(nacos_port)
    command = ["docker", "compose", "up", "-d", "nacos", "auth-server"]
    result = subprocess.run(
        command,
        cwd=PROJECT_ROOT,
        env=env,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
    )
    compose_log.write_text(result.stdout, encoding="utf-8")
    if result.returncode != 0:
        raise RuntimeError(
            f"Failed to start Nacos with Docker Compose. See {compose_log}. "
            "If Docker is stopped, run 'sudo systemctl restart docker'."
        )

    wait_for_http(nacos_url, args.startup_timeout, "Nacos")
    wait_for_http(auth_url, args.startup_timeout, "auth-server")
    print(f"[INFRA] Started Nacos at {args.nacos_addr} and auth-server at {args.auth_server_base}")


DEMO_AGENT_SPECS = [
    ("recon-a", "recon_agent/main.py", "RECON_AGENT_PORT", 18112, "recon"),
    ("recon-b", "recon_agent/main.py", "RECON_AGENT_PORT", 18116, "recon"),
    ("evaluator", "evaluator_agent/main.py", "EVALUATOR_AGENT_PORT", 18115, "evaluator"),
    ("assault", "assault_agent/main.py", "ASSAULT_AGENT_PORT", 18114, "assault"),
]


class DemoAgentProcesses:
    def __init__(self, files_dir: Path, nacos_addr: str, auth_server_base: str, timeout: float):
        self.files_dir = files_dir
        self.nacos_addr = nacos_addr
        self.auth_server_base = auth_server_base
        self.timeout = timeout
        self.processes: list[tuple[str, subprocess.Popen, object]] = []

    def start(self) -> None:
        for name, script, port_variable, port, _ in DEMO_AGENT_SPECS:
            log_handle = (self.files_dir / f"agent_{name}.log").open("w", encoding="utf-8")
            env = os.environ.copy()
            env["PYTHONPATH"] = f"{PROJECT_ROOT}:{env.get('PYTHONPATH', '')}"
            env["NACOS_ADDR"] = self.nacos_addr
            env["A2A_AUTH_SERVER_BASE"] = self.auth_server_base
            env[port_variable] = str(port)
            process = subprocess.Popen(
                [sys.executable, "-u", script],
                cwd=PROJECT_ROOT,
                env=env,
                stdout=log_handle,
                stderr=subprocess.STDOUT,
            )
            self.processes.append((name, process, log_handle))

        for name, _, _, port, _ in DEMO_AGENT_SPECS:
            wait_for_http(f"http://127.0.0.1:{port}/health", self.timeout, f"Agent {name}")
        self._wait_for_nacos_registration()
        print("[AGENTS] Registered 2 recon, 1 evaluator, and 1 assault Agent in Nacos")

    def _wait_for_nacos_registration(self) -> None:
        expected_ports = {port for _, _, _, port, _ in DEMO_AGENT_SPECS}
        url = f"{nacos_base_url(self.nacos_addr)}/nacos/v1/ns/instance/list"
        session = http_session()
        deadline = time.time() + self.timeout
        while time.time() < deadline:
            try:
                response = session.get(
                    url,
                    params={"serviceName": "A2A-Agent"},
                    timeout=2,
                )
                response.raise_for_status()
                registered_ports = {
                    int(host["port"])
                    for host in response.json().get("hosts", [])
                    if host.get("healthy", True)
                }
                if expected_ports.issubset(registered_ports):
                    return
            except Exception:
                pass
            time.sleep(0.5)
        raise RuntimeError(f"Demo Agents were not registered in Nacos within {self.timeout}s")

    def wait_until_idle(self) -> None:
        expected_ports = {port for _, _, _, port, _ in DEMO_AGENT_SPECS}
        url = f"{nacos_base_url(self.nacos_addr)}/nacos/v1/ns/instance/list"
        session = http_session()
        deadline = time.time() + self.timeout
        while time.time() < deadline:
            try:
                response = session.get(
                    url,
                    params={"serviceName": "A2A-Agent"},
                    timeout=2,
                )
                response.raise_for_status()
                idle_ports = {
                    int(host["port"])
                    for host in response.json().get("hosts", [])
                    if host.get("healthy", True)
                    and (host.get("metadata") or {}).get("status") == "idle"
                }
                if expected_ports.issubset(idle_ports):
                    print("[AGENTS] All demo Agent leases released; recovery run can start")
                    return
            except Exception:
                pass
            time.sleep(0.5)
        raise RuntimeError("Demo Agents did not return to idle before the recovery run")

    def stop(self) -> None:
        for _, process, _ in self.processes:
            if process.poll() is None:
                process.terminate()
        for _, process, log_handle in self.processes:
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=5)
            log_handle.close()


def write_json(path: Path, payload) -> Path:
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


def result_collections(context: dict) -> dict:
    return {
        key: context.get(key, [])
        for key in ("recon_report", "eval_score", "assault_result", "replan_result")
    }


def trace_events(context: dict) -> list[dict]:
    interesting = {
        "flow_activity_started",
        "flow_child_activity_scheduled",
        "flow_child_activity_finished",
        "flow_activity_finished",
        "dag_resume_cleanup",
        "interrupted_activities_recovered",
        "agent_result_applied",
        "agent_call_attempt",
        "agent_call_completed",
        "agent_call_failed",
        "workflow_finished",
    }
    return [
        event
        for event in context.get("trace", [])
        if event.get("event_type") in interesting
    ]


def calls_from_trace(context: dict) -> list[dict]:
    return [
        {
            "role": event.get("role"),
            "work_item": event.get("work_item"),
            "target": event.get("target"),
            "attempt": event.get("attempt"),
        }
        for event in context.get("trace", [])
        if event.get("event_type") in {"agent_call_completed", "local_agent_call_completed"}
    ]


def save_run_outputs(
    *,
    files_dir: Path,
    label: str,
    commander_log: str,
    context: dict,
    calls: list[dict],
    checkpoint_path: Path,
) -> dict[str, Path]:
    files = {
        "context": write_json(files_dir / f"{label}_context.json", context),
        "trace": write_json(files_dir / f"{label}_trace.json", trace_events(context)),
        "results": write_json(files_dir / f"{label}_result_collections.json", result_collections(context)),
        "calls": write_json(files_dir / f"{label}_calls.json", calls),
        "commander_log": files_dir / f"{label}_commander.log",
    }
    files["commander_log"].write_text(commander_log, encoding="utf-8")
    files["checkpoint"] = checkpoint_path
    return files


def write_markdown_report(
    *,
    files_dir: Path,
    workflow_path: Path,
    normal_context: dict,
    recovery_context: dict,
    normal_files: dict[str, Path],
    recovery_files: dict[str, Path],
    mode: str,
    nacos_addr: str,
    auth_server_base: str,
) -> Path:
    recovery_cleanup = [
        event
        for event in recovery_context.get("trace", [])
        if event.get("event_type") == "dag_resume_cleanup"
    ]
    cleanup_text = "无"
    if recovery_cleanup:
        event = recovery_cleanup[-1]
        cleanup_text = (
            f"affected={event['affected_activity_ids']}, "
            f"removed={event['removed_outputs']}"
        )

    report = f"""# A2A BPEL 全流程演示结果

## 输入

- BPEL 文件：`{workflow_path}`
- 执行模式：`{mode}`
- Nacos 地址：`{nacos_addr if mode == 'remote' else '未使用'}`
- Auth 地址：`{auth_server_base if mode == 'remote' else '未使用'}`
- 正常 workflow：`{normal_context['workflow_id']}`
- 恢复 workflow：`{recovery_context['workflow_id']}`

## 正常执行摘要

- 状态：`{normal_context['workflow_status']}`
- Recon 结果数：`{len(normal_context.get('recon_report', []))}`
- Eval 结果数：`{len(normal_context.get('eval_score', []))}`
- Assault 结果数：`{len(normal_context.get('assault_result', []))}`
- checkpoint：`{normal_files['checkpoint']}`

详细文件：

- context：`{normal_files['context']}`
- trace：`{normal_files['trace']}`
- result collections：`{normal_files['results']}`
- calls：`{normal_files['calls']}`
- commander log：`{normal_files['commander_log']}`

## 恢复执行摘要

- 状态：`{recovery_context['workflow_status']}`
- 恢复清理：`{cleanup_text}`
- Recon 结果数：`{len(recovery_context.get('recon_report', []))}`
- Eval 结果数：`{len(recovery_context.get('eval_score', []))}`
- Assault 结果数：`{len(recovery_context.get('assault_result', []))}`
- checkpoint：`{recovery_files['checkpoint']}`

详细文件：

- context：`{recovery_files['context']}`
- trace：`{recovery_files['trace']}`
- result collections：`{recovery_files['results']}`
- calls：`{recovery_files['calls']}`
- commander log：`{recovery_files['commander_log']}`

## 展示重点

1. BPEL 被解析成 `work_list`。
2. `flow` 中多个 Recon 并发执行。
3. `dependsOn` 与 `inputVariable/outputVariable` 一起形成 DAG 依赖。
4. 多个 `ReconReport` 作为结果集合传给 Evaluator。
5. `EvalScore` 作为结果集合传给 Assault。
6. 恢复时复用已完成 Recon，清理失败节点及下游旧输出，再重跑受影响节点。
"""
    path = files_dir / "demo_report.md"
    path.write_text(report, encoding="utf-8")
    return path


def print_work_list(commander: CommanderAgent) -> None:
    print("\n=== 1. BPEL LOADED AS work_list ===")
    print("idx  type       name                 role        output          depends_on")
    print("---  ---------  -------------------  ----------  --------------  ----------------")
    for item in commander.workflow_context["work_list"]:
        print(
            f"{item['activatity_index']:>3}  "
            f"{item['type']:<9}  "
            f"{item['name']:<19}  "
            f"{(item['role'] or '-'): <10}  "
            f"{str(item.get('output_variable') or '-'): <14}  "
            f"{','.join(item.get('depends_on', [])) or '-'}"
        )


def make_fake_delegate(commander: CommanderAgent, calls: list[dict], label: str, verbose: bool = False):
    def fake_delegate(role, payload, stream=False):
        started = time.perf_counter()
        activity_id = payload["activatity_id"]
        input_payload = payload.get("input", {})

        if role == "recon":
            value = f"{activity_id} reports obstacles and fire points in {input_payload.get('sector')}"
            time.sleep(0.08)
        elif role == "evaluator":
            recon_entries = input_payload.get("recon_report", [])
            value = int(input_payload.get("mock_eval_score", 40))
            if verbose:
                print(
                    f"[{label}] evaluator received recon_report collection: "
                    f"{len(recon_entries)} entries"
                )
                for index, entry in enumerate(recon_entries, start=1):
                    print(f"    recon[{index}] role={entry.get('role')} value={entry.get('value')}")
            time.sleep(0.02)
        elif role == "assault":
            eval_entries = input_payload.get("eval_score", [])
            latest_eval = eval_entries[-1]["value"] if eval_entries else "unknown"
            value = f"Assault executed after EvalScore={latest_eval}"
            if verbose:
                print(f"[{label}] assault received eval_score collection: {eval_entries}")
        else:
            value = f"{role} completed"

        duration_ms = round((time.perf_counter() - started) * 1000, 3)
        commander._remember_task_response(
            payload["work_item"],
            {
                "status": "completed",
                "output": {payload["output_hint"]: value},
                "metrics": {"duration_ms": duration_ms},
            },
            role=role,
            target="demo",
        )
        calls.append(
            {
                "role": role,
                "activity_id": activity_id,
                "input_keys": sorted(input_payload.keys()),
                "duration_ms": duration_ms,
            }
        )
        return True

    return fake_delegate


def print_trace_summary(context: dict, *, verbose: bool = False) -> None:
    if verbose:
        print("\n=== 3. DAG TRACE SUMMARY ===")
    for event in context["trace"]:
        if event["event_type"] == "flow_activity_started":
            if verbose:
                print(
                    f"[FLOW] activity={event['activity_id']} mode={event['execution_mode']} "
                    f"dependencies={event['dependencies']}"
                )
        if event["event_type"] == "flow_child_activity_scheduled":
            if verbose:
                print(
                    f"[SCHEDULE] child={event['child_activity_id']} "
                    f"depends_on={event['dependencies']} mode={event['execution_mode']}"
                )
        if event["event_type"] == "dag_resume_cleanup":
            if verbose:
                print(
                    f"[RECOVERY] failed={event['failed_activity_ids']} "
                    f"affected={event['affected_activity_ids']} removed={event['removed_outputs']}"
                )


def print_result_collections(context: dict, *, verbose: bool = False) -> None:
    if verbose:
        print("\n=== 4. RESULT COLLECTIONS PASSED DOWNSTREAM ===")
    for key in ("recon_report", "eval_score", "assault_result", "replan_result"):
        entries = context.get(key, [])
        if verbose:
            print(f"{key}: {len(entries)} entr{'y' if len(entries) == 1 else 'ies'}")
        for entry in entries:
            if verbose:
                print(
                    f"  - activity={entry.get('activity_id')} role={entry.get('role')} "
                    f"status={entry.get('status')} duration_ms={entry.get('duration_ms')} "
                    f"value={entry.get('value')}"
                )


def run_normal_workflow(
    workflow_path: Path,
    state_dir: Path,
    files_dir: Path,
    mock_eval_score: int,
    max_activity_workers: int,
    *,
    mode: str,
    details: bool = False,
):
    setup_output = io.StringIO()
    setup_redirect = contextlib.nullcontext() if details else contextlib.redirect_stdout(setup_output)
    with setup_redirect:
        commander = CommanderAgent(
            mode=mode,
            workflow="bpel",
            workflow_file=str(workflow_path),
            workflow_id="demo-full-normal",
            state_dir=str(state_dir),
            mock_eval_score=mock_eval_score,
            max_activity_workers=max_activity_workers,
        )
    print_work_list(commander)

    calls: list[dict] = []
    if mode == "local":
        commander.delegate_task = make_fake_delegate(commander, calls, "normal", verbose=details)

    print("\n=== 2. RUN WORKFLOW: BPEL + DAG + A2A PAYLOADS ===")
    output = io.StringIO()
    redirect = contextlib.nullcontext() if details else contextlib.redirect_stdout(output)
    with redirect:
        context = commander.run_bpel_workflow()
    if mode == "remote":
        calls = calls_from_trace(context)
    commander_log = "" if details else setup_output.getvalue() + output.getvalue()
    files = save_run_outputs(
        files_dir=files_dir,
        label="normal",
        commander_log=commander_log,
        context=context,
        calls=calls,
        checkpoint_path=commander.state_store.state_path(commander.workflow_id),
    )
    print(f"[DONE] workflow_status={context['workflow_status']}")
    print(f"[SUMMARY] calls={len(calls)} recon_results={len(context['recon_report'])} eval_results={len(context['eval_score'])} assault_results={len(context['assault_result'])}")
    print(f"[FILES] context={files['context']}")
    print(f"[FILES] trace={files['trace']}")
    print(f"[FILES] results={files['results']}")
    print(f"[FILES] commander_log={files['commander_log']}")
    print(f"[CHECKPOINT] {files['checkpoint']}")
    return commander, context, files


def activity_by_name(commander: CommanderAgent) -> dict[str, object]:
    return {
        activity.name: activity
        for activity in commander.bpel_definition.activatities_by_id.values()
    }


def work_items_by_activity(context: dict) -> dict[str, dict]:
    return {
        item["activatity_id"]: item
        for item in context["work_list"]
    }


def seed_failed_checkpoint(workflow_path: Path, state_dir: Path) -> tuple[str, dict]:
    workflow_id = "demo-full-recovery"
    with contextlib.redirect_stdout(io.StringIO()):
        seed = CommanderAgent(
            mode="local",
            workflow="bpel",
            workflow_file=str(workflow_path),
            workflow_id=workflow_id,
            state_dir=str(state_dir),
            mock_eval_score=30,
        )
    context = seed.workflow_context
    by_name = activity_by_name(seed)
    by_item = work_items_by_activity(context)

    root_id = by_name["RootSequence"].activatity_id
    flow_id = by_name["ReconAndEvaluateFlow"].activatity_id
    recon_north_id = by_name["ReconNorth"].activatity_id
    recon_south_id = by_name["ReconSouth"].activatity_id
    eval_id = by_name["EvaluateAllRecon"].activatity_id
    switch_id = by_name["DecisionSwitch"].activatity_id
    assault_case_id = by_name["AssaultCase"].activatity_id
    assault_id = by_name["AssaultAfterEval"].activatity_id

    for activity_id in (root_id, flow_id):
        by_item[activity_id]["status"] = "failed"
    for activity_id in (recon_north_id, recon_south_id):
        by_item[activity_id]["status"] = "completed"
    by_item[eval_id]["status"] = "failed"
    by_item[switch_id]["status"] = "completed"
    by_item[assault_case_id]["status"] = "completed"
    by_item[assault_id]["status"] = "completed"
    context["workflow_status"] = "paused"

    context["recon_report"] = [
        seed._make_context_entry(
            "cached north recon",
            activity_id=recon_north_id,
            work_item=by_item[recon_north_id]["work_item"],
            role="recon",
            output={"recon_report": "cached north recon"},
        ),
        seed._make_context_entry(
            "cached south recon",
            activity_id=recon_south_id,
            work_item=by_item[recon_south_id]["work_item"],
            role="recon",
            output={"recon_report": "cached south recon"},
        ),
    ]
    context["eval_score"] = [
        seed._make_context_entry(
            30,
            activity_id=eval_id,
            work_item=by_item[eval_id]["work_item"],
            role="evaluator",
            status="failed",
            error="simulated evaluator failure",
            output={"eval_score": 30},
        )
    ]
    context["assault_result"] = [
        seed._make_context_entry(
            "stale assault output",
            activity_id=assault_id,
            work_item=by_item[assault_id]["work_item"],
            role="assault",
            output={"assault_result": "stale assault output"},
        )
    ]
    context["agent_results"] = {
        by_item[eval_id]["work_item"]: {"output": {"eval_score": 30}},
        by_item[assault_id]["work_item"]: {"output": {"assault_result": "stale assault output"}},
    }

    seed.state_store.save(
        workflow_id,
        {
            "workflow_id": workflow_id,
            "workflow": "bpel",
            "mode": "local",
            "status": "paused",
            "context": context,
        },
    )
    return workflow_id, context


def run_recovery_workflow(
    workflow_path: Path,
    state_dir: Path,
    files_dir: Path,
    mock_eval_score: int,
    max_activity_workers: int,
    *,
    mode: str,
    details: bool = False,
):
    print("\n=== 5. RESUME FROM FAILED CHECKPOINT ===")
    workflow_id, seeded_context = seed_failed_checkpoint(workflow_path, state_dir)
    print("[SEEDED] failed evaluator and stale assault output")
    seeded_results_path = write_json(
        files_dir / "recovery_seeded_result_collections.json",
        result_collections(seeded_context),
    )
    print(f"[FILES] seeded_results={seeded_results_path}")

    setup_output = io.StringIO()
    setup_redirect = contextlib.nullcontext() if details else contextlib.redirect_stdout(setup_output)
    with setup_redirect:
        commander = CommanderAgent(
            mode=mode,
            workflow="bpel",
            workflow_file=str(workflow_path),
            workflow_id=workflow_id,
            state_dir=str(state_dir),
            mock_eval_score=mock_eval_score,
            max_activity_workers=max_activity_workers,
            resume=True,
        )
    calls: list[dict] = []
    if mode == "local":
        commander.delegate_task = make_fake_delegate(commander, calls, "recovery", verbose=details)
    output = io.StringIO()
    redirect = contextlib.nullcontext() if details else contextlib.redirect_stdout(output)
    with redirect:
        context = commander.run_bpel_workflow()
    if mode == "remote":
        calls = calls_from_trace(context)
    commander_log = "" if details else setup_output.getvalue() + output.getvalue()
    files = save_run_outputs(
        files_dir=files_dir,
        label="recovery",
        commander_log=commander_log,
        context=context,
        calls=calls,
        checkpoint_path=commander.state_store.state_path(commander.workflow_id),
    )

    print(f"[RECOVERED] workflow_status={context['workflow_status']}")
    print("[RECOVERY] completed recon nodes are reused; failed/downstream nodes rerun")
    print(f"[SUMMARY] rerun_calls={len(calls)} roles={[call['role'] for call in calls]}")
    cleanup_events = [
        event
        for event in context["trace"]
        if event.get("event_type") == "dag_resume_cleanup"
    ]
    if cleanup_events:
        event = cleanup_events[-1]
        print(
            f"[SUMMARY] cleanup affected={event['affected_activity_ids']} "
            f"removed={event['removed_outputs']}"
        )
    print(f"[FILES] context={files['context']}")
    print(f"[FILES] trace={files['trace']}")
    print(f"[FILES] results={files['results']}")
    print(f"[FILES] commander_log={files['commander_log']}")
    print(f"[CHECKPOINT] {files['checkpoint']}")
    return context, files


def main() -> None:
    load_env_file()
    args = parse_args()
    os.environ["NACOS_ADDR"] = args.nacos_addr
    os.environ["A2A_AUTH_SERVER_BASE"] = args.auth_server_base
    state_dir = Path(args.state_dir).expanduser().resolve()
    reset_dir(state_dir)
    workflow_path = write_demo_bpel(state_dir)
    files_dir = output_dir(state_dir)

    print(
        textwrap.dedent(
            f"""
            === FULL A2A BPEL FLOW DEMO ===
            mode: {args.mode}
            nacos_addr: {args.nacos_addr if args.mode == 'remote' else 'not used'}
            auth_server_base: {args.auth_server_base if args.mode == 'remote' else 'not used'}
            workflow_file: {workflow_path}
            state_dir: {state_dir}

            Remote mode uses Nacos service discovery, Agent leases, Agent Card discovery,
            authentication, and HTTP A2A sendMessage calls. Local mode uses fake responses.
            """
        ).strip()
    )

    agent_processes = None
    try:
        if args.mode == "remote":
            ensure_infrastructure(args, files_dir)
            agent_processes = DemoAgentProcesses(
                files_dir,
                args.nacos_addr,
                args.auth_server_base,
                args.startup_timeout,
            )
            agent_processes.start()

        _, normal_context, normal_files = run_normal_workflow(
            workflow_path,
            state_dir,
            files_dir,
            args.mock_eval_score,
            args.max_activity_workers,
            mode=args.mode,
            details=args.details,
        )
        if agent_processes is not None:
            agent_processes.wait_until_idle()
        recovery_context, recovery_files = run_recovery_workflow(
            workflow_path,
            state_dir,
            files_dir,
            args.mock_eval_score,
            args.max_activity_workers,
            mode=args.mode,
            details=args.details,
        )
    finally:
        if agent_processes is not None:
            agent_processes.stop()

    report_path = write_markdown_report(
        files_dir=files_dir,
        workflow_path=workflow_path,
        normal_context=normal_context,
        recovery_context=recovery_context,
        normal_files=normal_files,
        recovery_files=recovery_files,
        mode=args.mode,
        nacos_addr=args.nacos_addr,
        auth_server_base=args.auth_server_base,
    )
    print(f"\n=== REPORT ===")
    print(f"[REPORT] {report_path}")
    print(f"[OUTPUT_DIR] {files_dir}")


if __name__ == "__main__":
    try:
        main()
    except RuntimeError as exc:
        print(f"\n[ERROR] {exc}", file=sys.stderr)
        raise SystemExit(1)
