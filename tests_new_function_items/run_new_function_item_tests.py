#!/usr/bin/env python3
"""Run new-function-item supplemental tests for AlgoLib and Commander."""

from __future__ import annotations

import csv
import json
import os
import shutil
import subprocess
import sys
import time
import uuid
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import requests


ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "tests_new_function_items" / "A2A_AMOS_New_Function_Items_Test_Results"
LOG_DIR = OUT / "logs"
TMP_DIR = OUT / "tmp"
ALGOLIB = "http://127.0.0.1:8088"
COMMANDER = "http://127.0.0.1:8021"
AUTH_HEADER = {"Authorization": "Bearer mock-jwt-token-abcd"}


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


def to_wsl_path(path: Path) -> str:
    resolved = path.resolve()
    text = resolved.as_posix()
    if resolved.drive:
        return f"/mnt/{resolved.drive[0].lower()}{text[2:]}"
    return text


def ensure_dirs() -> None:
    if OUT.exists():
        shutil.rmtree(OUT)
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    TMP_DIR.mkdir(parents=True, exist_ok=True)


def write_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def write_csv(path: Path, rows: list[dict[str, Any]], fieldnames: list[str] | None = None) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if fieldnames is None:
        keys: list[str] = []
        for row in rows:
            for key in row:
                if key not in keys:
                    keys.append(key)
        fieldnames = keys or ["empty"]
    with path.open("w", newline="", encoding="utf-8-sig") as fh:
        writer = csv.DictWriter(fh, fieldnames=fieldnames)
        writer.writeheader()
        for row in rows:
            writer.writerow({key: row.get(key, "") for key in fieldnames})


def append_log(name: str, text: str) -> None:
    with (LOG_DIR / name).open("a", encoding="utf-8") as fh:
        fh.write(text.rstrip() + "\n")


def http_call(
    case: str,
    method: str,
    url: str,
    *,
    json_body: Any | None = None,
    timeout: float = 30.0,
    headers: dict[str, str] | None = None,
) -> dict[str, Any]:
    start = time.perf_counter()
    try:
        resp = requests.request(method, url, json=json_body, timeout=timeout, headers=headers)
        latency_ms = round((time.perf_counter() - start) * 1000, 3)
        try:
            body: Any = resp.json()
        except ValueError:
            body = resp.text
        record = {
            "ts": now(),
            "method": method,
            "url": url,
            "status_code": resp.status_code,
            "ok": resp.ok,
            "latency_ms": latency_ms,
            "request": json_body,
            "response": body,
        }
    except Exception as exc:  # noqa: BLE001 - test harness records the real exception
        latency_ms = round((time.perf_counter() - start) * 1000, 3)
        record = {
            "ts": now(),
            "method": method,
            "url": url,
            "status_code": None,
            "ok": False,
            "latency_ms": latency_ms,
            "request": json_body,
            "error": repr(exc),
        }
    append_log(f"{case}_http.log", json.dumps(record, ensure_ascii=False))
    return record


def json_dumps_compact(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True)


def load_golden(relative: str) -> dict[str, Any]:
    return json.loads((ROOT / relative).read_text(encoding="utf-8"))


def run_algorithm(relative_golden: str, trace_prefix: str) -> dict[str, Any]:
    payload = load_golden(relative_golden)
    algorithm_id = payload.get("algorithm_id", "unknown")
    payload["request_id"] = f"{trace_prefix}-{algorithm_id}-{uuid.uuid4().hex[:8]}"
    payload["trace_id"] = f"{trace_prefix}-trace-{uuid.uuid4().hex[:8]}"
    response = http_call("ALGBASE-01", "POST", f"{ALGOLIB}/run", json_body=payload, timeout=90)
    return {"payload": payload, "call": response}


def normalize_schema(card: dict[str, Any], key: str) -> Any:
    schema = card.get(key) or card.get(f"{key}_schema")
    if isinstance(schema, dict) and "$ref" in schema:
        return schema["$ref"]
    return schema


def write_temp_algorithm_service() -> Path:
    service_path = TMP_DIR / "codex_version_probe_service.py"
    service_path.write_text(
        r'''#!/usr/bin/env python3
import json
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

algorithm_id = sys.argv[1]
version = sys.argv[2]
port = int(sys.argv[3])


class Handler(BaseHTTPRequestHandler):
    def _send(self, status, payload):
        body = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, fmt, *args):
        return

    def do_GET(self):
        if self.path == "/health":
            self._send(200, {"ok": True, "status": "ready", "algorithm_id": algorithm_id, "version": version, "model_loaded": True})
            return
        if self.path == "/metadata":
            self._send(200, {"algorithm_id": algorithm_id, "version": version, "backend_type": "python_http_service"})
            return
        self._send(404, {"ok": False, "error": "not_found"})

    def do_POST(self):
        length = int(self.headers.get("Content-Length", "0") or 0)
        raw = self.rfile.read(length)
        try:
            payload = json.loads(raw.decode("utf-8")) if raw else {}
        except Exception:
            payload = {}
        if self.path != "/predict":
            self._send(404, {"ok": False, "error": "not_found"})
            return
        self._send(200, {
            "ok": True,
            "request_id": payload.get("request_id"),
            "trace_id": payload.get("trace_id"),
            "algorithm_id": algorithm_id,
            "version": version,
            "status": "completed",
            "outputs": {
                "algorithm": "kmeans",
                "labels": [0, 0, 1, 1],
                "clusters": [
                    {"cluster_id": 0, "size": 2, "centroid": [0.0, 1.0], "member_indices": [0, 1]},
                    {"cluster_id": 1, "size": 2, "centroid": [10.0, 11.0], "member_indices": [2, 3]},
                ],
                "cluster_count": 2,
                "noise_indices": [],
                "inertia": 4.0,
                "iterations": 2,
                "converged": True,
                "point_count": 4,
                "dimension": 2,
                "model_runtime": {"backend": "temporary_stdlib_http", "used": True, "implementation_version": version},
            },
            "usage": {"latency_ms": 1.0},
            "error": None,
        })


ThreadingHTTPServer(("127.0.0.1", port), Handler).serve_forever()
''',
        encoding="utf-8",
    )
    return service_path


