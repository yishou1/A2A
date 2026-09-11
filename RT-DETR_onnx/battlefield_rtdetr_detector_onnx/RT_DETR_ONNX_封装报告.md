# RT-DETR ONNX 封装报告

**日期：** 2026-09-11  
**对象权重：** `models/checkpoints/battlefield_rtdetr.pt`（Ultralytics RT-DETR-L，97 类 unit17/coco25）  
**产物包：** `examples/battlefield_rtdetr_detector_onnx/1.0.0/`  
**canonical 模型：** `models/battlefield_rtdetr.onnx`（约 125.6 MB，opset 17）

---

## 1. 结论摘要

| 项 | 结论 |
|---|---|
| 原先是否已是 ONNX 封装？ | **否**。此前按师兄规范做成 `backend_type: python_http_service`（`:9020`）+ `.pt` 推理。 |
| 相关 ONNX？ | 仅有兄弟头 **ODConv**（`odconv_confidence_refiner_onnx`），不是 RT-DETR 整网。 |
| 本次做了什么？ | 用 Ultralytics 将 RT-DETR 骨干导出为原生 `backend_type: onnx` 算法包，与 HTTP 服务并存。 |
| 边界 | ONNX 包只含检测前向；解码 / letterbox / conf·NMS / ODConv / `{detections,count}` 仍在 HTTP 兄弟服务。 |

---

## 2. 原封装核查（封装前）

### 2.1 师兄风格 HTTP 包（已有）

| 层 | 路径 / 说明 |
|---|---|
| 算法包 | `examples/battlefield_rtdetr_detector/1.0.0/`（`backend_type: python_http_service`） |
| 服务 | `services/battlefield_rtdetr_detector/` → `POST :9020/predict` |
| 算法入口 | `algorithms/battlefield_rtdetr_detector/` |
| 推理 | Ultralytics `RTDETR` + 可选 ODConv refine（`agent/inference/vision.py`） |
| 权重 | `models/checkpoints/battlefield_rtdetr.pt`（及多版本变体） |

包内**没有** `model.onnx` / `preprocess.yaml` / `tensor_contract.yaml`。

### 2.2 文档中的既有定位

`docs/TIA_ONNX_ALGORITHM_PACKAGES.md` 原先把「RT-DETR 整网」列在 **未转 ONNX**；SPEC 侧倾向「RT-DETR | ONNX 优先」，但落地一直是 HTTP + `.pt`。

---

## 3. 本次 ONNX 封装做法

### 3.1 导出命令

```powershell
.\.venv\Scripts\python.exe scripts\export_rtdetr_onnx.py
# 强制重导：
.\.venv\Scripts\python.exe scripts\export_rtdetr_onnx.py --force
```

实现要点：

1. `ultralytics.RTDETR(...).export(format="onnx", imgsz=640, opset=17, simplify=True, dynamic=False)`
2. 复制到 `models/battlefield_rtdetr.onnx`
3. 按 zsl/algorithmrepo 布局写 `examples/battlefield_rtdetr_detector_onnx/1.0.0/`
4. CPU `onnxruntime` 零输入冒烟，记录输出摘要到 golden

### 3.2 张量契约（实测）

| | 名称 | dtype | shape |
|---|---|---|---|
| 输入 | `images` | float32 | `[1, 3, 640, 640]` |
| 输出 | `output0` | float32 | `[1, 300, 6]` |

`output0` 末维 6 与 Ultralytics RT-DETR end2end 导出一致，语义为：

`[x1, y1, x2, y2, confidence, class_id]` × 最多 300 个 query。

类别名见包内 `label_map.json`（0–96，战场类 + COCO 扩展，共 97）。

### 3.3 算法包文件清单

