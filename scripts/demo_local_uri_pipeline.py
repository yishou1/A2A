"""无 MinIO 本地 URI 联调：local:// 输入 → 三技能流水线 → 本地标注图。"""

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

from tactical_intelligence_agent.bootstrap import create_engine
from tactical_intelligence_agent.payload_adapter import commander_payload_to_batch
from workflow_payloads import build_attachment_ref


def _export_packet(packet, output_dir: Path) -> Path:
    """将情报包写入 JSON（{mission_id}/{packet_id}.json + latest.json）。"""
    base = output_dir / packet.mission_id.replace("/", "_")
    base.mkdir(parents=True, exist_ok=True)
    body = json.dumps(packet.model_dump(mode="json"), ensure_ascii=False, indent=2)
    out_path = base / f"{packet.packet_id}.json"
    out_path.write_text(body, encoding="utf-8")
    (base / "latest.json").write_text(body, encoding="utf-8")
    return out_path


def _local_uri(path: Path) -> str:
    normalized = str(path.resolve()).replace("\\", "/")
    return f"local:///{normalized}"


def _checksum_file(path: Path) -> dict[str, str]:
    digest = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            digest.update(chunk)
    return {"algorithm": "sha256", "value": digest.hexdigest()}


def _guess_mime(path: Path) -> str:
    ext = path.suffix.lower()
    if ext == ".png":
        return "image/png"
    if ext in {".jpg", ".jpeg"}:
        return "image/jpeg"
    if ext == ".webp":
        return "image/webp"
    return "application/octet-stream"


def _resolve_image(path_arg: str | None) -> Path:
    candidates: list[Path] = []
    if path_arg:
        candidates.append(Path(path_arg))
    else:
        candidates.extend(
            [
                ROOT / "datasets/battlefield/images/val/P0002.png",
                ROOT / "datasets/battlefield/images/val/P0041.png",
                ROOT / "runs/detect/battlefield_rtdetr/val_batch0_pred.jpg",
                ROOT / "runs/detect/battlefield_rtdetr/val_batch0_labels.jpg",
            ]
        )

    for candidate in candidates:
        path = candidate if candidate.is_absolute() else ROOT / candidate
        if path.is_file():
            return path.resolve()
    raise FileNotFoundError(
        "Image not found. Pass --image, e.g. runs/detect/battlefield_rtdetr/val_batch0_pred.jpg"
    )


def _build_payload(args: argparse.Namespace, image_path: Path) -> dict:
    output_prefix = args.output_prefix or _local_uri(ROOT / "data/output/processed")
    attachment = build_attachment_ref(
        _local_uri(image_path),
        checksum=_checksum_file(image_path),
        kind="image",
        mime_type=_guess_mime(image_path),
        name=image_path.name,
        attachment_id="att-local-001",
        meta={
            "sensor_id": "EO-1",
            "modality": "eo_ir",
            "platform_lat": args.platform_lat,
            "platform_lon": args.platform_lon,
            "altitude_m": args.altitude_m,
            "heading_deg": args.heading_deg,
            "fov_deg": args.fov_deg,
        },
    )
    return {
        "workflow_id": args.mission_id,
        "work_item": f"{args.mission_id}:local-uri-demo",
        "command": "process_intelligence",
        "output_hint": "intelligence_packet",
        "attachments": [attachment],
        "input": {
            "recon_report": "Local URI demo without MinIO.",
            "sector": "Sector_A",
        },
        "context": {
            "jamming_level": args.jamming,
            "subscriber_agents": ["commander", "artillery"],
            "output_storage_prefix": output_prefix,
            "ground_elevation_m": args.ground_elevation_m,
            "sensor_telemetry": {
                "platform_lat": args.platform_lat,
                "platform_lon": args.platform_lon,
                "altitude_m": args.altitude_m,
            },
        },
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Local URI demo without MinIO")
    parser.add_argument("--image", default=None, help="Local image path")
    parser.add_argument("--mission-id", default="wf-local-uri-demo")
    parser.add_argument("--platform-lat", type=float, default=30.512)
    parser.add_argument("--platform-lon", type=float, default=114.381)
    parser.add_argument("--altitude-m", type=float, default=3200.0)
    parser.add_argument("--heading-deg", type=float, default=0.0)
    parser.add_argument("--fov-deg", type=float, default=45.0)
    parser.add_argument("--ground-elevation-m", type=float, default=120.0)
    parser.add_argument("--jamming", type=float, default=0.1)
    parser.add_argument("--output-prefix", default=None)
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=ROOT / "data/output/processed",
        help="情报包 JSON 落盘目录（按 mission_id 分子目录）",
    )
    parser.add_argument(
        "--config",
        type=Path,
        default=ROOT / "config/demo_local_uri.yaml",
        help="TIA 配置文件（默认 mock 联调，无需 torch）",
    )
    parser.add_argument(
        "--save-payload",
        type=Path,
        default=ROOT / "data/output/demo_local_uri_payload.json",
    )
    args = parser.parse_args()

    os.environ.setdefault("TIA_CONFIG", str(args.config if args.config.is_absolute() else ROOT / args.config))
    os.environ.setdefault("TIA_ALLOW_LOCAL_FILE", "1")
    os.environ.setdefault("TIA_ARTIFACT_ENABLED", "1")
    os.environ.setdefault("TIA_SKIP_WARMUP", "1")

    image_path = _resolve_image(args.image)
    payload = _build_payload(args, image_path)
    batch = commander_payload_to_batch(payload, allow_mock_fallback=False)

    print("Input image :", image_path)
    print("Input URI   :", payload["attachments"][0]["uri"])
    print("SHA256      :", payload["attachments"][0]["checksum"]["value"])
    print("Output prefix:", payload["context"]["output_storage_prefix"])

    engine = create_engine()
    packet = engine.process(batch)

    out_dir = args.output_dir if args.output_dir.is_absolute() else ROOT / args.output_dir
    packet_path = _export_packet(packet, out_dir)

    summary = {
        "packet_id": packet.packet_id,
        "target_count": len(packet.targets),
        "summary": packet.summary,
        "output_attachments": packet.output_attachments,
        "images_prefetched": batch.context.get("images_prefetched"),
        "exported_json": str(packet_path),
        "latest_json": str(packet_path.parent / "latest.json"),
    }
    print("\n==> Done")
    print(json.dumps(summary, ensure_ascii=False, indent=2))

    args.save_payload.parent.mkdir(parents=True, exist_ok=True)
    args.save_payload.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\nSaved payload JSON: {args.save_payload}")

    staging = ROOT / "data/output/artifacts" / args.mission_id.replace("/", "_")
    if staging.is_dir():
        print("\nAnnotated images:")
        for item in sorted(staging.iterdir()):
            print(" ", item)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
