#!/usr/bin/env python3
"""Export convertible TIA/scheduling model heads as native ONNX algorithm packages.

Follows the zsl/algorithmrepo ONNX package layout:
  examples/<id>_onnx/1.0.0/{algorithm_card.yaml, schemas, preprocess/postprocess,
                            tensor_contract.yaml, model.onnx, golden_cases/}

Also writes copies under models/ for reuse by Python services.
"""

from __future__ import annotations

import json
import shutil
import sys
from pathlib import Path
from typing import Any

import torch
import torch.nn as nn
import torch.nn.functional as F

REPO_ROOT = Path(__file__).resolve().parents[1]
CHECKPOINT_DIR = REPO_ROOT / "models" / "checkpoints"
MODELS_DIR = REPO_ROOT / "models"
EXAMPLES_DIR = REPO_ROOT / "examples"
ONNX_OPSET = 17


def _load_state(module: nn.Module, path: Path) -> nn.Module:
    if not path.is_file():
        raise FileNotFoundError(path)
    try:
        state = torch.load(path, map_location="cpu", weights_only=True)
    except TypeError:
        state = torch.load(path, map_location="cpu")
    if isinstance(state, dict) and "state_dict" in state:
        state = state["state_dict"]
    if isinstance(state, dict) and "model" in state and isinstance(state["model"], dict):
        state = state["model"]
    module.load_state_dict(state, strict=False)
    module.eval()
    return module


def _export(
    model: nn.Module,
    *,
    output_path: Path,
    args: tuple[torch.Tensor, ...],
    input_names: list[str],
    output_names: list[str],
    dynamic_axes: dict[str, dict[int, str]] | None = None,
) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    torch.onnx.export(
        model,
        args if len(args) > 1 else args[0],
        str(output_path),
        input_names=input_names,
        output_names=output_names,
        dynamic_axes=dynamic_axes,
        opset_version=ONNX_OPSET,
        do_constant_folding=True,
    )


def _write_json(path: Path, payload: Any, *, compact: bool = False) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if compact:
        path.write_text(json.dumps(payload, ensure_ascii=False, separators=(",", ":")) + "\n", encoding="utf-8")
    else:
        path.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def _write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text.rstrip() + "\n", encoding="utf-8")


def _ort_run(model_path: Path, feeds: dict[str, Any]) -> dict[str, Any]:
    import onnxruntime as ort
    import numpy as np

    sess = ort.InferenceSession(str(model_path), providers=["CPUExecutionProvider"])
    np_feeds = {k: np.asarray(v, dtype=np.float32) for k, v in feeds.items()}
    outs = sess.run(None, np_feeds)
    names = [o.name for o in sess.get_outputs()]
    return {name: out.tolist() for name, out in zip(names, outs)}


def _package_root(algorithm_id: str) -> Path:
    return EXAMPLES_DIR / algorithm_id / "1.0.0"


def _install_model(src: Path, package_dir: Path) -> None:
    dst = package_dir / "model.onnx"
    shutil.copy2(src, dst)


# ---------------------------------------------------------------------------
# Export wrappers (fixed tensor I/O for algolib ONNX runner)
# ---------------------------------------------------------------------------


class EvidentialHeadOnnx(nn.Module):
    """EDL core without Digamma (portable ONNX); aleatoric = categorical entropy."""

    def __init__(self, head: nn.Module):
        super().__init__()
        self.net = head.net
        self.num_classes = int(head.num_classes)

    def forward(self, features: torch.Tensor) -> tuple[torch.Tensor, torch.Tensor, torch.Tensor]:
        logits = self.net(features)
        evidence = F.softplus(logits)
        alpha = evidence + 1.0
        s = alpha.sum(dim=-1, keepdim=True)
        probability = alpha / s
        epistemic = self.num_classes / s
        aleatoric = -(probability * torch.log(probability.clamp_min(1e-8))).sum(dim=-1, keepdim=True)
        return probability, epistemic, aleatoric


