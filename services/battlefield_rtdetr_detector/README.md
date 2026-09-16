# Battlefield RT-DETR Detector Service

TIA 算法封装（端口 **9020**）。推理委托 `algorithms.battlefield_rtdetr_detector` → `agent.inference.vision.detect_objects`。

默认检测权重（profile）：`models/checkpoints/battlefield_rtdetr_unit17_coco25_ap_kaggle_best.pt`（97 类 unit17_ap_finetune best）。  
ONNX 骨干包：`examples/battlefield_rtdetr_detector_onnx/1.0.0/`（换权重后执行 `scripts/export_rtdetr_onnx.py --force`）。

支持：`odconv_enable` / `detection_half` / `detection_max_det`；输出 `rtdetr_confidence`、`odconv_confidence`、`odconv_large_box`。

```powershell
$env:TIA_USE_MOCK="1"
$env:PORT="9020"
python services/battlefield_rtdetr_detector/app/main.py
```
