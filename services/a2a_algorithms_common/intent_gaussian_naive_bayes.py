"""Frozen M07 Gaussian Naive Bayes inference with posterior probabilities."""
from __future__ import annotations

from functools import lru_cache
import hashlib
import json
import math
from pathlib import Path
from typing import Any

import joblib


MODEL_VERSION = "1.0.0"
MODEL_RELATIVE_PATH = Path("models/intent_gaussian_naive_bayes.joblib")
METADATA_RELATIVE_PATH = Path("models/intent_gaussian_naive_bayes.metadata.json")


def _root() -> Path:
    return Path(__file__).resolve().parents[2]


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


@lru_cache(maxsize=1)
def load_metadata() -> dict[str, Any]:
    return json.loads((_root() / METADATA_RELATIVE_PATH).read_text(encoding="utf-8"))


def model_loaded() -> bool:
    model_path = _root() / MODEL_RELATIVE_PATH
    metadata_path = _root() / METADATA_RELATIVE_PATH
    if not model_path.is_file() or not metadata_path.is_file():
        return False
    try:
        metadata = load_metadata()
        return (
            metadata.get("model_version") == MODEL_VERSION
            and metadata.get("artifact_sha256") == _sha256(model_path)
        )
    except (OSError, ValueError, json.JSONDecodeError):
        return False


@lru_cache(maxsize=1)
def load_model() -> Any:
    if not model_loaded():
        raise RuntimeError("GaussianNB artifact or its integrity metadata is invalid")
    return joblib.load(_root() / MODEL_RELATIVE_PATH)


def _feature(observation: dict, name: str) -> float:
    value = observation.get(name)
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{name} must be a finite number between 0 and 1")
    numeric = float(value)
    if not math.isfinite(numeric) or numeric < 0.0 or numeric > 1.0:
        raise ValueError(f"{name} must be a finite number between 0 and 1")
    return numeric


def predict_intents(inputs: dict, params: dict) -> dict:
    observations = inputs.get("observations")
    if not isinstance(observations, list) or not observations:
        raise ValueError("observations must be a non-empty array")
    if len(observations) > 10000:
        raise ValueError("observations cannot contain more than 10000 items")

    metadata = load_metadata()
    model = load_model()
    rows: list[list[float]] = []
    observation_ids: list[str] = []
    for index, observation in enumerate(observations):
        if not isinstance(observation, dict):
            raise ValueError(f"observations[{index}] must be an object")
        observation_id = str(observation.get("observation_id") or "").strip()
        if not observation_id:
            raise ValueError(f"observations[{index}].observation_id is required")
        observation_ids.append(observation_id)
        rows.append([_feature(observation, name) for name in metadata["features"]])

    probabilities = model.predict_proba(rows)
    model_classes = [str(value) for value in model.classes_]
    predictions: list[dict] = []
    for observation_id, probability_row in zip(observation_ids, probabilities):
        posterior = {
            class_name: round(float(probability_row[model_classes.index(class_name)]), 6)
            for class_name in metadata["classes"]
        }
        intent = max(
            metadata["classes"],
            key=lambda name: (posterior[name], -metadata["classes"].index(name)),
        )
        predictions.append(
            {
                "observation_id": observation_id,
                "intent": intent,
                "confidence": posterior[intent],
                "posterior_probabilities": posterior,
            }
        )

    return {
        "predictions": predictions,
        "observation_count": len(predictions),
        "model_version": metadata["model_version"],
        "assessment_scope": "synthetic_reference_model",
        "model_runtime": {
            "backend": "sklearn_gaussian_naive_bayes",
            "used": True,
            "artifact_sha256": metadata["artifact_sha256"],
        },
    }
