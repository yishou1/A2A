#!/usr/bin/env python3
"""Export the current decision-agent LR/LSTM weights as ONNX model assets.

The first exported models mirror the deterministic weights currently used by
the A2A agents. They are useful as reproducible bootstrap artifacts until real
training data is available.
"""

from __future__ import annotations

import json
import os
import struct
import sys

from pathlib import Path
from typing import Iterable


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
LSTM_STEP_FEATURES = [
    "risk_score",
    "probability",
    "inverse_priority",
    "resource_pressure",
]


def _ensure_a2a_import_path(repo_root: Path) -> None:
    configured = os.environ.get("A2A_REPO_ROOT")
    a2a_root = Path(configured).expanduser().resolve() if configured else repo_root.parent / "A2A"
    if str(a2a_root) not in sys.path:
        sys.path.insert(0, str(a2a_root))


def _varint(value: int) -> bytes:
    if value < 0:
        value += 1 << 64
    output = bytearray()
    while value > 0x7F:
        output.append((value & 0x7F) | 0x80)
        value >>= 7
    output.append(value)
    return bytes(output)


def _key(field_number: int, wire_type: int) -> bytes:
    return _varint((field_number << 3) | wire_type)


def _field_varint(field_number: int, value: int) -> bytes:
    return _key(field_number, 0) + _varint(value)


def _field_fixed32(field_number: int, value: float) -> bytes:
    return _key(field_number, 5) + struct.pack("<f", float(value))


def _field_bytes(field_number: int, value: bytes) -> bytes:
    return _key(field_number, 2) + _varint(len(value)) + value


def _field_string(field_number: int, value: str) -> bytes:
    return _field_bytes(field_number, value.encode("utf-8"))


def _message(fields: Iterable[bytes]) -> bytes:
    return b"".join(fields)


def _attribute_int(name: str, value: int) -> bytes:
    return _message(
        [
            _field_string(1, name),
            _field_varint(3, value),
            _field_varint(20, 2),
        ]
    )


def _tensor(name: str, dims: list[int], values: list[float]) -> bytes:
    fields: list[bytes] = []
    for dim in dims:
        fields.append(_field_varint(1, dim))
    fields.append(_field_varint(2, 1))  # TensorProto.FLOAT
    for value in values:
        fields.append(_field_fixed32(4, value))
    fields.append(_field_string(8, name))
    return _message(fields)


def _tensor_int64(name: str, dims: list[int], values: list[int]) -> bytes:
    fields: list[bytes] = []
    for dim in dims:
        fields.append(_field_varint(1, dim))
    fields.append(_field_varint(2, 7))  # TensorProto.INT64
    for value in values:
        fields.append(_field_varint(7, value))
    fields.append(_field_string(8, name))
    return _message(fields)


def _shape(dims: list[int]) -> bytes:
    dim_messages = [
        _message([_field_varint(1, dim)])
        for dim in dims
    ]
    return _message(_field_bytes(1, dim) for dim in dim_messages)


def _value_info(name: str, dims: list[int], elem_type: int = 1) -> bytes:
    tensor_type = _message(
        [
            _field_varint(1, elem_type),
            _field_bytes(2, _shape(dims)),
        ]
    )
    type_proto = _message([_field_bytes(1, tensor_type)])
    return _message(
        [
            _field_string(1, name),
            _field_bytes(2, type_proto),
        ]
    )


def _node(
    op_type: str,
    inputs: list[str],
    outputs: list[str],
    *,
    name: str | None = None,
    attributes: list[bytes] | None = None,
) -> bytes:
    fields: list[bytes] = []
    fields.extend(_field_string(1, item) for item in inputs)
    fields.extend(_field_string(2, item) for item in outputs)
    if name:
        fields.append(_field_string(3, name))
    fields.append(_field_string(4, op_type))
    fields.extend(_field_bytes(5, item) for item in attributes or [])
    return _message(fields)


def _model(graph_name: str, inputs: list[bytes], outputs: list[bytes], nodes: list[bytes], initializers: list[bytes]) -> bytes:
    graph_fields: list[bytes] = []
    graph_fields.extend(_field_bytes(1, node) for node in nodes)
    graph_fields.append(_field_string(2, graph_name))
    graph_fields.extend(_field_bytes(5, initializer) for initializer in initializers)
    graph_fields.extend(_field_bytes(11, item) for item in inputs)
    graph_fields.extend(_field_bytes(12, item) for item in outputs)
    graph = _message(graph_fields)
    opset = _message([_field_varint(2, 13)])
    return _message(
        [
            _field_varint(1, 8),
            _field_string(2, "a2a-decision-agent-exporter"),
            _field_string(5, "bootstrap"),
            _field_bytes(7, graph),
            _field_bytes(8, opset),
        ]
    )