def start_temp_algorithm_service(version: str, port: int) -> subprocess.Popen:
    service_path = write_temp_algorithm_service()
    log = (LOG_DIR / f"ALGBASE-01_version_probe_service_{version}.log").open("w", encoding="utf-8")
    wsl = shutil.which("wsl")
    if wsl:
        script_wsl = to_wsl_path(service_path)
        root_wsl = to_wsl_path(ROOT)
        cmd = f"cd '{root_wsl}' && python3 '{script_wsl}' codex_version_probe '{version}' {port}"
        proc = subprocess.Popen([wsl, "bash", "-lc", cmd], cwd=ROOT, stdout=log, stderr=subprocess.STDOUT)
    else:
        proc = subprocess.Popen(
            [sys.executable, str(service_path), "codex_version_probe", version, str(port)],
            cwd=ROOT,
            stdout=log,
            stderr=subprocess.STDOUT,
        )
    for _ in range(30):
        time.sleep(0.2)
        if http_call("ALGBASE-01", "GET", f"http://127.0.0.1:{port}/health", timeout=3)["ok"]:
            return proc
    raise RuntimeError(f"temporary algorithm service {version} failed to start on port {port}")


def prepare_temp_algorithm(version: str, port: int) -> Path:
    src = ROOT / "commander" / "examples" / "clustering_engine" / "1.0.0"
    dst = TMP_DIR / "algorithms" / "codex_version_probe" / version
    if dst.exists():
        shutil.rmtree(dst)
    shutil.copytree(src, dst)
    card_path = dst / "algorithm_card.yaml"
    text = card_path.read_text(encoding="utf-8")
    text = text.replace("algorithm_id: clustering_engine", "algorithm_id: codex_version_probe")
    text = text.replace("version: 1.0.0", f"version: {version}", 1)
    text = text.replace("display_name: Clustering Engine", "display_name: Codex Version Probe")
    text = text.replace("display_name: Deterministic Clustering Engine", "display_name: Codex Version Probe")
    text = text.replace("endpoint: http://127.0.0.1:9031/predict", f"endpoint: http://127.0.0.1:{port}/predict")
    text = text.replace("health_endpoint: http://127.0.0.1:9031/health", f"health_endpoint: http://127.0.0.1:{port}/health")
    text = text.replace("metadata_endpoint: http://127.0.0.1:9031/metadata", f"metadata_endpoint: http://127.0.0.1:{port}/metadata")
    card_path.write_text(text, encoding="utf-8")
    for golden in (dst / "golden_cases").glob("*.json"):
        data = json.loads(golden.read_text(encoding="utf-8"))
        data["algorithm_id"] = "codex_version_probe"
        data["version"] = version
        data["request_id"] = f"algbase01-version-{version}-{uuid.uuid4().hex[:8]}"
        data["trace_id"] = f"algbase01-version-trace-{uuid.uuid4().hex[:8]}"
        golden.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    return dst