class ODConvRefinerOnnx(nn.Module):
    def __init__(self, refiner: nn.Module):
        super().__init__()
        self.stem = refiner.stem
        self.head = refiner.head

    def forward(self, crops: torch.Tensor, base_conf: torch.Tensor) -> torch.Tensor:
        feat = self.stem(crops).flatten(1)
        x = torch.cat([feat, base_conf], dim=1)
        delta = self.head(x)
        refined = torch.clamp(0.5 * base_conf + 0.5 * delta, 0.05, 0.99)
        return refined


class SupConMetaOnnx(nn.Module):
    """Prototype softmax over fixed learned prototypes (no support-shot meta update)."""

    def __init__(self, net: nn.Module, temperature: float = 0.07):
        super().__init__()
        self.encoder = net.encoder
        self.prototypes = nn.Parameter(net.prototypes.detach().clone())
        self.temperature = float(temperature)

    def forward(self, features: torch.Tensor) -> torch.Tensor:
        z = F.normalize(self.encoder(features), dim=-1)
        protos = F.normalize(self.prototypes, dim=-1)
        sims = (z @ protos.T) / self.temperature
        return F.softmax(sims, dim=-1)


class MARLPolicyOnnx(nn.Module):
    def __init__(self, net: nn.Module):
        super().__init__()
        self.net = net.net
        self.channel_head = net.channel_head
        self.reliability_head = net.reliability_head

    def forward(self, state: torch.Tensor) -> tuple[torch.Tensor, torch.Tensor]:
        h = self.net(state)
        channel_logits = self.channel_head(h)
        reliability = torch.sigmoid(self.reliability_head(h))
        return channel_logits, reliability


class MARLPPOActorOnnx(nn.Module):
    def __init__(self, net: nn.Module):
        super().__init__()
        self.encoder = net.encoder
        self.actor = net.actor
        self.critic = net.critic

    def forward(self, obs: torch.Tensor) -> tuple[torch.Tensor, torch.Tensor]:
        h = self.encoder(obs)
        return self.actor(h), self.critic(h)


# ---------------------------------------------------------------------------
# Package templates
# ---------------------------------------------------------------------------


def _write_onnx_package(
    algorithm_id: str,
    *,
    display_name: str,
    task_family: str,
    capabilities: list[str],
    summary: str,
    when_to_use: list[str],
    when_not_to_use: list[str],
    input_description: str,
    output_description: str,
    input_schema: dict[str, Any],
    output_schema: dict[str, Any],
    tensor_contract: str,
    preprocess: str,
    postprocess: str,
    model_src: Path,
    golden_input: dict[str, Any],
    golden_expected: dict[str, Any],
    metadata: dict[str, Any],
    readme: str,
    param_count: int,
    flops_text: str,
    input_shape_note: str,
    compact_golden_input: bool = False,
) -> Path:
    root = _package_root(algorithm_id)
    if root.exists():
        shutil.rmtree(root)
    root.mkdir(parents=True)

    card = f"""algorithm_id: {algorithm_id}
version: 1.0.0
display_name: {display_name}
backend_type: onnx
status: draft

task_family: {task_family}
modalities:
  input:
    - structured_json
  output:
    - structured_json

capabilities:
{chr(10).join(f'  - {c}' for c in capabilities)}

agent_card:
  summary: >
    {summary}
  when_to_use:
{chr(10).join(f'    - {x}' for x in when_to_use)}
  when_not_to_use:
{chr(10).join(f'    - {x}' for x in when_not_to_use)}
  input_description: >
    {input_description}
  output_description: >
    {output_description}

machine_spec:
  input_schema_ref: input.schema.json
  output_schema_ref: output.schema.json
  tensor_contract_ref: tensor_contract.yaml
  runtime:
    backend_type: onnx
    model_uri: model.onnx
    execution_provider: cpu
  preprocess:
    config_uri: preprocess.yaml
  postprocess:
    config_uri: postprocess.yaml

constraints:
  max_input_chars: 2000000
  max_request_bytes: 33554432
  batch_supported: false
  streaming_supported: false

performance:
  latency_ms_p50: 5
  latency_ms_p95: 30
  primary_metric: onnx_native_inference
  primary_score: 1.0
  time_complexity: O(n)
  space_complexity: O(n)
  complexity_variable: {input_shape_note}
  performance_notes: >
    Native CPU ONNX Runtime inference for the model core only. Feature
    construction / orchestration remains in the python_http_service sibling.

resource_requirements:
  min_cpu_cores: 1
  recommended_cpu_cores: 2
  min_memory_mb: 256
  recommended_memory_mb: 1024
  min_gpu_count: 0
  gpu_type: none
  min_vram_mb: 0
  recommended_vram_mb: 0
  disk_mb: 64

model_profile:
  parameter_count: {param_count}
  parameter_count_text: "{param_count}"
  flops: 0
  flops_text: {flops_text}
  flops_input_shape: {input_shape_note}
  model_size_mb: 1
  precision: fp32

safety:
  risk_level: medium
  requires_human_review: true
"""
    _write_text(root / "algorithm_card.yaml", card)
    _write_json(root / "input.schema.json", input_schema)
    _write_json(root / "output.schema.json", output_schema)
    _write_text(root / "tensor_contract.yaml", tensor_contract)
    _write_text(root / "preprocess.yaml", preprocess)
    _write_text(root / "postprocess.yaml", postprocess)
    _write_json(root / "golden_cases" / "case_001_input.json", golden_input, compact=compact_golden_input)
    _write_json(root / "golden_cases" / "case_001_expected.json", golden_expected)
    _write_json(root / "model.metadata.json", metadata)
    _write_text(root / "README.md", readme)
    _install_model(model_src, root)
    return root


