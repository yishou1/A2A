from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path

import numpy as np
import onnxruntime as ort
import torch
from safetensors.torch import load_file


ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT / "services"), str(ROOT)]

from a2a_algorithms_common.conditional_tabular_gan import ConditionalGenerator
from agent.inference.models.marl_ppo_scheduler import MARLPPOSchedulerNet


GAN_PACKAGE = ROOT / "examples/conditional_tabular_gan_onnx/1.0.0"
MARL_PACKAGE = ROOT / "examples/marl_ppo_task_scheduler_onnx/1.0.0"


def _load_golden(package: Path) -> tuple[dict, dict]:
    golden = package / "golden_cases"
    return (
        json.loads((golden / "case_001_input.json").read_text(encoding="utf-8")),
        json.loads((golden / "case_001_expected.json").read_text(encoding="utf-8")),
    )


def test_policy_generative_packages_are_complete_and_hash_verified() -> None:
    for package in (GAN_PACKAGE, MARL_PACKAGE):
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
        assert hashlib.sha256((package / "model.onnx").read_bytes()).hexdigest() == metadata[
            "artifact_sha256"
        ]
        source = ROOT / metadata["source_model"]
        assert hashlib.sha256(source.read_bytes()).hexdigest() == metadata["source_model_sha256"]
        source_metadata = ROOT / metadata["source_metadata"]
        assert hashlib.sha256(source_metadata.read_bytes()).hexdigest() == metadata[
            "source_metadata_sha256"
        ]
        script = ROOT / metadata["export_script"]
        normalized = script.read_text(encoding="utf-8").replace("\r\n", "\n")
        assert hashlib.sha256(normalized.encode("utf-8")).hexdigest() == metadata[
            "export_script_sha256"
        ]


def test_conditional_gan_onnx_matches_trained_pytorch_generator() -> None:
    request, expected = _load_golden(GAN_PACKAGE)
    noise = np.asarray(request["noise"], dtype=np.float32)
    conditions = np.asarray(request["conditions"], dtype=np.float32)
    source_metadata = json.loads(
        (ROOT / "models/conditional_tabular_gan.metadata.json").read_text(encoding="utf-8")
    )
    architecture = source_metadata["architecture"]
    model = ConditionalGenerator(
        noise_dim=int(architecture["noise_dim"]),
        class_count=len(source_metadata["classes"]),
        feature_count=len(source_metadata["features"]),
        hidden_dim=int(architecture["hidden_dim"]),
    )
    payload = torch.load(
        ROOT / "models/conditional_tabular_gan_generator.pt",
        map_location="cpu",
        weights_only=True,
    )
    model.load_state_dict(payload["generator_state_dict"], strict=True)
    model.eval()
    with torch.inference_mode():
        reference = model(torch.from_numpy(noise), torch.from_numpy(conditions)).numpy()

    session = ort.InferenceSession(
        str(GAN_PACKAGE / "model.onnx"), providers=["CPUExecutionProvider"]
    )
    actual = session.run(
        ["generated_features"], {"noise": noise, "conditions": conditions}
    )[0]
    np.testing.assert_allclose(actual, reference, rtol=0.0, atol=2e-6)
    np.testing.assert_allclose(
        actual,
        np.asarray(expected["generated_features"], dtype=np.float32),
        rtol=0.0,
        atol=1e-7,
    )
    assert np.all((actual >= 0.0) & (actual <= 1.0))


def test_marl_ppo_onnx_matches_trained_masked_actor_critic() -> None:
    request, expected = _load_golden(MARL_PACKAGE)
    observations = np.asarray(request["observations"], dtype=np.float32)
    masks = np.asarray(request["action_mask"], dtype=np.float32)
    model = MARLPPOSchedulerNet(obs_dim=104, n_actions=9, n_agents=8, hidden=128)
    model.load_state_dict(
        load_file(
            str(ROOT / "models/checkpoints/marl_ppo_scheduler_s.safetensors"),
            device="cpu",
        ),
        strict=True,
    )
    model.eval()
    with torch.inference_mode():
        logits, values = model(torch.from_numpy(observations))
        masked_logits = torch.where(
            torch.from_numpy(masks) > 0.5,
            logits,
            torch.full_like(logits, -1.0e9),
        )
        references = {
            "masked_action_logits": masked_logits.numpy(),
            "action_probabilities": torch.softmax(masked_logits, dim=1).numpy(),
            "selected_actions": torch.argmax(masked_logits, dim=1).numpy(),
            "state_values": values.squeeze(1).numpy(),
        }

    output_names = list(references)
    session = ort.InferenceSession(
        str(MARL_PACKAGE / "model.onnx"), providers=["CPUExecutionProvider"]
    )
    actual = dict(
        zip(
            output_names,
            session.run(
                output_names,
                {"observations": observations, "action_mask": masks},
            ),
        )
    )
    for name, reference in references.items():
        if name == "selected_actions":
            np.testing.assert_array_equal(actual[name], reference)
            np.testing.assert_array_equal(actual[name], expected[name])
        else:
            np.testing.assert_allclose(actual[name], reference, rtol=0.0, atol=2e-6)
            np.testing.assert_allclose(
                actual[name], np.asarray(expected[name], dtype=np.float32), rtol=0.0, atol=1e-7
            )
    for row, action in enumerate(actual["selected_actions"]):
        assert masks[row, int(action)] == 1.0
    np.testing.assert_allclose(actual["action_probabilities"].sum(axis=1), 1.0, atol=1e-6)