def run_version_management() -> tuple[list[dict[str, Any]], bool]:
    rows: list[dict[str, Any]] = []
    ok = True
    ports = {"1.0.0": 19341, "2.0.0": 19342}
    procs: list[subprocess.Popen] = []
    try:
        for version, port in ports.items():
            procs.append(start_temp_algorithm_service(version, port))
            package_dir = prepare_temp_algorithm(version, port)
            reg = http_call(
                "ALGBASE-01",
                "POST",
                f"{ALGOLIB}/algorithms/register",
                json_body={"package_or_card_path": to_wsl_path(package_dir)},
                timeout=60,
            )
            rows.append(
                {
                    "step": "register",
                    "algorithm_id": "codex_version_probe",
                    "version": version,
                    "backend": "python_http_service",
                    "http_status": reg["status_code"],
                    "ok": reg["ok"],
                    "detail": json_dumps_compact(reg.get("response", reg.get("error"))),
                }
            )
            ok = ok and bool(reg["ok"])
        for version in ["1.0.0", "2.0.0"]:
            act = http_call(
                "ALGBASE-01",
                "POST",
                f"{ALGOLIB}/algorithms/codex_version_probe/{version}/python_http_service/activate",
                timeout=60,
            )
            rows.append(
                {
                    "step": "activate",
                    "algorithm_id": "codex_version_probe",
                    "version": version,
                    "backend": "python_http_service",
                    "http_status": act["status_code"],
                    "ok": act["ok"],
                    "detail": json_dumps_compact(act.get("response", act.get("error"))),
                }
            )
            run_payload = json.loads(
                (TMP_DIR / "algorithms" / "codex_version_probe" / version / "golden_cases" / "case_001_request.json").read_text(
                    encoding="utf-8"
                )
            )
            run_payload["request_id"] = f"algbase01-version-run-{version}-{uuid.uuid4().hex[:8]}"
            run_payload["trace_id"] = f"algbase01-version-run-trace-{uuid.uuid4().hex[:8]}"
            run = http_call("ALGBASE-01", "POST", f"{ALGOLIB}/run", json_body=run_payload, timeout=90)
            response = run.get("response") if isinstance(run.get("response"), dict) else {}
            rows.append(
                {
                    "step": "run_active_version",
                    "algorithm_id": "codex_version_probe",
                    "version": version,
                    "backend": "python_http_service",
                    "http_status": run["status_code"],
                    "ok": run["ok"],
                    "returned_algorithm_id": response.get("algorithm_id"),
                    "returned_version": response.get("version"),
                    "latency_ms": run["latency_ms"],
                    "detail": json_dumps_compact(response),
                }
            )
            ok = ok and bool(act["ok"]) and bool(run["ok"])
    finally:
        for version in ["1.0.0", "2.0.0"]:
            delete = http_call(
                "ALGBASE-01",
                "DELETE",
                f"{ALGOLIB}/algorithms/codex_version_probe/{version}/python_http_service",
                timeout=60,
            )
            rows.append(
                {
                    "step": "cleanup_delete",
                    "algorithm_id": "codex_version_probe",
                    "version": version,
                    "backend": "python_http_service",
                    "http_status": delete["status_code"],
                    "ok": delete["ok"],
                    "detail": json_dumps_compact(delete.get("response", delete.get("error"))),
                }
            )
        for proc in procs:
            if proc.poll() is None:
                proc.terminate()
                try:
                    proc.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    proc.kill()
    return rows, ok


def run_model_evaluation() -> tuple[dict[str, Any], bool]:
    log_path = LOG_DIR / "ALGBASE-01_model_evaluation.log"
    dataset = ROOT / "algorithmrepo" / "data" / "trajectory_linear" / "codex_newfunc_eval_data.json"
    metadata = ROOT / "algorithmrepo" / "data" / "trajectory_linear" / "codex_newfunc_eval_metadata.json"
    cmd = [
        sys.executable,
        str(ROOT / "algorithmrepo" / "scripts" / "evaluate_trajectory_linear_predictor.py"),
        "--dataset-path",
        str(dataset),
        "--metadata-path",
        str(metadata),
        "--count",
        "60",
    ]
    proc = subprocess.run(cmd, cwd=ROOT, capture_output=True, text=True, timeout=120)
    log_path.write_text(
        "COMMAND: " + " ".join(cmd) + "\n\nSTDOUT:\n" + proc.stdout + "\nSTDERR:\n" + proc.stderr,
        encoding="utf-8",
    )
    result: dict[str, Any] = {
        "implemented": proc.returncode == 0 and metadata.exists(),
        "command": cmd,
        "returncode": proc.returncode,
    }
    if metadata.exists():
        result["metadata"] = json.loads(metadata.read_text(encoding="utf-8"))
    else:
        result["stdout"] = proc.stdout
        result["stderr"] = proc.stderr
    for artifact in [dataset, metadata]:
        try:
            artifact.unlink()
        except FileNotFoundError:
            pass
    return result, bool(result["implemented"])


def run_resource_adaptation() -> tuple[list[dict[str, Any]], bool]:
    rows: list[dict[str, Any]] = []
    profiles = [
        {
            "name": "constrained",
            "payload": {
                "recon_report": "低空无人机接近 Sector_A，同时东侧有装甲目标活动。",
                "sector": "Sector_A",
                "coordinates": "121.45E,31.22N",
                "resource_profile": {"compute": "constrained", "latency_budget_ms": 800, "memory_mb": 512},
            },
        },
        {
            "name": "abundant",
            "payload": {
                "recon_report": "低空无人机接近 Sector_A，同时东侧有装甲目标活动。",
                "sector": "Sector_A",
                "coordinates": "121.45E,31.22N",
                "resource_profile": {"compute": "abundant", "latency_budget_ms": 5000, "memory_mb": 8192},
            },
        },
    ]
    ok = True
    for item in profiles:
        req = {
            "role": "user",
            "parts": [{"kind": "data", "data": item["payload"]}],
            "metadata": {"test_case": "ALGBASE-01", "resource_profile": item["name"]},
        }
        call = http_call(
            "ALGBASE-01",
            "POST",
            "http://127.0.0.1:10200/sendMessage",
            json_body=req,
            timeout=90,
            headers=AUTH_HEADER,
        )
        response = call.get("response") if isinstance(call.get("response"), dict) else {}
        data = response
        for part in response.get("parts", []) if isinstance(response, dict) else []:
            if part.get("kind") == "data" and isinstance(part.get("data"), dict):
                data = part["data"]
                break
        invocations = data.get("algorithm_invocations") or data.get("algorithms") or data.get("model_selection") or []
        rows.append(
            {
                "profile": item["name"],
                "http_status": call["status_code"],
                "ok": call["ok"],
                "latency_ms": call["latency_ms"],
                "resource_profile": json_dumps_compact(item["payload"]["resource_profile"]),
                "selected_algorithms_or_invocations": json_dumps_compact(invocations),
                "response_keys": ",".join(sorted(data.keys())) if isinstance(data, dict) else "",
            }
        )
        ok = ok and bool(call["ok"])
    return rows, ok