def export_edl() -> Path:
    from agent.inference.models.edl_head import EvidentialHead

    ckpt = CHECKPOINT_DIR / "edl_head.pt"
    head = _load_state(EvidentialHead(), ckpt)
    model = EvidentialHeadOnnx(head)
    out = MODELS_DIR / "edl_head.onnx"
    dummy = torch.zeros(1, 6, dtype=torch.float32)
    _export(
        model,
        output_path=out,
        args=(dummy,),
        input_names=["features"],
        output_names=["probability", "epistemic", "aleatoric"],
    )
    feeds = {"features": [[0.9, 0.2, 0.15, 0.5, 0.4, 0.1]]}
    predicted = _ort_run(out, feeds)
    param_count = sum(p.numel() for p in model.parameters())
    root = _write_onnx_package(
        "edl_evidential_verifier_onnx",
        display_name="EDL Evidential Verifier ONNX",
        task_family="verification",
        capabilities=["evidential_verification", "uncertainty_estimation", "onnx"],
        summary="Native ONNX core of the EDL evidential detection verifier.",
        when_to_use=[
            "Caller already built the six-dim detection feature row.",
            "Need belief probability plus uncertainty tensors without starting Python HTTP.",
        ],
        when_not_to_use=[
            "Raw detection JSON still needs bbox feature extraction and verified filtering.",
            "Full pipeline orchestration is required (use edl_evidential_verifier python service).",
        ],
        input_description="One float32 feature row: confidence, w/640, h/640, cx/640, cy/640, damage_score.",
        output_description="Class probability [1,2], epistemic [1,1], aleatoric entropy proxy [1,1].",
        input_schema={
            "title": "EDL Evidential Verifier ONNX Input",
            "type": "object",
            "required": ["features"],
            "properties": {
                "features": {
                    "type": "array",
                    "minItems": 1,
                    "maxItems": 1,
                    "items": {
                        "type": "array",
                        "minItems": 6,
                        "maxItems": 6,
                        "items": {"type": "number"},
                    },
                }
            },
            "additionalProperties": False,
        },
        output_schema={
            "title": "EDL Evidential Verifier ONNX Output",
            "type": "object",
            "required": ["probability", "epistemic", "aleatoric"],
            "properties": {
                "probability": {"type": "array", "items": {"type": "array", "items": {"type": "number"}}},
                "epistemic": {"type": "array", "items": {"type": "array", "items": {"type": "number"}}},
                "aleatoric": {"type": "array", "items": {"type": "array", "items": {"type": "number"}}},
            },
            "additionalProperties": False,
        },
        tensor_contract="""inputs:
  - name: features
    dtype: float32
    shape: [1, 6]
outputs:
  - name: probability
    dtype: float32
    shape: [1, 2]
  - name: epistemic
    dtype: float32
    shape: [1, 1]
  - name: aleatoric
    dtype: float32
    shape: [1, 1]
""",
        preprocess="""type: json_to_tensor_map
mappings:
  - json_path: $.features
    tensor_name: features
    dtype: float32
    shape: [1, 6]
""",
        postprocess="""type: raw_tensor_to_json
outputs:
  - tensor_name: probability
    json_path: $.probability
  - tensor_name: epistemic
    json_path: $.epistemic
  - tensor_name: aleatoric
    json_path: $.aleatoric
""",
        model_src=out,
        golden_input=feeds,
        golden_expected={
            "probability": predicted["probability"],
            "epistemic": predicted["epistemic"],
            "aleatoric": predicted["aleatoric"],
        },
        metadata={
            "model_source": "models/checkpoints/edl_head.pt",
            "model_type": "evidential_head",
            "format": "onnx",
            "feature_order": [
                "confidence",
                "bbox_w_norm",
                "bbox_h_norm",
                "bbox_cx_norm",
                "bbox_cy_norm",
                "damage_score",
            ],
            "aleatoric_note": "ONNX export uses categorical entropy proxy instead of Digamma Dirichlet entropy.",
            "sibling_python_service": "edl_evidential_verifier",
        },
        readme="""# EDL Evidential Verifier ONNX

Native ONNX package for the EDL belief head.

Feature order: confidence, w/640, h/640, cx/640, cy/640, damage_score.

Orchestration (detection list → features → verified filter) remains in
`edl_evidential_verifier` (`python_http_service`).
""",
        param_count=param_count,
        flops_text="small MLP",
        input_shape_note="[1, 6]",
    )
    print(f"exported {out} -> {root}")
    return out


