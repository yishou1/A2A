"""Train the small-profile multimodal Mamba-style fusion block deterministically."""

from __future__ import annotations

import argparse
import hashlib
import json
import random
import sys
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

CHECKPOINT = ROOT / "models" / "checkpoints" / "mamba_fusion_s.safetensors"
DATA_DIR = ROOT / "data" / "multimodal_mamba"
MODALITIES = ("eo_ir", "sar", "radar", "text_report")


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def generate_dataset(count: int, dimension: int, seed: int):
    rng = np.random.default_rng(seed)
    latent = rng.normal(size=(count, dimension)).astype(np.float32)
    latent /= np.linalg.norm(latent, axis=1, keepdims=True).clip(min=1e-8)
    coordinate = np.arange(dimension, dtype=np.float32)
    biases = np.stack(
        [0.14 * np.sin(coordinate * (0.007 + index * 0.003) + index) for index in range(4)]
    ).astype(np.float32)
    gains = np.stack(
        [0.72 + 0.32 * (0.5 + 0.5 * np.cos(coordinate * (0.005 + index * 0.002))) for index in range(4)]
    ).astype(np.float32)
    noise_levels = np.asarray([0.065, 0.085, 0.075, 0.10], dtype=np.float32)
    inputs = np.zeros((count, 4, dimension), dtype=np.float32)
    masks = np.zeros((count, 4), dtype=np.bool_)
    for row in range(count):
        modality_count = int(rng.integers(2, 5))
        selected = np.sort(rng.choice(4, size=modality_count, replace=False))
        masks[row, selected] = True
        for modality in selected:
            vector = (
                latent[row] * gains[modality]
                + biases[modality]
                + rng.normal(0.0, noise_levels[modality], size=dimension)
            ).astype(np.float32)
            vector /= max(float(np.linalg.norm(vector)), 1e-8)
            inputs[row, modality] = vector
    return inputs, masks, latent


def _normalized_masked_mean(inputs: np.ndarray, masks: np.ndarray) -> np.ndarray:
    weighted = inputs * masks[..., None]
    pooled = weighted.sum(axis=1) / masks.sum(axis=1, keepdims=True).clip(min=1)
    return pooled / np.linalg.norm(pooled, axis=1, keepdims=True).clip(min=1e-8)


def metrics(predictions: np.ndarray, targets: np.ndarray) -> dict[str, float]:
    cosine = np.sum(predictions * targets, axis=1) / (
        np.linalg.norm(predictions, axis=1) * np.linalg.norm(targets, axis=1)
    ).clip(min=1e-8)
    return {
        "mean_cosine_similarity": round(float(cosine.mean()), 6),
        "rmse": round(float(np.sqrt(np.mean((predictions - targets) ** 2))), 6),
        "p05_cosine_similarity": round(float(np.quantile(cosine, 0.05)), 6),
    }


