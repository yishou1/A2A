# Battlefield RT-DETR Detector ONNX

Native ONNX package for the Ultralytics RT-DETR-L battlefield detector.

## Contract

| Item | Value |
|------|-------|
| Weights source | `models/checkpoints/battlefield_rtdetr.pt` |
| Input | `images` float32 `[1, 3, 640, 640]` in `[0,1]` (letterboxed RGB) |
| Output | `output0` float32 `[1, 300, 6]` → `[x1,y1,x2,y2,conf,cls_id]` per query |
| Classes | 97 (see `label_map.json`) |
| Opset | 17 |

## Boundaries

Kept **inside** this ONNX package:

- RT-DETR backbone / decoder forward

Kept in sibling `battlefield_rtdetr_detector` (`python_http_service`):

- Frame / media decode
- Letterbox + color conversion
- Confidence threshold / NMS / Top-K
- ODConv crop refine (`odconv_confidence_refiner_onnx`)
- Structured `{detections, count}` response

## Rebuild

```powershell
.\.venv\Scripts\python.exe scripts\export_rtdetr_onnx.py
```

## Register (zsl algolib)

```powershell
.\build\Release\algolib.exe register .\examples\battlefield_rtdetr_detector_onnx\1.0.0
.\build\Release\algolib.exe activate battlefield_rtdetr_detector_onnx 1.0.0 onnx
```

Call with `backend_type: "onnx"`. Ensure preprocess feeds a real `images` tensor;
golden JSON uses shape/fill smoke descriptors because a full 640×640 array is too large for the repo.