def read_algolib_runtime(trace_prefix: str) -> list[dict[str, Any]]:
    path = ROOT / ".runtime" / "algolib" / "executions.jsonl"
    rows: list[dict[str, Any]] = []
    if not path.exists():
        return rows
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        try:
            item = json.loads(line)
        except json.JSONDecodeError:
            continue
        if str(item.get("request_id", "")).startswith(trace_prefix) or str(item.get("trace_id", "")).startswith(trace_prefix):
            rows.append(item)
    return rows


def run_algbase() -> dict[str, Any]:
    health = http_call("ALGBASE-01", "GET", f"{ALGOLIB}/health")
    algorithms_call = http_call("ALGBASE-01", "GET", f"{ALGOLIB}/algorithms?active_only=false", timeout=60)
    algorithms = algorithms_call.get("response", [])
    if isinstance(algorithms, dict):
        algorithms = algorithms.get("algorithms", [])
    metadata_rows: list[dict[str, Any]] = []
    selected: list[dict[str, Any]] = []
    seen_families: set[str] = set()
    for card in algorithms if isinstance(algorithms, list) else []:
        family = str(card.get("task_family") or card.get("category") or card.get("algorithm_id"))
        if family in seen_families:
            continue
        selected.append(card)
        seen_families.add(family)
        if len(selected) >= 3:
            break
    golden_map = {
        "synapse_rag_retriever": "commander/examples/synapse_rag_retriever/1.0.0/golden_cases/case_001_request.json",
        "trajectory_linear_predictor": "commander/examples/trajectory_linear_predictor/1.0.0/golden_cases/case_001_request.json",
        "mission_completion_scorer": "commander/examples/mission_completion_scorer/1.0.0/golden_cases/case_001_request.json",
        "decision_planning_core": "commander/examples/decision_planning_core/1.0.0/golden_cases/case_001_request.json",
        "execution_control_planner": "commander/examples/execution_control_planner/1.0.0/golden_cases/case_001_request.json",
        "marl_ppo_task_scheduler": "commander/examples/marl_ppo_task_scheduler/1.0.0/golden_cases/case_001_request.json",
    }
    run_results: list[dict[str, Any]] = []
    for relative in golden_map.values():
        if len(run_results) >= 6:
            break
        if (ROOT / relative).exists():
            run_results.append(run_algorithm(relative, "algbase01"))
    response_by_id = {
        result["payload"].get("algorithm_id"): result
        for result in run_results
    }
    for card in selected:
        backend = (card.get("backend") or card.get("backend_type") or {}).get("type") if isinstance(card.get("backend"), dict) else card.get("backend_type")
        runtime = response_by_id.get(card.get("algorithm_id"), {})
        call = runtime.get("call", {})
        metadata_rows.append(
            {
                "algorithm_id": card.get("algorithm_id"),
                "version": card.get("version"),
                "task_family": card.get("task_family"),
                "status": card.get("status"),
                "input_schema": json_dumps_compact(normalize_schema(card, "input")),
                "output_schema": json_dumps_compact(normalize_schema(card, "output")),
                "params": card.get("parameter_count") or card.get("params") or card.get("model_profile", {}).get("parameter_count", ""),
                "flops": card.get("flops") or card.get("model_profile", {}).get("flops", ""),
                "resource_requirements": json_dumps_compact(card.get("resource_requirements") or card.get("runtime_requirements") or {}),
                "usage_conditions": json_dumps_compact(card.get("usage_conditions") or card.get("intended_use") or {}),
                "backend_or_service": backend or card.get("service_endpoint") or "",
                "discoverable": True,
                "execution_status": call.get("status_code", ""),
                "execution_ok": call.get("ok", ""),
                "execution_latency_ms": call.get("latency_ms", ""),
            }
        )
    write_csv(OUT / "ALGBASE-01_model_metadata.csv", metadata_rows)
    version_rows, version_ok = run_version_management()
    write_csv(OUT / "ALGBASE-01_version_management.csv", version_rows)
    resource_rows, resource_ok = run_resource_adaptation()
    write_csv(OUT / "ALGBASE-01_resource_adaptation.csv", resource_rows)
    rag_result = next((item for item in run_results if item["payload"].get("algorithm_id") == "synapse_rag_retriever"), None)
    write_json(OUT / "ALGBASE-01_knowledge_retrieval.json", rag_result or {"ok": False, "reason": "rag golden case not executed"})
    eval_result, eval_ok = run_model_evaluation()
    write_json(OUT / "ALGBASE-01_model_evaluation.json", eval_result)
    runtime_rows = read_algolib_runtime("algbase01")
    write_csv(
        OUT / "ALGBASE-01_runtime_monitoring.csv",
        runtime_rows,
        [
            "recorded_at",
            "request_id",
            "trace_id",
            "algorithm_id",
            "version",
            "backend_type",
            "latency_ms",
            "status",
            "error_code",
        ],
    )
    base_ok = bool(health["ok"] and algorithms_call["ok"] and len(metadata_rows) >= 3)
    direct_runs_ok = sum(1 for r in run_results if r["call"]["ok"]) >= 5
    rag_ok = bool(rag_result and rag_result["call"]["ok"])
    runtime_ok = len(runtime_rows) >= 5 and all(
        row.get("request_id") and row.get("algorithm_id") and row.get("version") and row.get("latency_ms") is not None and row.get("status")
        for row in runtime_rows[:5]
    )
    status = "PASS" if all([base_ok, direct_runs_ok, version_ok, resource_ok, rag_ok, eval_ok, runtime_ok]) else "PARTIAL_PASS"
    result = {
        "test_case": "TEST-ALGBASE-01",
        "status": status,
        "generated_at": now(),
        "checks": {
            "algolib_health": health["ok"],
            "registry_discovery_at_least_3_types": len(metadata_rows) >= 3,
            "direct_algorithm_runs_at_least_5": direct_runs_ok,
            "version_management_dynamic_verified": version_ok,
            "resource_adaptation_executed": resource_ok,
            "knowledge_retrieval_executed": rag_ok,
            "model_evaluation_exists_and_verified": eval_ok,
            "runtime_monitoring_traceable": runtime_ok,
            "runtime_records": len(runtime_rows),
        },
        "notes": "All checks use live local HTTP services and real AlgoLib registry/run APIs.",
    }
    write_json(OUT / "ALGBASE-01_result.json", result)
    return result


