#!/usr/bin/env python3
"""Repeatable acceptance checks for the integrated A2A/AMOS demonstration."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import time
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


ROOT = Path(__file__).resolve().parents[1]
AMOS = os.getenv("AMOS_BASE_URL", "http://127.0.0.1:5000").rstrip("/")
GATEWAY = os.getenv("A2A_GATEWAY_URL", "http://127.0.0.1:8030").rstrip("/")
ALGOLIB = os.getenv("ALGOLIB_BASE_URL", "http://127.0.0.1:8088").rstrip("/")
NACOS = os.getenv("NACOS_ADDR", "127.0.0.1:8848")
if not NACOS.startswith(("http://", "https://")):
    NACOS = f"http://{NACOS}"
NACOS = NACOS.rstrip("/")

EXPECTED_ROLES = {
    "tactical_intelligence",
    "track_threat",
    "task_scheduling",
    "decision_planning",
    "compliance_authorization",
    "simulation_execution",
    "closed_loop",
}
EXPECTED_AGENT_PROCESSES = {
    "agent-tactical-intelligence",
    "agent-track-threat",
    "agent-task-scheduling",
    "agent-decision-planning",
    "agent-compliance",
    "agent-simulation-execution",
    "agent-closed-loop",
}
EXPECTED_CHECKPOINTS = [
    "MAR-CP-PERCEPTION",
    "MAR-CP-ASSESS",
    "MAR-CP-PLAN",
    "MAR-CP-CLOSE",
]
EXPECTED_ACTIVITY_COUNTS = {
    "MAR-CP-PERCEPTION": 3,
    "MAR-CP-ASSESS": 3,
    "MAR-CP-PLAN": 4,
    "MAR-CP-CLOSE": 3,
}
TERMINAL = {"completed", "failed", "cancelled", "paused", "input_required"}


class VerificationError(RuntimeError):
    pass


def http_json(url: str, body: dict[str, Any] | None = None, timeout: float = 90) -> tuple[int, dict[str, Any]]:
    data = None if body is None else json.dumps(body).encode("utf-8")
    request = Request(url, data=data, headers={"Content-Type": "application/json"})
    try:
        with urlopen(request, timeout=timeout) as response:
            payload = json.load(response)
            return response.status, payload
    except HTTPError as exc:
        try:
            payload = json.load(exc)
        except (json.JSONDecodeError, UnicodeDecodeError):
            payload = {"error": exc.reason}
        return exc.code, payload
    except URLError as exc:
        raise VerificationError(f"service unavailable at {url}: {exc.reason}") from exc


def require(condition: bool, message: str) -> None:
    if not condition:
        raise VerificationError(message)


def data_of(payload: dict[str, Any]) -> dict[str, Any]:
    value = payload.get("data")
    return value if isinstance(value, dict) else {}


def contains_value(value: Any, expected: str) -> bool:
    if value == expected:
        return True
    if isinstance(value, dict):
        return any(contains_value(item, expected) for item in value.values())
    if isinstance(value, list):
        return any(contains_value(item, expected) for item in value)
    return False


def planner_evidence(value: Any) -> dict[str, Any]:
    evidence: dict[str, Any] = {"llm_plan_count": 0, "raw_plan_count": 0, "modes": set(), "fallbacks": set()}

    def walk(item: Any) -> None:
        if isinstance(item, dict):
            if item.get("mode") == "llm" and isinstance(item.get("algorithm_calls"), list):
                evidence["llm_plan_count"] += 1
                if isinstance(item.get("raw_llm_plan"), dict):
                    evidence["raw_plan_count"] += 1
            if item.get("planner_mode"):
                evidence["modes"].add(str(item["planner_mode"]))
            if item.get("planner_fallback_reason"):
                evidence["fallbacks"].add(str(item["planner_fallback_reason"]))
            for nested in item.values():
                walk(nested)
        elif isinstance(item, list):
            for nested in item:
                walk(nested)

    walk(value)
    return evidence


def verify_agent_processes() -> list[dict[str, Any]]:
    records = []
    pids = set()
    for name in sorted(EXPECTED_AGENT_PROCESSES):
        path = ROOT / ".runtime" / "pids" / f"{name}.pid"
        require(path.is_file(), f"missing PID file: {path}")
        raw = path.read_text(encoding="ascii").strip()
        require(raw.isdigit(), f"invalid PID file: {path}")
        pid = int(raw)
        try:
            os.kill(pid, 0)
        except OSError as exc:
            raise VerificationError(f"agent process is not running: {name} ({pid})") from exc
        require(pid not in pids, f"agents share a PID unexpectedly: {pid}")
        pids.add(pid)
        records.append({"service": name, "pid": pid})
    return records


def verify_infrastructure() -> dict[str, Any]:
    health = {}
    for name, url in {
        "amos": f"{AMOS}/api/v1/status",
        "gateway": f"{GATEWAY}/gateway/v1/health",
        "algolib": f"{ALGOLIB}/health",
    }.items():
        status, payload = http_json(url)
        require(status == 200, f"{name} health failed with HTTP {status}")
        health[name] = payload.get("status", "ok")

    status, payload = http_json(
        f"{NACOS}/nacos/v1/ns/instance/list"
        "?serviceName=A2A-Agent&groupName=DEFAULT_GROUP&healthyOnly=true",
    )
    require(status == 200, f"Nacos query failed with HTTP {status}")
    hosts = payload.get("hosts") if isinstance(payload.get("hosts"), list) else []
    roles = {
        str(host.get("metadata", {}).get("role") or "")
        for host in hosts
        if isinstance(host, dict) and host.get("healthy") is True
    }
    require(roles == EXPECTED_ROLES, f"Nacos roles mismatch: {sorted(roles)}")
    return {
        "health": health,
        "agent_processes": verify_agent_processes(),
        "nacos": {"healthy_instances": len(hosts), "roles": sorted(roles)},
    }


def poll_workflow(workflow_id: str, timeout: float) -> dict[str, Any]:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        status, payload = http_json(f"{GATEWAY}/gateway/v1/workflows/{workflow_id}")
        require(status == 200, f"workflow {workflow_id} returned HTTP {status}")
        if str(payload.get("status") or "").casefold() in TERMINAL:
            return payload
        time.sleep(0.5)
    raise VerificationError(f"workflow timed out: {workflow_id}")


def simulation_tracks() -> tuple[dict[str, Any], dict[str, dict[str, Any]]]:
    status, payload = http_json(f"{AMOS}/api/v1/sim/state")
    require(status == 200, "AMOS simulation state unavailable")
    state = data_of(payload)
    tracks = {
        str(item.get("id")): item
        for item in state.get("fused_tracks", [])
        if isinstance(item, dict) and item.get("id")
    }
    return state, tracks


def fire(track_id: str, approved: bool) -> tuple[int, dict[str, Any]]:
    return http_json(f"{AMOS}/api/v1/sim/commands", {
        "command_type": "fire",
        "params": {
            "track_id": track_id,
            "asset_id": "ESCORT-01",
            "weapon_name": "舰载反舰导弹",
        },
        "authorization": {"approved": approved},
    })


def run_scenario(
    *,
    authorize_fire: bool,
    require_llm_evidence: bool,
    workflow_timeout: float,
    fast_forward: bool,
) -> dict[str, Any]:
    status, payload = http_json(f"{AMOS}/api/v1/director/configure", {
        "scenario_id": "maritime-convoy-air-defense",
        "mode": "demonstration",
        "branch": "standard",
    })
    require(status == 200, f"director configure failed with HTTP {status}")
    run_id = str(data_of(payload).get("run_id") or "")
    require(run_id.startswith("run-"), "director did not return a run_id")

    workflows: list[dict[str, Any]] = []
    validated_checkpoints: set[str] = set()
    authorization: dict[str, Any] = {}
    launched_weapon_id = None
    llm_evidence: dict[str, Any] = {
        "llm_plan_count": 0,
        "raw_plan_count": 0,
        "modes": set(),
        "fallbacks": set(),
    }
    initial_action = "advance_checkpoint" if fast_forward else "start_auto"
    status, payload = http_json(
        f"{AMOS}/api/v1/director/action",
        {"action": initial_action},
    )
    require(
        status == 200,
        f"director {initial_action} failed with HTTP {status}: {payload.get('error')}",
    )
    if not fast_forward:
        speed_status, speed_payload = http_json(
            f"{AMOS}/api/v1/sim/speed",
            {"speed": 32},
        )
        require(
            speed_status == 200 and float(data_of(speed_payload).get("speed") or 0) == 32.0,
            "simulation did not accept 32x demonstration speed",
        )

    deadline = time.monotonic() + max(120.0, workflow_timeout * len(EXPECTED_CHECKPOINTS) + 180.0)
    final_director_state: dict[str, Any] = {}
    fast_advanced_after: set[str] = set()
    fast_gate_started = False
    while time.monotonic() < deadline:
        status, payload = http_json(f"{AMOS}/api/v1/director/state")
        require(status == 200, f"director state failed with HTTP {status}")
        director_state = data_of(payload)
        final_director_state = director_state
        director_status = str(director_state.get("director_status") or "")
        require(
            director_status != "error",
            f"director failed: {director_state.get('last_error') or 'unknown error'}",
        )

        if fast_forward:
            current = director_state.get("current_checkpoint")
            if (
                isinstance(current, dict)
                and current.get("analysis_status") in {
                    "submitted", "running", "backend_unreachable"
                }
            ):
                refresh_status, refresh_payload = http_json(
                    f"{AMOS}/api/v1/director/action",
                    {"action": "refresh_analysis"},
                )
                require(
                    refresh_status == 200,
                    f"director refresh_analysis failed with HTTP {refresh_status}: "
                    f"{refresh_payload.get('error')}",
                )
                director_state = data_of(refresh_payload)
                final_director_state = director_state

        for checkpoint in director_state.get("reached_checkpoints") or []:
            if not isinstance(checkpoint, dict):
                continue
            checkpoint_id = str(checkpoint.get("checkpoint_id") or "")
            if checkpoint_id in validated_checkpoints or checkpoint.get("analysis_status") != "completed":
                continue
            expected_index = len(validated_checkpoints)
            require(
                expected_index < len(EXPECTED_CHECKPOINTS)
                and checkpoint_id == EXPECTED_CHECKPOINTS[expected_index],
                f"unexpected checkpoint order: {checkpoint_id}",
            )
            submission = checkpoint.get("submission") if isinstance(checkpoint.get("submission"), dict) else {}
            workflow_id = str(submission.get("workflow_id") or "")
            require(bool(workflow_id), f"{checkpoint_id} did not submit a workflow")
            projection_status, projection_payload = http_json(
                f"{GATEWAY}/gateway/v1/workflows/{workflow_id}"
            )
            require(projection_status == 200, f"workflow unavailable: {workflow_id}")
            require(
                str(projection_payload.get("status") or "").casefold() == "completed",
                f"workflow did not complete: {workflow_id}",
            )
            current_planner = planner_evidence(projection_payload)
            llm_evidence["llm_plan_count"] += current_planner["llm_plan_count"]
            llm_evidence["raw_plan_count"] += current_planner["raw_plan_count"]
            llm_evidence["modes"].update(current_planner["modes"])
            llm_evidence["fallbacks"].update(current_planner["fallbacks"])
            view_status, view_payload = http_json(f"{AMOS}/api/v1/a2a/workflows/{workflow_id}/view")
            require(view_status == 200, f"AMOS workflow view failed: {workflow_id}")
            view = data_of(view_payload)
            work_status, work_payload = http_json(
                f"{GATEWAY}/gateway/v1/workflows/{workflow_id}/work-list"
            )
            require(work_status == 200, f"work list failed: {workflow_id}")
            work_list = work_payload.get("work_list")
            expected_count = EXPECTED_ACTIVITY_COUNTS[checkpoint_id]
            require(
                isinstance(work_list, list) and len(work_list) == expected_count,
                f"expected {expected_count} activities for {workflow_id}",
            )
            require(
                all(str(item.get("status") or "").casefold() == "completed" for item in work_list),
                f"incomplete BPEL activity in {workflow_id}",
            )
            verified_algorithms = int(
                ((view.get("algorithms") or {}).get("counts") or {}).get("verified") or 0
            )
            require(verified_algorithms > 0, f"no verified algorithm evidence for {workflow_id}")
            workflows.append({
                "checkpoint": checkpoint_id,
                "workflow_id": workflow_id,
                "status": projection_payload.get("status"),
                "activities_completed": len(work_list),
                "verified_algorithms": verified_algorithms,
            })
            validated_checkpoints.add(checkpoint_id)

            if checkpoint_id == "MAR-CP-PLAN":
                _, plan_tracks = simulation_tracks()
                fishing = next(
                    (item for item in plan_tracks.values() if item.get("classification") == "FISHING_VESSEL"),
                    None,
                )
                hostile = next(
                    (item for item in plan_tracks.values() if item.get("classification") == "FAST_ATTACK_CRAFT"),
                    None,
                )
                require(fishing is not None, "fishing vessel classification is missing")
                require(hostile is not None, "hostile craft classification is missing")
                require(contains_value(projection_payload, "pending_review"), "pending_review authorization evidence is missing")
                require(contains_value(projection_payload, "review_required"), "review_required decision evidence is missing")
                denied_status, _ = fire(str(hostile["id"]), False)
                fishing_status, _ = fire(str(fishing["id"]), True)
                require(denied_status == 409, "unapproved hostile fire was not rejected")
                require(fishing_status == 409, "approved fishing-vessel fire was not rejected")
                authorization.update({
                    "pending_review": True,
                    "unapproved_hostile_http": denied_status,
                    "approved_fishing_http": fishing_status,
                    "hostile_track_id": hostile["id"],
                })

        if director_status == "awaiting_authorization":
            authorization["gate_observed"] = True
            if not authorize_fire:
                http_json(f"{AMOS}/api/v1/director/action", {"action": "stop_auto"})
                break
            if not launched_weapon_id:
                hostile_track_id = str(authorization.get("hostile_track_id") or "")
                require(bool(hostile_track_id), "authorization gate reached before hostile target validation")
                approved_status, approved_payload = fire(hostile_track_id, True)
                require(approved_status == 200, f"approved hostile fire failed with HTTP {approved_status}")
                result = data_of(approved_payload).get("result") or {}
                launched_weapon_id = str(result.get("weapon_id") or "")
                require(bool(launched_weapon_id), "approved fire did not return a weapon_id")
                authorization.update({
                    "approved_hostile_http": approved_status,
                    "weapon_id": launched_weapon_id,
                    "authorization": result.get("authorization"),
                })
                if fast_forward:
                    stop_status, stop_payload = http_json(
                        f"{AMOS}/api/v1/director/action",
                        {"action": "stop_auto"},
                    )
                    require(
                        stop_status == 200,
                        f"director stop_auto failed with HTTP {stop_status}: {stop_payload.get('error')}",
                    )
                    advance_status, advance_payload = http_json(
                        f"{AMOS}/api/v1/director/action",
                        {"action": "advance_checkpoint"},
                    )
                    require(
                        advance_status == 200,
                        f"post-authorization advance failed with HTTP {advance_status}: "
                        f"{advance_payload.get('error')}",
                    )
                    fast_advanced_after.add("MAR-CP-PLAN")

        if fast_forward and director_status not in {"awaiting_authorization", "completed"}:
            current = director_state.get("current_checkpoint")
            current_id = str(current.get("checkpoint_id") or "") if isinstance(current, dict) else ""
            current_analysis = str(current.get("analysis_status") or "") if isinstance(current, dict) else ""
            if (
                current_id == "MAR-CP-PLAN"
                and current_analysis == "completed"
                and current_id in validated_checkpoints
                and not fast_gate_started
            ):
                while True:
                    sim_state, _ = simulation_tracks()
                    phase = str(
                        (((sim_state.get("mission_phases") or {}).get("f2t2ea") or {}).get("current"))
                        or ""
                    )
                    if phase == "ENGAGE":
                        break
                    step_status, step_payload = http_json(
                        f"{AMOS}/api/v1/director/action",
                        {"action": "step_tick", "step_sec": 30},
                    )
                    require(
                        step_status == 200,
                        f"fast-forward to ENGAGE failed with HTTP {step_status}: {step_payload.get('error')}",
                    )
                gate_status, gate_payload = http_json(
                    f"{AMOS}/api/v1/director/action",
                    {"action": "start_auto"},
                )
                require(
                    gate_status == 200,
                    f"authorization gate start failed with HTTP {gate_status}: {gate_payload.get('error')}",
                )
                fast_gate_started = True
            elif (
                current_analysis == "completed"
                and current_id in validated_checkpoints
                and current_id not in fast_advanced_after
            ):
                advance_status, advance_payload = http_json(
                    f"{AMOS}/api/v1/director/action",
                    {"action": "advance_checkpoint"},
                )
                require(
                    advance_status == 200,
                    f"fast checkpoint advance failed with HTTP {advance_status}: {advance_payload.get('error')}",
                )
                fast_advanced_after.add(current_id)

        if director_status == "completed":
            break
        time.sleep(0.5)
    else:
        raise VerificationError("automatic scenario timed out")

    if not authorize_fire:
        require(authorization.get("gate_observed") is True, "authorization gate was not reached")
        return {
            "run_id": run_id,
            "checkpoints": [item["checkpoint"] for item in workflows],
            "workflows": workflows,
            "authorization": authorization,
            "completion": "awaiting_operator_authorization",
        }

    require(final_director_state.get("director_status") == "completed", "director did not complete")
    require(validated_checkpoints == set(EXPECTED_CHECKPOINTS), "not all checkpoints completed")

    final_state, final_tracks = simulation_tracks()
    require(any(item.get("classification") == "FISHING_VESSEL" for item in final_tracks.values()), "fishing-vessel classification was not preserved")
    final_weapons = final_state.get("weapons") if isinstance(final_state.get("weapons"), list) else []
    weapon_status = None
    if launched_weapon_id:
        weapon = next((item for item in final_weapons if item.get("id") == launched_weapon_id), None)
        require(weapon is not None, "launched weapon is absent from final state")
        weapon_status = str(weapon.get("status") or "")
        require(weapon_status == "hit", f"weapon remained in unexpected state: {weapon_status}")
    if require_llm_evidence:
        require(llm_evidence["llm_plan_count"] > 0, "no dynamic LLM algorithm plan was recorded")
        require(llm_evidence["raw_plan_count"] > 0, "no raw TIA LLM plan was recorded")
        require(
            any(
                "azure" in mode
                or "openai_compatible" in mode
                or "qwen" in mode
                for mode in llm_evidence["modes"]
            ),
            "Track Threat did not record an LLM planner provider",
        )
        require(not llm_evidence["fallbacks"], f"LLM planner fallback detected: {sorted(llm_evidence['fallbacks'])}")
    return {
        "run_id": run_id,
        "checkpoints": EXPECTED_CHECKPOINTS,
        "workflows": workflows,
        "authorization": authorization,
        "completion": "completed",
        "final_weapon_status": weapon_status,
        "final_classifications": {
            track_id: item.get("classification") for track_id, item in final_tracks.items()
        },
        "llm_evidence": {
            "llm_plan_count": llm_evidence["llm_plan_count"],
            "raw_plan_count": llm_evidence["raw_plan_count"],
            "modes": sorted(llm_evidence["modes"]),
            "fallbacks": sorted(llm_evidence["fallbacks"]),
        },
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-scenario", action="store_true", help="create and execute a clean four-checkpoint run")
    parser.add_argument("--authorize-fire", action="store_true", help="explicitly authorize one simulated fire command")
    parser.add_argument("--require-llm-evidence", action="store_true", help="fail unless dynamic LLM planning evidence is present")
    parser.add_argument("--workflow-timeout", type=float, default=180.0)
    parser.add_argument(
        "--wall-clock",
        action="store_true",
        help="play the simulation at 32x wall-clock speed instead of causal checkpoint fast-forward",
    )
    args = parser.parse_args()
    if args.authorize_fire and not args.run_scenario:
        parser.error("--authorize-fire requires --run-scenario")
    if args.require_llm_evidence and not args.run_scenario:
        parser.error("--require-llm-evidence requires --run-scenario")

    report: dict[str, Any] = {"infrastructure": verify_infrastructure()}
    if args.run_scenario:
        report["scenario"] = run_scenario(
            authorize_fire=args.authorize_fire,
            require_llm_evidence=args.require_llm_evidence,
            workflow_timeout=args.workflow_timeout,
            fast_forward=not args.wall_clock,
        )
    report["status"] = "passed"
    output_path = ROOT / ".runtime" / "verification-last.json"
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))
    print(f"verification report: {output_path}")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except VerificationError as exc:
        print(f"verification failed: {exc}")
        raise SystemExit(1) from exc
