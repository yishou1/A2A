"""Run an in-process A2A demo proving the learned predictor reaches artifacts."""

from __future__ import annotations

import asyncio
import argparse
import json
import sys
from pathlib import Path

AGENT_DIR = Path(__file__).resolve().parents[1]
REPO_DIR = AGENT_DIR.parent
for path in (str(AGENT_DIR), str(REPO_DIR)):
    if path not in sys.path:
        sys.path.insert(0, path)

from app import main


async def run_demo() -> dict:
    await main.demo_reset()
    bodies = []
    for frame_index in range(4):
        timestamp = 1_782_400_000.0 + frame_index * 10.0
        payload = {
            "task_id": f"task-learned-online-{frame_index}",
            "message_type": "perception_result",
            "algorithm_level": "medium",
            "scene": {
                "protected_zone_lat": 31.2304,
                "protected_zone_lon": 121.4737,
                "protected_radius_m": 30_000,
                "protected_assets": [],
            },
            "detections": [
                {
                    "detection_id": f"learned-aircraft-{frame_index}",
                    "object_type": "aircraft",
                    "timestamp": timestamp,
                    "lat": 31.0 + frame_index * 0.011,
                    "lon": 121.0 + frame_index * 0.018,
                    "alt": 10_000.0,
                    "speed": 230.0,
                    "heading": 58.0,
                    "confidence": 0.96,
                    "source_agent": "opensky_replay",
                    "metadata": {"demo": "learned_predictor_online"},
                }
            ],
        }
        task_payload = {
            "workflow_id": "wf-learned-online-demo",
            "work_item": f"wi-learned-online-demo-{frame_index}",
            "command": "analyze_perception_result",
            "role": "track_threat",
            "payload": payload,
        }
        bodies.append(await main.send_message(task_payload, token="demo-token"))

    artifact = bodies[-1]["artifact"]
    track = artifact["tracks"][0]
    learned_points = [
        {
            "dt_s": point.get("dt_s"),
            "lat": point.get("lat"),
            "lon": point.get("lon"),
            "model_used": point.get("model_used"),
            "learned_model": point.get("learned_model"),
            "st_gnn_trained_model_loaded": point.get("st_gnn", {}).get("trained_model_loaded"),
        }
        for point in track.get("predicted_path", [])
        if point.get("model_used") == "learned_numpy_sequence_predictor"
    ]
    return {
        "status": bodies[-1]["status"],
        "workflow_id": bodies[-1]["workflow_id"],
        "frames_sent": len(bodies),
        "learned_predictor_status": main.learned_predictor.status(),
        "summary": artifact["summary"],
        "track_id": track["track_id"],
        "track_learned_predictor": track["metadata"].get("learned_predictor"),
        "learned_predicted_points": learned_points,
        "safety_boundary": "Trajectory prediction demo only; no weapon control or engagement advice.",
    }


def main_cli() -> int:
    parser = argparse.ArgumentParser(description="Run an in-process learned-predictor A2A artifact demo.")
    parser.add_argument("--output", type=Path, default=None, help="Optional JSON report path.")
    args = parser.parse_args()
    report = asyncio.run(run_demo())
    text = json.dumps(report, ensure_ascii=False, indent=2)
    print(text)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(text + "\n", encoding="utf-8")
    if not report["learned_predicted_points"]:
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main_cli())