def _write_linear_sigmoid_model(
    output_path: Path,
    *,
    graph_name: str,
    feature_count: int,
    weights: list[float],
    intercept: float,
) -> None:
    nodes = [
        _node("MatMul", ["features", "weights"], ["linear"], name="linear_matmul"),
        _node("Add", ["linear", "bias"], ["logit"], name="linear_bias"),
        _node("Sigmoid", ["logit"], ["probability"], name="sigmoid_probability"),
    ]
    initializers = [
        _tensor("weights", [feature_count, 1], weights),
        _tensor("bias", [1], [intercept]),
    ]
    model = _model(
        graph_name,
        [_value_info("features", [1, feature_count])],
        [_value_info("probability", [1, 1])],
        nodes,
        initializers,
    )
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_bytes(model)


def _write_lstm_trend_model(output_path: Path, *, input_weights: dict, recurrent_weights: dict, biases: dict) -> None:
    nodes: list[bytes] = []
    initializers: list[bytes] = [
        _tensor("hidden0", [1, 1], [0.0]),
        _tensor("cell0", [1, 1], [0.0]),
        _tensor("one", [1, 1], [1.0]),
        _tensor("two", [1, 1], [2.0]),
    ]

    for gate_name, weights in input_weights.items():
        initializers.append(_tensor(f"{gate_name}_input_weights", [4, 1], list(weights)))
        initializers.append(_tensor(f"{gate_name}_recurrent_weight", [1, 1], [recurrent_weights[gate_name]]))
        initializers.append(_tensor(f"{gate_name}_bias", [1, 1], [biases[gate_name]]))

    hidden = "hidden0"
    cell = "cell0"
    for index in range(12):
        step = f"step_{index}"
        initializers.append(_tensor_int64(f"step_index_{index}", [], [index]))
        nodes.append(
            _node(
                "Gather",
                ["sequence", f"step_index_{index}"],
                [step],
                name=f"gather_step_{index}",
                attributes=[_attribute_int("axis", 1)],
            )
        )
        gate_outputs: dict[str, str] = {}
        for gate_name in ("input", "forget", "output", "candidate"):
            matmul = f"{gate_name}_{index}_matmul"
            recurrent = f"{gate_name}_{index}_recurrent"
            pre_bias = f"{gate_name}_{index}_pre_bias"
            linear = f"{gate_name}_{index}_linear"
            activated = f"{gate_name}_{index}_activated"
            nodes.extend(
                [
                    _node("MatMul", [step, f"{gate_name}_input_weights"], [matmul], name=f"{gate_name}_{index}_matmul"),
                    _node("Mul", [hidden, f"{gate_name}_recurrent_weight"], [recurrent], name=f"{gate_name}_{index}_recurrent"),
                    _node("Add", [matmul, recurrent], [pre_bias], name=f"{gate_name}_{index}_pre_bias"),
                    _node("Add", [pre_bias, f"{gate_name}_bias"], [linear], name=f"{gate_name}_{index}_bias"),
                ]
            )
            if gate_name == "candidate":
                nodes.append(_node("Tanh", [linear], [activated], name=f"{gate_name}_{index}_tanh"))
            else:
                nodes.append(_node("Sigmoid", [linear], [activated], name=f"{gate_name}_{index}_sigmoid"))
            gate_outputs[gate_name] = activated

        forget_cell = f"forget_cell_{index}"
        input_candidate = f"input_candidate_{index}"
        next_cell = f"cell_{index + 1}"
        tanh_cell = f"tanh_cell_{index + 1}"
        next_hidden = f"hidden_{index + 1}"
        nodes.extend(
            [
                _node("Mul", [gate_outputs["forget"], cell], [forget_cell], name=f"forget_cell_{index}"),
                _node("Mul", [gate_outputs["input"], gate_outputs["candidate"]], [input_candidate], name=f"input_candidate_{index}"),
                _node("Add", [forget_cell, input_candidate], [next_cell], name=f"cell_update_{index}"),
                _node("Tanh", [next_cell], [tanh_cell], name=f"cell_tanh_{index}"),
                _node("Mul", [gate_outputs["output"], tanh_cell], [next_hidden], name=f"hidden_update_{index}"),
            ]
        )
        hidden = next_hidden
        cell = next_cell

    shifted = "trend_shifted"
    nodes.extend(
        [
            _node("Add", [hidden, "one"], [shifted], name="trend_shift"),
            _node("Div", [shifted, "two"], ["trend_score"], name="trend_scale"),
        ]
    )
    model = _model(
        "decision_planning_lstm_trend",
        [_value_info("sequence", [1, 12, 4])],
        [_value_info("trend_score", [1, 1])],
        nodes,
        initializers,
    )
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_bytes(model)


