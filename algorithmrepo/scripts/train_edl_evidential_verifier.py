"""Train and calibrate the EDL detection-verification head deterministically."""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import random
import sys
from pathlib import Path
from typing import Any

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

CHECKPOINT = ROOT / "models" / "checkpoints" / "edl_head_s.safetensors"
DATASET = ROOT / "data" / "edl_verifier" / "reference_holdout.json"


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def generate_records(count: int, seed: int) -> list[dict[str, Any]]:
    rng = random.Random(seed)
    classes = ["tank", "vehicle", "person", "artillery", "drone"]
    records = []
    for index in range(count):
        confidence = rng.uniform(0.05, 0.98)
        width = rng.uniform(8.0, 220.0)
        height = rng.uniform(8.0, 220.0)
        x1 = rng.uniform(0.0, 640.0 - width)
        y1 = rng.uniform(0.0, 640.0 - height)
        damage = rng.uniform(0.0, 1.0)
        center_x = (x1 + width / 2.0) / 640.0
        center_y = (y1 + height / 2.0) / 640.0
        size_quality = min(width, height) / 160.0
        border_penalty = max(abs(center_x - 0.5), abs(center_y - 0.5))
        latent_quality = (
            5.4 * (confidence - 0.52)
            + 0.9 * (size_quality - 0.45)
            - 0.65 * border_penalty
            - 0.25 * damage
            + rng.gauss(0.0, 0.58)
        )
        label = int(latent_quality >= 0.0)
        detection = {
            "detection_id": f"D-{index:05d}",
            "sensor_id": "SYNTH-EO",
            "class_name": rng.choice(classes),
            "confidence": round(confidence, 8),
            "bbox": [
                round(x1, 6),
                round(y1, 6),
                round(x1 + width, 6),
                round(y1 + height, 6),
            ],
            "damage_score": round(damage, 8),
        }
        records.append({"detection": detection, "label": label})
    return records


def records_to_arrays(records: list[dict[str, Any]]) -> tuple[np.ndarray, np.ndarray]:
    from agent.inference.models.edl_head import EvidentialHead

    features = np.asarray(
        [EvidentialHead.detection_features(record["detection"]) for record in records],
        dtype=np.float32,
    )
    labels = np.asarray([record["label"] for record in records], dtype=np.int64)
    return features, labels


def _dirichlet_kl(alpha, num_classes: int):
    import torch

    uniform = torch.ones((1, num_classes), dtype=alpha.dtype, device=alpha.device)
    sum_alpha = alpha.sum(dim=1, keepdim=True)
    sum_uniform = uniform.sum(dim=1, keepdim=True)
    first = (
        torch.lgamma(sum_alpha)
        - torch.lgamma(alpha).sum(dim=1, keepdim=True)
        - torch.lgamma(sum_uniform)
        + torch.lgamma(uniform).sum(dim=1, keepdim=True)
    )
    second = ((alpha - uniform) * (torch.digamma(alpha) - torch.digamma(sum_alpha))).sum(
        dim=1, keepdim=True
    )
    return first + second


def evidential_loss(alpha, labels, epoch: int, annealing_epochs: int = 80):
    import torch
    import torch.nn.functional as functional

    targets = functional.one_hot(labels, num_classes=alpha.shape[1]).to(dtype=alpha.dtype)
    strength = alpha.sum(dim=1, keepdim=True)
    expected_cross_entropy = (targets * (torch.digamma(strength) - torch.digamma(alpha))).sum(dim=1)
    adjusted_alpha = targets + (1.0 - targets) * alpha
    annealing = min(1.0, epoch / max(annealing_epochs, 1))
    regularizer = _dirichlet_kl(adjusted_alpha, alpha.shape[1]).squeeze(1)
    return (expected_cross_entropy + 0.02 * annealing * regularizer).mean()


def expected_calibration_error(labels: np.ndarray, probabilities: np.ndarray, bins: int = 10) -> float:
    predictions = (probabilities >= 0.5).astype(np.int64)
    confidence = np.where(predictions == 1, probabilities, 1.0 - probabilities)
    correct = (predictions == labels).astype(np.float64)
    total = max(len(labels), 1)
    error = 0.0
    for lower in np.linspace(0.0, 1.0, bins, endpoint=False):
        upper = lower + 1.0 / bins
        mask = (confidence > lower) & (confidence <= upper)
        if mask.any():
            error += mask.sum() / total * abs(correct[mask].mean() - confidence[mask].mean())
    return float(error)


