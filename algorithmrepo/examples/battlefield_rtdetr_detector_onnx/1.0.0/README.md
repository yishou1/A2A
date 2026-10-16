# Battlefield RT-DETR Detector ONNX

Native ONNX package for the Ultralytics RT-DETR-L battlefield detector
(97 classes; checkpoint content is `unit17_ap_finetune best`).

The package metadata is version-controlled. The 125 MiB `model.onnx` is
installed separately from the verified release bundle so a broad `git add .`
does not accidentally add production weights.

## Install model artifacts

From the repository root:

```powershell
.\algorithmrepo\scripts\install_rtdetr_release_bundle.ps1
```

To use a bundle at a different location:

```powershell
.\algorithmrepo\scripts\install_rtdetr_release_bundle.ps1 `
  -BundlePath "D:\path\to\unit17_ap_finetune_upload_20260916"
```

The installer validates each source against `MANIFEST.json`. It installs one
physical ONNX file and creates this package's `model.onnx` as a hard link when
the filesystem supports it.

## Contract

| Item | Value |
|------|-------|
| Weights source | `models/checkpoints/battlefield_rtdetr_unit17_coco25_ap_kaggle_best.pt` |
| Weights lineage | `unit17_ap_finetune best` (airport + plane fine-tuning) |
| Input | `images` float32 `[1, 3, 640, 640]` in `[0,1]` (letterboxed RGB) |
| Output | `output0` float32 `[1, 300, 6]` (raw Ultralytics tensor) |
| Classes | 97 (see `label_map.json`) |
| Opset | 17 |

The ONNX graph contains the RT-DETR backbone/decoder forward pass. Raw image
decode, letterboxing, confidence filtering, NMS, Top-K and optional ODConv
refinement remain in the `battlefield_rtdetr_detector` Python HTTP service.

## Register

```powershell
.\build\Release\algolib.exe register .\examples\battlefield_rtdetr_detector_onnx\1.0.0
.\build\Release\algolib.exe activate battlefield_rtdetr_detector_onnx 1.0.0 onnx
```

The golden JSON uses shape/fill descriptors because embedding a complete
640×640 tensor in Git would be impractical.
