#!/usr/bin/env python3
"""Acceptance check for all six coastal-joint-recon-strike A2A checkpoints."""

from __future__ import annotations

import argparse
import json
import os
import time
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


AMOS = os.environ.get("AMOS_BASE_URL", "http://127.0.0.1:5000").rstrip("/")
GATEWAY = os.environ.get("A2A_GATEWAY_URL", "http://127.0.0.1:8030").rstrip("/")
CHECKPOINTS = (
    "CJR-CP-CUE",
    "CJR-CP-IDENTIFY",
    "CJR-CP-FUSION",
    "CJR-CP-PLAN",
    "CJR-CP-ENGAGE",
    "CJR-CP-CLOSE",
)
ACTIVITIES = (3, 3, 3, 4, 3, 3)
TERMINAL = {"completed", "failed", "cancelled", "paused", "input_required"}


class VerifyError(RuntimeError):
    pass


def require(condition: bool, message: str) -> None:
    if not condition:
        raise VerifyError(message)


def request_json(url: str, body: dict[str, Any] | None = None, *, timeout: float = 60) -> tuple[int, dict[str, Any]]:
    data = None if body is None else json.dumps(body).encode("utf-8")
    req = Request(url, data=data, headers={"Content-Type": "application/json"})
    try:
        with urlopen(req, timeout=timeout) as response:
            return response.status, json.load(response)
    except HTTPError as exc:
        try:
            payload = json.load(exc)
        except (ValueError, UnicodeError):
            payload = {"error": str(exc)}
        return exc.code, payload
    except (URLError, TimeoutError) as exc:
        raise VerifyError(f"unreachable {url}: {exc}") from exc


def data_of(payload: dict[str, Any]) -> dict[str, Any]:
    data = payload.get("data")
    return data if isinstance(data, dict) else {}


def action(name: str) -> dict[str, Any]:
    status, payload = request_json(
        f"{AMOS}/api/v1/director/action", {"action": name}, timeout=180
    )
    require(status == 200, f"director {name} failed: HTTP {status}: {payload}")
    return data_of(payload)


def state() -> dict[str, Any]:
    status, payload = request_json(f"{AMOS}/api/v1/director/state")
    require(status == 200, f"director state failed: HTTP {status}")
    return data_of(payload)


def current_checkpoint(expected: str, *, timeout: float = 60) -> tuple[dict[str, Any], str]:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        current = state().get("current_checkpoint") or {}
        require(current.get("checkpoint_id") == expected, f"expected {expected}, got {current.get('checkpoint_id')}")
        submission = current.get("submission") or {}
        workflow_id = str(submission.get("workflow_id") or "")
        if workflow_id:
            return current, workflow_id
        if current.get("analysis_status") in {"submission_failed", "submission_unavailable"}:
            raise VerifyError(f"{expected} submission failed: {submission}")
        time.sleep(1)
    raise VerifyError(f"{expected} did not publish a workflow_id")


def poll_workflow(workflow_id: str, timeout: float) -> dict[str, Any]:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        # Poll the small status-only endpoint. The full projection embeds a
        # multi-megabyte Commander checkpoint and is fetched once after the
        # workflow reaches a terminal state.
        status, payload = request_json(f"{GATEWAY}/gateway/v1/workflows/{workflow_id}/brief")
        require(status == 200, f"workflow {workflow_id} returned HTTP {status}: {payload}")
        workflow_status = str(payload.get("status") or "").lower()
        if workflow_status in TERMINAL:
            errors = [
                str(row.get("error")) for row in reversed(payload.get("trace") or [])
                if isinstance(row, dict) and row.get("error")
                and row.get("event_type") in {"agent_call_failed", "agent_failure_recorded", "agent_failover_reassigning"}
            ]
            detail = errors[0] if errors else str(payload.get("last_error") or "no error detail")
            require(workflow_status == "completed", f"workflow {workflow_id} ended {workflow_status}: {detail}")
            return payload
        time.sleep(2)
    raise VerifyError(f"workflow {workflow_id} timed out after {timeout}s")


def dictionaries(value: Any):
    if isinstance(value, dict):
        yield value
        for nested in value.values():
            yield from dictionaries(nested)
    elif isinstance(value, list):
        for nested in value:
            yield from dictionaries(nested)