def evaluate(model, inputs: np.ndarray, masks: np.ndarray, targets: np.ndarray) -> dict:
    import torch

    with torch.inference_mode():
        predictions = model.fused_tensor(
            torch.from_numpy(inputs), torch.from_numpy(masks)
        ).cpu().numpy()
    baseline = _normalized_masked_mean(inputs, masks)
    return {
        "trained_fusion": metrics(predictions, targets),
        "normalized_mean_baseline": metrics(baseline, targets),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Train deterministic multimodal fusion block")
    parser.add_argument("--epochs", type=int, default=160)
    parser.add_argument("--samples-per-epoch", type=int, default=384)
    parser.add_argument("--holdout-count", type=int, default=480)
    parser.add_argument("--batch-size", type=int, default=64)
    parser.add_argument("--dimension", type=int, default=256)
    parser.add_argument("--learning-rate", type=float, default=8e-4)
    parser.add_argument("--seed", type=int, default=20260826)
    parser.add_argument("--holdout-seed", type=int, default=20260827)
    parser.add_argument("--save", type=Path, default=CHECKPOINT)
    args = parser.parse_args()

    import sklearn
    import torch
    import torch.nn.functional as functional
    from safetensors.torch import save_file

    from agent.inference.models.mamba_fusion import MultimodalMambaBlock

    random.seed(args.seed)
    np.random.seed(args.seed)
    torch.manual_seed(args.seed)
    torch.use_deterministic_algorithms(True)
    torch.set_num_threads(1)
    holdout_inputs, holdout_masks, holdout_targets = generate_dataset(
        args.holdout_count, args.dimension, args.holdout_seed
    )
    model = MultimodalMambaBlock(args.dimension)
    optimizer = torch.optim.AdamW(model.parameters(), lr=args.learning_rate, weight_decay=1e-4)
    generator = torch.Generator().manual_seed(args.seed + 1)
    scenario_rng = random.Random(args.seed + 2)

    for epoch in range(1, args.epochs + 1):
        train_inputs, train_masks, train_targets = generate_dataset(
            args.samples_per_epoch,
            args.dimension,
            scenario_rng.randrange(2**31),
        )
        inputs = torch.from_numpy(train_inputs)
        masks = torch.from_numpy(train_masks)
        targets = torch.from_numpy(train_targets)
        permutation = torch.randperm(len(inputs), generator=generator)
        losses = []
        for start in range(0, len(inputs), args.batch_size):
            indices = permutation[start : start + args.batch_size]
            prediction = model.fused_tensor(inputs[indices], masks[indices])
            cosine_loss = 1.0 - functional.cosine_similarity(prediction, targets[indices]).mean()
            mse_loss = functional.mse_loss(prediction, targets[indices])
            loss = cosine_loss + 0.25 * mse_loss
            optimizer.zero_grad()
            loss.backward()
            torch.nn.utils.clip_grad_norm_(model.parameters(), 1.0)
            optimizer.step()
            losses.append(float(loss.item()))
        if epoch == 1 or epoch % 30 == 0 or epoch == args.epochs:
            print(f"epoch={epoch:03d} loss={np.mean(losses):.6f}")

    model.eval()
    evaluation = evaluate(model, holdout_inputs, holdout_masks, holdout_targets)
    trained = evaluation["trained_fusion"]
    baseline = evaluation["normalized_mean_baseline"]
    if trained["mean_cosine_similarity"] <= baseline["mean_cosine_similarity"]:
        raise RuntimeError(f"trained fusion did not beat normalized mean baseline: {evaluation}")
    if trained["rmse"] >= baseline["rmse"]:
        raise RuntimeError(f"trained fusion RMSE did not beat normalized mean baseline: {evaluation}")

    DATA_DIR.mkdir(parents=True, exist_ok=True)
    paths = {
        "inputs": DATA_DIR / "reference_holdout_inputs.npy",
        "masks": DATA_DIR / "reference_holdout_masks.npy",
        "targets": DATA_DIR / "reference_holdout_targets.npy",
    }
    np.save(paths["inputs"], holdout_inputs, allow_pickle=False)
    np.save(paths["masks"], holdout_masks, allow_pickle=False)
    np.save(paths["targets"], holdout_targets, allow_pickle=False)
    args.save.parent.mkdir(parents=True, exist_ok=True)
    state = {key: value.detach().cpu().contiguous() for key, value in model.state_dict().items()}
    save_file(state, str(args.save))
    metadata_path = args.save.with_suffix(".metadata.json")
    metadata = {
        "model_id": "multimodal_mamba_fusion",
        "model_version": "1.0.0",
        "model_family": "mamba_style_selective_state_space_fusion",
        "compute_profile": "small",
        "artifact_path": args.save.relative_to(ROOT).as_posix(),
        "artifact_sha256": sha256(args.save),
        "training_script": Path(__file__).resolve().relative_to(ROOT).as_posix(),
        "training_script_sha256": sha256(Path(__file__).resolve()),
        "dataset": {
            "source": "deterministic_synthetic_multimodal_latent_generator",
            "modalities": list(MODALITIES),
            "samples_per_epoch": args.samples_per_epoch,
            "total_training_scenarios_seen": args.samples_per_epoch * args.epochs,
            "holdout_count": args.holdout_count,
            "holdout_seed": args.holdout_seed,
            "artifacts": {
                key: {
                    "path": path.relative_to(ROOT).as_posix(),
                    "sha256": sha256(path),
                }
                for key, path in paths.items()
            },
            "limitation": "Synthetic latent vectors only; not a benchmark of raw sensor encoders.",
        },
        "architecture": {
            "embedding_dimension": args.dimension,
            "state_dimension": 16,
            "expansion_factor": 2,
            "maximum_reference_modalities": 4,
            "parameter_count": sum(parameter.numel() for parameter in model.parameters()),
        },
        "training": {
            "objective": "cosine_reconstruction_plus_MSE",
            "epochs": args.epochs,
            "batch_size": args.batch_size,
            "optimizer": "AdamW",
            "learning_rate": args.learning_rate,
            "weight_decay": 0.0001,
            "random_state": args.seed,
        },
        "evaluation": {
            "method": "independent_deterministic_synthetic_holdout",
            **evaluation,
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
    print(json.dumps(evaluation, ensure_ascii=False, indent=2))
    print(f"saved={args.save} sha256={metadata['artifact_sha256']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
