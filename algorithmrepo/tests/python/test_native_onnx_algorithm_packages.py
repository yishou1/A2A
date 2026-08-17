from __future__ import annotations

import hashlib
import json
import unittest
from pathlib import Path

import numpy as np
import onnx
import onnxruntime as ort


ROOT = Path(__file__).resolve().parents[2]

PACKAGES = (
    {
        "algorithm_id": "decision_plan_recommender_onnx",
        "input_name": "features",
        "input_shape": [1, 8],
        "output_name": "probability",
        "output_key": "recommendation_probability",
        "output_shape": [1, 1],
    },
    {
        "algorithm_id": "target_trend_predictor_onnx",
        "input_name": "sequence",
        "input_shape": [1, 12, 4],
        "output_name": "trend_score",
        "output_key": "trend_score",
        "output_shape": [1, 1],
    },
    {
        "algorithm_id": "compliance_risk_scorer_onnx",
        "input_name": "features",
        "input_shape": [1, 6],
        "output_name": "probability",
        "output_key": "risk_probability",
        "output_shape": [1, 1],
    },
)


class NativeOnnxAlgorithmPackageTests(unittest.TestCase):
    def test_package_files_and_golden_inference(self) -> None:
        for spec in PACKAGES:
            with self.subTest(algorithm_id=spec["algorithm_id"]):
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
                    self.assertTrue((package / relative_path).is_file(), relative_path)

                request = json.loads(
                    (package / "golden_cases/case_001_input.json").read_text(encoding="utf-8")
                )
                expected = json.loads(
                    (package / "golden_cases/case_001_expected.json").read_text(encoding="utf-8")
                )
                session = ort.InferenceSession(
                    str(package / "model.onnx"), providers=["CPUExecutionProvider"]
                )

                model_input = session.get_inputs()[0]
                model_output = session.get_outputs()[0]
                self.assertEqual(model_input.name, spec["input_name"])
                self.assertEqual(model_input.shape, spec["input_shape"])
                self.assertEqual(model_output.name, spec["output_name"])
                self.assertEqual(model_output.shape, spec["output_shape"])

                actual = session.run(
                    [spec["output_name"]],
                    {
                        spec["input_name"]: np.asarray(
                            request[spec["input_name"]], dtype=np.float32
                        )
                    },
                )[0]
                np.testing.assert_allclose(
                    actual,
                    np.asarray(expected[spec["output_key"]], dtype=np.float32),
                    rtol=1e-6,
                    atol=1e-7,
                )

    def test_trained_logistic_packages_have_reproducible_provenance(self) -> None:
        output_keys = {
            "decision_plan_recommender_onnx": "recommendation_probability",
            "compliance_risk_scorer_onnx": "risk_probability",
        }
        for algorithm_id, output_key in output_keys.items():
            with self.subTest(algorithm_id=algorithm_id):
                package = ROOT / "examples" / algorithm_id / "1.0.0"
                model_path = package / "model.onnx"
                metadata = json.loads(
                    (package / "model.metadata.json").read_text(encoding="utf-8")
                )
                self.assertEqual(metadata["model_family"], "trained_logistic_regression")
                self.assertEqual(
                    hashlib.sha256(model_path.read_bytes()).hexdigest(),
                    metadata["artifact_sha256"],
                )
                dataset_path = ROOT / metadata["dataset"]["path"]
                self.assertEqual(
                    hashlib.sha256(dataset_path.read_bytes()).hexdigest(),
                    metadata["dataset"]["sha256"],
                )
                self.assertGreaterEqual(metadata["evaluation"]["accuracy"], 0.85)
                self.assertGreaterEqual(metadata["evaluation"]["roc_auc"], 0.95)
                self.assertLess(
                    metadata["evaluation"][
                        "onnx_sklearn_max_abs_probability_difference"
                    ],
                    1e-5,
                )

                graph = onnx.load(model_path).graph
                self.assertEqual(
                    [node.op_type for node in graph.node],
                    ["MatMul", "Add", "Sigmoid"],
                )
                request = json.loads(
                    (package / "golden_cases/case_001_input.json").read_text(
                        encoding="utf-8"
                    )
                )
                expected = json.loads(
                    (package / "golden_cases/case_001_expected.json").read_text(
                        encoding="utf-8"
                    )
                )
                row = request["features"][0]
                logit = metadata["intercept"] + sum(
                    float(value) * float(metadata["coefficients"][name])
                    for name, value in zip(metadata["feature_order"], row)
                )
                probability = 1.0 / (1.0 + np.exp(-logit))
                self.assertAlmostEqual(
                    float(expected[output_key][0][0]),
                    float(probability),
                    places=5,
                )

    def test_text_contract_fixture_encodes_probabilities_as_logits(self) -> None:
        package = ROOT / "examples" / "onnx_text_classifier" / "1.0.0"
        expected = json.loads(
            (package / "golden_cases" / "case_001_expected.json").read_text(
                encoding="utf-8"
            )
        )
        session = ort.InferenceSession(
            str(package / "model.onnx"), providers=["CPUExecutionProvider"]
        )
        raw = session.run(
            ["logits"],
            {
                "input_ids": np.asarray([[101, 102]], dtype=np.int64),
                "attention_mask": np.asarray([[1, 1]], dtype=np.int64),
            },
        )[0].reshape(-1)
        probabilities = np.exp(raw - raw.max())
        probabilities /= probabilities.sum()

        self.assertEqual(expected["label"], "task")
        self.assertAlmostEqual(
            float(probabilities[0]), expected["confidence"], places=6
        )


if __name__ == "__main__":
    unittest.main()
