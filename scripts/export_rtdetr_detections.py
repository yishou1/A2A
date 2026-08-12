"""
批量图片 RT-DETR 检测，保存标注图 + JSON，便于传给下游平台。

用法（项目根目录）:
  # 处理单张或多张图片
  python scripts/export_rtdetr_detections.py --input path/to/img.jpg
  python scripts/export_rtdetr_detections.py --input path/to/folder

  # 使用验证集样例
  python scripts/export_rtdetr_detections.py --input datasets/battlefield/images/val --limit 5

输出目录（默认）:
  data/output/rtdetr_exports/<时间戳>/
    annotated/          # 画好检测框的图
    detections/         # 每张图一个 .json
    manifest.json       # 批次清单（给下一平台）
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

import cv2
import numpy as np

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from agent.pipeline import load_config

_IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".bmp", ".tif", ".tiff", ".webp"}


def _collect_images(path: Path, *, limit: int | None) -> list[Path]:
    if path.is_file():
        return [path] if path.suffix.lower() in _IMAGE_EXTS else []
    files = sorted(
        p for p in path.rglob("*") if p.is_file() and p.suffix.lower() in _IMAGE_EXTS
    )
    if limit is not None and limit > 0:
        files = files[:limit]
    return files


def _detections_from_result(result, *, source_id: str) -> list[dict]:
    names = result.names or {}
    dets: list[dict] = []
    if result.boxes is None or len(result.boxes) == 0:
        return dets
    for box in result.boxes:
        xyxy = box.xyxy[0].tolist()
        cls_id = int(box.cls[0])
        conf = float(box.conf[0])
        dets.append(
            {
                "class_id": cls_id,
                "class_name": names.get(cls_id, str(cls_id)),
                "confidence": round(conf, 4),
                "bbox_xyxy": [round(x, 2) for x in xyxy],
            }
        )
    return dets


def export_batch(
    *,
    images: list[Path],
    output_dir: Path,
    weights: Path,
    conf: float,
    device: str | int,
    imgsz: int,
) -> dict:
    from ultralytics import RTDETR

    annotated_dir = output_dir / "annotated"
    detections_dir = output_dir / "detections"
    annotated_dir.mkdir(parents=True, exist_ok=True)
    detections_dir.mkdir(parents=True, exist_ok=True)

    model = RTDETR(str(weights))
    manifest_items: list[dict] = []

    for idx, img_path in enumerate(images, start=1):
        results = model.predict(
            source=str(img_path),
            conf=conf,
            imgsz=imgsz,
            device=device,
            verbose=False,
        )
        if not results:
            continue
        r0 = results[0]
        dets = _detections_from_result(r0, source_id=img_path.stem)

        stem = img_path.stem
        annotated_name = f"{stem}_det.jpg"
        annotated_path = annotated_dir / annotated_name
        plotted = r0.plot()
        cv2.imwrite(str(annotated_path), cv2.cvtColor(plotted, cv2.COLOR_RGB2BGR))

        det_json = {
            "source_image": img_path.name,
            "source_path": str(img_path.resolve()),
            "annotated_image": annotated_name,
            "image_size": {"width": int(r0.orig_shape[1]), "height": int(r0.orig_shape[0])},
            "model": str(weights),
            "confidence_threshold": conf,
            "detection_count": len(dets),
            "detections": dets,
            "exported_at": datetime.now(timezone.utc).isoformat(),
        }
        det_path = detections_dir / f"{stem}.json"
        det_path.write_text(json.dumps(det_json, ensure_ascii=False, indent=2), encoding="utf-8")

        manifest_items.append(
            {
                "index": idx,
                "source_image": img_path.name,
                "annotated_image": str(annotated_path.relative_to(output_dir)),
                "detection_json": str(det_path.relative_to(output_dir)),
                "detection_count": len(dets),
                "classes": sorted({d["class_name"] for d in dets}),
            }
        )
        print(f"[{idx}/{len(images)}] {img_path.name} -> {len(dets)} detections")

    manifest = {
        "export_id": output_dir.name,
        "exported_at": datetime.now(timezone.utc).isoformat(),
        "model": str(weights),
        "confidence_threshold": conf,
        "image_count": len(manifest_items),
        "total_detections": sum(m["detection_count"] for m in manifest_items),
        "items": manifest_items,
        "schema_version": "1.0",
        "note": "annotated/ 为可视化图；detections/ 为结构化结果，可供下游平台读取",
    }
    manifest_path = output_dir / "manifest.json"
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    return manifest


def main() -> int:
    parser = argparse.ArgumentParser(description="RT-DETR batch export: annotated images + JSON")
    parser.add_argument("--input", type=Path, required=True, help="图片文件或文件夹")
    parser.add_argument(
        "--output",
        type=Path,
        default=None,
        help="输出目录，默认 data/output/rtdetr_exports/<时间戳>",
    )
    parser.add_argument("--weights", type=Path, default=None, help="覆盖 config 中的 detection_model")
    parser.add_argument("--conf", type=float, default=None, help="置信度阈值")
    parser.add_argument("--imgsz", type=int, default=640)
    parser.add_argument("--device", default="0")
    parser.add_argument("--limit", type=int, default=None, help="最多处理 N 张（调试）")
    args = parser.parse_args()

    cfg = load_config()
    inf = cfg.get("inference") or {}
    weights = args.weights or Path(inf.get("detection_model", "models/checkpoints/battlefield_rtdetr.pt"))
    if not weights.is_absolute():
        weights = ROOT / weights
    conf = args.conf if args.conf is not None else float(inf.get("confidence_threshold", 0.35))

    input_path = args.input if args.input.is_absolute() else ROOT / args.input
    if not input_path.exists():
        raise FileNotFoundError(f"输入不存在: {input_path}")
    if not weights.is_file():
        raise FileNotFoundError(f"权重不存在: {weights}")

    images = _collect_images(input_path, limit=args.limit)
    if not images:
        raise RuntimeError(f"未找到图片: {input_path}")

    ts = datetime.now().strftime("%Y%m%d-%H%M%S")
    output_dir = args.output or (ROOT / "data" / "output" / "rtdetr_exports" / ts)
    if not output_dir.is_absolute():
        output_dir = ROOT / output_dir
    output_dir.mkdir(parents=True, exist_ok=True)

    print(f"==> 输入: {input_path} ({len(images)} 张)")
    print(f"==> 权重: {weights}")
    print(f"==> 输出: {output_dir}")

    manifest = export_batch(
        images=images,
        output_dir=output_dir,
        weights=weights,
        conf=conf,
        device=args.device,
        imgsz=args.imgsz,
    )
    print(f"\n[DONE] 共 {manifest['image_count']} 张, {manifest['total_detections']} 个目标")
    print(f"[DONE] 标注图: {output_dir / 'annotated'}")
    print(f"[DONE] JSON:   {output_dir / 'detections'}")
    print(f"[DONE] 清单:   {output_dir / 'manifest.json'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