```text
examples/battlefield_rtdetr_detector_onnx/1.0.0/
  algorithm_card.yaml      # backend_type: onnx
  input.schema.json
  output.schema.json
  tensor_contract.yaml
  preprocess.yaml          # json_to_tensor_map → images
  postprocess.yaml         # raw_tensor_to_json ← output0
  label_map.json
  model.onnx               # ~125.6 MB
  model.metadata.json
  README.md
  golden_cases/
    case_001_input.json    # shape/fill 冒烟描述（不内嵌整图）
    case_001_expected.json # 零图 ORT 输出摘要（shape/统计/sha256）
```

与现有 `edl_*_onnx` / `odconv_*_onnx` 等同构；因 640×640 全量 JSON 过大，golden 故意不写完整 tensor。

### 3.4 与 HTTP / ODConv 的分工

```text
帧 / media_refs
    │
    ▼
battlefield_rtdetr_detector  (python_http_service)
    │  decode · letterbox · conf/NMS · 结构化输出
    │
    ├─► battlefield_rtdetr_detector_onnx   ← 本次新增（骨干）
    │         images[1,3,640,640] → output0[1,300,6]
    │
    └─► odconv_confidence_refiner_onnx     ← 已有（crop 置信度精炼）
              crops + base_conf → refined_confidence
```

---

## 4. 验证

| 检查 | 结果 |
|---|---|
| Ultralytics 导出 | 成功（约 24s，onnxslim 简化） |
| ORT CPU 冒烟 | `images` 全零 → `output0` shape `[1,300,6]` |
| 包布局 | card / schema / contract / model / golden 齐全 |
| 自动化 | `tests/test_tia_onnx_packages.py` 已加入 `battlefield_rtdetr_detector_onnx` |

本地复测：

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_tia_onnx_packages.py -k battlefield_rtdetr -q
```

---

## 5. 注册与调用（algolib）

```powershell
.\build\Release\algolib.exe register .\examples\battlefield_rtdetr_detector_onnx\1.0.0
.\build\Release\algolib.exe activate battlefield_rtdetr_detector_onnx 1.0.0 onnx
```

调用时显式 `backend_type: "onnx"`，并自行构造 letterbox 后的 `images` float32 张量。  
当前 C++ preprocess 子集仍偏张量/分类；**端到端图像检测编排继续用 HTTP 包更稳妥**，ONNX 包适合「已有 NCHW 张量、只要骨干推理」的场景。

---

## 6. 限制与后续建议

1. **体积：** `model.onnx` ≈ 126 MB，提交仓库建议 Git LFS / 对象存储（见 `.gitignore` 注释）。
2. **预处理缺口：** algolib 尚无通用 letterbox / 图像解码适配；完整视觉链路仍依赖 `battlefield_rtdetr_detector`。
3. **后处理：** ONNX 输出为 raw `output0`；业务侧仍需 conf 过滤、可选 NMS、`label_map` 映射、ODConv。
4. **权重版本：** 当前绑定默认 `battlefield_rtdetr.pt`（与 unit17_coco25 同内容）。换权重后执行 `export_rtdetr_onnx.py --force`。
5. **重复文件：** Ultralytics 会在 `models/checkpoints/battlefield_rtdetr.onnx` 旁路落盘；canonical 以 `models/battlefield_rtdetr.onnx` 与 examples 包内拷贝为准，旁路文件可删以省磁盘。

---

## 7. 变更文件一览

| 路径 | 说明 |
|---|---|
| `scripts/export_rtdetr_onnx.py` | 新增导出与打包装脚本 |
| `models/battlefield_rtdetr.onnx` | 导出权重 |
| `examples/battlefield_rtdetr_detector_onnx/1.0.0/` | 原生 ONNX 算法包 |
| `docs/TIA_ONNX_ALGORITHM_PACKAGES.md` | 登记新包；更新「未转 ONNX」说明 |
| `docs/RT_DETR_ONNX_封装报告.md` | 本报告 |
| `tests/test_tia_onnx_packages.py` | 纳入布局 / ORT smoke |
