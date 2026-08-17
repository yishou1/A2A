import pytest

from scripts.evaluate_dbn_calibration import evaluate_labeled_records


def test_dbn_calibration_report_contains_probability_metrics():
    records = [
        {
            "sequence_id": "low-sequence",
            "track_id": "low-track",
            "object_type": "ship",
            "base_score": 0.12,
            "factors": {
                "distance_factor": 0.1,
                "closing_factor": 0.05,
                "type_factor": 0.4,
                "anomaly_factor": 0.0,
                "quality_factor": 0.9,
            },
            "label": "low",
        },
        {
            "sequence_id": "high-sequence",
            "track_id": "high-track",
            "object_type": "aircraft",
            "base_score": 0.88,
            "factors": {
                "distance_factor": 0.95,
                "closing_factor": 0.92,
                "type_factor": 0.8,
                "anomaly_factor": 0.5,
                "quality_factor": 0.95,
            },
            "label": "high",
        },
    ]

    report = evaluate_labeled_records(records, bins=5)

    assert report["schema_version"] == "dbn_calibration_evaluation/v1"
    assert report["sample_count"] == 2
    assert report["sequence_count"] == 2
    assert 0.0 <= report["metrics"]["multiclass_brier"] <= 2.0
    assert 0.0 <= report["metrics"]["ece"] <= 1.0
    assert report["metrics"]["negative_log_likelihood"] >= 0.0
    assert 0.0 <= report["metrics"]["macro_f1"] <= 1.0
    assert set(report["confusion_matrix"]) == {"low", "medium", "high"}
    assert len(report["parameter_model"]["sha256"]) == 64


def test_dbn_calibration_rejects_unknown_labels():
    with pytest.raises(ValueError, match="label"):
        evaluate_labeled_records(
            [
                {
                    "sequence_id": "bad",
                    "track_id": "bad",
                    "object_type": "unknown",
                    "base_score": 0.5,
                    "factors": {},
                    "label": "critical",
                }
            ]
        )
