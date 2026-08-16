#!/usr/bin/env python3
"""Train the M08 conditional tabular GAN on the reference observation data."""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import random
import sys
from pathlib import Path

import torch
from torch import nn


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "services"))

from a2a_algorithms_common.conditional_tabular_gan import ConditionalGenerator  # noqa: E402


FEATURE_NAMES = [
    "radar_activity",
    "communication_intensity",
    "movement_consistency",
    "weapon_signature",
    "proximity_score",
]
CLASS_NAMES = ["benign", "surveillance", "hostile"]
TRAINING_SEED = 20260822


class ConditionalDiscriminator(nn.Module):
    def __init__(self, feature_count: int, class_count: int, hidden_dim: int) -> None:
        super().__init__()
        self.network = nn.Sequential(
            nn.Linear(feature_count + class_count, hidden_dim),
            nn.LeakyReLU(0.2),
            nn.Linear(hidden_dim, hidden_dim),
            nn.LeakyReLU(0.2),
            nn.Linear(hidden_dim, 1),
        )

    def forward(self, features: torch.Tensor, conditions: torch.Tensor) -> torch.Tensor:
        return self.network(torch.cat((features, conditions), dim=1))


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def normalized_text_sha256(path: Path) -> str:
    text = path.read_text(encoding="utf-8").replace("\r\n", "\n")
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def load_dataset(path: Path) -> tuple[torch.Tensor, torch.Tensor]:
    features: list[list[float]] = []
    labels: list[int] = []
    with path.open("r", encoding="utf-8", newline="") as handle:
        for row in csv.DictReader(handle):
            features.append([float(row[name]) for name in FEATURE_NAMES])
            labels.append(CLASS_NAMES.index(row["intent_label"]))
    if not features or set(labels) != set(range(len(CLASS_NAMES))):
        raise RuntimeError("reference dataset must contain every GAN condition")
    return torch.tensor(features, dtype=torch.float32), torch.tensor(labels, dtype=torch.long)


def one_hot(labels: torch.Tensor) -> torch.Tensor:
    return torch.nn.functional.one_hot(labels, num_classes=len(CLASS_NAMES)).float()


def distribution_metrics(
    generator: ConditionalGenerator,
    real_features: torch.Tensor,
    real_labels: torch.Tensor,
    noise_dim: int,
) -> dict:
    random_source = torch.Generator(device="cpu")
    random_source.manual_seed(TRAINING_SEED + 100)
    per_class: dict[str, dict] = {}
    errors: list[float] = []
    with torch.inference_mode():
        for class_index, class_name in enumerate(CLASS_NAMES):
            count = 512
            noise = torch.randn(count, noise_dim, generator=random_source)
            conditions = torch.zeros(count, len(CLASS_NAMES))
            conditions[:, class_index] = 1.0
            generated = generator(noise, conditions)
            real_mean = real_features[real_labels == class_index].mean(dim=0)
            generated_mean = generated.mean(dim=0)
            absolute_error = torch.abs(real_mean - generated_mean)
            errors.extend(float(value) for value in absolute_error)
            per_class[class_name] = {
                "real_feature_means": [round(float(value), 6) for value in real_mean],
                "generated_feature_means": [round(float(value), 6) for value in generated_mean],
                "mean_absolute_error": round(float(absolute_error.mean()), 6),
            }
    return {
        "metric": "class_conditional_feature_mean_absolute_error",
        "generated_samples_per_class": 512,
        "overall_mean_absolute_error": round(sum(errors) / len(errors), 6),
        "per_class": per_class,
    }


