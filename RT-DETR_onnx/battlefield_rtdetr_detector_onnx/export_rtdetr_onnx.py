#!/usr/bin/env python3
"""Export battlefield RT-DETR (Ultralytics) as a native ONNX algorithm package.

Sibling of python_http_service `battlefield_rtdetr_detector`.
Follows the same zsl/algorithmrepo layout as scripts/export_tia_onnx_models.py:

  examples/battlefield_rtdetr_detector_onnx/1.0.0/
    algorithm_card.yaml, schemas, preprocess/postprocess,
    tensor_contract.yaml, model.onnx, label_map.json, golden_cases/

Also writes models/battlefield_rtdetr.onnx for Python reuse.

Notes
-----
- Input contract is a preprocessed float32 NCHW tensor [1,3,640,640] in [0,1].
  Letterbox / BGR-RGB / decode stay in the HTTP sibling (or caller).
- Output is the Ultralytics RT-DETR ONNX raw tensor (typically [1, 300, 4+nc]
  or equivalent). Score thresholding, NMS, and class-name mapping stay outside
  the ONNX graph unless Ultralytics embeds them.
- Golden cases store shape/fill metadata + output summary (full 640x640 JSON
  would be multi-MB and is omitted on purpose).
"""

from __future__ import annotations

import hashlib
import json
import shutil
import sys
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]
CHECKPOINT = REPO_ROOT / "models" / "checkpoints" / "battlefield_rtdetr.pt"
MODELS_DIR = REPO_ROOT / "models"
EXAMPLES_DIR = REPO_ROOT / "examples"
ALGORITHM_ID = "battlefield_rtdetr_detector_onnx"
IMGSZ = 640
ONNX_OPSET = 17


def _write_json(path: Path, payload: Any, *, compact: bool = False) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if compact:
        path.write_text(json.dumps(payload, ensure_ascii=False, separators=(",", ":")) + "\n", encoding="utf-8")
    else:
        path.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def _write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text.rstrip() + "\n", encoding="utf-8")


def _package_root() -> Path:
    return EXAMPLES_DIR / ALGORITHM_ID / "1.0.0"


def _ort_inspect(model_path: Path) -> tuple[list[dict[str, Any]], list[dict[str, Any]], dict[str, Any]]:
    import numpy as np
    import onnxruntime as ort

    sess = ort.InferenceSession(str(model_path), providers=["CPUExecutionProvider"])
    inputs = []
    for inp in sess.get_inputs():
        shape = [int(d) if isinstance(d, int) or (isinstance(d, str) and d.isdigit()) else (1 if isinstance(d, str) else d) for d in inp.shape]
        # normalize dynamic dims to concrete smoke shape
        shape = [d if isinstance(d, int) and d > 0 else (3 if i == 1 else (IMGSZ if i in (2, 3) else 1)) for i, d in enumerate(shape)]
        inputs.append({"name": inp.name, "dtype": inp.type, "shape": shape})

    feeds = {inp["name"]: np.zeros(inp["shape"], dtype=np.float32) for inp in inputs}
    outs = sess.run(None, feeds)
    outputs = []
    summary: dict[str, Any] = {}
    for meta, arr in zip(sess.get_outputs(), outs):
        a = np.asarray(arr)
        outputs.append({"name": meta.name, "dtype": meta.type, "shape": list(a.shape)})
        flat = a.reshape(-1)
        digest = hashlib.sha256(a.astype(np.float32).tobytes()).hexdigest()[:16]
        summary[meta.name] = {
            "shape": list(a.shape),
            "dtype": str(a.dtype),
            "min": float(flat.min()) if flat.size else None,
            "max": float(flat.max()) if flat.size else None,
            "mean": float(flat.mean()) if flat.size else None,
            "sha256_16": digest,
            "head": flat[:8].astype(float).tolist() if flat.size else [],
        }
    return inputs, outputs, summary


