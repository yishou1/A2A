"""
在 DOTA 转换后的 YOLO 数据集上微调 RT-DETR。

用法（项目根目录，建议 GPU 服务器）:
  python scripts/train_battlefield_rtdetr.py
  python scripts/train_battlefield_rtdetr.py --data datasets/battlefield/data.yaml --epochs 80

训练完成后，在 config/default.yaml 修改:
  inference:
    detection_model: models/checkpoints/battlefield_rtdetr.pt
"""

from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
from pathlib import Path


def main() -> int:
    parser = argparse.ArgumentParser(description="Fine-tune RT-DETR on battlefield/DOTA dataset")
    parser.add_argument(
        "--data",
        type=Path,
        default=Path("datasets/battlefield/data.yaml"),
    )
    parser.add_argument(
        "--pretrained",
        type=Path,
        default=Path("rtdetr-l.pt"),
        help="预训练起点（默认 COCO rtdetr-l.pt，仅作骨干初始化）",
    )
    parser.add_argument("--epochs", type=int, default=100)
    parser.add_argument(
        "--imgsz",
        type=int,
        default=640,
        help="输入边长；RTX 4060 8GB 建议 640，勿用 1280",
    )
    parser.add_argument(
        "--batch",
        type=int,
        default=2,
        help="batch size；8GB 显存建议 2，仍 OOM 则改为 1",
    )
    parser.add_argument("--device", default="0", help="GPU id，CPU 用 cpu")
    parser.add_argument(
        "--workers",
        type=int,
        default=0 if sys.platform == "win32" else 4,
        help="DataLoader 进程数；Windows 建议 0，避免页面文件不足 (WinError 1455)",
    )
    parser.add_argument(
        "--resume",
        action="store_true",
        help="从 runs/detect/<name>/weights/last.pt 断点续训",
    )
    parser.add_argument("--name", default="battlefield_rtdetr")
    parser.add_argument(
        "--copy-to",
        type=Path,
        default=Path("models/checkpoints/battlefield_rtdetr.pt"),
        help="训练后将 best.pt 复制到此路径",
    )
    parser.add_argument(
        "--prepare",
        action="store_true",
        help="训练前将数据集图像统一缩放到 --imgsz（推荐首次训练开启）",
    )
    args = parser.parse_args()

    root = Path(__file__).resolve().parents[1]
    data_yaml = args.data if args.data.is_absolute() else root / args.data
    pretrained = args.pretrained if args.pretrained.is_absolute() else root / args.pretrained

    if not data_yaml.is_file():
        raise FileNotFoundError(
            f"未找到 {data_yaml}，请先运行:\n"
            f"  python scripts/convert_dota_to_yolo.py --dota-root D:/BaiduNetdiskDownload/train"
        )
    if not pretrained.is_file():
        raise FileNotFoundError(f"未找到预训练权重 {pretrained}，请先下载 rtdetr-l.pt")

    if args.prepare:
        resize_script = root / "scripts" / "resize_yolo_dataset.py"
        dataset_root = data_yaml.parent.relative_to(root)
        subprocess.run(
            [
                sys.executable,
                str(resize_script),
                "--root",
                str(dataset_root),
                "--size",
                str(args.imgsz),
            ],
            cwd=str(root),
            check=True,
        )

    from ultralytics import RTDETR

    run_dir = root / "runs" / "detect" / args.name
    last_ckpt = run_dir / "weights" / "last.pt"
    if args.resume and last_ckpt.is_file():
        model = RTDETR(str(last_ckpt))
        print(f"[resume] {last_ckpt}")
    else:
        model = RTDETR(str(pretrained))

    # DOTA 原图尺寸差异大；RT-DETR 多尺度会在同一 batch 混 640/1280 导致 stack 报错
    results = model.train(
        data=str(data_yaml),
        epochs=args.epochs,
        imgsz=args.imgsz,
        batch=args.batch,
        device=args.device,
        workers=args.workers,
        patience=20,
        project=str(root / "runs" / "detect"),
        name=args.name,
        exist_ok=True,
        resume=args.resume and last_ckpt.is_file(),
        amp=True,
        cache=False,
        rect=False,
        mosaic=0.0,
        multi_scale=0.0,
    )

    best = Path(results.save_dir) / "weights" / "best.pt"
    if not best.is_file():
        best = root / "runs" / "detect" / args.name / "weights" / "best.pt"

    dest = args.copy_to if args.copy_to.is_absolute() else root / args.copy_to
    dest.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(best, dest)
    print(f"\n[DONE] best weights: {best}")
    print(f"[DONE] copied to:     {dest}")
    print("\n请在 config/default.yaml 设置:")
    print(f"  detection_model: {dest.relative_to(root).as_posix()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