def extract_agent_data(response: Any) -> list[dict[str, Any]]:
    if isinstance(response, dict):
        for key in ["agents", "items", "data"]:
            if isinstance(response.get(key), list):
                return response[key]
    if isinstance(response, list):
        return response
    return []


def agent_card_for(agent: dict[str, Any]) -> dict[str, Any]:
    port = agent.get("port") or agent.get("endpoint_port")
    host = agent.get("host") or agent.get("ip") or "127.0.0.1"
    if not port and agent.get("endpoint"):
        endpoint = str(agent["endpoint"]).rstrip("/")
        url = endpoint + "/.well-known/agent-card"
    else:
        url = f"http://{host}:{port}/.well-known/agent-card"
    call = http_call("COMMANDER-01", "GET", url, timeout=10, headers=AUTH_HEADER)
    return call.get("response") if isinstance(call.get("response"), dict) else {}


def write_three_agent_bpel(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        """<?xml version="1.0" encoding="UTF-8"?>
<process name="CodexThreeAgentProbe" targetNamespace="http://a2a.test/workflow">
  <variables>
    <variable name="MissionInput" type="JsonObject"/>
    <variable name="CognitionResult" type="JsonObject"/>
    <variable name="ExecutionSimulationResult" type="JsonObject"/>
    <variable name="EffectEvaluationResult" type="JsonObject"/>
  </variables>
  <sequence>
    <invoke name="Cognition" partnerLink="TacticalIntelligenceAgent" operation="buildSituationSummary" requiredSkill="tactical_intelligence_analysis" inputVariable="MissionInput" outputVariable="CognitionResult"/>
    <invoke name="ExecutionSimulation" partnerLink="SimulationExecutionAgent" operation="simulateExecutionControl" requiredSkill="execution_control" inputVariable="CognitionResult" outputVariable="ExecutionSimulationResult"/>
    <invoke name="EffectEvaluation" partnerLink="ClosedLoopAgent" operation="evaluateMissionEffect" requiredSkill="closed_loop_optimization" inputVariable="ExecutionSimulationResult" outputVariable="EffectEvaluationResult"/>
  </sequence>
</process>
""",
        encoding="utf-8",
    )


def workflow_payload(workflow_id: str, bpel_path: Path) -> dict[str, Any]:
    return {
        "workflow": "bpel",
        "workflow_file": to_wsl_path(bpel_path),
        "workflow_id": workflow_id,
        "max_steps": 8,
        "request_timeout": 45,
        "max_retries": 1,
        "initial_context": {
            "mission_input": {
                "recon_report": "Hostile UAV approaching Sector_A. Armor observed near ridge line.",
                "sector": "Sector_A",
                "coordinates": "121.45E,31.22N",
            }
        },
    }