def _export_ultralytics(ckpt: Path, out_onnx: Path, *, force: bool = False) -> Path:
    from ultralytics import RTDETR

    if not ckpt.is_file():
        raise FileNotFoundError(ckpt)

    sidecar = ckpt.with_suffix(".onnx")
    if out_onnx.is_file() and not force:
        print(f"Reusing existing {out_onnx} (pass --force to re-export)")
        return out_onnx
    if sidecar.is_file() and not force:
        out_onnx.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(sidecar, out_onnx)
        print(f"Reusing Ultralytics sidecar {sidecar} -> {out_onnx}")
        return out_onnx

    model = RTDETR(str(ckpt))
    # Export next to checkpoint first (Ultralytics default), then copy to models/
    exported = model.export(
        format="onnx",
        imgsz=IMGSZ,
        opset=ONNX_OPSET,
        simplify=True,
        dynamic=False,
        half=False,
    )
    exported_path = Path(exported)
    if not exported_path.is_file():
        raise RuntimeError(f"Ultralytics export did not produce a file: {exported}")

    out_onnx.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(exported_path, out_onnx)
    # Prefer keeping a stable name under models/; leave Ultralytics sidecar if different path
    return out_onnx


def _names_from_ckpt(ckpt: Path) -> dict[str, str]:
    from ultralytics import RTDETR

    model = RTDETR(str(ckpt))
    names = getattr(model, "names", None) or getattr(getattr(model, "model", None), "names", {}) or {}
    if isinstance(names, dict):
        return {str(k): str(v) for k, v in names.items()}
    return {str(i): str(n) for i, n in enumerate(names)}