def verify_llm(workflow_id: str, projection: dict[str, Any], *, agent: str) -> dict[str, Any]:
    result = projection.get("result") or {}
    activities = [
        row for row in result.get("activity_results") or []
        if isinstance(row, dict) and row.get("role") == agent and row.get("status") == "completed"
    ]
    require(bool(activities), f"{workflow_id}: no completed {agent} activity")
    calls = [
        row for activity in activities for row in activity.get("llm_calls") or []
        if isinstance(row, dict)
        if row.get("llm_call_id") and row.get("agent") == agent
        and row.get("workflow_id") == workflow_id
        and row.get("status") == "success"
        and row.get("provider") == "openai_compatible"
        and row.get("model") == "qwen3:1.7b"
        and row.get("response_model") == "qwen3:1.7b"
        and not row.get("fallback_reason")
    ]
    require(bool(calls), f"{workflow_id}: missing successful business Qwen call for {agent}")
    plans = [
        row for activity in activities for row in dictionaries(activity.get("output") or {})
        if row.get("mode") == "llm"
        and isinstance(row.get("raw_llm_plan"), dict)
        and isinstance(row.get("algorithm_calls"), list)
        and not row.get("fallback_reason")
        and isinstance(row.get("llm_call"), dict)
        and row["llm_call"].get("llm_call_id") in {item["llm_call_id"] for item in calls}
    ]
    require(bool(plans), f"{workflow_id}: no validated raw LLM plan matches the {agent} call")
    return {"agent": agent, "llm_call_ids": sorted({item["llm_call_id"] for item in calls})}


def verify_workflow(checkpoint_id: str, workflow_id: str, projection: dict[str, Any], expected_activities: int) -> dict[str, Any]:
    status, work_payload = request_json(
        f"{GATEWAY}/gateway/v1/workflows/{workflow_id}/work-list", timeout=180
    )
    require(status == 200, f"{workflow_id}: work list HTTP {status}")
    work_list = work_payload.get("work_list")
    require(isinstance(work_list, list) and len(work_list) == expected_activities,
            f"{workflow_id}: expected {expected_activities} activities, got {len(work_list) if isinstance(work_list, list) else 'missing'}")
    require(all(str(item.get("status") or "").lower() == "completed" for item in work_list),
            f"{workflow_id}: incomplete A2A activity")
    view_status, view_payload = request_json(
        f"{AMOS}/api/v1/a2a/workflows/{workflow_id}/view", timeout=180
    )
    require(view_status == 200, f"{workflow_id}: AMOS workflow view HTTP {view_status}")
    view = data_of(view_payload)
    verified = int(((view.get("algorithms") or {}).get("counts") or {}).get("verified") or 0)
    require(verified > 0, f"{workflow_id}: no verified algorithm evidence")
    llm_evidence = None
    if checkpoint_id == "CJR-CP-CUE":
        llm_evidence = verify_llm(workflow_id, projection, agent="tactical_intelligence")
    elif checkpoint_id == "CJR-CP-PLAN":
        llm_evidence = verify_llm(workflow_id, projection, agent="task_scheduling")
    bad = [
        row for row in dictionaries(projection)
        if row.get("workflow_id") == workflow_id and row.get("llm_call_id")
        and (row.get("status") != "success" or row.get("fallback_reason"))
    ]
    require(not bad, f"{workflow_id}: failed or fallback LLM business call: {bad[:2]}")
    return {
        "checkpoint": checkpoint_id,
        "workflow_id": workflow_id,
        "status": "completed",
        "activities": len(work_list),
        "verified_algorithms": verified,
        "llm_evidence": llm_evidence,
    }


def eligible_track() -> str:
    status, payload = request_json(f"{AMOS}/api/v1/sim/state")
    require(status == 200, "simulation state unavailable")
    tracks = data_of(payload).get("fused_tracks") or []
    eligible = [row for row in tracks if isinstance(row, dict) and row.get("engagement_eligible") is True]
    require(bool(eligible), "ENGAGE gate has no eligible target after PLAN projection")
    return str(eligible[0].get("id") or eligible[0].get("track_id") or "")


def authorize_engagement() -> dict[str, Any]:
    action("start_auto")
    deadline = time.monotonic() + 90
    while time.monotonic() < deadline:
        director = state()
        if director.get("director_status") == "awaiting_authorization":
            break
        require(director.get("director_status") != "error", f"director gate error: {director.get('last_error')}")
        time.sleep(1)
    else:
        raise VerifyError("director did not enter explicit fire authorization gate")
    require(director.get("authorization_stage") == "fire", "expected FIRE authorization stage")
    track_id = eligible_track()
    denied_status, _ = request_json(f"{AMOS}/api/v1/sim/commands", {
        "command_type": "fire",
        "params": {"track_id": track_id, "asset_id": "SEA-C2-01", "weapon_name": "舰载对陆巡航导弹"},
        "authorization": {"approved": False},
    })
    require(denied_status == 409, f"unapproved fire returned HTTP {denied_status}, expected 409")
    approved_status, payload = request_json(f"{AMOS}/api/v1/sim/commands", {
        "command_type": "fire",
        "params": {"track_id": track_id, "asset_id": "SEA-C2-01", "weapon_name": "舰载对陆巡航导弹"},
        "authorization": {"approved": True},
    })
    require(approved_status == 200, f"authorized fire failed: HTTP {approved_status}: {payload}")
    result = data_of(payload).get("result") or {}
    require(bool(result.get("weapon_id")), "authorized fire returned no weapon_id")
    action("stop_auto")
    return {"track_id": track_id, "weapon_id": result["weapon_id"], "denied_http": denied_status}