def export_odconv() -> Path:
    from agent.inference.models.odconv import ODConvRefiner

    ckpt = CHECKPOINT_DIR / "odconv_refiner.pt"
    refiner = _load_state(ODConvRefiner(), ckpt)
    model = ODConvRefinerOnnx(refiner)
    out = MODELS_DIR / "odconv_refiner.onnx"
    crops = torch.zeros(1, 3, 128, 128, dtype=torch.float32)
    base = torch.tensor([[0.8]], dtype=torch.float32)
    _export(
        model,
        output_path=out,
        args=(crops, base),
        input_names=["crops", "base_conf"],
        output_names=["refined_confidence"],
    )
    # Keep golden JSON small: zero crop + compact separators.
    feeds = {
        "crops": [[[[0 for _ in range(128)] for _ in range(128)] for _ in range(3)]],
        "base_conf": [[0.8]],
    }
    predicted = _ort_run(out, feeds)
    param_count = sum(p.numel() for p in model.parameters())
    root = _write_onnx_package(
        "odconv_confidence_refiner_onnx",
        display_name="ODConv Confidence Refiner ONNX",
        task_family="detection_refinement",
        capabilities=["odconv", "confidence_refine", "onnx"],
        summary="Native ONNX ODConv crop refiner that outputs refined detection confidence.",
        when_to_use=[
            "Caller already cropped a 128x128 RGB patch and has RT-DETR base confidence.",
        ],
        when_not_to_use=[
            "Full-frame detection is required (use battlefield_rtdetr_detector).",
            "Crop extraction from bbox is still needed.",
        ],
        input_description="crops [1,3,128,128] in [0,1] and base_conf [1,1].",
        output_description="refined_confidence [1,1] in [0.05, 0.99].",
        input_schema={
            "title": "ODConv Confidence Refiner ONNX Input",
            "type": "object",
            "required": ["crops", "base_conf"],
            "properties": {
                "crops": {"type": "array", "minItems": 1, "maxItems": 1},
                "base_conf": {
                    "type": "array",
                    "minItems": 1,
                    "maxItems": 1,
                    "items": {
                        "type": "array",
                        "minItems": 1,
                        "maxItems": 1,
                        "items": {"type": "number"},
                    },
                },
            },
            "additionalProperties": False,
        },
        output_schema={
            "title": "ODConv Confidence Refiner ONNX Output",
            "type": "object",
            "required": ["refined_confidence"],
            "properties": {
                "refined_confidence": {
                    "type": "array",
                    "items": {"type": "array", "items": {"type": "number"}},
                }
            },
            "additionalProperties": False,
        },
        tensor_contract="""inputs:
  - name: crops
    dtype: float32
    shape: [1, 3, 128, 128]
  - name: base_conf
    dtype: float32
    shape: [1, 1]
outputs:
  - name: refined_confidence
    dtype: float32
    shape: [1, 1]
""",
        preprocess="""type: json_to_tensor_map
mappings:
  - json_path: $.crops
    tensor_name: crops
    dtype: float32
    shape: [1, 3, 128, 128]
  - json_path: $.base_conf
    tensor_name: base_conf
    dtype: float32
    shape: [1, 1]
""",
        postprocess="""type: raw_tensor_to_json
outputs:
  - tensor_name: refined_confidence
    json_path: $.refined_confidence
""",
        model_src=out,
        golden_input=feeds,
        golden_expected={"refined_confidence": predicted["refined_confidence"]},
        metadata={
            "model_source": "models/checkpoints/odconv_refiner.pt",
            "model_type": "odconv_refiner",
            "format": "onnx",
            "crop_size": 128,
            "sibling_python_service": "battlefield_rtdetr_detector",
        },
        readme="""# ODConv Confidence Refiner ONNX

Native ONNX package for the ODConv detection-confidence refiner.

Inputs are already-cropped RGB tensors plus RT-DETR base confidence.
Frame loading / bbox crop logic stays in `battlefield_rtdetr_detector`.
""",
        param_count=param_count,
        flops_text="ODConv stem + MLP head",
        input_shape_note="[1, 3, 128, 128]",
        compact_golden_input=True,
    )
    print(f"exported {out} -> {root}")
    return out