def train(
    dataset_path: Path,
    model_path: Path,
    metadata_path: Path,
    epochs: int,
) -> dict:
    random.seed(TRAINING_SEED)
    torch.manual_seed(TRAINING_SEED)
    torch.set_num_threads(1)
    torch.use_deterministic_algorithms(True)
    features, labels = load_dataset(dataset_path)
    conditions_all = one_hot(labels)

    noise_dim = 8
    hidden_dim = 32
    batch_size = 64
    generator = ConditionalGenerator(
        noise_dim=noise_dim,
        class_count=len(CLASS_NAMES),
        feature_count=len(FEATURE_NAMES),
        hidden_dim=hidden_dim,
    )
    discriminator = ConditionalDiscriminator(
        feature_count=len(FEATURE_NAMES),
        class_count=len(CLASS_NAMES),
        hidden_dim=hidden_dim,
    )
    generator_optimizer = torch.optim.Adam(generator.parameters(), lr=0.0015, betas=(0.5, 0.999))
    discriminator_optimizer = torch.optim.Adam(discriminator.parameters(), lr=0.0015, betas=(0.5, 0.999))
    criterion = nn.BCEWithLogitsLoss()
    random_source = torch.Generator(device="cpu")
    random_source.manual_seed(TRAINING_SEED + 1)
    final_generator_loss = 0.0
    final_discriminator_loss = 0.0

    for _epoch in range(epochs):
        permutation = torch.randperm(len(features), generator=random_source)
        for start in range(0, len(features), batch_size):
            indices = permutation[start : start + batch_size]
            real_batch = features[indices]
            condition_batch = conditions_all[indices]
            current_batch = len(indices)

            discriminator_optimizer.zero_grad()
            real_logits = discriminator(real_batch, condition_batch)
            real_loss = criterion(real_logits, torch.ones_like(real_logits))
            noise = torch.randn(current_batch, noise_dim, generator=random_source)
            generated_batch = generator(noise, condition_batch)
            generated_logits = discriminator(generated_batch.detach(), condition_batch)
            generated_loss = criterion(generated_logits, torch.zeros_like(generated_logits))
            discriminator_loss = real_loss + generated_loss
            discriminator_loss.backward()
            discriminator_optimizer.step()

            generator_optimizer.zero_grad()
            noise = torch.randn(current_batch, noise_dim, generator=random_source)
            generated_batch = generator(noise, condition_batch)
            generated_logits = discriminator(generated_batch, condition_batch)
            generator_loss = criterion(generated_logits, torch.ones_like(generated_logits))
            generator_loss.backward()
            generator_optimizer.step()
            final_generator_loss = float(generator_loss.detach())
            final_discriminator_loss = float(discriminator_loss.detach())

    generator.eval()
    metrics = distribution_metrics(generator, features, labels, noise_dim)
    model_path.parent.mkdir(parents=True, exist_ok=True)
    torch.save(
        {
            "generator_state_dict": generator.state_dict(),
            "noise_dim": noise_dim,
            "hidden_dim": hidden_dim,
            "classes": CLASS_NAMES,
            "features": FEATURE_NAMES,
        },
        model_path,
    )
    generator_source = ROOT / "services" / "a2a_algorithms_common" / "conditional_tabular_gan.py"
    metadata = {
        "model_id": "conditional_tabular_gan_generator",
        "model_version": "1.0.0",
        "model_family": "pytorch_conditional_gan",
        "artifact_path": str(model_path.relative_to(ROOT)).replace("\\", "/"),
        "artifact_sha256": sha256(model_path),
        "generator_source": str(generator_source.relative_to(ROOT)).replace("\\", "/"),
        "generator_source_sha256": normalized_text_sha256(generator_source),
        "training_script": str(Path(__file__).resolve().relative_to(ROOT)).replace("\\", "/"),
        "training_script_sha256": normalized_text_sha256(Path(__file__).resolve()),
        "dataset": {
            "path": str(dataset_path.relative_to(ROOT)).replace("\\", "/"),
            "sha256": sha256(dataset_path),
            "source": "M07 deterministic class-conditional reference observations",
            "row_count": len(features),
            "limitation": "Synthetic reference observations only; generated output must not be represented as real sensor data.",
        },
        "classes": CLASS_NAMES,
        "features": FEATURE_NAMES,
        "architecture": {
            "type": "conditional_mlp_gan",
            "noise_dim": noise_dim,
            "hidden_dim": hidden_dim,
            "output_activation": "sigmoid",
            "generator_parameter_count": sum(parameter.numel() for parameter in generator.parameters()),
        },
        "training": {
            "seed": TRAINING_SEED,
            "epochs": epochs,
            "batch_size": batch_size,
            "optimizer": "Adam",
            "learning_rate": 0.0015,
            "final_generator_loss": round(final_generator_loss, 6),
            "final_discriminator_loss": round(final_discriminator_loss, 6),
        },
        "evaluation": metrics,
        "runtime_versions": {"torch": torch.__version__},
    }
    metadata_path.write_text(
        json.dumps(metadata, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    return metadata


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--dataset-path",
        type=Path,
        default=ROOT / "data" / "intent_naive_bayes" / "reference_observations.csv",
    )
    parser.add_argument(
        "--model-path",
        type=Path,
        default=ROOT / "models" / "conditional_tabular_gan_generator.pt",
    )
    parser.add_argument(
        "--metadata-path",
        type=Path,
        default=ROOT / "models" / "conditional_tabular_gan.metadata.json",
    )
    parser.add_argument("--epochs", type=int, default=180)
    args = parser.parse_args()
    report = train(
        args.dataset_path.resolve(),
        args.model_path.resolve(),
        args.metadata_path.resolve(),
        args.epochs,
    )
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