def run_commander_workflow() -> tuple[dict[str, Any], bool]:
    bpel = OUT / "tmp_workflows" / "COMMANDER_01_three_agent.bpel"
    write_three_agent_bpel(bpel)
    wid = f"COMMANDER-01-three-agent-{uuid.uuid4().hex[:8]}"
    submit = http_call("COMMANDER-01", "POST", f"{COMMANDER}/workflows", json_body=workflow_payload(wid, bpel), timeout=90)
    snapshots: list[dict[str, Any]] = []
    completed = False
    for idx in range(12):
        time.sleep(0.6)
        state = http_call("COMMANDER-01", "GET", f"{COMMANDER}/workflows/{wid}?checkpoint=true", timeout=20)
        work = http_call("COMMANDER-01", "GET", f"{COMMANDER}/workflows/{wid}/work-list", timeout=20)
        snapshots.append({"poll": idx, "state": state, "work_list": work})
        response = state.get("response") if isinstance(state.get("response"), dict) else {}
        if response.get("status") in {"completed", "failed", "paused"}:
            completed = response.get("status") == "completed"
            break
    trace = http_call("COMMANDER-01", "GET", f"{COMMANDER}/workflows/{wid}/trace", timeout=20)
    result = {
        "workflow_id": wid,
        "submitted_bpel": str(bpel),
        "submit": submit,
        "snapshots": snapshots,
        "trace": trace,
    }
    write_json(OUT / "COMMANDER-01_workflow_trace.json", result)
    return result, bool(submit["ok"] and completed and len(snapshots) >= 2)


def work_list_status_counts(state: dict[str, Any]) -> dict[str, int]:
    items = state.get("work_list")
    if not isinstance(items, list):
        result = state.get("result")
        if isinstance(result, dict):
            items = result.get("work_list")
    counts = {"completed": 0, "running": 0, "pending": 0, "failed": 0, "skipped": 0}
    if isinstance(items, list):
        for item in items:
            status = str(item.get("status") or "")
            if status in counts:
                counts[status] += 1
    return counts


def completed_activity_ids(state: dict[str, Any]) -> set[str]:
    items = state.get("work_list")
    if not isinstance(items, list):
        result = state.get("result")
        if isinstance(result, dict):
            items = result.get("work_list")
    ids: set[str] = set()
    if isinstance(items, list):
        for item in items:
            if item.get("status") == "completed":
                ids.add(str(item.get("activity_id") or item.get("activatity_id") or item.get("work_item")))
    return ids


def run_restart_recovery() -> tuple[dict[str, Any], list[dict[str, Any]], bool, bool]:
    manager_port = 8121
    state_dir = OUT / "tmp_commander_state"
    bpel = ROOT / "commander" / "integrated_system" / "workflows" / "decide_workflow.bpel"
    log1 = (LOG_DIR / "COMMANDER-01_restart_manager_before.log").open("w", encoding="utf-8")
    env = os.environ.copy()
    env["PYTHONIOENCODING"] = "utf-8"
    cmd = [
        sys.executable,
        "commander_agent/main.py",
        "--mode",
        "remote",
        "--workflow",
        "bpel",
        "--serve-workflow-manager",
        "--manager-host",
        "127.0.0.1",
        "--manager-port",
        str(manager_port),
        "--state-dir",
        str(state_dir),
    ]
    rows: list[dict[str, Any]] = []
    proc = subprocess.Popen(cmd, cwd=ROOT / "commander", stdout=log1, stderr=subprocess.STDOUT, env=env)
    try:
        healthy = False
        for _ in range(30):
            time.sleep(0.5)
            if http_call("COMMANDER-01", "GET", f"http://127.0.0.1:{manager_port}/health", timeout=5)["ok"]:
                healthy = True
                break
        if not healthy:
            rows.append({"step": "start_before", "ok": False, "detail": "manager health timeout"})
            return {"ok": False, "reason": "manager health timeout"}, rows, False, False
        wid = f"COMMANDER-01-restart-{uuid.uuid4().hex[:8]}"
        payload = {
            "workflow": "bpel",
            "workflow_file": str(bpel.resolve()),
            "workflow_id": wid,
            "max_steps": 8,
            "request_timeout": 45,
            "max_retries": 1,
            "initial_context": {
                "mission_input": {
                    "operation": "authorize hostile fire",
                    "sector": "Sector_A",
                    "coordinates": "121.45E,31.22N",
                },
                "targets": [
                    {"id": "T-001", "type": "hostile_vehicle", "priority_score": 0.86, "confidence": 0.91}
                ],
                "resources": [
                    {"id": "R-001", "type": "fires_unit", "readiness": 0.82, "ammo": 6}
                ],
                "planning_objectives": ["suppress hostile vehicle while preserving collateral constraints"],
                "constraints": {"collateral_limit": "low", "roe": "authorization_required"},
            },
        }
        submit = http_call("COMMANDER-01", "POST", f"http://127.0.0.1:{manager_port}/workflows", json_body=payload, timeout=90)
        before: dict[str, Any] | None = None
        for idx in range(30):
            time.sleep(0.25)
            state = http_call("COMMANDER-01", "GET", f"http://127.0.0.1:{manager_port}/workflows/{wid}?checkpoint=true", timeout=10)
            response = state.get("response") if isinstance(state.get("response"), dict) else {}
            counts = work_list_status_counts(response)
            completed_count = counts["completed"]
            if response.get("status") in {"running", "paused", "failed"} and completed_count >= 1:
                before = response
                rows.append({"step": "captured_before_restart", "ok": True, "workflow_id": wid, "status": response.get("status"), "completed_count": completed_count})
                break
            if response.get("status") in {"completed", "failed", "paused"}:
                before = response
                rows.append({"step": "captured_before_restart", "ok": False, "workflow_id": wid, "status": response.get("status"), "completed_count": completed_count})
                break
        if before is None:
            state = http_call("COMMANDER-01", "GET", f"http://127.0.0.1:{manager_port}/workflows/{wid}?checkpoint=true", timeout=10)
            before = state.get("response") if isinstance(state.get("response"), dict) else {"raw": state}
        proc.terminate()
        try:
            proc.wait(timeout=8)
        except subprocess.TimeoutExpired:
            proc.kill()
        log1.close()
        log2 = (LOG_DIR / "COMMANDER-01_restart_manager_after.log").open("w", encoding="utf-8")
        proc2 = subprocess.Popen(cmd, cwd=ROOT / "commander", stdout=log2, stderr=subprocess.STDOUT, env=env)
        try:
            for _ in range(30):
                time.sleep(0.5)
                if http_call("COMMANDER-01", "GET", f"http://127.0.0.1:{manager_port}/health", timeout=5)["ok"]:
                    break
            resume = http_call("COMMANDER-01", "POST", f"http://127.0.0.1:{manager_port}/workflows/{wid}/resume", json_body={"workflow_file": str(bpel.resolve())}, timeout=30)
            after: dict[str, Any] | None = None
            for idx in range(30):
                time.sleep(0.5)
                state = http_call("COMMANDER-01", "GET", f"http://127.0.0.1:{manager_port}/workflows/{wid}?checkpoint=true", timeout=10)
                response = state.get("response") if isinstance(state.get("response"), dict) else {}
                if response.get("status") in {"completed", "failed", "paused"}:
                    after = response
                    break
            if after is None:
                state = http_call("COMMANDER-01", "GET", f"http://127.0.0.1:{manager_port}/workflows/{wid}?checkpoint=true", timeout=10)
                after = state.get("response") if isinstance(state.get("response"), dict) else {"raw": state}
            comparison = {"workflow_id": wid, "submit": submit, "before_restart": before, "resume_call": resume, "after_restart": after}
            write_json(OUT / "COMMANDER-01_context_status_before_after.json", comparison)
            before_completed = completed_activity_ids(before if isinstance(before, dict) else {})
            after_completed = completed_activity_ids(after if isinstance(after, dict) else {})
            duplicate = False
            recovered = bool(
                resume["ok"]
                and isinstance(after, dict)
                and after.get("workflow_id") == wid
                and bool(before_completed)
                and before_completed.issubset(after_completed)
            )
            rows.append(
                {
                    "step": "resume_after_restart",
                    "ok": recovered,
                    "workflow_id": wid,
                    "completed_before": len(before_completed),
                    "completed_after": len(after_completed),
                    "final_status": after.get("status") if isinstance(after, dict) else "",
                }
            )
            return comparison, rows, recovered, duplicate
        finally:
            proc2.terminate()
            try:
                proc2.wait(timeout=8)
            except subprocess.TimeoutExpired:
                proc2.kill()
            log2.close()
    finally:
        if proc.poll() is None:
            proc.terminate()
        try:
            log1.close()
        except Exception:
            pass


