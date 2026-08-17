#!/usr/bin/env python3
"""Train the M06 small-profile SupCon/prototypical neural classifier."""
from __future__ import annotations

import argparse
import hashlib
import json
import random
import sys
from pathlib import Path

import numpy as np
import torch
import torch.nn.functional as F
from sklearn.metrics import accuracy_score, confusion_matrix, f1_score
from sklearn.model_selection import train_test_split
from safetensors.torch import save_file
from torch.utils.data import DataLoader, TensorDataset


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from agent.inference.models.supcon_meta import SupConMetaNet  # noqa: E402


LABELS = ("friendly", "neutral", "hostile", "unknown")
DATASET_SEED = 20260820
MODEL_SEED = 20260821
INPUT_DIM = 256
PROJECTION_DIM = 128


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def normalized_text_sha256(path: Path) -> str:
    text = path.read_text(encoding="utf-8").replace("\r\n", "\n")
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def generate_reference_dataset(samples_per_class: int) -> tuple[np.ndarray, np.ndarray]:
    if samples_per_class < 100:
        raise ValueError("at least 100 samples per class are required")
    rng = np.random.default_rng(DATASET_SEED)
    axis = np.linspace(0.0, 2.0 * np.pi, INPUT_DIM, endpoint=False)
    centers = np.stack(
        [
            np.sin(axis) + 0.45 * np.cos(3.0 * axis),
            np.cos(axis) - 0.35 * np.sin(2.0 * axis),
            np.sin(2.0 * axis + 0.7) + 0.3 * np.cos(5.0 * axis),
            np.cos(3.0 * axis - 0.4) - 0.25 * np.sin(4.0 * axis),
        ]
    ).astype(np.float32)
    centers /= np.linalg.norm(centers, axis=1, keepdims=True)
    embeddings: list[np.ndarray] = []
    labels: list[int] = []
    for class_index, center in enumerate(centers):
        for _ in range(samples_per_class):
            scale = rng.uniform(0.75, 1.35)
            noise = rng.normal(0.0, 0.055, size=INPUT_DIM).astype(np.float32)
            sample = scale * center + noise
            # Add weak nuisance factors so the encoder must learn a projection
            # rather than classify a single untouched prototype vector.
            sample += rng.normal(0.0, 0.018) * np.roll(center, rng.integers(1, 24))
            embeddings.append(sample.astype(np.float32))
            labels.append(class_index)
    order = rng.permutation(len(labels))
    return np.stack(embeddings)[order], np.asarray(labels, dtype=np.int64)[order]


def supervised_contrastive_loss(z: torch.Tensor, labels: torch.Tensor, temperature: float) -> torch.Tensor:
    similarity = (z @ z.T) / temperature
    row_max = similarity.detach().max(dim=1, keepdim=True).values
    stabilized = similarity - row_max
    diagonal = torch.eye(len(z), dtype=torch.bool, device=z.device)
    denominator_mask = ~diagonal
    exp_similarity = torch.exp(stabilized) * denominator_mask
    log_probability = stabilized - torch.log(exp_similarity.sum(dim=1, keepdim=True).clamp_min(1e-12))
    positive_mask = (labels[:, None] == labels[None, :]) & denominator_mask
    positive_count = positive_mask.sum(dim=1).clamp_min(1)
    mean_positive_log_probability = (log_probability * positive_mask).sum(dim=1) / positive_count
    return -mean_positive_log_probability.mean()


