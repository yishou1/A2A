"""Frozen M05 random-forest inference for structured threat prioritization."""
from __future__ import annotations

from functools import lru_cache
import hashlib
import json
import math
from pathlib import Path
from typing import Any

import joblib


MODEL_VERSION = "1.0.0"
MODEL_RELATIVE_PATH = Path("models/threat_priority_random_forest.joblib")
METADATA_RELATIVE_PATH = Path("models/threat_priority_random_forest.metadata.json")


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
        raise RuntimeError("random-forest artifact or its integrity metadata is invalid")
    return joblib.load(_root() / MODEL_RELATIVE_PATH)


def _finite_feature(target: dict, name: str, minimum: float, maximum: float) -> float:
    value = target.get(name)
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{name} must be a finite number")
    numeric = float(value)
    if not math.isfinite(numeric) or numeric < minimum or numeric > maximum:
        raise ValueError(f"{name} must be between {minimum} and {maximum}")
    return numeric


def _features(target: dict, feature_names: list[str]) -> list[float]:
    ranges = {
        "threat_score": (0.0, 1.0),
        "distance_km": (0.0, 10000.0),
        "speed_mps": (0.0, 5000.0),
        "asset_value": (0.0, 1.0),
        "intel_confidence": (0.0, 1.0),
    }
    return [
        _finite_feature(target, name, *ranges[name])
        for name in feature_names
    ]


def predict_threat_priorities(inputs: dict, params: dict) -> dict:
    targets = inputs.get("targets")
    if not isinstance(targets, list) or not targets:
        raise ValueError("targets must be a non-empty array")
    if len(targets) > 10000:
        raise ValueError("targets cannot contain more than 10000 items")

    metadata = load_metadata()
    model = load_model()
    feature_names = list(metadata["features"])
    rows: list[list[float]] = []
    target_ids: list[str] = []
    for index, target in enumerate(targets):
        if not isinstance(target, dict):
            raise ValueError(f"targets[{index}] must be an object")
        target_id = str(target.get("target_id") or "").strip()
        if not target_id:
            raise ValueError(f"targets[{index}].target_id is required")
        target_ids.append(target_id)
        rows.append(_features(target, feature_names))

    probabilities = model.predict_proba(rows)
    model_classes = [str(value) for value in model.classes_]
    results: list[dict] = []
    for target_id, probability_row in zip(target_ids, probabilities):
        scores = {
            class_name: round(float(probability_row[model_classes.index(class_name)]), 6)
            for class_name in metadata["classes"]
        }
        label = max(metadata["classes"], key=lambda item: (scores[item], -metadata["classes"].index(item)))
        results.append(
            {
                "target_id": target_id,
                "priority": label,
                "confidence": scores[label],
                "class_probabilities": scores,
            }
        )

    if bool(params.get("sort_descending", False)):
        rank = {"high": 2, "medium": 1, "low": 0}
        results.sort(
            key=lambda item: (-rank[item["priority"]], -item["confidence"], item["target_id"])
        )

    return {
        "priorities": results,
        "target_count": len(results),
        "model_version": metadata["model_version"],
        "assessment_scope": "repository_reference_model",
        "model_runtime": {
            "backend": "sklearn_random_forest",
            "used": True,
            "artifact_sha256": metadata["artifact_sha256"],
        },
    }