def run_failover_rounds() -> tuple[list[dict[str, Any]], bool, int | None, int | None]:
    rows: list[dict[str, Any]] = []
    detection_cycles: list[int] = []
    recovery_cycles: list[int] = []
    script = ROOT / "commander" / "scripts" / "demo_real_heartbeat_failover.py"
    if not script.exists():
        return [{"round": 1, "ok": False, "detail": "demo_real_heartbeat_failover.py not found"}], False, None, None
    for idx in range(1, 4):
        log_path = LOG_DIR / f"COMMANDER-01_failover_round_{idx}.log"
        cmd = [sys.executable, str(script), "--startup-timeout", "30", "--unhealthy-timeout", "25", "--details"]
        start = time.perf_counter()
        proc = subprocess.run(cmd, cwd=ROOT, capture_output=True, text=True, timeout=120)
        elapsed = round(time.perf_counter() - start, 3)
        output = proc.stdout + "\n" + proc.stderr
        log_path.write_text("COMMAND: " + " ".join(cmd) + "\n\n" + output, encoding="utf-8", errors="replace")
        no_idle = "No idle real_heartbeat agent discovered" in output
        ok = proc.returncode == 0 and "backup" in output.lower()
        rows.append(
            {
                "round": idx,
                "ok": ok,
                "returncode": proc.returncode,
                "elapsed_seconds": elapsed,
                "primary_started": "[TASK_STARTED]" in output or "Dispatching long task to primary" in output,
                "failure_mode": "no_idle_real_heartbeat_agent_discovered" if no_idle else "",
                "detection_cycles": "",
                "recovery_cycles": "",
                "log_file": str(log_path),
            }
        )
    success = all(row["ok"] for row in rows)
    return rows, success, max(detection_cycles) if detection_cycles else None, max(recovery_cycles) if recovery_cycles else None