def export_supcon() -> Path:
    from agent.inference.models.supcon_meta import SupConMetaNet

    ckpt = CHECKPOINT_DIR / "supcon_meta.pt"
    net = SupConMetaNet()
    # infer in_dim from checkpoint if present
    try:
        state = torch.load(ckpt, map_location="cpu", weights_only=True)
    except TypeError:
        state = torch.load(ckpt, map_location="cpu")
    if isinstance(state, dict) and "encoder.0.weight" in state:
        in_dim = int(state["encoder.0.weight"].shape[1])
        proj_dim = int(state["encoder.2.weight"].shape[0])
        num_classes = int(state["prototypes"].shape[0]) if "prototypes" in state else 4
        net = SupConMetaNet(in_dim=in_dim, proj_dim=proj_dim, num_classes=num_classes)
    _load_state(net, ckpt)
    in_dim = int(net.encoder[0].in_features)
    model = SupConMetaOnnx(net)
    out = MODELS_DIR / "supcon_meta.onnx"
    dummy = torch.zeros(1, in_dim, dtype=torch.float32)
    _export(
        model,
        output_path=out,
        args=(dummy,),
        input_names=["features"],
        output_names=["class_probabilities"],
    )
    feeds = {"features": [[0.01 * ((i * 17) % 97) for i in range(in_dim)]]}
    predicted = _ort_run(out, feeds)
    param_count = sum(p.numel() for p in model.parameters())
    labels = list(net.labels)
    root = _write_onnx_package(
        "supcon_meta_classifier_onnx",
        display_name="SupCon Meta Classifier ONNX",
        task_family="classification",
        capabilities=["affiliation_labeling", "supcon", "onnx"],
        summary="Native ONNX SupCon projector + fixed-prototype softmax classifier.",
        when_to_use=[
            "Caller already has a fixed-dim fused embedding vector.",
            "Need class probabilities without support-shot meta update.",
        ],
        when_not_to_use=[
            "Few-shot support shots must update prototypes at request time.",
            "Need target_id bookkeeping (use supcon_meta_classifier python service).",
        ],
        input_description=f"One float32 embedding row of length {in_dim}.",
        output_description=f"class_probabilities [1,{len(labels)}] ordered as {labels}.",
        input_schema={
            "title": "SupCon Meta Classifier ONNX Input",
            "type": "object",
            "required": ["features"],
            "properties": {
                "features": {
                    "type": "array",
                    "minItems": 1,
                    "maxItems": 1,
                    "items": {
                        "type": "array",
                        "minItems": in_dim,
                        "maxItems": in_dim,
                        "items": {"type": "number"},
                    },
                }
            },
            "additionalProperties": False,
        },
        output_schema={
            "title": "SupCon Meta Classifier ONNX Output",
            "type": "object",
            "required": ["class_probabilities"],
            "properties": {
                "class_probabilities": {
                    "type": "array",
                    "items": {"type": "array", "items": {"type": "number"}},
                }
            },
            "additionalProperties": False,
        },
        tensor_contract=f"""inputs:
  - name: features
    dtype: float32
    shape: [1, {in_dim}]
outputs:
  - name: class_probabilities
    dtype: float32
    shape: [1, {len(labels)}]
""",
        preprocess=f"""type: json_to_tensor_map
mappings:
  - json_path: $.features
    tensor_name: features
    dtype: float32
    shape: [1, {in_dim}]
""",
        postprocess="""type: raw_tensor_to_json
outputs:
  - tensor_name: class_probabilities
    json_path: $.class_probabilities
""",
        model_src=out,
        golden_input=feeds,
        golden_expected={"class_probabilities": predicted["class_probabilities"]},
        metadata={
            "model_source": "models/checkpoints/supcon_meta.pt",
            "model_type": "supcon_meta_fixed_prototypes",
            "format": "onnx",
            "in_dim": in_dim,
            "labels": labels,
            "temperature": 0.07,
            "sibling_python_service": "supcon_meta_classifier",
            "note": "Support-shot prototype updates are not included in this ONNX core.",
        },
        readme=f"""# SupCon Meta Classifier ONNX

Native ONNX package for the SupCon projection head + fixed prototypes.

Label order: {', '.join(labels)}.

Few-shot support-shot prototype blending stays in `supcon_meta_classifier`.
""",
        param_count=param_count,
        flops_text="MLP projector + prototype softmax",
        input_shape_note=f"[1, {in_dim}]",
    )
    print(f"exported {out} -> {root}")
    return out