def train(
    embeddings_path: Path,
    labels_path: Path,
    checkpoint_path: Path,
    metadata_path: Path,
    *,
    samples_per_class: int,
    epochs: int,
) -> dict:
    random.seed(MODEL_SEED)
    np.random.seed(MODEL_SEED)
    torch.manual_seed(MODEL_SEED)
    torch.set_num_threads(1)
    torch.use_deterministic_algorithms(True)

    embeddings, labels = generate_reference_dataset(samples_per_class)
    embeddings_path.parent.mkdir(parents=True, exist_ok=True)
    np.save(embeddings_path, embeddings, allow_pickle=False)
    np.save(labels_path, labels, allow_pickle=False)
    train_x, test_x, train_y, test_y = train_test_split(
        embeddings,
        labels,
        test_size=0.25,
        random_state=MODEL_SEED,
        stratify=labels,
    )

    model = SupConMetaNet(
        in_dim=INPUT_DIM,
        proj_dim=PROJECTION_DIM,
        num_classes=len(LABELS),
    )
    optimizer = torch.optim.AdamW(model.parameters(), lr=0.0015, weight_decay=1e-4)
    generator = torch.Generator().manual_seed(MODEL_SEED)
    loader = DataLoader(
        TensorDataset(torch.from_numpy(train_x), torch.from_numpy(train_y)),
        batch_size=64,
        shuffle=True,
        generator=generator,
    )
    temperature = 0.1
    final_loss = 0.0
    model.train()
    for _ in range(epochs):
        epoch_losses = []
        for batch_x, batch_y in loader:
            optimizer.zero_grad(set_to_none=True)
            projected = model.project(batch_x)
            prototypes = F.normalize(model.prototypes, dim=-1)
            logits = projected @ prototypes.T / temperature
            classification_loss = F.cross_entropy(logits, batch_y)
            contrastive_loss = supervised_contrastive_loss(projected, batch_y, temperature)
            loss = 0.75 * classification_loss + 0.25 * contrastive_loss
            loss.backward()
            optimizer.step()
            epoch_losses.append(float(loss.detach()))
        final_loss = float(np.mean(epoch_losses))

    model.eval()
    with torch.inference_mode():
        projected = model.project(torch.from_numpy(test_x))
        prototypes = F.normalize(model.prototypes, dim=-1)
        probabilities = F.softmax(projected @ prototypes.T / temperature, dim=1)
        predictions = probabilities.argmax(dim=1).cpu().numpy()
        confidences = probabilities.max(dim=1).values.cpu().numpy()

    checkpoint_path.parent.mkdir(parents=True, exist_ok=True)
    cpu_state = {name: tensor.detach().cpu() for name, tensor in model.state_dict().items()}
    save_file(cpu_state, str(checkpoint_path))
    script_path = Path(__file__).resolve()
    parameter_count = sum(parameter.numel() for parameter in model.parameters())
    metadata = {
        "model_id": "supcon_meta_classifier",
        "model_version": "1.0.0",
        "model_family": "supervised_contrastive_prototypical_mlp",
        "compute_profile": "small",
        "artifact_path": str(checkpoint_path.relative_to(ROOT)).replace("\\", "/"),
        "artifact_sha256": sha256(checkpoint_path),
        "training_script": str(script_path.relative_to(ROOT)).replace("\\", "/"),
        "training_script_sha256": normalized_text_sha256(script_path),
        "dataset": {
            "embeddings_path": str(embeddings_path.relative_to(ROOT)).replace("\\", "/"),
            "embeddings_sha256": sha256(embeddings_path),
            "labels_path": str(labels_path.relative_to(ROOT)).replace("\\", "/"),
            "labels_sha256": sha256(labels_path),
            "source": "deterministic_synthetic_embedding_generator",
            "seed": DATASET_SEED,
            "row_count": len(labels),
            "class_counts": {name: int(np.sum(labels == index)) for index, name in enumerate(LABELS)},
            "limitation": "Synthetic embedding clusters only; not an operational target-identification benchmark.",
        },
        "architecture": {
            "input_dim": INPUT_DIM,
            "hidden_dim": 256,
            "projection_dim": PROJECTION_DIM,
            "class_count": len(LABELS),
            "labels": list(LABELS),
            "parameter_count": parameter_count,
        },
        "training": {
            "epochs": epochs,
            "batch_size": 64,
            "optimizer": "AdamW",
            "learning_rate": 0.0015,
            "weight_decay": 0.0001,
            "temperature": temperature,
            "classification_loss_weight": 0.75,
            "supervised_contrastive_loss_weight": 0.25,
            "random_state": MODEL_SEED,
            "final_combined_loss": round(final_loss, 6),
        },
        "evaluation": {
            "method": "stratified_holdout",
            "test_ratio": 0.25,
            "train_count": len(train_x),
            "test_count": len(test_x),
            "accuracy": round(float(accuracy_score(test_y, predictions)), 6),
            "macro_f1": round(float(f1_score(test_y, predictions, average="macro")), 6),
            "mean_confidence": round(float(np.mean(confidences)), 6),
            "confusion_matrix_labels": list(LABELS),
            "confusion_matrix": confusion_matrix(
                test_y, predictions, labels=list(range(len(LABELS)))
            ).tolist(),
        },
        "runtime_versions": {
            "torch": torch.__version__,
            "numpy": np.__version__,
            "scikit_learn": __import__("sklearn").__version__,
        },
    }
    metadata_path.write_text(
        json.dumps(metadata, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return metadata


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--samples-per-class", type=int, default=400)
    parser.add_argument("--epochs", type=int, default=30)
    args = parser.parse_args()
    data_dir = ROOT / "data" / "supcon_meta"
    checkpoint = ROOT / "models" / "checkpoints" / "supcon_meta_s.safetensors"
    metadata = checkpoint.with_suffix(".metadata.json")
    result = train(
        data_dir / "reference_embeddings.npy",
        data_dir / "reference_labels.npy",
        checkpoint,
        metadata,
        samples_per_class=args.samples_per_class,
        epochs=args.epochs,
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
