"""
URL 输入 → 三技能完整流水线 → 情报包 JSON（+ 可选标注图产物）。

用法（项目根目录）:

  # 本地图片（开发联调，模拟 URL，不走 base64 直传）
  $env:PYTHONPATH="."
  $env:TIA_CONFIG="config/default.yaml"
  $env:TIA_ALLOW_LOCAL_FILE="1"
  python scripts/run_url_intelligence_pipeline.py --local-image datasets/battlefield/images/val/P0002.png

  # 真实对象存储 URL（生产）
  python scripts/run_url_intelligence_pipeline.py ^
    --uri https://minio.example.local/a2a/recon/P0002.png ^
    --sensor-id EO-1

  # 多张图 + 侦察报告 + 传感器位姿
  python scripts/run_url_intelligence_pipeline.py ^
    --local-image datasets/battlefield/images/val/P0002.png ^
    --local-image datasets/battlefield/images/val/P0041.png ^
    --recon-report "Sector_A: armor column observed" ^
    --platform-lat 30.512 --platform-lon 114.381 --altitude-m 3200
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from agent.pipeline import create_agent, export_packet, load_config, skill_alignment_report
from tactical_intelligence_agent.payload_adapter import commander_payload_to_batch
from workflow_payloads import build_attachment_ref


def _local_uri(path: Path) -> str:
    resolved = path.resolve()
    return "local:///" + str(resolved).replace("\\", "/")


def _checksum_file(path: Path) -> dict[str, str]:
    digest = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            digest.update(chunk)
    return {"algorithm": "sha256", "value": digest.hexdigest()}


def _build_payload(args: argparse.Namespace) -> dict:
    attachments: list[dict] = []
    for idx, item in enumerate(args.uri or []):
        attachments.append(
            build_attachment_ref(
                item,
                kind="image",
                mime_type="image/png",
                attachment_id=f"att-{idx:03d}",
                meta={
                    "sensor_id": args.sensor_id or f"EO-{idx + 1}",
                    "modality": "eo_ir",
                    "platform_lat": args.platform_lat,
                    "platform_lon": args.platform_lon,
                    "altitude_m": args.altitude_m,
                    "heading_deg": args.heading_deg,
                    "depression_angle_deg": args.depression_deg,
                    "fov_deg": args.fov_deg,
                },
            )
        )

    for idx, item in enumerate(args.local_image or []):
        path = Path(item)
        if not path.is_absolute():
            path = ROOT / path
        if not path.is_file():
            raise FileNotFoundError(f"local image not found: {path}")
        att_idx = len(attachments)
        attachments.append(
            build_attachment_ref(
                _local_uri(path),
                checksum=_checksum_file(path),
                kind="image",
                mime_type="image/png",
                name=path.name,
                attachment_id=f"local-{att_idx:03d}",
                meta={
                    "sensor_id": args.sensor_id or f"EO-{att_idx + 1}",
                    "modality": "eo_ir",
                    "platform_lat": args.platform_lat,
                    "platform_lon": args.platform_lon,
                    "altitude_m": args.altitude_m,
                    "heading_deg": args.heading_deg,
                    "depression_angle_deg": args.depression_deg,
                    "fov_deg": args.fov_deg,
                    "resolution": "640x640",
                },
            )
        )

    if not attachments:
        raise ValueError("至少指定 --uri 或 --local-image")

    context: dict = {
        "jamming_level": args.jamming,
        "subscriber_agents": ["commander", "artillery"],
        "ground_elevation_m": args.ground_elevation_m,
    }
    if args.output_prefix:
        context["output_storage_prefix"] = args.output_prefix

    sensor_telemetry = {
        k: v
        for k, v in {
            "platform_lat": args.platform_lat,
            "platform_lon": args.platform_lon,
            "altitude_m": args.altitude_m,
            "heading_deg": args.heading_deg,
            "depression_angle_deg": args.depression_deg,
            "fov_deg": args.fov_deg,
        }.items()
        if v is not None
    }
    if sensor_telemetry:
        context["sensor_telemetry"] = sensor_telemetry

    payload = {
        "workflow_id": args.mission_id,
        "work_item": f"{args.mission_id}:url-pipeline",
        "command": "process_intelligence",
        "attachments": attachments,
        "context": context,
    }
    if args.recon_report:
        payload["input"] = {"recon_report": args.recon_report}
    return payload


def main() -> int:
    parser = argparse.ArgumentParser(description="URL 附件 → 战术情报三技能流水线")
    parser.add_argument("--uri", action="append", help="对象存储 https URL，可重复")
    parser.add_argument("--local-image", action="append", help="本地图片路径（需 TIA_ALLOW_LOCAL_FILE=1）")
    parser.add_argument("--mission-id", default="WF-URL-DEMO")
    parser.add_argument("--sensor-id", default=None)
    parser.add_argument("--recon-report", default=None)
    parser.add_argument("--platform-lat", type=float, default=30.512)
    parser.add_argument("--platform-lon", type=float, default=114.381)
    parser.add_argument("--altitude-m", type=float, default=3200.0)
    parser.add_argument("--heading-deg", type=float, default=0.0)
    parser.add_argument("--depression-deg", type=float, default=75.0)
    parser.add_argument("--fov-deg", type=float, default=45.0)
    parser.add_argument("--ground-elevation-m", type=float, default=120.0)
    parser.add_argument("--jamming", type=float, default=0.1)
    parser.add_argument("--output-prefix", default=None, help="产物上传 URI 前缀")
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=ROOT / "data" / "output" / "url_pipeline",
    )
    args = parser.parse_args()

    if args.local_image and os.environ.get("TIA_ALLOW_LOCAL_FILE", "0") != "1":
        print("[WARN] 使用 --local-image 建议设置 TIA_ALLOW_LOCAL_FILE=1")

    payload = _build_payload(args)
    batch = commander_payload_to_batch(payload, allow_mock_fallback=False)

    print("==> 技能对齐报告")
    print(json.dumps(skill_alignment_report(batch), ensure_ascii=False, indent=2))

    cfg = load_config()
    agent = create_agent(cfg)
    print(f"\n==> 开始流水线 (mock={cfg.get('use_mock')})")
    packet = agent.process(batch)
    print(f"    预拉取图像: {batch.context.get('images_prefetched', 0)} 张")

    out_dir = args.output_dir if args.output_dir.is_absolute() else ROOT / args.output_dir
    out_path = export_packet(packet, out_dir, mission_subdir=True)

    summary = {
        "packet_id": packet.packet_id,
        "target_count": len(packet.targets),
        "summary": packet.summary,
        "output_attachments": packet.output_attachments,
        "perception_trace": packet.provenance.get("perception"),
        "exported_json": str(out_path),
    }
    print("\n==> 完成")
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