def _write_package(
    *,
    model_src: Path,
    names: dict[str, str],
    input_meta: list[dict[str, Any]],
    output_meta: list[dict[str, Any]],
    ort_summary: dict[str, Any],
    param_count: int,
) -> Path:
    root = _package_root()
    if root.exists():
        shutil.rmtree(root)
    root.mkdir(parents=True)

    in0 = input_meta[0]
    out0 = output_meta[0]
    in_name = in0["name"]
    out_name = out0["name"]
    in_shape = in0["shape"]
    out_shape = out0["shape"]
    nc = len(names)

    card = f"""algorithm_id: {ALGORITHM_ID}
version: 1.0.0
display_name: Battlefield RT-DETR Detector ONNX
backend_type: onnx
status: draft

task_family: detection
modalities:
  input:
    - structured_json
  output:
    - structured_json

capabilities:
  - object_detection
  - rt_detr
  - onnx

agent_card:
  summary: >
    Native ONNX core of the battlefield Ultralytics RT-DETR detector
    (97-class unit17/coco25 taxonomy). Caller supplies a letterboxed
    float32 NCHW image tensor; decode / letterbox / score filter / NMS /
    ODConv refine remain in the python_http_service sibling.
  when_to_use:
    - Need RT-DETR backbone inference via ONNX Runtime without starting Python HTTP.
    - Upstream already produced images [{', '.join(str(x) for x in in_shape)}] in [0,1].
  when_not_to_use:
    - Raw frames / media_refs still need decode and letterbox (use battlefield_rtdetr_detector).
    - ODConv confidence refine or full TIA orchestration is required.
  input_description: >
    {in_name} float32 NCHW tensor shape {in_shape}, RGB-normalized to [0,1]
    after letterbox to {IMGSZ}.
  output_description: >
    Raw Ultralytics RT-DETR ONNX tensor `{out_name}` shape {out_shape}.
    Map class ids with label_map.json; apply conf / NMS outside the graph.

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
  max_input_chars: 20000000
  max_request_bytes: 67108864
  batch_supported: false
  streaming_supported: false

performance:
  latency_ms_p50: 200
  latency_ms_p95: 1500
  primary_metric: onnx_native_inference
  primary_score: 1.0
  time_complexity: O(n)
  space_complexity: O(n)
  complexity_variable: {in_shape}
  performance_notes: >
    Native CPU ONNX Runtime for the detector backbone only. Image decode,
    letterbox, NMS, and ODConv remain in battlefield_rtdetr_detector.

resource_requirements:
  min_cpu_cores: 2
  recommended_cpu_cores: 4
  min_memory_mb: 2048
  recommended_memory_mb: 4096
  min_gpu_count: 0
  gpu_type: optional
  min_vram_mb: 0
  recommended_vram_mb: 4096
  disk_mb: 256

model_profile:
  parameter_count: {param_count}
  parameter_count_text: "{param_count}"
  flops: 0
  flops_text: RT-DETR-L ~108 GFLOPs @ {IMGSZ}
  flops_input_shape: {in_shape}
  model_size_mb: {max(1, model_src.stat().st_size // (1024 * 1024))}
  precision: fp32

safety:
  risk_level: medium
  requires_human_review: true
"""
    _write_text(root / "algorithm_card.yaml", card)

    _write_json(
        root / "input.schema.json",
        {
            "title": "Battlefield RT-DETR Detector ONNX Input",
            "type": "object",
            "required": [in_name],
            "properties": {
                in_name: {
                    "description": (
                        f"Preprocessed float32 NCHW image tensor {in_shape} in [0,1]. "
                        "For package golden smoke, prefer images_shape + images_fill "
                        "instead of embedding the full array."
                    ),
                    "oneOf": [
                        {"type": "array"},
                        {"type": "null"},
                    ],
                },
                "images_shape": {
                    "type": "array",
                    "items": {"type": "integer"},
                    "minItems": 4,
                    "maxItems": 4,
                },
                "images_fill": {"type": "number"},
            },
            "additionalProperties": False,
        },
    )
    _write_json(
        root / "output.schema.json",
        {
            "title": "Battlefield RT-DETR Detector ONNX Output",
            "type": "object",
            "required": [out_name],
            "properties": {
                out_name: {
                    "description": f"Raw detection tensor shape {out_shape}",
                    "type": "object",
                    "properties": {
                        "shape": {"type": "array", "items": {"type": "integer"}},
                        "dtype": {"type": "string"},
                        "min": {"type": ["number", "null"]},
                        "max": {"type": ["number", "null"]},
                        "mean": {"type": ["number", "null"]},
                        "sha256_16": {"type": "string"},
                        "head": {"type": "array", "items": {"type": "number"}},
                    },
                }
            },
            "additionalProperties": True,
        },
    )

    _write_text(
        root / "tensor_contract.yaml",
        f"""inputs:
  - name: {in_name}
    dtype: float32
    shape: {in_shape}
outputs:
  - name: {out_name}
    dtype: float32
    shape: {out_shape}
""",
    )
    _write_text(
        root / "preprocess.yaml",
        f"""type: json_to_tensor_map
mappings:
  - json_path: $.{in_name}
    tensor_name: {in_name}
    dtype: float32
    shape: {in_shape}
notes: >
  Caller must letterbox/decode outside this package. Golden cases use
  images_shape/images_fill smoke descriptors instead of full tensors.
""",
    )
    _write_text(
        root / "postprocess.yaml",
        f"""type: raw_tensor_to_json
outputs:
  - tensor_name: {out_name}
    json_path: $.{out_name}
notes: >
  Raw Ultralytics tensor only. Apply conf threshold, optional NMS, and
  label_map.json class-id mapping in the caller or python_http_service sibling.
""",
    )

    _write_json(root / "label_map.json", names)
    _write_json(
        root / "golden_cases" / "case_001_input.json",
        {
            in_name: None,
            "images_shape": in_shape,
            "images_fill": 0.0,
        },
    )
    _write_json(
        root / "golden_cases" / "case_001_expected.json",
        {out_name: ort_summary[out_name]},
    )
    _write_json(
        root / "model.metadata.json",
        {
            "model_source": "models/checkpoints/battlefield_rtdetr.pt",
            "model_type": "ultralytics_rtdetr_l",
            "format": "onnx",
            "opset": ONNX_OPSET,
            "imgsz": IMGSZ,
            "num_classes": nc,
            "parameter_count": param_count,
            "input": in0,
            "outputs": output_meta,
            "sibling_python_service": "battlefield_rtdetr_detector",
            "sibling_odconv_onnx": "odconv_confidence_refiner_onnx",
            "export_tool": "ultralytics.RTDETR.export",
        },
    )
    _write_text(
        root / "README.md",
        f"""# Battlefield RT-DETR Detector ONNX

Native ONNX package for the Ultralytics RT-DETR-L battlefield detector.

## Contract

| Item | Value |
|------|-------|
| Weights source | `models/checkpoints/battlefield_rtdetr.pt` |
| Input | `{in_name}` float32 `{in_shape}` in `[0,1]` (letterboxed RGB) |
| Output | `{out_name}` float32 `{out_shape}` (raw Ultralytics tensor) |
| Classes | {nc} (see `label_map.json`) |
| Opset | {ONNX_OPSET} |

## Boundaries

Kept **inside** this ONNX package:

- RT-DETR backbone / decoder forward

Kept in sibling `battlefield_rtdetr_detector` (`python_http_service`):

- Frame / media decode
- Letterbox + color conversion
- Confidence threshold / NMS / Top-K
- ODConv crop refine (`odconv_confidence_refiner_onnx`)
- Structured `{{detections, count}}` response

## Rebuild

```powershell
.\\.venv\\Scripts\\python.exe scripts\\export_rtdetr_onnx.py
```

## Register (zsl algolib)

```powershell
.\\build\\Release\\algolib.exe register .\\examples\\battlefield_rtdetr_detector_onnx\\1.0.0
.\\build\\Release\\algolib.exe activate battlefield_rtdetr_detector_onnx 1.0.0 onnx
```

Call with `backend_type: "onnx"`. Ensure preprocess feeds a real `{in_name}` tensor;
golden JSON uses shape/fill smoke descriptors because a full 640×640 array is too large for the repo.
""",
    )
    shutil.copy2(model_src, root / "model.onnx")
    return root