def run(workflow_timeout: float) -> dict[str, Any]:
    for name, url in {
        "AMOS": f"{AMOS}/api/v1/status",
        "Gateway": f"{GATEWAY}/gateway/v1/health",
        "AlgoLib UI": f"{AMOS}/algolib/",
        "AlgoLib API": f"{AMOS}/algolib-api/health",
    }.items():
        try:
            with urlopen(url, timeout=20) as response:
                require(response.status == 200, f"{name} returned HTTP {response.status}")
        except URLError as exc:
            raise VerifyError(f"{name} unavailable: {exc}") from exc
    status, payload = request_json(f"{AMOS}/api/v1/director/configure", {
        "scenario_id": "coastal-joint-recon-strike", "mode": "demonstration", "branch": "standard",
    })
    require(status == 200, f"scenario configuration failed: {payload}")
    run_id = str(data_of(payload).get("run_id") or "")
    require(run_id.startswith("run-"), "director returned no run_id")
    records = []
    authorization = None
    for index, checkpoint_id in enumerate(CHECKPOINTS):
        advanced = action("advance_checkpoint")
        require((advanced.get("current_checkpoint") or {}).get("checkpoint_id") == checkpoint_id,
                f"unexpected checkpoint after advance: {advanced.get('current_checkpoint')}")
        checkpoint, workflow_id = current_checkpoint(checkpoint_id)
        print(f"[{checkpoint_id}] workflow {workflow_id} submitted", flush=True)
        poll_workflow(workflow_id, workflow_timeout)
        projection_deadline = time.monotonic() + 120
        while True:
            refreshed = action("refresh_analysis")
            checkpoint_status = (refreshed.get("current_checkpoint") or {}).get("analysis_status")
            if checkpoint_status == "completed":
                break
            require(checkpoint_status not in {"failed", "error", "projection_failed"},
                    f"{checkpoint_id}: AMOS projection failed: {checkpoint_status}")
            require(time.monotonic() < projection_deadline,
                    f"{checkpoint_id}: analysis did not project into AMOS within 120s ({checkpoint_status})")
            time.sleep(2)
        projection_status, projection = request_json(
            f"{GATEWAY}/gateway/v1/workflows/{workflow_id}", timeout=300
        )
        require(
            projection_status == 200
            and str(projection.get("status") or "").lower() == "completed",
            f"{checkpoint_id}: completed Commander projection unavailable (HTTP {projection_status})",
        )
        record = verify_workflow(checkpoint_id, workflow_id, projection, ACTIVITIES[index])
        records.append(record)
        print(f"[{checkpoint_id}] completed ({record['activities']} activities)", flush=True)
        if checkpoint_id == "CJR-CP-ENGAGE":
            authorization = authorize_engagement()
        if checkpoint_id == "CJR-CP-CLOSE":
            reviewed = action("review_checkpoint")
            require(bool((reviewed.get("current_checkpoint") or {}).get("reviewed_at")),
                    "operator review was not recorded")
    final = action("advance_checkpoint")
    require(final.get("director_status") == "completed", "director did not complete after review")
    require([item.get("checkpoint_id") for item in final.get("reached_checkpoints") or []] == list(CHECKPOINTS),
            "six checkpoints were not reached in order")
    require(any(item.get("action") == "review_checkpoint" for item in final.get("action_log") or []),
            "operator review is absent from action log")
    return {"run_id": run_id, "scenario_id": "coastal-joint-recon-strike", "workflows": records,
            "authorization": authorization, "reviewed": True, "result": "completed"}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workflow-timeout", type=float, default=900)
    args = parser.parse_args()
    try:
        started_at = time.monotonic()
        result = run(args.workflow_timeout)
        result["elapsed_seconds"] = round(time.monotonic() - started_at, 2)
        print(json.dumps(result, ensure_ascii=False, indent=2))
    except VerifyError as exc:
        print(f"VERIFY FAILED: {exc}", flush=True)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
