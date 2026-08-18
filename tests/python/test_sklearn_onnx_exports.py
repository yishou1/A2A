from __future__ import annotations

import hashlib
import json
from pathlib import Path

import joblib
import numpy as np
import onnxruntime as ort


ROOT = Path(__file__).resolve().parents[2]

SPECS = (
    {
        "algorithm_id": "threat_priority_random_forest_onnx",
        "source_model": "models/threat_priority_random_forest.joblib",
        "classes": ["low", "medium", "high"],
        "maximum_difference": 2e-6,
    },
    {
        "algorithm_id": "intent_gaussian_naive_bayes_onnx",
        "source_model": "models/intent_gaussian_naive_bayes.joblib",
        "classes": ["benign", "surveillance", "hostile"],
        "maximum_difference": 1e-6,
    },
)


def _load_golden(package: Path) -> tuple[dict, dict]:
    golden = package / "golden_cases"
    request = json.loads((golden / "case_001_input.json").read_text(encoding="utf-8"))
    expected = json.loads(
        (golden / "case_001_expected.json").read_text(encoding="utf-8")
    )
    return request, expected


def test_sklearn_onnx_packages_are_complete_and_hash_verified() -> None:
    for spec in SPECS:
        package = ROOT / "examples" / spec["algorithm_id"] / "1.0.0"
        for relative_path in (
            "algorithm_card.yaml",
            "input.schema.json",
            "output.schema.json",
            "tensor_contract.yaml",
            "preprocess.yaml",
            "postprocess.yaml",
            "model.onnx",
            "model.metadata.json",
            "golden_cases/case_001_input.json",
            "golden_cases/case_001_expected.json",
        ):
            assert (package / relative_path).is_file(), relative_path

        metadata = json.loads((package / "model.metadata.json").read_text(encoding="utf-8"))
        assert metadata["model_id"] == spec["algorithm_id"]
        assert metadata["class_order"] == spec["classes"]
        assert hashlib.sha256((package / "model.onnx").read_bytes()).hexdigest() == metadata[
            "artifact_sha256"
        ]
        source_model = ROOT / spec["source_model"]
        assert hashlib.sha256(source_model.read_bytes()).hexdigest() == metadata[
            "source_model_sha256"
        ]


def test_onnx_probabilities_match_source_sklearn_models() -> None:
    for spec in SPECS:
        package = ROOT / "examples" / spec["algorithm_id"] / "1.0.0"
        request, expected = _load_golden(package)
        features = np.asarray(request["features"], dtype=np.float32)
        source_model = joblib.load(ROOT / spec["source_model"])
        source_class_order = [str(value) for value in source_model.classes_]
        source_probabilities = source_model.predict_proba(features)
        source_probabilities = source_probabilities[
            :, [source_class_order.index(label) for label in spec["classes"]]
        ]

        session = ort.InferenceSession(
            str(package / "model.onnx"), providers=["CPUExecutionProvider"]
        )
        indices, probabilities = session.run(
            ["predicted_class_indices", "class_probabilities"],
            {"features": features},
        )

        np.testing.assert_allclose(
            probabilities,
            source_probabilities,
            rtol=0.0,
            atol=spec["maximum_difference"],
        )
        np.testing.assert_array_equal(indices, probabilities.argmax(axis=1))
        np.testing.assert_array_equal(
            indices,
            np.asarray(expected["predicted_class_indices"], dtype=np.int64),
        )
        np.testing.assert_allclose(
            probabilities,
            np.asarray(expected["class_probabilities"], dtype=np.float32),
            rtol=0.0,
            atol=1e-7,
        )
