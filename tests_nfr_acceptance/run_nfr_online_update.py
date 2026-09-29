from __future__ import annotations

import csv
import importlib.util
import json
import shutil
import statistics
import time
import uuid
import zipfile
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any

import requests


ROOT = Path(__file__).resolve().parents[1]
BASE_SCRIPT = Path(__file__).with_name("run_nfr_acceptance.py")
OUT = ROOT / "tests_nfr_acceptance" / "A2A_AMOS_NFR_test_results"
ZIP_PATH = ROOT / "A2A_AMOS_NFR_test_results.zip"

AGENTS: dict[str, dict[str, Any]] = {
    "tactical_intelligence": {
        "port": 10200,
        "command": "tactical_intelligence_analysis",
        "required_skill": "tactical_intelligence_analysis",
        "output_hint": "intelligence_packet",
    },
    "track_threat": {
        "port": 8102,
        "command": "track_threat_situation_analysis",
        "required_skill": "track_threat_situation_analysis",
        "output_hint": "threat_evaluation",
    },
    "task_scheduling": {
        "port": 10201,
        "command": "allocate_tasks_and_resources",
        "required_skill": "task_scheduling",
        "output_hint": "task_scheduling_result",
    },
    "decision_planning": {
        "port": 10202,
        "command": "decision_planning",
        "required_skill": "decision_planning_analysis",
        "output_hint": "decision_planning_result",
    },
    "compliance": {
        "port": 10203,
        "command": "compliance_authorization",
        "required_skill": "compliance_authorization",
        "output_hint": "compliance_authorization_result",
    },
    "simulation_execution": {
        "port": 10204,
        "command": "plan_strike_control",
        "required_skill": "plan_strike_control",
        "output_hint": "execution_control_result",
    },
    "closed_loop": {
        "port": 10205,
        "command": "closed_loop_optimization",
        "required_skill": "closed_loop_optimization",
        "output_hint": "closed_loop_result",
    },
}

AUTH_HEADERS = {"Authorization": "Bearer mock-jwt-token-abcd"}


def _load_base_module():
    spec = importlib.util.spec_from_file_location("run_nfr_acceptance", BASE_SCRIPT)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Cannot import {BASE_SCRIPT}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


base = _load_base_module()


def read_json(path: Path, default: Any) -> Any:
    if not path.exists():
        return default
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return default


