"""ONNX runtime helpers for decision-agent algorithm services."""

from __future__ import annotations

import json
import os

from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any

import numpy as np


DEFAULT_PROVIDERS = ["CPUExecutionProvider"]


@dataclass(frozen=True)
class OnnxScalarResult:
    value: float | None
    runtime: dict[str, Any]


def repo_root() -> Path:
    return Path(__file__).resolve().parents[2]


def model_dir() -> Path:
    configured = os.environ.get("DECISION_AGENT_MODEL_DIR")
    if configured:
        return Path(configured).expanduser().resolve()
    return repo_root() / "models"


def run_scalar_model(model_name: str, inputs: np.ndarray) -> OnnxScalarResult:
    model_path = model_dir() / model_name
    runtime = {
        "model": model_name,
        "backend": "onnxruntime",
        "fallback": False,
    }
    if not model_path.exists():
        runtime.update({"backend": "python_formula", "fallback": True, "reason": "onnx_model_not_found"})
        return OnnxScalarResult(None, runtime)
    try:
        session = _load_session(str(model_path))
        input_name = session.get_inputs()[0].name
        output = session.run(None, {input_name: inputs.astype(np.float32)})[0]
        runtime.update(
            {
                "model_path": str(model_path),
                "input_name": input_name,
                "output_name": session.get_outputs()[0].name,
            }
        )
        return OnnxScalarResult(float(output.reshape(-1)[0]), runtime)
    except Exception as exc:  # pragma: no cover - exercised through fallback behavior.
        runtime.update(
            {
                "backend": "python_formula",
                "fallback": True,
                "reason": f"onnx_runtime_failed:{type(exc).__name__}",
            }
        )
        return OnnxScalarResult(None, runtime)


def load_metadata(model_name: str) -> dict[str, Any]:
    metadata_path = model_dir() / model_name.replace(".onnx", ".metadata.json")
    if not metadata_path.exists():
        return {}
    return json.loads(metadata_path.read_text(encoding="utf-8"))


def feature_tensor(features: dict[str, float], feature_order: list[str]) -> np.ndarray:
    return np.asarray([[float(features.get(name, 0.0)) for name in feature_order]], dtype=np.float32)


def target_history_tensor(steps) -> np.ndarray:
    recent_steps = list(steps)[-12:]
    values = [
        [
            float(step.risk_score) / 100.0,
            float(step.probability),
            1.0 / max(float(step.priority), 1.0),
            float(step.resource_pressure),
        ]
        for step in recent_steps
    ]
    return np.asarray([values], dtype=np.float32)


@lru_cache(maxsize=8)
def _load_session(model_path: str):
    try:
        import onnxruntime as ort
    except ModuleNotFoundError as exc:
        raise RuntimeError("onnxruntime_not_installed") from exc
    return ort.InferenceSession(model_path, providers=DEFAULT_PROVIDERS)
