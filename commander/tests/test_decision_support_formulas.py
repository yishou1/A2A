from decision_support.capabilities.formulas import (
    logistic_probability,
    lstm_hidden_state,
    sigmoid,
)


def test_sigmoid_is_stable_for_large_values():
    assert sigmoid(1000.0) == 1.0
    assert sigmoid(-1000.0) == 0.0


def test_logistic_probability_uses_named_features():
    probability = logistic_probability(
        {"coverage": 0.5, "risk": 0.25},
        {"intercept": -1.0, "coverage": 2.0, "risk": 4.0},
    )
    assert round(probability, 6) == round(sigmoid(1.0), 6)


def test_scalar_lstm_reference_is_deterministic():
    weights = {name: (0.5, -0.25) for name in ("input", "forget", "output", "candidate")}
    recurrent = {name: 0.1 for name in weights}
    biases = {name: 0.0 for name in weights}
    sequence = [(0.2, 0.4), (0.6, 0.8)]

    first = lstm_hidden_state(sequence, weights, recurrent, biases)
    second = lstm_hidden_state(sequence, weights, recurrent, biases)

    assert first == second
    assert -1.0 <= first <= 1.0
