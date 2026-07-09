# 内网离线部署指南

项目默认会从 HuggingFace / Ultralytics 在线拉取预训练权重。内网环境采用 **「有网机打包 → U 盘/专线拷贝 → 内网机离线加载」** 两阶段方案。

## 一、有网机器：一次性打包

在项目根目录执行：

```powershell
pip install huggingface_hub ultralytics transformers sentence-transformers torch
python scripts/package_offline_models.py
```

产物目录：

```text
models/
  checkpoints/          # .pt 辅助头、检测器、MARL 等
  pretrained/           # HuggingFace 模型快照（本地目录）
    mask2former-swin-tiny-ade-semantic/
    flan-t5-small/
    paraphrase-MiniLM-L6-v2/
    clip-vit-base-patch32/
  offline_manifest.json # 文件清单 + SHA256（拷贝后校验用）
```

将整个 `models/` 目录（或整个项目）拷贝到内网机器。

若你已有自训权重（如 `battlefield_rtdetr.pt`、`motr_tracker_battlefield.pt`），请一并放入 `models/checkpoints/`。

## 二、内网机器：环境变量

```powershell
set TIA_OFFLINE=1
set HF_HUB_OFFLINE=1
set TRANSFORMERS_OFFLINE=1
set TIA_COMPUTE_PROFILE=offline
set TIA_USE_MOCK=0
```

Linux:

```bash
export TIA_OFFLINE=1 HF_HUB_OFFLINE=1 TRANSFORMERS_OFFLINE=1
export TIA_COMPUTE_PROFILE=offline TIA_USE_MOCK=0
```

## 三、内网机器：校验与启动

```powershell
python scripts/package_offline_models.py --verify-only
python scripts/verify_tia_algorithms.py
./scripts/start_a2a_algorithm_services.ps1 -TiaOnly
```

## 四、配置说明

离线档配置：`config/profiles/offline.yaml`

所有模型 ID 已改为本地路径，例如：

```yaml
mask2former_model: models/pretrained/mask2former-swin-tiny-ade-semantic
semantic_comm_model: models/pretrained/flan-t5-small
page_index_model: models/pretrained/paraphrase-MiniLM-L6-v2
clip_fallback_model: models/pretrained/clip-vit-base-patch32
detection_model: models/checkpoints/battlefield_rtdetr.pt
```

代码在 `TIA_OFFLINE=1` 时会对 HuggingFace 加载使用 `local_files_only=True`，不会尝试联网。

## 五、模型清单

| 用途 | 在线来源 | 离线本地路径 |
|------|----------|--------------|
| 目标检测 | Ultralytics RT-DETR | `models/checkpoints/battlefield_rtdetr.pt` |
| 毁伤分割 | `facebook/mask2former-...` | `models/pretrained/mask2former-swin-tiny-ade-semantic/` |
| 语义压缩 | `google/flan-t5-small` | `models/pretrained/flan-t5-small/` |
| RAG 向量 | `paraphrase-MiniLM-L6-v2` | `models/pretrained/paraphrase-MiniLM-L6-v2/` |
| 多模态嵌入 | CLIP 回退 | `models/pretrained/clip-vit-base-patch32/` |
| 辅助头 | 本地初始化/自训 | `models/checkpoints/*.pt` |

## 六、常见问题

**Q: 内网仍报 `OSError: Can't load model`？**  
A: 检查 `models/pretrained/<name>/config.json` 是否存在；运行 `--verify-only`。

**Q: 不想装 ImageBind？**  
A: 默认走 CLIP 回退，只需打包 `clip-vit-base-patch32`。

**Q: 算法库 HTTP 服务如何离线？**  
A: 启动服务前设置 `TIA_OFFLINE=1` 与 `TIA_COMPUTE_PROFILE=offline`，与 TIA Agent 相同。

**Q: 只有部分模型？**  
A: 可仅用 `TIA_USE_MOCK=1` 做流程联调；实战推理必须备齐 `offline_manifest.json` 中列出的文件。