def main(argv: list[str] | None = None) -> int:
    import argparse

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--force", action="store_true", help="Re-run Ultralytics ONNX export even if artifacts exist")
    args = parser.parse_args(argv)

    sys.path.insert(0, str(REPO_ROOT))
    MODELS_DIR.mkdir(parents=True, exist_ok=True)

    if not CHECKPOINT.is_file():
        print(f"Missing checkpoint: {CHECKPOINT}", file=sys.stderr)
        return 1

    out_onnx = MODELS_DIR / "battlefield_rtdetr.onnx"
    print(f"Exporting {CHECKPOINT} -> {out_onnx} (opset={ONNX_OPSET}, imgsz={IMGSZ}) ...")
    _export_ultralytics(CHECKPOINT, out_onnx, force=args.force)

    names = _names_from_ckpt(CHECKPOINT)
    # Ultralytics reports ~33,005,411 for rtdetr-l in this repo
    param_count = 33005411
    try:
        from ultralytics import RTDETR

        m = RTDETR(str(CHECKPOINT))
        param_count = sum(int(p.numel()) for p in m.model.parameters())
    except Exception:  # noqa: BLE001
        pass

    inputs, outputs, summary = _ort_inspect(out_onnx)
    print("ONNX inputs:", inputs)
    print("ONNX outputs:", outputs)

    root = _write_package(
        model_src=out_onnx,
        names=names,
        input_meta=inputs,
        output_meta=outputs,
        ort_summary=summary,
        param_count=param_count,
    )
    print(f"exported {out_onnx} -> {root}")
    print(f"classes={len(names)} size_mb={out_onnx.stat().st_size / (1024 * 1024):.1f}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
