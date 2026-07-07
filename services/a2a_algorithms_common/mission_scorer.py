"""Mission completion scoring using persisted SC2LE proxy models."""
from __future__ import annotations

import json
import pickle
from pathlib import Path
from typing import Optional

from .mission_feature_adapter import bundle_to_vector, normalize_feature_bundle
from .mission_feature_schema import (
    DEFAULT_MODEL_METADATA_PATH,
    DEFAULT_MODEL_PATH,
    FEATURE_VERSION,
    MISSION_COMPLETION_THRESHOLD,
)
from .pickle_compat import RandomForestRegressor, register_pickle_aliases


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[2]


def _default_model_path(path: str | None = None) -> Path:
    if path:
        return Path(path).expanduser().resolve()
    return _repo_root() / DEFAULT_MODEL_PATH


def _default_metadata_path(path: str | None = None) -> Path:
    if path:
        return Path(path).expanduser().resolve()
    return _repo_root() / DEFAULT_MODEL_METADATA_PATH


def load_mission_model(model_path: str | Path | None = None) -> RandomForestRegressor:
    path = _default_model_path(str(model_path) if model_path is not None else None)
    if not path.exists():
        raise FileNotFoundError(f"Mission model not found: {path}")
    register_pickle_aliases()
    with path.open("rb") as handle:
        return pickle.load(handle)


def load_model_metadata(metadata_path: str | Path | None = None) -> dict:
    path = _default_metadata_path(str(metadata_path) if metadata_path is not None else None)
    if not path.exists():
        raise FileNotFoundError(f"Mission model metadata not found: {path}")
    return json.loads(path.read_text(encoding="utf-8"))


def score_mission(
    feature_bundle: dict,
    *,
    model_path: str | Path | None = None,
    metadata_path: str | Path | None = None,
) -> dict:
    """Score mission completion probability from a feature bundle."""
    if feature_bundle.get("assessment_status") == "insufficient_data":
        return {
            "mission_completion": None,
            "mission_result": None,
            "threshold": MISSION_COMPLETION_THRESHOLD,
            "model_source": "sc2le_proxy",
            "feature_version": FEATURE_VERSION,
            "assessment_status": "insufficient_data",
            "missing_fields": list(feature_bundle.get("missing_fields") or []),
            "warnings": list(feature_bundle.get("warnings") or []),
        }

    metadata = load_model_metadata(metadata_path)
    model = load_mission_model(model_path)
    normalized = normalize_feature_bundle(feature_bundle, metadata=metadata)
    vector = bundle_to_vector(normalized)
    completion = float(model.predict_one(vector))
    threshold = float(metadata.get("threshold") or MISSION_COMPLETION_THRESHOLD)
    return {
        "mission_completion": round(completion, 4),
        "mission_result": "success" if completion >= threshold else "failure",
        "threshold": threshold,
        "model_source": str(metadata.get("model_source") or "sc2le_proxy"),
        "feature_version": str(metadata.get("feature_version") or FEATURE_VERSION),
        "assessment_status": "proxy_model_estimate",
        "warnings": list(normalized.get("warnings") or []),
        "feature_values": normalized.get("values") or {},
    }


def predict_mission_assessment(
    feature_bundle: dict,
    *,
    model_path: Optional[str | Path] = None,
    metadata_path: Optional[str | Path] = None,
) -> dict:
    """Backward-compatible alias for score_mission."""
    return score_mission(feature_bundle, model_path=model_path, metadata_path=metadata_path)