def export_marl_router() -> Path:
    from agent.inference.models.marl_policy import MARLPolicyNetwork

    ckpt = CHECKPOINT_DIR / "marl_policy.pt"
    net = _load_state(MARLPolicyNetwork(), ckpt)
    model = MARLPolicyOnnx(net)
    out = MODELS_DIR / "marl_policy.onnx"
    dummy = torch.zeros(1, 8, dtype=torch.float32)
    _export(
        model,
        output_path=out,
        args=(dummy,),
        input_names=["state"],
        output_names=["channel_logits", "reliability"],
    )
    feeds = {"state": [[0.3, 0.4, 0.2, 0.6, 0.5, 0.0, 0.7, 0.5]]}
    predicted = _ort_run(out, feeds)
    param_count = sum(p.numel() for p in model.parameters())
    channels = list(MARLPolicyNetwork.CHANNELS)
    root = _write_onnx_package(
        "marl_dynamic_router_onnx",
        display_name="MARL Dynamic Router ONNX",
        task_family="routing",
        capabilities=["channel_selection", "anti_jam", "onnx"],
        summary="Native ONNX MARL routing policy: channel logits + reliability.",
        when_to_use=[
            "Caller already built the 8-dim routing state vector.",
        ],
        when_not_to_use=[
            "Need per-subscriber route list construction (use marl_dynamic_router service).",
        ],
        input_description="state [1,8]: jamming, target_density, high_threat_ratio, agent_density, compression, jam_flag, semantic_len, bias.",
        output_description=f"channel_logits [1,{len(channels)}] ordered {channels}; reliability [1,1].",
        input_schema={
            "title": "MARL Dynamic Router ONNX Input",
            "type": "object",
            "required": ["state"],
            "properties": {
                "state": {
                    "type": "array",
                    "minItems": 1,
                    "maxItems": 1,
                    "items": {
                        "type": "array",
                        "minItems": 8,
                        "maxItems": 8,
                        "items": {"type": "number"},
                    },
                }
            },
            "additionalProperties": False,
        },
        output_schema={
            "title": "MARL Dynamic Router ONNX Output",
            "type": "object",
            "required": ["channel_logits", "reliability"],
            "properties": {
                "channel_logits": {"type": "array", "items": {"type": "array", "items": {"type": "number"}}},
                "reliability": {"type": "array", "items": {"type": "array", "items": {"type": "number"}}},
            },
            "additionalProperties": False,
        },
        tensor_contract="""inputs:
  - name: state
    dtype: float32
    shape: [1, 8]
outputs:
  - name: channel_logits
    dtype: float32
    shape: [1, 3]
  - name: reliability
    dtype: float32
    shape: [1, 1]
""",
        preprocess="""type: json_to_tensor_map
mappings:
  - json_path: $.state
    tensor_name: state
    dtype: float32
    shape: [1, 8]
""",
        postprocess="""type: raw_tensor_to_json
outputs:
  - tensor_name: channel_logits
    json_path: $.channel_logits
  - tensor_name: reliability
    json_path: $.reliability
""",
        model_src=out,
        golden_input=feeds,
        golden_expected={
            "channel_logits": predicted["channel_logits"],
            "reliability": predicted["reliability"],
        },
        metadata={
            "model_source": "models/checkpoints/marl_policy.pt",
            "model_type": "marl_policy_network",
            "format": "onnx",
            "channels": channels,
            "sibling_python_service": "marl_dynamic_router",
        },
        readme="""# MARL Dynamic Router ONNX

Native ONNX package for the MARL channel/reliability policy head.

Route fan-out to subscribers remains in `marl_dynamic_router`.
""",
        param_count=param_count,
        flops_text="small MLP policy",
        input_shape_note="[1, 8]",
    )
    print(f"exported {out} -> {root}")
    return out


