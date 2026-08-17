"""Send a Track Threat A2A demo task to a running Agent."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

AGENT_DIR = Path(__file__).resolve().parents[1]
if str(AGENT_DIR) not in sys.path:
    sys.path.insert(0, str(AGENT_DIR))

from app.scenario_generator import generate_long_operation_sequence


def main() -> int:
    parser = argparse.ArgumentParser(description="Send a Track Threat Agent A2A task.")
    parser.add_argument("--base-url", default="http://127.0.0.1:8102")
    parser.add_argument("--frame", type=int, default=0, help="Frame index from the deterministic long scenario.")
    parser.add_argument("--token", default="demo-token")
    parser.add_argument("--workflow-id", default="wf-track-threat-demo")
    parser.add_argument("--work-item", default=None)
    args = parser.parse_args()

    sequence = generate_long_operation_sequence(frame_count=max(args.frame + 1, 90))
    frame = sequence["frames"][args.frame]
    work_item = args.work_item or f"track-threat-frame-{args.frame:03d}"
    task_payload = {
        "workflow_id": args.workflow_id,
        "work_item": work_item,
        "command": "analyze_perception_result",
        "role": "track_threat",
        "work_list": [
            {"activity": "perception_fusion", "role": "recon"},
            {"activity": "track_threat_analysis", "role": "track_threat"},
            {"activity": "situation_display", "role": "commander"},
        ],
        "payload": frame,
    }

    request = Request(
        f"{args.base_url.rstrip('/')}/sendMessage",
        data=json.dumps(task_payload).encode("utf-8"),
        headers={"Content-Type": "application/json", "Authorization": f"Bearer {args.token}"},
        method="POST",
    )
    try:
        with urlopen(request, timeout=20) as response:
            body = json.loads(response.read().decode("utf-8"))
    except HTTPError as exc:
        print(exc.read().decode("utf-8"), file=sys.stderr)
        return 1
    except URLError as exc:
        print(f"request failed: {exc}", file=sys.stderr)
        return 1

    artifact = body.get("artifact") or body.get("output", {}).get("artifact", {})
    summary = artifact.get("summary", {})
    print(json.dumps({
        "status": body.get("status"),
        "workflow_id": body.get("workflow_id"),
        "work_item": body.get("work_item"),
        "cached": body.get("cached"),
        "summary": summary,
        "ranking_top_3": artifact.get("unified_threat_ranking", [])[:3],
    }, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
