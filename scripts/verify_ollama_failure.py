#!/usr/bin/env python3
"""Acceptance check: stopping Ollama during CUE must fail the A2A workflow."""

from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import time
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


ROOT = Path(__file__).resolve().parents[1]
AMOS = os.environ.get("AMOS_BASE_URL", "http://127.0.0.1:5000").rstrip("/")
GATEWAY = os.environ.get("A2A_GATEWAY_URL", "http://127.0.0.1:8030").rstrip("/")
TERMINAL = {"completed", "failed", "cancelled", "paused", "input_required"}


class VerifyError(RuntimeError):
    pass


def request_json(url: str, body: dict[str, Any] | None = None) -> tuple[int, dict[str, Any]]:
    data = None if body is None else json.dumps(body).encode("utf-8")
    request = Request(url, data=data, headers={"Content-Type": "application/json"})
    try:
        with urlopen(request, timeout=20) as response:
            return response.status, json.load(response)
    except HTTPError as exc:
        try:
            return exc.code, json.load(exc)
        except (ValueError, UnicodeError):
            return exc.code, {"error": str(exc)}
    except (URLError, TimeoutError) as exc:
        raise VerifyError(f"unreachable {url}: {exc}") from exc


def docker(*args: str) -> None:
    command = [
        "powershell", "-NoProfile", "-ExecutionPolicy", "Bypass",
        "-File", str(ROOT / "scripts" / "docker-compose.ps1"), *args,
    ]
    result = subprocess.run(command, cwd=ROOT, capture_output=True, text=True)
    if result.returncode:
        raise VerifyError(
            f"docker compose {' '.join(args)} failed ({result.returncode}):\n"
            f"{result.stdout}\n{result.stderr}"
        )


def get_data(payload: dict[str, Any]) -> dict[str, Any]:
    value = payload.get("data")
    return value if isinstance(value, dict) else {}


def main() -> int:
    try:
        status, payload = request_json(f"{AMOS}/api/v1/director/configure", {
            "scenario_id": "coastal-joint-recon-strike",
            "mode": "demonstration",
            "branch": "standard",
        })
        if status != 200:
            raise VerifyError(f"configure failed: HTTP {status}: {payload}")

        status, payload = request_json(
            f"{AMOS}/api/v1/director/action", {"action": "advance_checkpoint"}
        )
        if status != 200:
            raise VerifyError(f"advance CUE failed: HTTP {status}: {payload}")
        checkpoint = get_data(payload).get("current_checkpoint") or {}
        if checkpoint.get("checkpoint_id") != "CJR-CP-CUE":
            raise VerifyError(f"expected CUE checkpoint, received {checkpoint}")
        workflow_id = str((checkpoint.get("submission") or {}).get("workflow_id") or "")
        if not workflow_id:
            raise VerifyError("CUE did not submit an A2A workflow")
        print(f"[CUE] workflow {workflow_id} submitted", flush=True)

        active_deadline = time.monotonic() + 120
        active_rows: list[dict[str, Any]] = []
        while time.monotonic() < active_deadline:
            status, work_payload = request_json(
                f"{GATEWAY}/gateway/v1/workflows/{workflow_id}/work-list"
            )
            if status == 200:
                rows = work_payload.get("work_list") or []
                active_rows = [
                    row for row in rows
                    if isinstance(row, dict)
                    and str(row.get("status") or "").lower() == "running"
                    and "tactical_intelligence" in " ".join(
                        str(row.get(key) or "").lower()
                        for key in ("role", "agent", "agent_id", "target", "name", "work_item")
                    )
                ]
                if active_rows:
                    break
            brief_status, brief = request_json(
                f"{GATEWAY}/gateway/v1/workflows/{workflow_id}/brief"
            )
            if brief_status == 200 and str(brief.get("status") or "").lower() in TERMINAL:
                raise VerifyError(
                    f"workflow ended before Tactical Intelligence was running: {brief}"
                )
            time.sleep(0.5)
        else:
            raise VerifyError("Tactical Intelligence never entered running state")

        print("[CUE] Tactical Intelligence is running; stopping Ollama", flush=True)
        time.sleep(1)
        docker("stop", "ollama")

        workflow_status = "unknown"
        deadline = time.monotonic() + 180
        while time.monotonic() < deadline:
            status, brief = request_json(
                f"{GATEWAY}/gateway/v1/workflows/{workflow_id}/brief"
            )
            if status == 200:
                workflow_status = str(brief.get("status") or "unknown").lower()
                if workflow_status in TERMINAL:
                    break
            time.sleep(1)
        if workflow_status not in TERMINAL:
            raise VerifyError("workflow did not reach a terminal state after Ollama stopped")
        if workflow_status == "completed":
            raise VerifyError("workflow incorrectly completed after Ollama stopped")

        _, trace_payload = request_json(
            f"{GATEWAY}/gateway/v1/workflows/{workflow_id}/trace"
        )
        trace_rows = trace_payload.get("trace") or []
        failure_events = [
            row for row in trace_rows
            if isinstance(row, dict)
            and row.get("event_type") in {
                "agent_call_failed", "agent_failure_recorded", "agent_failover_reassigning",
            }
        ]
        if not failure_events:
            _, projection = request_json(f"{GATEWAY}/gateway/v1/workflows/{workflow_id}")
            failure_detail = projection.get("last_error")
            if not failure_detail:
                raise VerifyError(
                    f"workflow ended {workflow_status} without recorded failure evidence"
                )
        else:
            failure_detail = failure_events[-1].get("error") or failure_events[-1].get("event_type")

        print(json.dumps({
            "workflow_id": workflow_id,
            "status": workflow_status,
            "failure_recorded": True,
            "failure_detail": failure_detail,
            "qwen_success_claimed": False,
            "result": "passed",
        }, ensure_ascii=False, indent=2), flush=True)
        return 0
    except VerifyError as exc:
        print(f"VERIFY FAILED: {exc}", flush=True)
        return 1
    finally:
        try:
            docker("up", "-d", "ollama")
        except Exception as exc:
            print(f"WARNING: could not restart Ollama after test: {exc}", flush=True)


if __name__ == "__main__":
    raise SystemExit(main())
