"""Smoke tests for native TIA ONNX algorithm packages."""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pytest

REPO = Path(__file__).resolve().parents[1]
EXAMPLES = REPO / "examples"

PACKAGES = [
    "edl_evidential_verifier_onnx",
    "odconv_confidence_refiner_onnx",
    "supcon_meta_classifier_onnx",
    "marl_dynamic_router_onnx",
    "marl_ppo_task_scheduler_onnx",
    "decision_plan_recommender_onnx",
    "compliance_risk_scorer_onnx",
    "target_trend_predictor_onnx",
]


@pytest.mark.parametrize("algorithm_id", PACKAGES)
def test_onnx_package_layout(algorithm_id: str) -> None:
    root = EXAMPLES / algorithm_id / "1.0.0"
    required = [
        "algorithm_card.yaml",
        "input.schema.json",
        "output.schema.json",
        "model.onnx",
        "preprocess.yaml",
        "postprocess.yaml",
        "tensor_contract.yaml",
        "golden_cases/case_001_input.json",
        "golden_cases/case_001_expected.json",
    ]
    for rel in required:
        assert (root / rel).is_file(), f"missing {rel} in {algorithm_id}"
    card = (root / "algorithm_card.yaml").read_text(encoding="utf-8")
    assert "backend_type: onnx" in card


@pytest.mark.parametrize("algorithm_id", PACKAGES)
def test_onnx_runtime_smoke(algorithm_id: str) -> None:
    ort = pytest.importorskip("onnxruntime")
    root = EXAMPLES / algorithm_id / "1.0.0"
    sess = ort.InferenceSession(str(root / "model.onnx"), providers=["CPUExecutionProvider"])
    feeds = {
        inp.name: np.zeros([d if isinstance(d, int) else 1 for d in inp.shape], dtype=np.float32)
        for inp in sess.get_inputs()
    }
    outs = sess.run(None, feeds)
    assert outs
    assert all(np.asarray(o).size > 0 for o in outs)


def test_edl_golden_roundtrip() -> None:
    ort = pytest.importorskip("onnxruntime")
    root = EXAMPLES / "edl_evidential_verifier_onnx" / "1.0.0"
    feeds = json.loads((root / "golden_cases" / "case_001_input.json").read_text(encoding="utf-8"))
    expected = json.loads((root / "golden_cases" / "case_001_expected.json").read_text(encoding="utf-8"))
    sess = ort.InferenceSession(str(root / "model.onnx"), providers=["CPUExecutionProvider"])
    np_feeds = {k: np.asarray(v, dtype=np.float32) for k, v in feeds.items()}
    names = [o.name for o in sess.get_outputs()]
    outs = {n: v.tolist() for n, v in zip(names, sess.run(None, np_feeds))}
    for key in ("probability", "epistemic", "aleatoric"):
        assert np.allclose(outs[key], expected[key], atol=1e-5)
