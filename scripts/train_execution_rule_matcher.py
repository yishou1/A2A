#!/usr/bin/env python3
"""Mine and evaluate the reproducible M02 association-rule artifact."""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "services"))

from a2a_algorithms_common.association_rules import (  # noqa: E402
    choose_primary_rule,
    discretize_situation,
    match_rules,
    mine_association_rules,
    save_rules,
)


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def normalized_text_sha256(path: Path) -> str:
    text = path.read_text(encoding="utf-8").replace("\r\n", "\n")
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def read_records(path: Path) -> list[dict]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, list) or not payload:
        raise ValueError(f"dataset must be a non-empty JSON list: {path}")
    return payload


def evaluate(rules: list[dict], records: list[dict]) -> dict:
    correct_action = 0
    correct_executor = 0
    non_default = 0
    case_results: list[dict] = []
    for record in records:
        phase = str(record["phase"])
        items = discretize_situation(dict(record["situation"]), phase)
        matched = match_rules(items, rules, phase=phase)
        primary = choose_primary_rule(
            matched,
            default_executor_role="artillery" if phase == "strike" else "assault",
        )
        actual = primary["consequent"]
        expected = record["expected"]
        action_ok = actual["action"] == expected["action"]
        executor_ok = actual["executor_role"] == expected["executor_role"]
        correct_action += int(action_ok)
        correct_executor += int(executor_ok)
        non_default += int(primary["rule_id"] != "RULE-DEFAULT")
        case_results.append(
            {
                "case_id": record["case_id"],
                "expected_action": expected["action"],
                "predicted_action": actual["action"],
                "rule_id": primary["rule_id"],
                "correct": action_ok and executor_ok,
            }
        )
    total = len(records)
    return {
        "method": "independent_reference_policy_holdout",
        "test_count": total,
        "action_accuracy": round(correct_action / total, 6),
        "executor_accuracy": round(correct_executor / total, 6),
        "non_default_rule_coverage": round(non_default / total, 6),
        "case_results": case_results,
    }


def train(
    training_path: Path,
    evaluation_path: Path,
    rules_path: Path,
    metadata_path: Path,
) -> dict:
    training_records = read_records(training_path)
    evaluation_records = read_records(evaluation_path)
    mining_parameters = {
        "min_support": 0.15,
        "min_confidence": 0.6,
        "max_itemset_size": 4,
    }
    rules = mine_association_rules(training_records, **mining_parameters)
    save_rules(rules, rules_path)
    evaluation = evaluate(rules, evaluation_records)

    implementation_path = ROOT / "services" / "a2a_algorithms_common" / "association_rules.py"
    script_path = Path(__file__).resolve()
    metadata = {
        "model_id": "execution_rule_matcher",
        "model_version": "1.0.0",
        "model_family": "apriori_association_rule_miner",
        "artifact_path": str(rules_path.relative_to(ROOT)).replace("\\", "/"),
        "artifact_sha256": sha256(rules_path),
        "rule_count": len(rules),
        "implementation_path": str(implementation_path.relative_to(ROOT)).replace("\\", "/"),
        "implementation_sha256": normalized_text_sha256(implementation_path),
        "training_script": str(script_path.relative_to(ROOT)).replace("\\", "/"),
        "training_script_sha256": normalized_text_sha256(script_path),
        "training_dataset": {
            "path": str(training_path.relative_to(ROOT)).replace("\\", "/"),
            "sha256": sha256(training_path),
            "row_count": len(training_records),
            "source": "repository_reference_policy_fixtures",
        },
        "evaluation_dataset": {
            "path": str(evaluation_path.relative_to(ROOT)).replace("\\", "/"),
            "sha256": sha256(evaluation_path),
            "row_count": len(evaluation_records),
            "source": "independent_repository_reference_policy_holdout",
            "limitation": "Synthetic policy scenarios only; not an operational battlefield benchmark.",
        },
        "mining_parameters": mining_parameters,
        "evaluation": evaluation,
        "runtime": {"language": "python", "external_ml_dependencies": []},
    }
    metadata_path.parent.mkdir(parents=True, exist_ok=True)
    metadata_path.write_text(
        json.dumps(metadata, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return metadata


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--training-path",
        type=Path,
        default=ROOT / "data" / "execution_control" / "fixtures" / "rule_training_records.json",
    )
    parser.add_argument(
        "--evaluation-path",
        type=Path,
        default=ROOT / "data" / "execution_control" / "fixtures" / "rule_evaluation_records.json",
    )
    parser.add_argument(
        "--rules-path",
        type=Path,
        default=ROOT / "data" / "execution_control" / "processed" / "mined_rules.json",
    )
    parser.add_argument(
        "--metadata-path",
        type=Path,
        default=ROOT / "models" / "execution_rule_matcher.metadata.json",
    )
    args = parser.parse_args()
    metadata = train(
        args.training_path.resolve(),
        args.evaluation_path.resolve(),
        args.rules_path.resolve(),
        args.metadata_path.resolve(),
    )
    print(json.dumps(metadata, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
