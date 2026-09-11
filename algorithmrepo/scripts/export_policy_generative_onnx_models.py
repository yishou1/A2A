#!/usr/bin/env python3
"""Export the trained M08 generator and M13 policy as native ONNX graphs."""
from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path

import numpy as np
import onnx
import onnxruntime as ort
import torch
import torch.nn as nn
from safetensors.torch import load_file


ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "services"), str(ROOT)]

from a2a_algorithms_common.conditional_tabular_gan import ConditionalGenerator
from agent.inference.models.marl_ppo_scheduler import MARLPPOSchedulerNet
from agent.training.battlefield_scheduling_env import BattlefieldSchedulingEnv
from scripts.train_marl_ppo_scheduler import situation_from_dict


VERSION = "1.0.0"

GAN_SOURCE = ROOT / "models/conditional_tabular_gan_generator.pt"
GAN_SOURCE_METADATA = ROOT / "models/conditional_tabular_gan.metadata.json"
GAN_PACKAGE = ROOT / "examples/conditional_tabular_gan_onnx" / VERSION

MARL_SOURCE = ROOT / "models/checkpoints/marl_ppo_scheduler_s.safetensors"
MARL_SOURCE_METADATA = ROOT / "models/checkpoints/marl_ppo_scheduler_s.metadata.json"
MARL_PACKAGE = ROOT / "examples/marl_ppo_task_scheduler_onnx" / VERSION