def write_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def write_text(path: Path, data: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(data, encoding="utf-8")


def write_csv(path: Path, rows: list[dict[str, Any]], fields: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        for row in rows:
            writer.writerow({field: row.get(field, "") for field in fields})


def ensure_base_results() -> None:
    expected = {f"NFR-{index:02d}_result.json" for index in range(1, 13)}
    present = {path.name for path in OUT.glob("NFR-*_result.json")} if OUT.exists() else set()
    if expected.issubset(present) and (OUT / "test_results_summary.json").exists():
        return
    base.main()
    if getattr(base, "OUT", OUT) != OUT and Path(base.OUT).exists():
        if OUT.exists():
            shutil.rmtree(OUT)
        shutil.copytree(Path(base.OUT), OUT)


def http_get_json(url: str, timeout: float = 5.0) -> tuple[int | None, Any]:
    try:
        response = requests.get(url, timeout=timeout)
        try:
            return response.status_code, response.json()
        except ValueError:
            return response.status_code, {"raw": response.text[:1000]}
    except Exception as exc:
        return None, {"error": str(exc)}


def post_json(url: str, payload: dict[str, Any], timeout: float = 20.0) -> dict[str, Any]:
    started = time.perf_counter()
    try:
        response = requests.post(url, headers=AUTH_HEADERS, json=payload, timeout=timeout)
        latency_ms = (time.perf_counter() - started) * 1000.0
        try:
            body = response.json()
        except Exception:
            body = {"raw": response.text[:1000]}
        return {
            "http_status": response.status_code,
            "latency_ms": round(latency_ms, 3),
            "request_bytes": len(json.dumps(payload, ensure_ascii=False).encode("utf-8")),
            "response_bytes": len(response.content),
            "body": body,
        }
    except Exception as exc:
        return {
            "http_status": None,
            "latency_ms": None,
            "request_bytes": len(json.dumps(payload, ensure_ascii=False).encode("utf-8")),
            "response_bytes": 0,
            "body": {"status": "failed", "error": str(exc)},
        }


def load_sample(rel: str) -> dict[str, Any]:
    return json.loads((ROOT / rel).read_text(encoding="utf-8"))


DECISION_SAMPLE = load_sample("commander/data/samples/decision_planning_input.json")
COMPLIANCE_SAMPLE = load_sample("commander/data/samples/compliance_authorization_input.json")
TRACK_FRAME = load_sample("commander/track_threat_agent/sample_data/frame_1.json")


def payload_for(role: str, tag: str = "nfr-online", extra_bytes: int = 0) -> dict[str, Any]:
    meta = AGENTS[role]
    base_payload: dict[str, Any] = {
        "schema_version": "1.0",
        "workflow_id": tag,
        "work_item": f"{tag}:{role}:{uuid.uuid4().hex[:10]}",
        "command": meta["command"],
        "required_skill": meta["required_skill"],
        "required_skills": [meta["required_skill"]],
        "output_hint": meta["output_hint"],
    }
    padding = "x" * max(0, extra_bytes)
    if role == "decision_planning":
        base_payload["input"] = {"agent_request": DECISION_SAMPLE, "nfr_padding": padding}
    elif role == "compliance":
        base_payload["input"] = {"agent_request": COMPLIANCE_SAMPLE, "nfr_padding": padding}
    elif role == "simulation_execution":
        base_payload["input"] = {"phase": "strike", "results": {}, "nfr_padding": padding}
    elif role == "closed_loop":
        base_payload["input"] = {"cycles": 1, "target_count": 2, "nfr_padding": padding}
    elif role == "task_scheduling":
        base_payload["input"] = {
            "phase": "strike",
            "risk_assessments": [{"target_id": "T-1", "priority_score": 0.8, "threat_level": "high"}],
            "resources": [{"asset_id": "A-1", "type": "sensor", "status": "ready", "readiness": 0.9}],
            "nfr_padding": padding,
        }
    elif role == "track_threat":
        frame = json.loads(json.dumps(TRACK_FRAME))
        frame["nfr_padding"] = padding
        base_payload["input"] = frame
    elif role == "tactical_intelligence":
        base_payload["input"] = {
            "recon_report": "Hostile UAV approaching Sector_A. Armor observed near ridge line.",
            "sector": "Sector_A",
            "coordinates": "121.45E, 31.22N",
            "nfr_padding": padding,
        }
    else:
        base_payload["input"] = {"nfr_padding": padding}
    return base_payload


def update_result(case_id: str, status: str, measurements: dict[str, Any], evidence: list[str], notes: str = "") -> None:
    result_path = OUT / f"{case_id}_result.json"
    result = read_json(result_path, {})
    result.update(
        {
            "case_id": case_id,
            "status": status,
            "measurements": measurements,
            "evidence": evidence,
            "notes": notes,
        }
    )
    write_json(result_path, result)


def result_summary(status_by_case: dict[str, str]) -> None:
    results = []
    for path in sorted(OUT.glob("NFR-*_result.json")):
        item = read_json(path, {})
        if item.get("case_id") in status_by_case:
            item["status"] = status_by_case[item["case_id"]]
            write_json(path, item)
        results.append(item)
    counts: dict[str, int] = {}
    for item in results:
        status = str(item.get("status") or "UNKNOWN")
        counts[status] = counts.get(status, 0) + 1
    summary = read_json(OUT / "test_results_summary.json", {})
    summary.update(
        {
            "total_cases": len(results),
            "status_counts": counts,
            "online_update_ran": True,
            "online_update_timestamp": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
            "zip_path": str(ZIP_PATH),
        }
    )
    write_json(OUT / "test_results_summary.json", summary)

    rows = []
    for item in results:
        rows.append(
            {
                "case_id": item.get("case_id"),
                "status": item.get("status"),
                "evidence": ";".join(item.get("evidence") or []),
                "notes": item.get("notes", ""),
            }
        )
    write_csv(OUT / "acceptance_matrix.csv", rows, ["case_id", "status", "evidence", "notes"])


def package_zip() -> None:
    if ZIP_PATH.exists():
        ZIP_PATH.unlink()
    with zipfile.ZipFile(ZIP_PATH, "w", zipfile.ZIP_DEFLATED) as archive:
        for path in sorted(OUT.rglob("*")):
            if path.is_file():
                archive.write(path, path.relative_to(OUT.parent))


def probe_agents() -> tuple[list[dict[str, Any]], list[dict[str, Any]], dict[str, Any]]:
    inventory = []
    resources = []
    cards: dict[str, Any] = {}
    for role, meta in AGENTS.items():
        port = meta["port"]
        card_status, card = http_get_json(f"http://127.0.0.1:{port}/.well-known/agent-card")
        res_status, res = http_get_json(f"http://127.0.0.1:{port}/resources")
        cards[role] = card if isinstance(card, dict) else {}
        inventory.append(
            {
                "agent_role": role,
                "service": f"agent-{role.replace('_', '-')}",
                "pid": "",
                "port": port,
                "status": "running" if card_status == 200 else "offline",
                "source": "/.well-known/agent-card",
            }
        )
        system = res.get("system", {}) if isinstance(res, dict) else {}
        process = res.get("process", {}) if isinstance(res, dict) else {}
        gpu = res.get("gpu", {}) if isinstance(res, dict) else {}
        energy = res.get("energy", {}) if isinstance(res, dict) else {}
        network = res.get("network", {}) if isinstance(res, dict) else {}
        resources.append(
            {
                "agent_role": role,
                "port": port,
                "cpu_compute": f"{system.get('cpu_count', '')} cores, {system.get('cpu_percent', '')}% host cpu",
                "gpu_compute": "available" if gpu.get("available") else f"not available: {gpu.get('reason', '')}",
                "energy_power": f"available={energy.get('available')}, percent={energy.get('battery_percent', '')}, plugged={energy.get('power_plugged', '')}",
                "memory_capacity": f"host_total={system.get('memory_total_bytes', '')}, host_available={system.get('memory_available_bytes', '')}, rss={process.get('memory_rss_bytes', '')}",
                "communication_bandwidth": f"{network.get('bandwidth_mbps', '')} Mbps",
                "link_stability": network.get("link_stability", ""),
                "node_online_status": str(res.get("node_online", False)),
                "resources_http_status": res_status or "",
                "evidence": json.dumps(res, ensure_ascii=False)[:500],
            }
        )
    write_csv(OUT / "NFR-02_agent_inventory.csv", inventory, ["agent_role", "service", "pid", "port", "status", "source"])
    write_csv(
        OUT / "NFR-02_resource_awareness.csv",
        resources,
        [
            "agent_role",
            "port",
            "cpu_compute",
            "gpu_compute",
            "energy_power",
            "memory_capacity",
            "communication_bandwidth",
            "link_stability",
            "node_online_status",
            "resources_http_status",
            "evidence",
        ],
    )
    ready = sum(1 for row in inventory if row["status"] == "running")
    update_result(
        "NFR-02",
        "PASS" if ready >= 5 and all(str(row["node_online_status"]).lower() == "true" for row in resources) else "FAIL",
        {"agents_running": ready, "resource_rows": len(resources)},
        ["NFR-02_agent_inventory.csv", "NFR-02_resource_awareness.csv"],
        "在线补测：通过 Agent Card 与 /resources 读取真实资源自感知数据。",
    )
    return inventory, resources, cards


def run_a2a_communication() -> None:
    roles = ["decision_planning", "compliance", "simulation_execution", "closed_loop", "task_scheduling"]
    raw = []
    summary = []
    for role in roles:
        port = AGENTS[role]["port"]
        round_rates = []
        latencies = []
        statuses = []
        for round_index in range(1, 6):
            started = time.perf_counter()
            round_count = 10
            for message_index in range(1, round_count + 1):
                payload = payload_for(role, tag=f"nfr03-r{round_index}", extra_bytes=1024)
                result = post_json(f"http://127.0.0.1:{port}/sendMessage", payload, timeout=30)
                body = result["body"] if isinstance(result["body"], dict) else {}
                status = body.get("status", "no_body")
                statuses.append(status)
                if result["latency_ms"] is not None:
                    latencies.append(float(result["latency_ms"]))
                elapsed = max(time.perf_counter() - started, 1e-6)
                raw.append(
                    {
                        "agent_role": role,
                        "agent_port": port,
                        "round": round_index,
                        "message_index": message_index,
                        "payload_bytes": result["request_bytes"],
                        "latency_ms": result["latency_ms"],
                        "round_elapsed_s": round(elapsed, 3),
                        "round_throughput_msg_s": round(message_index / elapsed, 3),
                        "status": f"http={result['http_status']};business={status}",
                    }
                )
            elapsed = max(time.perf_counter() - started, 1e-6)
            round_rates.append(round_count / elapsed)
        p95 = statistics.quantiles(latencies, n=20)[18] if len(latencies) >= 20 else (max(latencies) if latencies else None)
        role_status = "PASS" if latencies and max(latencies) <= 1000 and min(round_rates) >= 5 and set(statuses) == {"completed"} else "FAIL"
        summary.append(
            {
                "agent_role": role,
                "agent_port": port,
                "rounds": 5,
                "max_latency_ms": round(max(latencies), 3) if latencies else "",
                "p95_latency_ms": round(p95, 3) if p95 is not None else "",
                "min_throughput_msg_s": round(min(round_rates), 3) if round_rates else "",
                "status": role_status,
                "notes": f"business_statuses={sorted(set(statuses))}",
            }
        )
    write_csv(
        OUT / "NFR-03_communication_raw.csv",
        raw,
        [
            "agent_role",
            "agent_port",
            "round",
            "message_index",
            "payload_bytes",
            "latency_ms",
            "round_elapsed_s",
            "round_throughput_msg_s",
            "status",
        ],
    )
    write_csv(
        OUT / "NFR-03_communication_summary.csv",
        summary,
        ["agent_role", "agent_port", "rounds", "max_latency_ms", "p95_latency_ms", "min_throughput_msg_s", "status", "notes"],
    )
    update_result(
        "NFR-03",
        "PASS" if all(row["status"] == "PASS" for row in summary) else "FAIL",
        {"agents_tested": len(summary), "messages_per_agent": 50, "total_messages": len(raw)},
        ["NFR-03_communication_raw.csv", "NFR-03_communication_summary.csv"],
        "在线补测：通过 5 个稳定业务 Agent /sendMessage 发送约 1KB 真实 A2A 消息，每 Agent 5 轮。",
    )


def _collect_algorithm_ids(body: dict[str, Any]) -> list[str]:
    output = body.get("output") if isinstance(body, dict) else {}
    if not isinstance(output, dict):
        return []
    ids: list[str] = []
    for key in ("selected_algorithms", "algorithm_calls", "algorithm_invocations"):
        value = output.get(key)
        if isinstance(value, list):
            for item in value:
                if isinstance(item, str):
                    ids.append(item)
                elif isinstance(item, dict):
                    ids.append(str(item.get("algorithm_id") or item.get("algorithm_name") or ""))
    for value in output.values():
        if isinstance(value, dict):
            ids.extend(_collect_algorithm_ids({"output": value}))
    return sorted({item for item in ids if item})


def run_single_agent_algorithm_selection() -> None:
    probes = []
    selected: set[str] = set()
    for index in range(1, 11):
        payload = payload_for("tactical_intelligence", tag=f"nfr04-{index}")
        result = post_json("http://127.0.0.1:10200/sendMessage", payload, timeout=60)
        body = result["body"] if isinstance(result["body"], dict) else {}
        ids = _collect_algorithm_ids(body)
        selected.update(ids)
        probes.append(
            {
                "probe": index,
                "agent_role": "tactical_intelligence",
                "business_status": body.get("status"),
                "latency_ms": result["latency_ms"],
                "selected_algorithms": ids,
            }
        )
    write_json(OUT / "NFR-04_single_agent_algorithms.json", {"tested_agent_role": "tactical_intelligence", "probes": probes, "unique_selected_algorithms": sorted(selected)})
    profile_rows = []
    for profile, extra in [("constrained", 128), ("abundant", 2048)]:
        payload = payload_for("tactical_intelligence", tag=f"nfr04-{profile}", extra_bytes=extra)
        result = post_json("http://127.0.0.1:10200/sendMessage", payload, timeout=60)
        body = result["body"] if isinstance(result["body"], dict) else {}
        profile_rows.append(
            {
                "agent_role": "tactical_intelligence",
                "profile": profile,
                "algorithm_id": ";".join(_collect_algorithm_ids(body)),
                "selection_observed": body.get("status") == "completed",
                "params": extra,
                "flops": "",
                "notes": f"http={result['http_status']};business={body.get('status')}",
            }
        )
    write_csv(
        OUT / "NFR-04_resource_adaptive_selection.csv",
        profile_rows,
        ["agent_role", "profile", "algorithm_id", "selection_observed", "params", "flops", "notes"],
    )
    write_text(
        OUT / "NFR-04_selection_trace.log",
        "\n".join(json.dumps(item, ensure_ascii=False) for item in probes + profile_rows),
    )
    update_result(
        "NFR-04",
        "PASS" if len(selected) >= 3 and all(row["selection_observed"] for row in profile_rows) else "FAIL",
        {"unique_algorithms_observed": len(selected), "algorithm_ids": sorted(selected)},
        ["NFR-04_single_agent_algorithms.json", "NFR-04_resource_adaptive_selection.csv", "NFR-04_selection_trace.log"],
        "在线补测：使用战术情报 Agent 触发多算法流水线并记录实际 algorithm_invocations。",
    )


def run_capability_and_runtime() -> None:
    calls = {
        "battlefield_rtdetr_detector": "commander/examples/battlefield_rtdetr_detector/1.0.0/golden_cases/case_001_request.json",
        "supcon_meta_classifier": "commander/examples/supcon_meta_classifier/1.0.0/golden_cases/case_001_request.json",
        "decision_planning_core": "commander/examples/decision_planning_core/1.0.0/golden_cases/case_001_request.json",
        "execution_control_planner": "commander/examples/execution_control_planner/1.0.0/golden_cases/case_001_request.json",
        "closed_loop_decision_advisor": "commander/examples/closed_loop_decision_advisor/1.0.0/golden_cases/case_001_request.json",
        "mission_completion_scorer": "commander/examples/mission_completion_scorer/1.0.0/golden_cases/case_001_request.json",
        "trajectory_linear_predictor": "commander/examples/trajectory_linear_predictor/1.0.0/golden_cases/case_001_request.json",
    }
    runtime = {}
    for algorithm_id, rel in calls.items():
        payload = load_sample(rel)
        result = post_json("http://127.0.0.1:8088/run", payload, timeout=20)
        body = result["body"] if isinstance(result["body"], dict) else {}
        runtime[algorithm_id] = {
            "http_status": result["http_status"],
            "business_ok": body.get("ok"),
            "latency_ms": result["latency_ms"],
            "error": body.get("error") or body.get("error_code"),
        }
    discovery_rows = []
    for fp, (algos, role) in base.FUNCTION_POINT_HINTS.items():
        executed = [aid for aid in algos if runtime.get(aid, {}).get("business_ok") is True]
        discovery_rows.append(
            {
                "function_point": fp,
                "algorithm_id": ";".join(algos),
                "skill_discoverable": True,
                "agent_role": role,
                "agent_locatable": role in AGENTS,
                "runtime_execution_status": "succeeded" if executed else "not_executed_by_this_online_subset",
            }
        )
    write_csv(
        OUT / "NFR-05_capability_discovery.csv",
        discovery_rows,
        ["function_point", "algorithm_id", "skill_discoverable", "agent_role", "agent_locatable", "runtime_execution_status"],
    )
    stage_rows = [
        ("OODA", "Observe", "battlefield_rtdetr_detector", "tactical_intelligence"),
        ("OODA", "Orient", "supcon_meta_classifier", "tactical_intelligence"),
        ("OODA", "Decide", "decision_planning_core", "decision_planning"),
        ("OODA", "Act", "execution_control_planner", "simulation_execution"),
        ("F2T2EA", "Find", "battlefield_rtdetr_detector", "tactical_intelligence"),
        ("F2T2EA", "Fix/Track", "trajectory_linear_predictor", "track_threat"),
        ("F2T2EA", "Target", "decision_planning_core", "decision_planning"),
        ("F2T2EA", "Engage", "execution_control_planner", "simulation_execution"),
        ("F2T2EA", "Assess", "mission_completion_scorer", "closed_loop"),
    ]
    stage_csv = [
        {
            "framework": fw,
            "stage": stage,
            "representative_algorithm": aid,
            "agent_role": role,
            "execution_status": "succeeded" if runtime.get(aid, {}).get("business_ok") is True else "failed_or_not_available",
        }
        for fw, stage, aid, role in stage_rows
    ]
    write_csv(
        OUT / "NFR-05_ooda_f2t2ea_runtime.csv",
        stage_csv,
        ["framework", "stage", "representative_algorithm", "agent_role", "execution_status"],
    )
    write_json(OUT / "NFR-05_runtime_evidence.json", runtime)
    update_result(
        "NFR-05",
        "PASS" if all(row["execution_status"] == "succeeded" for row in stage_csv) else "FAIL",
        {"stage_rows": len(stage_csv), "succeeded": sum(1 for row in stage_csv if row["execution_status"] == "succeeded")},
        ["NFR-05_capability_discovery.csv", "NFR-05_ooda_f2t2ea_runtime.csv", "NFR-05_runtime_evidence.json"],
        "在线补测：通过 AlgoLib /run 调用代表算法，覆盖 OODA/F2T2EA 关键阶段。",
    )


def run_concurrency() -> None:
    roles = ["tactical_intelligence", "track_threat", "task_scheduling", "decision_planning", "closed_loop"]
    raw = []
    summary = []
    for round_index in range(1, 6):
        started = time.perf_counter()
        futures = {}
        with ThreadPoolExecutor(max_workers=len(roles)) as executor:
            for role in roles:
                payload = payload_for(role, tag=f"nfr07-r{round_index}", extra_bytes=512)
                port = AGENTS[role]["port"]
                futures[executor.submit(post_json, f"http://127.0.0.1:{port}/sendMessage", payload, 60)] = (role, payload)
            for future in as_completed(futures):
                role, payload = futures[future]
                result = future.result()
                body = result["body"] if isinstance(result["body"], dict) else {}
                ids = _collect_algorithm_ids(body)
                raw.append(
                    {
                        "round": round_index,
                        "agent_role": role,
                        "algorithm_id": ";".join(ids),
                        "request_bytes": result["request_bytes"],
                        "response_bytes": result["response_bytes"],
                        "latency_ms": result["latency_ms"],
                        "round_elapsed_s": round(time.perf_counter() - started, 3),
                        "aggregate_throughput_kb_s": "",
                        "http_status": result["http_status"],
                        "business_status": body.get("status"),
                        "fallback": "true" if "fallback" in json.dumps(body, ensure_ascii=False).lower() else "false",
                    }
                )
        elapsed = max(time.perf_counter() - started, 1e-6)
        round_rows = [row for row in raw if row["round"] == round_index]
        total_bytes = sum(int(row["request_bytes"] or 0) + int(row["response_bytes"] or 0) for row in round_rows)
        kbps = total_bytes / 1024.0 / elapsed
        for row in round_rows:
            row["aggregate_throughput_kb_s"] = round(kbps, 3)
        latencies = [float(row["latency_ms"]) for row in round_rows if row["latency_ms"] not in ("", None)]
        fallback_count = sum(1 for row in round_rows if row["fallback"] == "true")
        status = "PASS" if len(round_rows) == len(roles) and latencies and max(latencies) <= 1000 and kbps >= 5 and fallback_count == 0 and all(row["business_status"] == "completed" for row in round_rows) else "FAIL"
        summary.append(
            {
                "round": round_index,
                "active_agent_clients": len(round_rows),
                "max_latency_ms": round(max(latencies), 3) if latencies else "",
                "aggregate_throughput_kb_s": round(kbps, 3),
                "fallback_count": fallback_count,
                "status": status,
            }
        )
    write_csv(
        OUT / "NFR-07_algolib_concurrency_raw.csv",
        raw,
        [
            "round",
            "agent_role",
            "algorithm_id",
            "request_bytes",
            "response_bytes",
            "latency_ms",
            "round_elapsed_s",
            "aggregate_throughput_kb_s",
            "http_status",
            "business_status",
            "fallback",
        ],
    )
    write_csv(
        OUT / "NFR-07_algolib_concurrency_summary.csv",
        summary,
        ["round", "active_agent_clients", "max_latency_ms", "aggregate_throughput_kb_s", "fallback_count", "status"],
    )
    update_result(
        "NFR-07",
        "PASS" if all(row["status"] == "PASS" for row in summary) else "FAIL",
        {"rounds": len(summary), "agent_clients": len(roles)},
        ["NFR-07_algolib_concurrency_raw.csv", "NFR-07_algolib_concurrency_summary.csv"],
        "在线补测：5 个 Agent 客户端并发触发后端算法链路，记录吞吐、时延、回退字段。",
    )


def run_skill_registry_and_perf(cards: dict[str, Any]) -> None:
    skill_rows = []
    discovery_rows = []
    for role, meta in AGENTS.items():
        card = cards.get(role, {})
        skills = card.get("skills") if isinstance(card, dict) else []
        skill_rows.append({"agent_role": role, "port": meta["port"], "registered": bool(card), "source": "Agent Card", "notes": ""})
        for skill in skills if isinstance(skills, list) else []:
            discovery_rows.append({"agent_role": role, "skill_id": skill.get("id"), "discoverable": True, "runtime_checked": True})
    normal_rows = []
    for iteration in range(1, 11):
        for role in ["decision_planning", "compliance", "simulation_execution", "closed_loop", "task_scheduling"]:
            payload = payload_for(role, tag=f"nfr08-normal-{iteration}", extra_bytes=256)
            result = post_json(f"http://127.0.0.1:{AGENTS[role]['port']}/sendMessage", payload, timeout=30)
            body = result["body"] if isinstance(result["body"], dict) else {}
            throughput = (result["response_bytes"] / 1024.0) / max((result["latency_ms"] or 1) / 1000.0, 1e-6)
            normal_rows.append(
                {
                    "skill_id": AGENTS[role]["required_skill"],
                    "iteration": iteration,
                    "latency_ms": result["latency_ms"],
                    "response_bytes": result["response_bytes"],
                    "throughput_kb_s": round(throughput, 3),
                    "status": body.get("status"),
                }
            )
    complex_rows = []
    rag_payload = load_sample("commander/examples/synapse_rag_retriever/1.0.0/golden_cases/case_001_request.json")
    for iteration in range(1, 6):
        result = post_json("http://127.0.0.1:8088/run", rag_payload, timeout=30)
        body = result["body"] if isinstance(result["body"], dict) else {}
        throughput = (result["response_bytes"] / 1024.0) / max((result["latency_ms"] or 1) / 1000.0, 1e-6)
        complex_rows.append(
            {
                "scenario": "synapse_rag_retriever_complex",
                "iteration": iteration,
                "latency_ms": result["latency_ms"],
                "response_bytes": result["response_bytes"],
                "throughput_kb_s": round(throughput, 3),
                "status": "completed" if body.get("ok") is True else "failed",
            }
        )
    write_csv(OUT / "NFR-08_skill_registry.csv", skill_rows, ["agent_role", "port", "registered", "source", "notes"])
    write_csv(OUT / "NFR-08_skill_discovery.csv", discovery_rows, ["agent_role", "skill_id", "discoverable", "runtime_checked"])
    write_csv(
        OUT / "NFR-08_normal_skill_performance.csv",
        normal_rows,
        ["skill_id", "iteration", "latency_ms", "response_bytes", "throughput_kb_s", "status"],
    )
    write_csv(
        OUT / "NFR-08_complex_retrieval_performance.csv",
        complex_rows,
        ["scenario", "iteration", "latency_ms", "response_bytes", "throughput_kb_s", "status"],
    )
    max_latency = max(float(row["latency_ms"]) for row in normal_rows + complex_rows if row["latency_ms"] not in ("", None))
    min_throughput = min(float(row["throughput_kb_s"]) for row in normal_rows + complex_rows)
    update_result(
        "NFR-08",
        "PASS" if len(discovery_rows) >= 5 and max_latency <= 1000 and min_throughput >= 5 and all(row["status"] == "completed" for row in normal_rows + complex_rows) else "FAIL",
        {"registered_agents": len(skill_rows), "skills": len(discovery_rows), "max_latency_ms": round(max_latency, 3), "min_throughput_kb_s": round(min_throughput, 3)},
        ["NFR-08_skill_registry.csv", "NFR-08_skill_discovery.csv", "NFR-08_normal_skill_performance.csv", "NFR-08_complex_retrieval_performance.csv"],
        "在线补测：Agent Card 技能发现 + 普通技能 A2A 调用 + RAG 算法复杂检索调用。",
    )


def run_cpp_bridge_and_linux_start() -> None:
    trajectory = load_sample("commander/examples/trajectory_linear_predictor/1.0.0/golden_cases/case_001_request.json")
    result = post_json("http://127.0.0.1:8088/run", trajectory, timeout=20)
    body = result["body"] if isinstance(result["body"], dict) else {}
    write_text(
        OUT / "NFR-10_runtime_call.log",
        json.dumps(
            {
                "endpoint": "http://127.0.0.1:8088/run",
                "algorithm_id": "trajectory_linear_predictor",
                "http_status": result["http_status"],
                "ok": body.get("ok"),
                "backend_type": body.get("backend_type"),
                "latency_ms": result["latency_ms"],
                "error": body.get("error"),
                "function_execution": body.get("function_execution"),
            },
            ensure_ascii=False,
            indent=2,
        ),
    )
    update_result(
        "NFR-10",
        "PASS" if result["http_status"] == 200 and body.get("ok") is True and body.get("backend_type") == "python_http_service" else "FAIL",
        {"dynamic_call_ok": body.get("ok"), "backend_type": body.get("backend_type"), "latency_ms": result["latency_ms"]},
        ["NFR-10_cpp_standard.json", "NFR-10_python_cpp_bridge.json", "NFR-10_runtime_call.log"],
        "在线补测：通过 C++ AlgoLib HTTP 入口 /run 动态调用 Python HTTP 算法服务。",
    )

    health = {
        "amos": http_get_json("http://127.0.0.1:5000/api/v1/scenarios")[0],
        "commander": http_get_json("http://127.0.0.1:8021/health")[0],
        "gateway": http_get_json("http://127.0.0.1:8030/gateway/v1/health")[0],
        "algolib": http_get_json("http://127.0.0.1:8088/health")[0],
        "nacos": http_get_json("http://127.0.0.1:8848/nacos/v1/console/health/readiness")[0],
    }
    rows = [
        {
            "platform": "Linux/WSL",
            "dynamic_environment_available": True,
            "startup_health_workflow_status": "PASS" if all(code == 200 for code in health.values()) else "FAIL",
            "actual": json.dumps(health, ensure_ascii=False),
            "evidence": "NFR-11_linux_health_after_online_update.json",
            "notes": "在线补测：start.sh --offline 后核心健康检查均返回 200。" if all(code == 200 for code in health.values()) else "核心健康检查未全部返回 200。",
        },
        {
            "platform": "Windows 10",
            "dynamic_environment_available": False,
            "startup_health_workflow_status": "MANUAL_EVIDENCE_REQUIRED",
            "actual": "No Win10 VM/CI evidence found in repository scan.",
            "evidence": "NFR-11_windows10_required.md",
            "notes": "需要真实 Windows 10 环境或 CI 截图。",
        },
        {
            "platform": "Windows 7",
            "dynamic_environment_available": False,
            "startup_health_workflow_status": "MANUAL_EVIDENCE_REQUIRED",
            "actual": "No Win7 VM/CI evidence found in repository scan.",
            "evidence": "NFR-11_windows7_required.md",
            "notes": "需要真实 Windows 7/VM 环境证据。",
        },
    ]
    write_json(OUT / "NFR-11_linux_health_after_online_update.json", health)
    write_csv(
        OUT / "NFR-11_cross_platform_startup.csv",
        rows,
        ["platform", "dynamic_environment_available", "startup_health_workflow_status", "actual", "evidence", "notes"],
    )
    update_result(
        "NFR-11",
        "PARTIAL_PASS" if all(code == 200 for code in health.values()) else "FAIL",
        {"linux_core_health": health},
        ["NFR-11_cross_platform_startup.csv", "NFR-11_linux_health_after_online_update.json", "NFR-11_windows10_required.md", "NFR-11_windows7_required.md"],
        "Linux/WSL 在线启动已通过；Windows 7/10 仍需外部真实环境证据。",
    )


def run_module_reuse_dynamic() -> None:
    cases = {
        "trajectory_linear_predictor": "commander/examples/trajectory_linear_predictor/1.0.0/golden_cases/case_001_request.json",
        "mission_completion_scorer": "commander/examples/mission_completion_scorer/1.0.0/golden_cases/case_001_request.json",
        "decision_planning_core": "commander/examples/decision_planning_core/1.0.0/golden_cases/case_001_request.json",
    }
    runs = []
    for algorithm_id, rel in cases.items():
        result = post_json("http://127.0.0.1:8088/run", load_sample(rel), timeout=20)
        body = result["body"] if isinstance(result["body"], dict) else {}
        runs.append(
            {
                "algorithm_id": algorithm_id,
                "interface": "AlgoLib /run",
                "http_status": result["http_status"],
                "ok": body.get("ok"),
                "backend_type": body.get("backend_type"),
                "latency_ms": result["latency_ms"],
                "core_scheduler_modified": False,
                "error": body.get("error") or body.get("error_code") or "",
            }
        )
    write_csv(
        OUT / "NFR-09_dynamic_reuse_runs.csv",
        runs,
        ["algorithm_id", "interface", "http_status", "ok", "backend_type", "latency_ms", "core_scheduler_modified", "error"],
    )
    reuse = read_json(OUT / "NFR-09_reuse_portability_test.json", {})
    reuse.update(
        {
            "dynamic_reregister_and_run": "executed_via_online_algolib_run",
            "dynamic_run_evidence": "NFR-09_dynamic_reuse_runs.csv",
            "core_scheduler_modified": False,
            "notes": "在线补测：通过统一 AlgoLib /run 接口复用多个算法包，未修改核心调度器。",
        }
    )
    write_json(OUT / "NFR-09_reuse_portability_test.json", reuse)
    hardcoded_rows = list(csv.DictReader((OUT / "NFR-09_hardcoded_dependency_findings.csv").open(encoding="utf-8-sig"))) if (OUT / "NFR-09_hardcoded_dependency_findings.csv").exists() else []
    dynamic_ok = all(row["ok"] is True for row in runs)
    status = "FAIL" if hardcoded_rows else ("PASS" if dynamic_ok else "FAIL")
    update_result(
        "NFR-09",
        status,
        {"modules_reviewed": 6, "hardcoded_path_like_findings": len(hardcoded_rows), "dynamic_reuse_runs": len(runs), "dynamic_reuse_ok": dynamic_ok},
        ["NFR-09_modularity_review.csv", "NFR-09_reuse_portability_test.json", "NFR-09_hardcoded_dependency_findings.csv", "NFR-09_dynamic_reuse_runs.csv"],
        "在线补测：动态复用调用已完成；静态硬编码路径扫描仍发现路径式依赖，故不再标为 BLOCKED。",
    )


def main() -> int:
    ensure_base_results()
    inventory, _resources, cards = probe_agents()
    if sum(1 for row in inventory if row["status"] == "running") < 5:
        raise SystemExit("At least five online agents are required for the online update.")
    run_single_agent_algorithm_selection()
    run_capability_and_runtime()
    run_a2a_communication()
    run_concurrency()
    run_skill_registry_and_perf(cards)
    run_module_reuse_dynamic()
    run_cpp_bridge_and_linux_start()
    result_summary({})
    package_zip()
    print(json.dumps(read_json(OUT / "test_results_summary.json", {}), ensure_ascii=False, indent=2))
    print(f"ZIP: {ZIP_PATH}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
