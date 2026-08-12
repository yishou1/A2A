"""两帧真实联调：验证 intelligence_packet.tracks + history_path 跨帧累积。

用法（PowerShell）:
  $env:TIA_ALLOW_LOCAL_FILE="1"
  $env:TIA_EXECUTION_MODE="in_process"   # 无算法库 HTTP 时用本地推理
  $env:TIA_SKIP_WARMUP="1"
  python scripts/smoke_tia_packet_tracks.py
"""

from __future__ import annotations

import hashlib
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

os.environ.setdefault("TIA_ALLOW_LOCAL_FILE", "1")
os.environ.setdefault("TIA_SKIP_WARMUP", "1")
# 默认走本地推理，避免联调时依赖未启动的 902x 算法服务
os.environ.setdefault("TIA_EXECUTION_MODE", "in_process")
# auto 在部分机器会落到 small/rtdetr-n.pt；联调默认 medium 用 battlefield_rtdetr.pt
os.environ.setdefault("TIA_COMPUTE_PROFILE", "medium")

from agent.orchestrator import TacticalIntelligenceAgent  # noqa: E402
from agent.pipeline import agent_config_from_yaml, load_config  # noqa: E402
from tactical_intelligence_agent.downstream_adapter import (  # noqa: E402
    to_trajectory_predictor_input,
)
from tactical_intelligence_agent.payload_adapter import commander_payload_to_batch  # noqa: E402
from workflow_payloads import build_attachment_ref  # noqa: E402

EXPORT = ROOT / "examples" / "ue_naval_scenario" / "export" / "OP-IRON-SEA-001"
OUT = ROOT / "data" / "output" / "smoke_tia_packet"


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    h.update(path.read_bytes())
    return h.hexdigest()


def _local_uri(path: Path) -> str:
    # Windows: local:///D:/...
    return "local:///" + str(path.resolve()).replace("\\", "/")


def _payload(frame_index: int, image: Path, work_item: str) -> dict:
    return {
        "schema_version": "1.0",
        "workflow_id": "wf-smoke-tia-packet",
        "work_item": work_item,
        "command": "process_intelligence",
        "required_skill": "tactical_intelligence_analysis",
        "output_hint": "intelligence_packet",
        "attachments": [
            build_attachment_ref(
                _local_uri(image),
                sha256=_sha256(image),
                kind="image",
                attachment_id=f"att-scene-{frame_index:03d}",
                meta={
                    "sensor_id": "EO-UAV-01",
                    "modality": "eo_ir",
                    "platform_lat": 30.521,
                    "platform_lon": 114.392,
                    "altitude_m": 3200.0,
                    "heading_deg": 45.0,
                    "depression_angle_deg": 75.0,
                    "fov_deg": 45.0,
                    "frame_index": frame_index,
                },
            )
        ],
        "input": {
            "agent_request": {
                "recon_report": f"UE naval smoke frame {frame_index}",
                "sector": "Sector_A",
                "coordinates": {"lat": 30.52, "lon": 114.39},
            }
        },
        "context": {
            "jamming_level": 0.0,
            "subscriber_agents": ["commander", "trajectory_predictor", "artillery"],
            "ground_elevation_m": 0.0,
            "sea_surface_elevation_m": 0.0,
            "sensor_telemetry": {
                "platform_lat": 30.521,
                "platform_lon": 114.392,
                "altitude_m": 3200.0,
                "heading_deg": 45.0,
            },
        },
    }


def main() -> int:
    images = [
        EXPORT / "battlefield" / "scene_000.png",
        EXPORT / "battlefield" / "scene_001.png",
    ]
    for img in images:
        if not img.is_file():
            print(f"[FAIL] missing image: {img}")
            return 1

    cfg = load_config()
    # 真实演练：强制非 mock
    cfg["use_mock"] = False
    engine = TacticalIntelligenceAgent(
        use_mock=False,
        config=agent_config_from_yaml(cfg),
    )

    OUT.mkdir(parents=True, exist_ok=True)
    packets = []
    for i, img in enumerate(images):
        payload = _payload(i, img, f"wf-smoke-tia-packet:frame-{i:03d}")
        batch = commander_payload_to_batch(payload)
        packet = engine.process(batch)
        dump = packet.model_dump(mode="json")
        packets.append(dump)
        out_path = OUT / f"packet_frame_{i:03d}.json"
        out_path.write_text(json.dumps(dump, ensure_ascii=False, indent=2), encoding="utf-8")
        print(
            f"[frame {i}] tracks={len(packet.tracks)} targets={len(packet.targets)} "
            f"schema={packet.schema_version} -> {out_path}"
        )
        for t in packet.tracks[:5]:
            hist = t.get("history_path") or []
            print(
                f"  - {t.get('track_id')} type={t.get('object_type')} "
                f"lat={t.get('lat')} lon={t.get('lon')} hist_len={len(hist)}"
            )

    traj = to_trajectory_predictor_input(packets[-1])
    traj_path = OUT / "trajectory_request.json"
    traj_path.write_text(json.dumps(traj, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"[downstream] trajectory_predictor input -> {traj_path}")

    # 跨帧：同 track_id 的 history 应增长
    if packets[0]["tracks"] and packets[1]["tracks"]:
        tid = packets[1]["tracks"][0].get("track_id")
        h0 = next((t for t in packets[0]["tracks"] if t.get("track_id") == tid), None)
        h1 = next((t for t in packets[1]["tracks"] if t.get("track_id") == tid), None)
        if h0 and h1:
            n0, n1 = len(h0.get("history_path") or []), len(h1.get("history_path") or [])
            print(f"[check] track {tid} history_path: frame0={n0} -> frame1={n1}")
            if n1 > n0:
                print("[OK] history_path grew across frames")
            else:
                print("[WARN] history_path did not grow (association may have rematched IDs)")
        else:
            print("[WARN] no shared track_id across frames yet (first-frame association)")
    else:
        print("[WARN] empty tracks on one or both frames — check detector weights / image content")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