class MARLPolicyOnnx(nn.Module):
    def __init__(self, model: MARLPPOSchedulerNet) -> None:
        super().__init__()
        self.model = model

    def forward(
        self, observations: torch.Tensor, action_mask: torch.Tensor
    ) -> tuple[torch.Tensor, torch.Tensor, torch.Tensor, torch.Tensor]:
        logits, values = self.model(observations)
        allowed = action_mask > 0.5
        masked_logits = torch.where(
            allowed, logits, torch.full_like(logits, -1.0e9)
        )
        probabilities = torch.softmax(masked_logits, dim=1)
        actions = torch.argmax(masked_logits, dim=1)
        return masked_logits, probabilities, actions, values.squeeze(1)


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def normalized_text_sha256(path: Path) -> str:
    text = path.read_text(encoding="utf-8").replace("\r\n", "\n")
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def write_json(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def export_model(
    model: nn.Module,
    inputs: tuple[torch.Tensor, ...],
    path: Path,
    input_names: list[str],
    output_names: list[str],
    dynamic_axes: dict[str, dict[int, str]],
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    torch.onnx.export(
        model,
        inputs,
        str(path),
        export_params=True,
        opset_version=17,
        do_constant_folding=True,
        input_names=input_names,
        output_names=output_names,
        dynamic_axes=dynamic_axes,
        dynamo=False,
    )
    exported = onnx.load(path)
    onnx.checker.check_model(exported)


def metadata_base(
    *, algorithm_id: str, family: str, package: Path, source: Path,
    source_metadata: Path, maximum_difference: float, compared_outputs: list[str],
) -> dict:
    script = Path(__file__).resolve()
    artifact = package / "model.onnx"
    return {
        "model_id": algorithm_id,
        "model_version": VERSION,
        "model_family": family,
        "format": "onnx",
        "artifact_path": str(artifact.relative_to(ROOT)).replace("\\", "/"),
        "artifact_sha256": sha256(artifact),
        "export_script": str(script.relative_to(ROOT)).replace("\\", "/"),
        "export_script_sha256": normalized_text_sha256(script),
        "source_model": str(source.relative_to(ROOT)).replace("\\", "/"),
        "source_model_sha256": sha256(source),
        "source_metadata": str(source_metadata.relative_to(ROOT)).replace("\\", "/"),
        "source_metadata_sha256": sha256(source_metadata),
        "precision": "float32",
        "opset": 17,
        "equivalence": {
            "reference": "source PyTorch artifact in evaluation mode",
            "compared_outputs": compared_outputs,
            "maximum_absolute_difference": maximum_difference,
        },
        "runtime_versions": {
            "numpy": np.__version__,
            "onnx": onnx.__version__,
            "onnxruntime": ort.__version__,
            "torch": torch.__version__,
        },
    }


def export_gan() -> dict:
    metadata = json.loads(GAN_SOURCE_METADATA.read_text(encoding="utf-8"))
    if sha256(GAN_SOURCE) != metadata["artifact_sha256"]:
        raise ValueError("Conditional GAN source artifact SHA256 mismatch")
    architecture = metadata["architecture"]
    generator = ConditionalGenerator(
        noise_dim=int(architecture["noise_dim"]),
        class_count=len(metadata["classes"]),
        feature_count=len(metadata["features"]),
        hidden_dim=int(architecture["hidden_dim"]),
    )
    payload = torch.load(GAN_SOURCE, map_location="cpu", weights_only=True)
    generator.load_state_dict(payload["generator_state_dict"], strict=True)
    generator.eval()

    random_source = torch.Generator(device="cpu").manual_seed(20260818)
    noise = torch.randn(3, 8, generator=random_source, dtype=torch.float32)
    conditions = torch.eye(3, dtype=torch.float32)
    path = GAN_PACKAGE / "model.onnx"
    export_model(
        generator,
        (noise, conditions),
        path,
        ["noise", "conditions"],
        ["generated_features"],
        {
            "noise": {0: "batch"},
            "conditions": {0: "batch"},
            "generated_features": {0: "batch"},
        },
    )
    session = ort.InferenceSession(str(path), providers=["CPUExecutionProvider"])
    actual = session.run(
        ["generated_features"],
        {"noise": noise.numpy(), "conditions": conditions.numpy()},
    )[0]
    with torch.inference_mode():
        reference = generator(noise, conditions).numpy()
    error = float(np.max(np.abs(actual - reference)))
    write_json(
        GAN_PACKAGE / "golden_cases/case_001_input.json",
        {"noise": noise.tolist(), "conditions": conditions.tolist()},
    )
    write_json(
        GAN_PACKAGE / "golden_cases/case_001_expected.json",
        {"generated_features": actual.tolist()},
    )
    exported_metadata = metadata_base(
        algorithm_id="conditional_tabular_gan_onnx",
        family="onnx_conditional_tabular_generator",
        package=GAN_PACKAGE,
        source=GAN_SOURCE,
        source_metadata=GAN_SOURCE_METADATA,
        maximum_difference=error,
        compared_outputs=["generated_features"],
    )
    exported_metadata.update(
        {
            "noise_dimension": architecture["noise_dim"],
            "condition_order": metadata["classes"],
            "feature_order": metadata["features"],
            "parameter_count": architecture["generator_parameter_count"],
            "source_evaluation": metadata["evaluation"],
            "limitations": [
                "The caller supplies noise and one-hot conditions; seed and temperature handling remain in the full Python service.",
                "Outputs are synthetic development data and must not be represented as real sensor observations.",
                metadata["dataset"]["limitation"],
            ],
        }
    )
    write_json(GAN_PACKAGE / "model.metadata.json", exported_metadata)
    return {"maximum_absolute_difference": error, "artifact_bytes": path.stat().st_size}


def export_marl() -> dict:
    metadata = json.loads(MARL_SOURCE_METADATA.read_text(encoding="utf-8"))
    if sha256(MARL_SOURCE) != metadata["artifact_sha256"]:
        raise ValueError("MARL-PPO source checkpoint SHA256 mismatch")
    architecture = metadata["architecture"]
    env = BattlefieldSchedulingEnv()
    model = MARLPPOSchedulerNet(
        obs_dim=int(architecture["observation_dimension"]),
        n_actions=int(architecture["action_count"]),
        n_agents=int(architecture["maximum_agent_count"]),
        hidden=int(architecture["hidden_dimension"]),
    )
    model.load_state_dict(load_file(str(MARL_SOURCE), device="cpu"), strict=True)
    wrapper = MARLPolicyOnnx(model.eval()).eval()

    holdout = json.loads((ROOT / metadata["dataset"]["path"]).read_text(encoding="utf-8"))
    situation = situation_from_dict(holdout[0])
    env.reset(situation)
    observations = torch.from_numpy(
        np.stack([env.build_agent_obs(index) for index in range(2)]).astype(np.float32)
    )
    valid_target_count = min(len(situation.targets), int(architecture["action_count"]) - 1)
    masks = torch.zeros(2, int(architecture["action_count"]), dtype=torch.float32)
    masks[:, : valid_target_count + 1] = 1.0
    if valid_target_count >= 1:
        masks[1, 1] = 0.0

    path = MARL_PACKAGE / "model.onnx"
    output_names = [
        "masked_action_logits",
        "action_probabilities",
        "selected_actions",
        "state_values",
    ]
    export_model(
        wrapper,
        (observations, masks),
        path,
        ["observations", "action_mask"],
        output_names,
        {
            "observations": {0: "agents"},
            "action_mask": {0: "agents"},
            "masked_action_logits": {0: "agents"},
            "action_probabilities": {0: "agents"},
            "selected_actions": {0: "agents"},
            "state_values": {0: "agents"},
        },
    )
    session = ort.InferenceSession(str(path), providers=["CPUExecutionProvider"])
    actual_outputs = session.run(
        output_names,
        {"observations": observations.numpy(), "action_mask": masks.numpy()},
    )
    with torch.inference_mode():
        reference_outputs = wrapper(observations, masks)
    error = max(
        float(np.max(np.abs(actual.astype(np.float32) - reference.numpy().astype(np.float32))))
        for actual, reference in zip(actual_outputs, reference_outputs)
    )
    write_json(
        MARL_PACKAGE / "golden_cases/case_001_input.json",
        {"observations": observations.tolist(), "action_mask": masks.tolist()},
    )
    write_json(
        MARL_PACKAGE / "golden_cases/case_001_expected.json",
        {name: np.asarray(value).tolist() for name, value in zip(output_names, actual_outputs)},
    )
    exported_metadata = metadata_base(
        algorithm_id="marl_ppo_task_scheduler_onnx",
        family="onnx_parameter_shared_marl_ppo_actor_critic",
        package=MARL_PACKAGE,
        source=MARL_SOURCE,
        source_metadata=MARL_SOURCE_METADATA,
        maximum_difference=error,
        compared_outputs=output_names,
    )
    exported_metadata.update(
        {
            "observation_dimension": architecture["observation_dimension"],
            "action_count": architecture["action_count"],
            "maximum_agent_count": architecture["maximum_agent_count"],
            "parameter_count": architecture["parameter_count"],
            "source_evaluation": metadata["evaluation"],
            "limitations": [
                "The caller constructs 104-dimensional per-agent observations and valid-action masks.",
                "Returns independent deterministic policy decisions; sequential cross-agent de-duplication and assignment object construction remain in the full Python service.",
                metadata["dataset"]["limitation"],
            ],
        }
    )
    write_json(MARL_PACKAGE / "model.metadata.json", exported_metadata)
    return {"maximum_absolute_difference": error, "artifact_bytes": path.stat().st_size}


def export_all() -> None:
    results = {
        "conditional_tabular_gan_onnx": export_gan(),
        "marl_ppo_task_scheduler_onnx": export_marl(),
    }
    if any(result["maximum_absolute_difference"] > 1e-5 for result in results.values()):
        raise RuntimeError(f"ONNX equivalence tolerance exceeded: {results}")
    print(json.dumps(results, indent=2))


if __name__ == "__main__":
    export_all()
