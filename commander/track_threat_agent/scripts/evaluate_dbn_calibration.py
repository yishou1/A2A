#!/usr/bin/env python3
"""Evaluate DBN attention-state probabilities on expert-labeled sequences."""

from __future__ import annotations

import argparse
import json
import math
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

from app.dbn_threat_evaluator import DBNThreatEvaluator
from app.models import TrackState


STATES = DBNThreatEvaluator.STATES


def evaluate_labeled_records(
    records: Iterable[Mapping[str, Any]],
    *,
    parameter_path: str | Path | None = None,
    bins: int = 10,
    max_brier: float | None = None,
    max_ece: float | None = None,
) -> dict[str, Any]:
    if bins <= 0:
        raise ValueError("bins must be positive")
    evaluators: dict[str, DBNThreatEvaluator] = {}
    probability_rows: list[dict[str, float]] = []
    labels: list[str] = []
    predictions: list[str] = []
    sequence_ids: set[str] = set()
    parameter_model: dict[str, Any] | None = None

    for index, record in enumerate(records):
        label = str(record.get("label", "")).lower()
        if label not in STATES:
            raise ValueError(f"record {index} label must be one of {STATES}")
        sequence_id = str(record.get("sequence_id") or record.get("track_id") or index)
        evaluator = evaluators.setdefault(
            sequence_id,
            DBNThreatEvaluator(parameter_path=parameter_path),
        )
        result = evaluator.update(
            _track_from_record(record, index),
            float(record.get("base_score", 0.0)),
            {key: float(value) for key, value in dict(record.get("factors") or {}).items()},
        )
        probabilities = {
            state: float(result["posterior"][state])
            for state in STATES
        }
        probability_rows.append(probabilities)
        labels.append(label)
        predictions.append(max(probabilities, key=probabilities.get))
        sequence_ids.add(sequence_id)
        parameter_model = dict(result["parameter_model"])

    if not labels:
        raise ValueError("labeled DBN evaluation set must not be empty")

    brier = sum(
        sum(
            (row[state] - float(state == label)) ** 2
            for state in STATES
        )
        for row, label in zip(probability_rows, labels)
    ) / len(labels)
    negative_log_likelihood = -sum(
        math.log(max(row[label], 1e-12))
        for row, label in zip(probability_rows, labels)
    ) / len(labels)
    ece, calibration_bins = _expected_calibration_error(
        probability_rows,
        labels,
        predictions,
        bins,
    )
    confusion = {
        actual: {predicted: 0 for predicted in STATES}
        for actual in STATES
    }
    for actual, predicted in zip(labels, predictions):
        confusion[actual][predicted] += 1
    accuracy = sum(actual == predicted for actual, predicted in zip(labels, predictions)) / len(labels)
    macro_f1 = sum(_f1_for_state(confusion, state) for state in STATES) / len(STATES)

    checks: dict[str, bool] = {}
    if max_brier is not None:
        checks["multiclass_brier"] = brier <= max_brier
    if max_ece is not None:
        checks["ece"] = ece <= max_ece
    return {
        "schema_version": "dbn_calibration_evaluation/v1",
        "sample_count": len(labels),
        "sequence_count": len(sequence_ids),
        "states": list(STATES),
        "parameter_model": parameter_model,
        "metrics": {
            "multiclass_brier": round(brier, 6),
            "ece": round(ece, 6),
            "negative_log_likelihood": round(negative_log_likelihood, 6),
            "accuracy": round(accuracy, 6),
            "macro_f1": round(macro_f1, 6),
        },
        "calibration_bins": calibration_bins,
        "confusion_matrix": confusion,
        "gate": {
            "configured": bool(checks),
            "passed": all(checks.values()) if checks else None,
            "checks": checks,
        },
        "safety_boundary": (
            "Labels and probabilities describe situation-attention priority only; "
            "no weapon, guidance, attack or engagement decision is evaluated."
        ),
    }


def _track_from_record(record: Mapping[str, Any], index: int) -> TrackState:
    timestamp = float(record.get("timestamp", index))
    return TrackState(
        track_id=str(record.get("track_id") or f"dbn-eval-{index}"),
        object_type=str(record.get("object_type", "unknown")),
        lat=float(record.get("lat", 0.0)),
        lon=float(record.get("lon", 0.0)),
        alt=float(record.get("alt", 0.0)),
        speed=float(record.get("speed", 0.0)),
        heading=float(record.get("heading", 0.0)),
        vx=float(record.get("vx", 0.0)),
        vy=float(record.get("vy", 0.0)),
        track_quality=float(record.get("track_quality", 0.9)),
        last_update_time=timestamp,
        missed_count=int(record.get("missed_count", 0)),
        history_path=[],
        predicted_path=[],
        metadata=dict(record.get("metadata") or {}),
    )


def _expected_calibration_error(
    rows: Sequence[Mapping[str, float]],
    labels: Sequence[str],
    predictions: Sequence[str],
    bins: int,
) -> tuple[float, list[dict[str, Any]]]:
    bucket_values: list[list[tuple[float, bool]]] = [[] for _ in range(bins)]
    for row, label, prediction in zip(rows, labels, predictions):
        confidence = max(float(value) for value in row.values())
        bucket = min(int(confidence * bins), bins - 1)
        bucket_values[bucket].append((confidence, prediction == label))
    total = len(labels)
    ece = 0.0
    report = []
    for index, values in enumerate(bucket_values):
        if not values:
            continue
        confidence = sum(value[0] for value in values) / len(values)
        accuracy = sum(value[1] for value in values) / len(values)
        ece += len(values) / total * abs(accuracy - confidence)
        report.append(
            {
                "lower": round(index / bins, 4),
                "upper": round((index + 1) / bins, 4),
                "count": len(values),
                "mean_confidence": round(confidence, 6),
                "accuracy": round(accuracy, 6),
            }
        )
    return ece, report


def _f1_for_state(confusion: Mapping[str, Mapping[str, int]], state: str) -> float:
    true_positive = confusion[state][state]
    false_positive = sum(confusion[actual][state] for actual in STATES if actual != state)
    false_negative = sum(confusion[state][predicted] for predicted in STATES if predicted != state)
    denominator = 2 * true_positive + false_positive + false_negative
    return 2 * true_positive / denominator if denominator else 0.0


def _read_records(path: Path) -> list[Mapping[str, Any]]:
    text = path.read_text(encoding="utf-8")
    stripped = text.lstrip()
    if stripped.startswith("["):
        payload = json.loads(text)
        if not isinstance(payload, list):
            raise ValueError("JSON evaluation input must be an array")
        return payload
    return [json.loads(line) for line in text.splitlines() if line.strip()]


def main() -> None:
    parser = argparse.ArgumentParser(description="Evaluate DBN probabilities on labeled JSON/JSONL.")
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--parameters", type=Path)
    parser.add_argument("--bins", type=int, default=10)
    parser.add_argument("--max-brier", type=float)
    parser.add_argument("--max-ece", type=float)
    args = parser.parse_args()
    report = evaluate_labeled_records(
        _read_records(args.input),
        parameter_path=args.parameters,
        bins=args.bins,
        max_brier=args.max_brier,
        max_ece=args.max_ece,
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(report, ensure_ascii=False, indent=2))
    if report["gate"]["configured"] and not report["gate"]["passed"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
