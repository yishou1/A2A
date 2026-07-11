"""End-to-end smoke check for a running Track Threat Agent."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Dict, List
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

AGENT_DIR = Path(__file__).resolve().parents[1]
if str(AGENT_DIR) not in sys.path:
    sys.path.insert(0, str(AGENT_DIR))

from app.scenario_generator import generate_long_operation_sequence


def build_smoke_task_payload(
    frame_index: int = 0,
    workflow_id: str = "wf-track-threat-smoke",
    work_item: str | None = None,
) -> Dict[str, Any]:
    sequence = generate_long_operation_sequence(frame_count=max(frame_index + 1, 90))
    frame = sequence["frames"][frame_index]
    return {
        "workflow_id": workflow_id,
        "work_item": work_item or f"track-threat-smoke-{frame_index:03d}",
        "command": "analyze_perception_result",
        "role": "track_threat",
        "work_list": [
            {"activity": "perception_fusion", "role": "recon", "status": "completed"},
            {"activity": "track_threat_analysis", "role": "track_threat", "status": "ready"},
            {"activity": "situation_display", "role": "commander", "status": "waiting"},
        ],
        "payload": frame,
    }


def validate_artifact(artifact: Dict[str, Any]) -> List[str]:
    errors: List[str] = []
    if not artifact.get("tracks"):
        errors.append("artifact.tracks is empty")
    if not artifact.get("asset_impacts"):
        errors.append("artifact.asset_impacts is empty")
    ranking = artifact.get("unified_threat_ranking") or []
    if not ranking:
        errors.append("artifact.unified_threat_ranking is empty")
    elif not ranking[0].get("reason"):
        errors.append("top ranking item has no reason")
    summary = artifact.get("summary") or {}
    if int(summary.get("track_count", 0) or 0) <= 0:
        errors.append("summary.track_count is zero")
    if int(summary.get("asset_impact_count", 0) or 0) <= 0:
        errors.append("summary.asset_impact_count is zero")
    return errors


def main() -> int:
    parser = argparse.ArgumentParser(description="Run Track Threat Agent health/schema/A2A smoke checks.")
    parser.add_argument("--base-url", default="http://127.0.0.1:8102")
    parser.add_argument("--token", default="demo-token")
    parser.add_argument("--frame", type=int, default=0)
    parser.add_argument("--workflow-id", default="wf-track-threat-smoke")
    parser.add_argument("--work-item", default=None)
    args = parser.parse_args()

    base_url = args.base_url.rstrip("/")
    try:
        checks = {
            "health": _get_json(f"{base_url}/health"),
            "ready": _get_json(f"{base_url}/ready"),
            "agent_card": _get_json(f"{base_url}/.well-known/agent-card"),
            "input_schema": _get_json(f"{base_url}/schema/input"),
            "output_schema": _get_json(f"{base_url}/schema/output"),
        }
        task_payload = build_smoke_task_payload(args.frame, args.workflow_id, args.work_item)
        body = _post_json(f"{base_url}/sendMessage", task_payload, args.token)
    except (HTTPError, URLError) as exc:
        print(f"smoke request failed: {exc}", file=sys.stderr)
        return 1

    artifact = body.get("artifact") or body.get("output", {}).get("artifact", {})
    errors = []
    if checks["health"].get("status") != "ok":
        errors.append("health.status is not ok")
    if not checks["ready"].get("ready", False):
        errors.append("ready.ready is false")
    if "track_threat" != checks["agent_card"].get("role"):
        errors.append("agent card role is not track_threat")
    if "protected_assets" not in checks["input_schema"].get("scene_fields", []):
        errors.append("input schema does not document protected_assets")
    if "asset_impacts" not in checks["output_schema"].get("artifact_fields", []):
        errors.append("output schema does not document asset_impacts")
    errors.extend(validate_artifact(artifact))

    report = {
        "status": "passed" if not errors else "failed",
        "base_url": base_url,
        "errors": errors,
        "health": {
            "ready": checks["health"].get("ready"),
            "active_track_count": checks["health"].get("active_track_count"),
            "active_group_count": checks["health"].get("active_group_count"),
            "nacos": checks["health"].get("nacos"),
        },
        "task": {
            "workflow_id": body.get("workflow_id"),
            "work_item": body.get("work_item"),
            "status": body.get("status"),
            "cached": body.get("cached"),
        },
        "artifact_summary": artifact.get("summary", {}),
        "ranking_top_3": (artifact.get("unified_threat_ranking") or [])[:3],
        "safety_boundary": "Smoke test verifies situation-awareness outputs only; no weapon control or engagement advice.",
    }
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if not errors else 2


def _get_json(url: str) -> Dict[str, Any]:
    with urlopen(url, timeout=10) as response:
        return json.loads(response.read().decode("utf-8"))


def _post_json(url: str, payload: Dict[str, Any], token: str) -> Dict[str, Any]:
    request = Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json", "Authorization": f"Bearer {token}"},
        method="POST",
    )
    with urlopen(request, timeout=30) as response:
        return json.loads(response.read().decode("utf-8"))


if __name__ == "__main__":
    raise SystemExit(main())
