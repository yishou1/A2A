# Battlefield RT-DETR Detector

TIA `python_http_service` 包：`battlefield_rtdetr_detector`（端口 9020）。

## 能力（与当前推理对齐）

- Ultralytics **RT-DETR**（权重名含 yolo 时也可走 YOLO）
- 默认权重文件名：`battlefield_rtdetr_unit17_coco25_ap_kaggle_best.pt`（97 类；当前为 **unit17_ap_finetune** 机场+飞机续训 best；三档 profile 已指向此路径）
- 可选 **ODConv** 置信度精炼（`odconv_enable`）
- RGB→BGR 喂入 Ultralytics；输出保留 `rtdetr_confidence` / `odconv_confidence`
- 大框（面积占比 ≥ 0.20）轻微融合，避免机场等主体框被 ODConv 拉偏
- 支持 `detection_half`、`detection_max_det`、`detection_imgsz`
- ONNX 骨干兄弟包：`examples/battlefield_rtdetr_detector_onnx/1.0.0/`（由同权重 `export_rtdetr_onnx.py` 生成）

## 配置键（`rt_detr_odconv` / inference）

| 键 | 含义 |
|----|------|
| `detection_model` | 检测权重（默认见上） |
| `odconv_enable` | 是否启用 ODConv |
| `odconv_checkpoint` / `odconv_crop_size` | ODConv 权重与 crop |
| `detection_half` | CUDA FP16 |
| `detection_max_det` | Top-K（按原置信度截断） |
| `confidence_threshold` / `detection_imgsz` | 阈值与输入边长 |

契约 schema：与 `algorithms/contracts/schemas/battlefield_rtdetr_detector/` 保持一致。
