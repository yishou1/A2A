from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path

import numpy as np
import onnx
import onnxruntime as ort


ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from scripts.train_target_trend_predictor import evaluate_onnx  # noqa: E402


PACKAGE = ROOT / "examples" / "target_trend_predictor_onnx" / "1.0.0"
MODEL = PACKAGE / "model.onnx"
METADATA = PACKAGE / "model.metadata.json"


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def test_trained_artifact_and_dataset_hashes_are_verified() -> None:
    metadata = json.loads(METADATA.read_text(encoding="utf-8"))
    assert metadata["model_family"] == "trained_lstm_time_series_regressor"
    assert metadata["architecture"]["parameter_count"] == 1425
    assert _sha256(MODEL) == metadata["artifact_sha256"]
    assert _sha256(ROOT / metadata["training_script"]) == metadata[
        "training_script_sha256"
    ]
    for artifact in metadata["dataset"]["artifacts"].values():
        assert _sha256(ROOT / artifact["path"]) == artifact["sha256"]


def test_onnx_reproduces_holdout_metrics_and_beats_persistence() -> None:
    metadata = json.loads(METADATA.read_text(encoding="utf-8"))
    artifacts = metadata["dataset"]["artifacts"]
    inputs = np.load(ROOT / artifacts["holdout_inputs"]["path"], allow_pickle=False)
    targets = np.load(ROOT / artifacts["holdout_targets"]["path"], allow_pickle=False)
    actual = evaluate_onnx(MODEL, inputs, targets)
    assert actual == {
        key: value
        for key, value in metadata["evaluation"].items()
        if key != "method"
    }
    assert actual["trained_lstm"]["rmse"] < actual[
        "last_observation_baseline"
    ]["rmse"]
    assert actual["trained_lstm"]["r2"] >= 0.85


def test_model_has_fixed_contract_and_real_lstm_graph() -> None:
    session = ort.InferenceSession(str(MODEL), providers=["CPUExecutionProvider"])
    assert session.get_inputs()[0].name == "sequence"
    assert session.get_inputs()[0].shape == [1, 12, 4]
    assert session.get_outputs()[0].name == "trend_score"
    assert session.get_outputs()[0].shape == [1, 1]
    operation_types = {node.op_type for node in onnx.load(MODEL).graph.node}
    assert {"LSTM", "Gemm", "Sigmoid"}.issubset(operation_types)
