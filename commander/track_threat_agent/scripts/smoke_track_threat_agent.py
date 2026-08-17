"""End-to-end smoke check for a running Track Threat Agent."""

from __future__ import annotations

import argparse
import json
import sys
from copy import deepcopy
from pathlib import Path
from typing import Any, Dict, List
from uuid import uuid4
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
    minimum_timestamp: float | None = None,
) -> Dict[str, Any]:
    sequence = generate_long_operation_sequence(frame_count=max(frame_index + 1, 90))
    frame = deepcopy(sequence["frames"][frame_index])
    resolved_work_item = work_item or f"track-threat-smoke-{frame_index:03d}-{uuid4().hex[:8]}"
    detections = frame.get("detections", [])
    if detections and minimum_timestamp is not None:
        earliest = min(float(item["timestamp"]) for item in detections)
        offset = max(0.0, float(minimum_timestamp) - earliest + 1.0)
        for detection in detections:
            detection["timestamp"] = float(detection["timestamp"]) + offset
    for detection in detections:
        detection["detection_id"] = f"{detection['detection_id']}-{resolved_work_item}"
    return {
        "workflow_id": workflow_id,
        "work_item": resolved_work_item,
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
        initial_diagnostics = checks["health"].get("tracking_diagnostics") or {}
        initial_watermark = initial_diagnostics.get("latest_detection_time")
        task_payload = build_smoke_task_payload(
            args.frame,
            args.workflow_id,
            args.work_item,
            minimum_timestamp=float(initial_watermark) if initial_watermark is not None else None,
        )
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
    expected_detection_ids = {
        str(item["detection_id"])
        for item in task_payload["payload"].get("detections", [])
    }
    updated_detection_ids = {
        str((track.get("metadata") or {}).get("last_detection_id"))
        for track in artifact.get("tracks", [])
    }
    if expected_detection_ids and not expected_detection_ids <= updated_detection_ids:
        errors.append("smoke detections did not advance all expected tracks")
    final_diagnostics = (artifact.get("summary") or {}).get("tracking_diagnostics") or {}
    if initial_watermark is not None and float(final_diagnostics.get("latest_detection_time") or 0.0) <= float(initial_watermark):
        errors.append("tracking timestamp watermark did not advance")
    if int(final_diagnostics.get("ignored_out_of_order_detection_count", 0)) != int(
        initial_diagnostics.get("ignored_out_of_order_detection_count", 0)
    ):
        errors.append("smoke frame was rejected as out of order")

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
