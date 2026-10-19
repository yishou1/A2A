"""Frozen conditional GAN generator for bounded tabular reference features."""
from __future__ import annotations

from functools import lru_cache
import hashlib
import json
import math
from pathlib import Path
from typing import Any

import torch
from torch import nn


MODEL_RELATIVE_PATH = Path("models/conditional_tabular_gan_generator.pt")
METADATA_RELATIVE_PATH = Path("models/conditional_tabular_gan.metadata.json")


def _root() -> Path:
    return Path(__file__).resolve().parents[2]


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _normalized_text_sha256(path: Path) -> str:
    text = path.read_text(encoding="utf-8").replace("\r\n", "\n")
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


class ConditionalGenerator(nn.Module):
    def __init__(
        self,
        noise_dim: int,
        class_count: int,
        feature_count: int,
        hidden_dim: int,
    ) -> None:
        super().__init__()
        self.network = nn.Sequential(
            nn.Linear(noise_dim + class_count, hidden_dim),
            nn.ReLU(),
            nn.Linear(hidden_dim, hidden_dim),
            nn.ReLU(),
            nn.Linear(hidden_dim, feature_count),
            nn.Sigmoid(),
        )

    def forward(self, noise: torch.Tensor, conditions: torch.Tensor) -> torch.Tensor:
        return self.network(torch.cat((noise, conditions), dim=1))


@lru_cache(maxsize=1)
def load_metadata() -> dict[str, Any]:
    return json.loads((_root() / METADATA_RELATIVE_PATH).read_text(encoding="utf-8"))


def model_loaded() -> bool:
    model_path = _root() / MODEL_RELATIVE_PATH
    metadata_path = _root() / METADATA_RELATIVE_PATH
    source_path = Path(__file__).resolve()
    if not model_path.is_file() or not metadata_path.is_file():
        return False
    try:
        metadata = load_metadata()
        dataset_path = _root() / metadata["dataset"]["path"]
        return (
            metadata.get("artifact_sha256") == _sha256(model_path)
            and metadata.get("generator_source_sha256")
            == _normalized_text_sha256(source_path)
            and dataset_path.is_file()
            and metadata["dataset"]["sha256"] == _sha256(dataset_path)
        )
    except (KeyError, OSError, ValueError, json.JSONDecodeError):
        return False


@lru_cache(maxsize=1)
def load_generator() -> ConditionalGenerator:
    if not model_loaded():
        raise RuntimeError("conditional GAN artifact or integrity metadata is invalid")
    metadata = load_metadata()
    architecture = metadata["architecture"]
    generator = ConditionalGenerator(
        noise_dim=int(architecture["noise_dim"]),
        class_count=len(metadata["classes"]),
        feature_count=len(metadata["features"]),
        hidden_dim=int(architecture["hidden_dim"]),
    )
    payload = torch.load(
        _root() / MODEL_RELATIVE_PATH,
        map_location="cpu",
        weights_only=True,
    )
    generator.load_state_dict(payload["generator_state_dict"])
    generator.eval()
    return generator


def generate_conditional_samples(inputs: dict, params: dict) -> dict:
    condition = str(inputs.get("condition") or "").strip().lower()
    metadata = load_metadata()
    classes = list(metadata["classes"])
    if condition not in classes:
        raise ValueError(f"condition must be one of: {', '.join(classes)}")

    sample_count = inputs.get("sample_count", 1)
    if isinstance(sample_count, bool) or not isinstance(sample_count, int):
        raise ValueError("sample_count must be an integer")
    if sample_count < 1 or sample_count > 1000:
        raise ValueError("sample_count must be between 1 and 1000")

    seed = params.get("seed", 0)
    if isinstance(seed, bool) or not isinstance(seed, int):
        raise ValueError("seed must be an integer")
    if seed < 0 or seed > 2**63 - 1:
        raise ValueError("seed must be between 0 and 2^63-1")
    temperature = params.get("temperature", 1.0)
    if isinstance(temperature, bool) or not isinstance(temperature, (int, float)):
        raise ValueError("temperature must be a finite positive number")
    temperature = float(temperature)
    if not math.isfinite(temperature) or temperature <= 0.0 or temperature > 5.0:
        raise ValueError("temperature must be between 0 and 5")

    architecture = metadata["architecture"]
    class_index = classes.index(condition)
    random_source = torch.Generator(device="cpu")
    random_source.manual_seed(seed)
    noise = torch.randn(
        sample_count,
        int(architecture["noise_dim"]),
        generator=random_source,
        dtype=torch.float32,
    ) * temperature
    conditions = torch.zeros(sample_count, len(classes), dtype=torch.float32)
    conditions[:, class_index] = 1.0
    with torch.inference_mode():
        generated = load_generator()(noise, conditions).cpu().tolist()

    samples = [
        {
            name: round(float(value), 6)
            for name, value in zip(metadata["features"], row)
        }
        for row in generated
    ]
    return {
        "condition": condition,
        "samples": samples,
        "sample_count": sample_count,
        "generation_seed": seed,
        "data_status": "synthetic_generated_data",
        "model_version": metadata["model_version"],
        "model_runtime": {
            "backend": "pytorch_conditional_gan",
            "used": True,
            "artifact_sha256": metadata["artifact_sha256"],
        },
    }