def _write_metadata(path: Path, payload: dict) -> None:
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def main() -> None:
    repo_root = Path(__file__).resolve().parent.parent
    _ensure_a2a_import_path(repo_root)

    from decision_agents.compliance_authorization.local_algorithm import COMPLIANCE_LOGISTIC_WEIGHTS
    from decision_agents.decision_planning.local_algorithm import (
        LSTM_BIASES,
        LSTM_INPUT_WEIGHTS,
        LSTM_RECURRENT_WEIGHTS,
        MAX_LSTM_STEPS,
        PLANNING_LOGISTIC_WEIGHTS,
    )

    model_dir = repo_root / "models"
    planning_lr = model_dir / "decision_planning_lr.onnx"
    compliance_lr = model_dir / "compliance_authorization_lr.onnx"
    planning_lstm = model_dir / "decision_planning_lstm.onnx"

    _write_linear_sigmoid_model(
        planning_lr,
        graph_name="decision_planning_lr",
        feature_count=len(PLANNING_LR_FEATURES),
        weights=[PLANNING_LOGISTIC_WEIGHTS[name] for name in PLANNING_LR_FEATURES],
        intercept=PLANNING_LOGISTIC_WEIGHTS["intercept"],
    )
    _write_metadata(
        planning_lr.with_suffix(".metadata.json"),
        {
            "model_source": "a2a_decision_planning_bootstrap_lr",
            "model_type": "logistic_regression",
            "format": "onnx",
            "input_name": "features",
            "output_name": "probability",
            "feature_order": PLANNING_LR_FEATURES,
            "weights": {name: PLANNING_LOGISTIC_WEIGHTS[name] for name in PLANNING_LR_FEATURES},
            "intercept": PLANNING_LOGISTIC_WEIGHTS["intercept"],
            "training_status": "bootstrap_from_existing_agent_weights",
        },
    )

    _write_linear_sigmoid_model(
        compliance_lr,
        graph_name="compliance_authorization_lr",
        feature_count=len(COMPLIANCE_LR_FEATURES),
        weights=[COMPLIANCE_LOGISTIC_WEIGHTS[name] for name in COMPLIANCE_LR_FEATURES],
        intercept=COMPLIANCE_LOGISTIC_WEIGHTS["intercept"],
    )
    _write_metadata(
        compliance_lr.with_suffix(".metadata.json"),
        {
            "model_source": "a2a_compliance_authorization_bootstrap_lr",
            "model_type": "logistic_regression",
            "format": "onnx",
            "input_name": "features",
            "output_name": "probability",
            "feature_order": COMPLIANCE_LR_FEATURES,
            "weights": {name: COMPLIANCE_LOGISTIC_WEIGHTS[name] for name in COMPLIANCE_LR_FEATURES},
            "intercept": COMPLIANCE_LOGISTIC_WEIGHTS["intercept"],
            "training_status": "bootstrap_from_existing_agent_weights",
        },
    )

    _write_lstm_trend_model(
        planning_lstm,
        input_weights=LSTM_INPUT_WEIGHTS,
        recurrent_weights=LSTM_RECURRENT_WEIGHTS,
        biases=LSTM_BIASES,
    )
    _write_metadata(
        planning_lstm.with_suffix(".metadata.json"),
        {
            "model_source": "a2a_decision_planning_bootstrap_lstm",
            "model_type": "unrolled_lstm_cell",
            "format": "onnx",
            "input_name": "sequence",
            "output_name": "trend_score",
            "max_steps": MAX_LSTM_STEPS,
            "step_feature_order": LSTM_STEP_FEATURES,
            "input_weights": LSTM_INPUT_WEIGHTS,
            "recurrent_weights": LSTM_RECURRENT_WEIGHTS,
            "biases": LSTM_BIASES,
            "training_status": "bootstrap_from_existing_agent_weights",
        },
    )

    print(f"exported {planning_lr}")
    print(f"exported {compliance_lr}")
    print(f"exported {planning_lstm}")


if __name__ == "__main__":
    main()