def evaluate(model, records: list[dict[str, Any]]) -> dict[str, float]:
    import torch
    from sklearn.metrics import accuracy_score, brier_score_loss, f1_score, log_loss

    features, labels = records_to_arrays(records)
    with torch.inference_mode():
        _, _, probabilities, epistemic, aleatoric = model(torch.tensor(features))
    verified_probability = probabilities[:, 1].cpu().numpy()
    predictions = (verified_probability >= 0.5).astype(np.int64)
    raw_confidence = features[:, 0]
    correct = predictions == labels
    keep_count = max(1, int(math.ceil(len(labels) * 0.8)))
    selected = np.argsort(epistemic.cpu().numpy())[:keep_count]
    return {
        "accuracy": round(float(accuracy_score(labels, predictions)), 6),
        "macro_f1": round(float(f1_score(labels, predictions, average="macro")), 6),
        "brier_score": round(float(brier_score_loss(labels, verified_probability)), 6),
        "expected_calibration_error_10_bin": round(
            expected_calibration_error(labels, verified_probability), 6
        ),
        "log_loss": round(float(log_loss(labels, verified_probability, labels=[0, 1])), 6),
        "mean_epistemic_uncertainty": round(float(epistemic.mean().item()), 6),
        "mean_aleatoric_uncertainty": round(float(aleatoric.mean().item()), 6),
        "error_mean_epistemic_uncertainty": round(
            float(epistemic.cpu().numpy()[~correct].mean()) if (~correct).any() else 0.0, 6
        ),
        "selective_accuracy_at_80_percent_coverage": round(
            float(accuracy_score(labels[selected], predictions[selected])), 6
        ),
        "raw_detector_brier_baseline": round(float(brier_score_loss(labels, raw_confidence)), 6),
        "raw_detector_ece_baseline": round(expected_calibration_error(labels, raw_confidence), 6),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Train deterministic EDL verifier")
    parser.add_argument("--epochs", type=int, default=260)
    parser.add_argument("--training-count", type=int, default=4000)
    parser.add_argument("--holdout-count", type=int, default=1000)
    parser.add_argument("--batch-size", type=int, default=128)
    parser.add_argument("--learning-rate", type=float, default=1e-3)
    parser.add_argument("--seed", type=int, default=20260824)
    parser.add_argument("--holdout-seed", type=int, default=20260825)
    parser.add_argument("--save", type=Path, default=CHECKPOINT)
    args = parser.parse_args()

    import sklearn
    import torch
    from safetensors.torch import save_file

    from agent.inference.models.edl_head import EvidentialHead

    random.seed(args.seed)
    np.random.seed(args.seed)
    torch.manual_seed(args.seed)
    torch.use_deterministic_algorithms(True)
    torch.set_num_threads(1)
    training_records = generate_records(args.training_count, args.seed)
    holdout_records = generate_records(args.holdout_count, args.holdout_seed)
    train_features, train_labels = records_to_arrays(training_records)
    features = torch.tensor(train_features)
    labels = torch.tensor(train_labels, dtype=torch.long)
    model = EvidentialHead()
    optimizer = torch.optim.AdamW(model.parameters(), lr=args.learning_rate, weight_decay=1e-4)
    generator = torch.Generator().manual_seed(args.seed + 1)

    for epoch in range(1, args.epochs + 1):
        permutation = torch.randperm(len(labels), generator=generator)
        epoch_losses = []
        for start in range(0, len(labels), args.batch_size):
            indices = permutation[start : start + args.batch_size]
            _, alpha, _, _, _ = model(features[indices])
            loss = evidential_loss(alpha, labels[indices], epoch)
            optimizer.zero_grad()
            loss.backward()
            torch.nn.utils.clip_grad_norm_(model.parameters(), 5.0)
            optimizer.step()
            epoch_losses.append(float(loss.item()))
        if epoch == 1 or epoch % 40 == 0 or epoch == args.epochs:
            print(f"epoch={epoch:03d} loss={np.mean(epoch_losses):.6f}")

    model.eval()
    metrics = evaluate(model, holdout_records)
    if metrics["brier_score"] >= metrics["raw_detector_brier_baseline"]:
        raise RuntimeError(f"EDL calibration did not beat detector baseline: {metrics}")
    if metrics["accuracy"] < 0.8:
        raise RuntimeError(f"EDL holdout accuracy is too low: {metrics}")

    DATASET.parent.mkdir(parents=True, exist_ok=True)
    DATASET.write_text(
        json.dumps(holdout_records, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    args.save.parent.mkdir(parents=True, exist_ok=True)
    state = {key: value.detach().cpu().contiguous() for key, value in model.state_dict().items()}
    save_file(state, str(args.save))
    metadata_path = args.save.with_suffix(".metadata.json")
    metadata = {
        "model_id": "edl_evidential_verifier",
        "model_version": "1.0.0",
        "model_family": "dirichlet_evidential_neural_classifier",
        "compute_profile": "small",
        "artifact_path": args.save.relative_to(ROOT).as_posix(),
        "artifact_sha256": sha256(args.save),
        "training_script": Path(__file__).resolve().relative_to(ROOT).as_posix(),
        "training_script_sha256": sha256(Path(__file__).resolve()),
        "dataset": {
            "path": DATASET.relative_to(ROOT).as_posix(),
            "sha256": sha256(DATASET),
            "source": "deterministic_synthetic_detection_quality_generator",
            "training_count": args.training_count,
            "holdout_count": args.holdout_count,
            "holdout_seed": args.holdout_seed,
            "class_counts": {
                "rejected": int(sum(record["label"] == 0 for record in holdout_records)),
                "verified": int(sum(record["label"] == 1 for record in holdout_records)),
            },
            "limitation": "Synthetic detection-quality labels only; not an operational detector benchmark.",
        },
        "architecture": {
            "input_dimension": 6,
            "hidden_dimension": 32,
            "class_count": 2,
            "class_labels": ["rejected", "verified"],
            "parameter_count": sum(parameter.numel() for parameter in model.parameters()),
        },
        "training": {
            "objective": "Dirichlet_expected_cross_entropy_plus_annealed_KL",
            "epochs": args.epochs,
            "batch_size": args.batch_size,
            "optimizer": "AdamW",
            "learning_rate": args.learning_rate,
            "weight_decay": 0.0001,
            "random_state": args.seed,
        },
        "decision_policy": {
            "verified_probability_threshold": 0.5,
            "maximum_epistemic_uncertainty": 0.45,
            "manual_review_margin": 0.08,
            "human_review_required_for_manual_review_decision": True,
        },
        "evaluation": {
            "method": "independent_deterministic_synthetic_holdout",
            **metrics,
        },
        "runtime_versions": {
            "torch": torch.__version__,
            "numpy": np.__version__,
            "scikit_learn": sklearn.__version__,
        },
    }
    metadata_path.write_text(
        json.dumps(metadata, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(metrics, ensure_ascii=False, indent=2))
    print(f"saved={args.save} sha256={metadata['artifact_sha256']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