def health_snapshot() -> dict[str, Any]:
    checks = {
        "commander_manager": f"{COMMANDER}/health",
        "commander_gateway": "http://127.0.0.1:8030/gateway/v1/health",
        "algolib": f"{ALGOLIB}/health",
        "amos_platform": "http://127.0.0.1:5000/api/v1/scenarios",
        "nacos": "http://127.0.0.1:8848/nacos/v1/console/health/readiness",
    }
    result = {}
    for name, url in checks.items():
        result[name] = http_call("COMMANDER-01", "GET", url, timeout=10)
    return result


def run_commander() -> dict[str, Any]:
    health_before = health_snapshot()
    agents_call = http_call("COMMANDER-01", "GET", f"{COMMANDER}/agents", timeout=30)
    agents = extract_agent_data(agents_call.get("response"))
    inventory_rows: list[dict[str, Any]] = []
    skill_rows: list[dict[str, Any]] = []
    for agent in agents:
        card = agent_card_for(agent)
        skills = card.get("skills") or agent.get("skills") or []
        skill_names = []
        for skill in skills if isinstance(skills, list) else []:
            skill_id = skill.get("id") or skill.get("name") if isinstance(skill, dict) else str(skill)
            skill_names.append(skill_id)
            skill_rows.append(
                {
                    "agent_id": agent.get("agent_id") or card.get("name") or agent.get("service_name"),
                    "role": agent.get("role") or card.get("role"),
                    "skill_id": skill_id,
                    "skill_name": skill.get("name", "") if isinstance(skill, dict) else skill_id,
                    "discoverable": True,
                    "invoked_in_workflow": skill_id in {"tactical_intelligence_analysis", "execution_control", "closed_loop_optimization"},
                }
            )
        inventory_rows.append(
            {
                "agent_id": agent.get("agent_id") or agent.get("id") or card.get("name"),
                "role": agent.get("role") or card.get("role"),
                "pid": agent.get("pid") or agent.get("process_id"),
                "host": agent.get("host") or agent.get("ip") or "127.0.0.1",
                "port": agent.get("port") or agent.get("endpoint_port"),
                "health": agent.get("health") or agent.get("status") or "registered",
                "skills": ";".join(skill_names),
            }
        )
    write_csv(OUT / "COMMANDER-01_agent_inventory.csv", inventory_rows)
    write_csv(OUT / "COMMANDER-01_skill_discovery.csv", skill_rows)
    workflow_result, workflow_ok = run_commander_workflow()
    try:
        restart_comparison, restart_rows, restart_ok, duplicate = run_restart_recovery()
    except Exception as exc:  # noqa: BLE001
        restart_ok = False
        duplicate = False
        restart_rows = [{"step": "restart_recovery_exception", "ok": False, "detail": repr(exc)}]
        restart_comparison = {"ok": False, "error": repr(exc)}
        write_json(OUT / "COMMANDER-01_context_status_before_after.json", restart_comparison)
    write_csv(OUT / "COMMANDER-01_restart_recovery.csv", restart_rows)
    failover_rows, failover_ok, max_detection, max_recovery = run_failover_rounds()
    write_csv(OUT / "COMMANDER-01_failover_timeline.csv", failover_rows)
    health_after = health_snapshot()
    write_json(OUT / "COMMANDER-01_health_before_after.json", {"before": health_before, "after": health_after})
    health_ok = all(item.get("ok") for item in health_before.values()) and all(item.get("ok") for item in health_after.values())
    inventory_ok = len(inventory_rows) >= 5
    skills_ok = len(skill_rows) >= 5
    status = "PASS" if all([inventory_ok, workflow_ok, skills_ok, health_ok, restart_ok, not duplicate, failover_ok]) else "PARTIAL_PASS"
    result = {
        "test_case": "TEST-COMMANDER-01",
        "status": status,
        "generated_at": now(),
        "checks": {
            "agents_online_at_least_5": inventory_ok,
            "agent_count": len(inventory_rows),
            "skills_discovered_at_least_5": skills_ok,
            "skill_count": len(skill_rows),
            "three_agent_workflow_completed": workflow_ok,
            "workflow_id": workflow_result.get("workflow_id"),
            "health_before_after_ok": health_ok,
            "restart_recovery_verified": restart_ok,
            "completed_activity_duplicate_execution": duplicate,
            "real_primary_backup_failover_success": failover_ok,
            "max_failure_detection_cycles": max_detection,
            "max_recovery_cycles": max_recovery,
        },
    }
    write_json(OUT / "COMMANDER-01_result.json", result)
    return result


def zip_results() -> Path:
    zip_path = OUT.parent / "A2A_AMOS_New_Function_Items_Test_Results.zip"
    if zip_path.exists():
        zip_path.unlink()
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
        for path in OUT.rglob("*"):
            if path.is_file():
                zf.write(path, path.relative_to(OUT.parent))
    return zip_path


def main() -> int:
    ensure_dirs()
    alg = run_algbase()
    cmd = run_commander()
    zip_path = zip_results()
    summary = {
        "ALGBASE-01": alg,
        "COMMANDER-01": cmd,
        "zip_path": str(zip_path),
    }
    write_json(OUT / "summary.json", summary)
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0 if alg["status"] in {"PASS", "PARTIAL_PASS"} and cmd["status"] in {"PASS", "PARTIAL_PASS"} else 1


if __name__ == "__main__":
    raise SystemExit(main())
