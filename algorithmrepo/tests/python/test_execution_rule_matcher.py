from __future__ import annotations

import json
from pathlib import Path

from services.a2a_algorithms_common.association_rules import (
    load_training_records,
    mine_association_rules,
    rule_artifact_identity,
    rule_artifact_loaded,
)
from services.a2a_algorithms_common.service_predictors import (
    predict_execution_rule_matcher,
)


ROOT = Path(__file__).resolve().parents[2]


def test_confidence_measures_selected_consequent_frequency() -> None:
    records = [
        {"items": ["phase=strike"], "consequent": {"action": "a", "executor_role": "x"}},
        {"items": ["phase=strike"], "consequent": {"action": "a", "executor_role": "x"}},
        {"items": ["phase=strike"], "consequent": {"action": "b", "executor_role": "x"}},
    ]
    rules = mine_association_rules(records, min_confidence=0.6)
    phase_rule = next(rule for rule in rules if rule["antecedent"] == ["phase=strike"])
    assert phase_rule["consequent"]["action"] == "a"
    assert phase_rule["confidence"] == 0.6667


def test_persisted_rules_are_reproducible_and_hash_verified() -> None:
    expected = json.loads(
        (ROOT / "data/execution_control/processed/mined_rules.json").read_text(
            encoding="utf-8"
        )
    )
    actual = mine_association_rules(
        load_training_records(),
        min_support=0.15,
        min_confidence=0.6,
        max_itemset_size=4,
    )
    assert actual == expected
    assert rule_artifact_loaded() is True
    identity = rule_artifact_identity()
    assert identity["model_family"] == "apriori_association_rule_miner"
    assert len(identity["artifact_sha256"]) == 64


def test_independent_holdout_metrics_are_persisted() -> None:
    metadata = json.loads(
        (ROOT / "models/execution_rule_matcher.metadata.json").read_text(
            encoding="utf-8"
        )
    )
    evaluation = metadata["evaluation"]
    assert evaluation["method"] == "independent_reference_policy_holdout"
    assert evaluation["test_count"] == 12
    assert evaluation["action_accuracy"] == 1.0
    assert evaluation["executor_accuracy"] == 1.0
    assert evaluation["non_default_rule_coverage"] == 0.833333


def test_predictor_returns_verified_artifact_identity() -> None:
    outputs = predict_execution_rule_matcher(
        {
            "phase": "assault",
            "situation": {
                "threat_score": 0.82,
                "intel_confidence": 0.66,
                "resource_readiness": 0.58,
                "commander_decision": "RE-PLAN",
            },
        },
        {},
    )
    assert outputs["primary_rule"]["consequent"]["action"] == "hold_and_recon"
    assert outputs["model"]["model_id"] == "execution_rule_matcher"
