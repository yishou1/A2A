from __future__ import annotations

import sys

import json

from math import exp
from pathlib import Path

import numpy as np
import onnxruntime as ort


ROOT = Path(__file__).resolve().parents[2]
A2A_ROOT = ROOT.parent / "A2A"
SERVICES = ROOT / "services"
sys.path.insert(0, str(SERVICES))
sys.path.insert(0, str(A2A_ROOT))

from decision_agents.compliance_authorization.local_algorithm import (  # noqa: E402
    COMPLIANCE_LOGISTIC_WEIGHTS,
)
from decision_agents.decision_planning.local_algorithm import (  # noqa: E402
    LSTM_BIASES,
    LSTM_INPUT_WEIGHTS,
    LSTM_RECURRENT_WEIGHTS,
    PLANNING_LOGISTIC_WEIGHTS,
)
from a2a_algorithms_common.decision_agent_predictors import (  # noqa: E402
    predict_compliance_authorization_core,
    predict_decision_planning_core,
)


PLANNING_LR_FEATURES = [
    "coverage",
    "risk_alignment",
    "resource_efficiency",
    "constraint_fit",
    "authorization",
    "lstm_trend",
    "priority",
    "objective_fit",
]
COMPLIANCE_LR_FEATURES = [
    "blocking_violation_count",
    "warning_violation_count",
    "authorization_status_score",
    "authorization_out_of_scope",
    "rag_evidence_count",
    "law_of_war_rule_hit",
]


def test_decision_planning_lr_onnx_matches_agent_weights():
    features = np.array([[0.7, 0.8, 0.6, 0.9, 1.0, 0.55, 0.5, 0.85]], dtype=np.float32)
    actual = _run_onnx(ROOT / "models" / "decision_planning_lr.onnx", features)
    expected = _logistic_expected(features[0], PLANNING_LR_FEATURES, PLANNING_LOGISTIC_WEIGHTS)
    assert round(actual, 6) == round(expected, 6)


def test_compliance_authorization_lr_onnx_matches_agent_weights():
    features = np.array([[0.0, 0.2, 0.55, 0.0, 0.0, 1.0]], dtype=np.float32)
    actual = _run_onnx(ROOT / "models" / "compliance_authorization_lr.onnx", features)
    expected = _logistic_expected(features[0], COMPLIANCE_LR_FEATURES, COMPLIANCE_LOGISTIC_WEIGHTS)
    assert round(actual, 6) == round(expected, 6)


def test_decision_planning_lstm_onnx_matches_agent_weights():
    sequence = np.array(
        [
            [
                [0.2, 0.5, 1.0, 0.1],
                [0.4, 0.6, 0.5, 0.2],
                [0.7, 0.8, 0.33333334, 0.4],
            ]
            + [[0.0, 0.0, 0.0, 0.0]] * 9
        ],
        dtype=np.float32,
    )
    actual = _run_onnx(ROOT / "models" / "decision_planning_lstm.onnx", sequence)
    expected = _lstm_expected(sequence)
    assert round(actual, 6) == round(expected, 6)


def test_decision_agent_core_predictors_use_onnx_runtime():
    planning_payload = _load_case("decision_planning_core")
    compliance_payload = _load_case("compliance_authorization_core")

    planning_outputs = predict_decision_planning_core(
        planning_payload["inputs"],
        planning_payload.get("params", {}),
    )
    compliance_outputs = predict_compliance_authorization_core(
        compliance_payload["inputs"],
        compliance_payload.get("params", {}),
    )

    planning_lr_runtime = planning_outputs["model_runtime"]["decision_planning_lr"]
    assert any(item["used"] is True for item in planning_lr_runtime["plans"])
    assert {item["backend"] for item in planning_lr_runtime["plans"]} == {"onnxruntime"}
    assert planning_outputs["model_runtime"]["decision_planning_lstm"]["targets"][0]["fallback"] is True

    compliance_runtime = compliance_outputs["model_runtime"]["compliance_authorization_lr"]
    assert compliance_runtime["used"] is True
    assert compliance_runtime["backend"] == "onnxruntime"


def test_decision_agent_core_predictors_fallback_when_models_missing(monkeypatch, tmp_path):
    monkeypatch.setenv("DECISION_AGENT_MODEL_DIR", str(tmp_path))
    planning_payload = _load_case("decision_planning_core")
    compliance_payload = _load_case("compliance_authorization_core")

    planning_outputs = predict_decision_planning_core(
        planning_payload["inputs"],
        planning_payload.get("params", {}),
    )
    compliance_outputs = predict_compliance_authorization_core(
        compliance_payload["inputs"],
        compliance_payload.get("params", {}),
    )

    planning_lr_runtime = planning_outputs["model_runtime"]["decision_planning_lr"]
    assert all(item["fallback"] is True for item in planning_lr_runtime["plans"])
    assert all(item["backend"] == "python_formula" for item in planning_lr_runtime["plans"])
    compliance_runtime = compliance_outputs["model_runtime"]["compliance_authorization_lr"]
    assert compliance_runtime["fallback"] is True
    assert compliance_runtime["backend"] == "python_formula"


def _run_onnx(model_path: Path, inputs: np.ndarray) -> float:
    session = ort.InferenceSession(str(model_path), providers=["CPUExecutionProvider"])
    output = session.run(None, {session.get_inputs()[0].name: inputs})[0]
    return float(output.reshape(-1)[0])


def _load_case(algorithm_id: str) -> dict:
    path = ROOT / "examples" / algorithm_id / "1.0.0" / "golden_cases" / "case_001_request.json"
    return json.loads(path.read_text(encoding="utf-8"))


def _logistic_expected(row: np.ndarray, names: list[str], weights: dict[str, float]) -> float:
    z = weights["intercept"] + sum(weights[name] * float(row[index]) for index, name in enumerate(names))
    return _sigmoid(z)


def _lstm_expected(sequence: np.ndarray) -> float:
    hidden = 0.0
    cell = 0.0
    for vector in sequence[0]:
        gates = {}
        for name in ("input", "forget", "output", "candidate"):
            z = (
                LSTM_BIASES[name]
                + sum(LSTM_INPUT_WEIGHTS[name][index] * float(vector[index]) for index in range(4))
                + LSTM_RECURRENT_WEIGHTS[name] * hidden
            )
            gates[name] = np.tanh(z) if name == "candidate" else _sigmoid(z)
        cell = gates["forget"] * cell + gates["input"] * gates["candidate"]
        hidden = gates["output"] * np.tanh(cell)
    return (hidden + 1.0) / 2.0


def _sigmoid(value: float) -> float:
    if value >= 0:
        z = exp(-value)
        return 1.0 / (1.0 + z)
    z = exp(value)
    return z / (1.0 + z)
