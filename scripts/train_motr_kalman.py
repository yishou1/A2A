"""
在 VisDrone2019-MOT 上训练 MOTR 关联网络 + Neural Kalman，输出 motr_tracker 权重。

VisDrone 为 UAV 俯视多目标跟踪，与战场光电/SAR 俯视场景相近，可用于：
  - 训练目标关联（MOTRCostNet）
  - 训练 bbox 神经卡尔曼（KalmanNet）

注意：VisDrone 类别为行人/车辆等城市目标，与 DOTA 战场类（坦克/舰船等）存在域差。
推荐与 battlefield_rtdetr.pt 检测器配合使用；纯战场跟踪需后续 DOTA 视频轨迹微调。

用法（项目根目录）:
  .\\.venv\\Scripts\\python.exe scripts/train_motr_kalman.py ^
    --train-root "D:\\软件\\dataset\\VisDrone2019-MOT-train" ^
    --val-root "D:\\软件\\dataset\\VisDrone2019-MOT-val"

  # 训练完成后在 config/profiles/medium.yaml 或 default.yaml 中:
  #   motr_checkpoint: models/checkpoints/motr_tracker_battlefield.pt
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def triplet_association_loss(anchor_emb, pos_emb, neg_emb, margin: float = 0.2) -> "torch.Tensor":
    import torch
    import torch.nn.functional as F

    pos_d = 1.0 - F.cosine_similarity(anchor_emb, pos_emb, dim=-1)
    neg_d = 1.0 - F.cosine_similarity(anchor_emb, neg_emb, dim=-1)
    return F.relu(pos_d - neg_d + margin).mean()


def kalman_supervision_loss(kalman, state, obs) -> "torch.Tensor":
    import torch
    import torch.nn.functional as F

    updated, _ = kalman(state, obs)
    target = obs
    return F.mse_loss(updated[:, :4], target)


def evaluate(model, loader, device) -> dict[str, float]:
    import torch

    model.eval()
    assoc_losses: list[float] = []
    kalman_losses: list[float] = []
    with torch.no_grad():
        for batch in loader:
            anchor = batch["anchor"].to(device)
            positive = batch["positive"].to(device)
            negative = batch["negative"].to(device)
            state = batch["state"].to(device)
            obs = batch["obs"].to(device)

            a_emb = model.cost_net.embed_crops(anchor)
            p_emb = model.cost_net.embed_crops(positive)
            n_emb = model.cost_net.embed_crops(negative)
            assoc_losses.append(float(triplet_association_loss(a_emb, p_emb, n_emb).item()))
            kalman_losses.append(float(kalman_supervision_loss(model.kalman, state, obs).item()))
    model.train()
    return {
        "assoc_loss": sum(assoc_losses) / max(len(assoc_losses), 1),
        "kalman_loss": sum(kalman_losses) / max(len(kalman_losses), 1),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Train MOTR+Kalman on VisDrone2019-MOT")
    parser.add_argument(
        "--train-root",
        type=Path,
        default=Path(r"D:\软件\dataset\VisDrone2019-MOT-train"),
        help="VisDrone MOT train 根目录（含 annotations/ sequences/）",
    )
    parser.add_argument(
        "--val-root",
        type=Path,
        default=Path(r"D:\软件\dataset\VisDrone2019-MOT-val"),
    )
    parser.add_argument("--epochs", type=int, default=15)
    parser.add_argument("--batch-size", type=int, default=32)
    parser.add_argument("--lr", type=float, default=1e-4)
    parser.add_argument("--workers", type=int, default=0 if sys.platform == "win32" else 4)
    parser.add_argument("--samples-per-epoch", type=int, default=6000)
    parser.add_argument("--device", default="cuda" if __import__("torch").cuda.is_available() else "cpu")
    parser.add_argument(
        "--init-checkpoint",
        type=Path,
        default=Path("models/checkpoints/motr_tracker.pt"),
        help="可选：从已有 motr_tracker.pt 微调",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("models/checkpoints/motr_tracker_battlefield.pt"),
    )
    parser.add_argument("--assoc-weight", type=float, default=1.0)
    parser.add_argument("--kalman-weight", type=float, default=0.5)
    args = parser.parse_args()

    import torch
    from torch.utils.data import DataLoader

    from agent.inference.models.motr_kalman import MOTRTracker
    from agent.training.visdrone_mot import VisDroneAssociationDataset

    train_root = args.train_root if args.train_root.is_absolute() else ROOT / args.train_root
    val_root = args.val_root if args.val_root.is_absolute() else ROOT / args.val_root
    if not train_root.is_dir():
        raise FileNotFoundError(f"训练集不存在: {train_root}")

    print(f"Train: {train_root}", flush=True)
    print(f"Val:   {val_root if val_root.is_dir() else '(skip)'}", flush=True)
    print(f"Device: {args.device}", flush=True)

    train_ds = VisDroneAssociationDataset(train_root, samples_per_epoch=args.samples_per_epoch)
    print(f"Positive track pairs indexed: {len(train_ds.positive_pairs)}", flush=True)

    train_loader = DataLoader(
        train_ds,
        batch_size=args.batch_size,
        shuffle=True,
        num_workers=args.workers,
        pin_memory=args.device.startswith("cuda"),
    )

    val_loader = None
    if val_root.is_dir():
        val_ds = VisDroneAssociationDataset(val_root, samples_per_epoch=1500, rng_seed=99)
        val_loader = DataLoader(val_ds, batch_size=args.batch_size, shuffle=False, num_workers=args.workers)

    model = MOTRTracker().to(args.device)
    init_ckpt = args.init_checkpoint if args.init_checkpoint.is_absolute() else ROOT / args.init_checkpoint
    if init_ckpt.is_file():
        state = torch.load(init_ckpt, map_location="cpu", weights_only=True)
        model.load_state_dict(state, strict=False)
        print(f"[OK] loaded init weights: {init_ckpt}", flush=True)

    optim = torch.optim.AdamW(model.parameters(), lr=args.lr, weight_decay=1e-4)

    best_val = float("inf")
    output_path = args.output if args.output.is_absolute() else ROOT / args.output
    output_path.parent.mkdir(parents=True, exist_ok=True)

    for epoch in range(1, args.epochs + 1):
        model.train()
        running = 0.0
        n_batches = 0
        for batch in train_loader:
            anchor = batch["anchor"].to(args.device)
            positive = batch["positive"].to(args.device)
            negative = batch["negative"].to(args.device)
            state = batch["state"].to(args.device)
            obs = batch["obs"].to(args.device)

            a_emb = model.cost_net.embed_crops(anchor)
            p_emb = model.cost_net.embed_crops(positive)
            n_emb = model.cost_net.embed_crops(negative)
            loss_assoc = triplet_association_loss(a_emb, p_emb, n_emb)
            loss_kalman = kalman_supervision_loss(model.kalman, state, obs)
            loss = args.assoc_weight * loss_assoc + args.kalman_weight * loss_kalman

            optim.zero_grad()
            loss.backward()
            torch.nn.utils.clip_grad_norm_(model.parameters(), 5.0)
            optim.step()

            running += float(loss.item())
            n_batches += 1
            if n_batches % 25 == 0:
                print(f"  epoch {epoch} batch {n_batches} loss={loss.item():.4f}", flush=True)

        train_loss = running / max(n_batches, 1)
        msg = f"Epoch {epoch}/{args.epochs}  train_loss={train_loss:.4f}"

        if val_loader is not None:
            metrics = evaluate(model, val_loader, args.device)
            val_total = metrics["assoc_loss"] + metrics["kalman_loss"]
            msg += f"  val_assoc={metrics['assoc_loss']:.4f}  val_kalman={metrics['kalman_loss']:.4f}"
            if val_total < best_val:
                best_val = val_total
                torch.save(model.state_dict(), output_path)
                msg += "  [saved best]"
        torch.save(model.state_dict(), output_path.with_name(output_path.stem + "_last.pt"))
        if val_loader is None and epoch == args.epochs:
            torch.save(model.state_dict(), output_path)

        print(msg, flush=True)

    if not output_path.is_file():
        torch.save(model.state_dict(), output_path)

    print(f"\n[DONE] checkpoint -> {output_path}", flush=True)
    print("配置示例 (config/profiles/medium.yaml):", flush=True)
    print("  motr_checkpoint: models/checkpoints/motr_tracker_battlefield.pt", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
