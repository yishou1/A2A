"""Framework-independent numerical reference implementations."""

from __future__ import annotations

from math import exp, tanh
from typing import Mapping, Sequence


def sigmoid(value: float) -> float:
    """Compute a numerically stable logistic sigmoid."""
    if value >= 0:
        z = exp(-value)
        return 1.0 / (1.0 + z)
    z = exp(value)
    return z / (1.0 + z)


def logistic_probability(
    features: Mapping[str, float],
    weights: Mapping[str, float],
) -> float:
    """Apply a named-feature linear model followed by a sigmoid."""
    value = float(weights.get("intercept", 0.0))
    value += sum(
        float(weight) * float(features.get(name, 0.0))
        for name, weight in weights.items()
        if name != "intercept"
    )
    return sigmoid(value)


def lstm_hidden_state(
    sequence: Sequence[Sequence[float]],
    input_weights: Mapping[str, Sequence[float]],
    recurrent_weights: Mapping[str, float],
    biases: Mapping[str, float],
) -> float:
    """Evaluate a scalar-state LSTM reference cell over a feature sequence."""
    hidden = 0.0
    cell = 0.0
    for vector in sequence:
        gates = {
            name: _gate(
                name,
                vector,
                hidden,
                input_weights,
                recurrent_weights,
                biases,
            )
            for name in ("input", "forget", "output", "candidate")
        }
        input_gate = sigmoid(gates["input"])
        forget_gate = sigmoid(gates["forget"])
        output_gate = sigmoid(gates["output"])
        candidate = tanh(gates["candidate"])
        cell = forget_gate * cell + input_gate * candidate
        hidden = output_gate * tanh(cell)
    return hidden


def _gate(
    name: str,
    vector: Sequence[float],
    hidden: float,
    input_weights: Mapping[str, Sequence[float]],
    recurrent_weights: Mapping[str, float],
    biases: Mapping[str, float],
) -> float:
    return (
        float(biases[name])
        + sum(
            float(weight) * float(value)
            for weight, value in zip(input_weights[name], vector)
        )
        + float(recurrent_weights[name]) * hidden
    )