def export_marl_ppo() -> Path:
    from agent.inference.models.marl_ppo_scheduler import MARLPPOSchedulerNet
    from agent.training.battlefield_scheduling_env import BattlefieldSchedulingEnv

    env = BattlefieldSchedulingEnv()
    ckpt = CHECKPOINT_DIR / "marl_ppo_scheduler.pt"
    net = MARLPPOSchedulerNet(
        obs_dim=env.obs_dim,
        n_actions=env.n_actions,
        n_agents=env.n_agents,
    )
    _load_state(net, ckpt)
    model = MARLPPOActorOnnx(net)
    out = MODELS_DIR / "marl_ppo_scheduler.onnx"
    dummy = torch.zeros(1, env.obs_dim, dtype=torch.float32)
    _export(
        model,
        output_path=out,
        args=(dummy,),
        input_names=["obs"],
        output_names=["action_logits", "value"],
    )
    feeds = {"obs": [[0.01 * ((i * 13) % 89) for i in range(env.obs_dim)]]}
    predicted = _ort_run(out, feeds)
    param_count = sum(p.numel() for p in model.parameters())
    root = _write_onnx_package(
        "marl_ppo_task_scheduler_onnx",
        display_name="MARL-PPO Task Scheduler ONNX",
        task_family="planning",
        capabilities=["task_scheduling", "marl_ppo", "onnx"],
        summary="Native ONNX MARL-PPO actor/critic core for one agent observation.",
        when_to_use=[
            "Caller already built one agent observation vector from BattlefieldSchedulingEnv.",
            "Need action logits / value without Python HTTP.",
        ],
        when_not_to_use=[
            "Need full multi-agent assignment decoding into sensor/strike plans.",
            "Use marl_ppo_task_scheduler python service for end-to-end scheduling.",
        ],
        input_description=f"obs [1,{env.obs_dim}] from BattlefieldSchedulingEnv.build_agent_obs.",
        output_description=f"action_logits [1,{env.n_actions}], value [1,1].",
        input_schema={
            "title": "MARL-PPO Task Scheduler ONNX Input",
            "type": "object",
            "required": ["obs"],
            "properties": {
                "obs": {
                    "type": "array",
                    "minItems": 1,
                    "maxItems": 1,
                    "items": {
                        "type": "array",
                        "minItems": env.obs_dim,
                        "maxItems": env.obs_dim,
                        "items": {"type": "number"},
                    },
                }
            },
            "additionalProperties": False,
        },
        output_schema={
            "title": "MARL-PPO Task Scheduler ONNX Output",
            "type": "object",
            "required": ["action_logits", "value"],
            "properties": {
                "action_logits": {"type": "array", "items": {"type": "array", "items": {"type": "number"}}},
                "value": {"type": "array", "items": {"type": "array", "items": {"type": "number"}}},
            },
            "additionalProperties": False,
        },
        tensor_contract=f"""inputs:
  - name: obs
    dtype: float32
    shape: [1, {env.obs_dim}]
outputs:
  - name: action_logits
    dtype: float32
    shape: [1, {env.n_actions}]
  - name: value
    dtype: float32
    shape: [1, 1]
""",
        preprocess=f"""type: json_to_tensor_map
mappings:
  - json_path: $.obs
    tensor_name: obs
    dtype: float32
    shape: [1, {env.obs_dim}]
""",
        postprocess="""type: raw_tensor_to_json
outputs:
  - tensor_name: action_logits
    json_path: $.action_logits
  - tensor_name: value
    json_path: $.value
""",
        model_src=out,
        golden_input=feeds,
        golden_expected={
            "action_logits": predicted["action_logits"],
            "value": predicted["value"],
        },
        metadata={
            "model_source": "models/checkpoints/marl_ppo_scheduler.pt",
            "model_type": "marl_ppo_actor_critic",
            "format": "onnx",
            "obs_dim": env.obs_dim,
            "n_actions": env.n_actions,
            "n_agents": env.n_agents,
            "sibling_python_service": "marl_ppo_task_scheduler",
        },
        readme=f"""# MARL-PPO Task Scheduler ONNX

Native ONNX actor/critic core for one scheduling agent observation.

- obs_dim={env.obs_dim}
- n_actions={env.n_actions} (target index or no-assignment)

Multi-agent env stepping and plan decoding remain in `marl_ppo_task_scheduler`.
""",
        param_count=param_count,
        flops_text="shared encoder + actor/critic",
        input_shape_note=f"[1, {env.obs_dim}]",
    )
    print(f"exported {out} -> {root}")
    return out


def main() -> int:
    sys.path.insert(0, str(REPO_ROOT))
    MODELS_DIR.mkdir(parents=True, exist_ok=True)

    exporters = [
        ("edl", export_edl),
        ("odconv", export_odconv),
        ("supcon", export_supcon),
        ("marl_router", export_marl_router),
        ("marl_ppo", export_marl_ppo),
    ]
    failed: list[str] = []
    for name, fn in exporters:
        try:
            fn()
        except Exception as exc:  # noqa: BLE001 - report all failures at end
            failed.append(f"{name}: {exc}")
            print(f"FAILED {name}: {exc}", file=sys.stderr)

    if failed:
        print("Some exports failed:", file=sys.stderr)
        for item in failed:
            print(f"  - {item}", file=sys.stderr)
        return 1
    print("All convertible TIA ONNX packages exported.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
