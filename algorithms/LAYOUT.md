# 算法库目录布局（上传 GitHub 用）

## 规范根目录（应纳入版本库）

```
algorithms/                          # 【唯一算法实现根】11 个独立包 + 契约 + 三档
  ├── __init__.py / __main__.py / base.py / compute_tiers.py / README.md
  ├── contracts/                     # I/O 契约与 JSON Schema
  └── <algorithm_id>/{__init__,backend}.py

config/profiles/{small,medium,large,offline}.yaml   # 高/中/低算力档（param_tier）

examples/<algorithm_id>/1.0.0/       # 算法卡、schema、golden（对外包装）
services/<algorithm_id>/             # 薄 HTTP 适配（9020–9030）
services/a2a_algorithms_common/      # 公共 HTTP 工厂 + predictors
services/tia_algolib_gateway/        # 可选：集中网关（含 /compute-tiers）

agent/algorithm_library/             # TIA 目录/远程调用（编排层，非算法本体）
agent/inference/                     # 真推理权重加载（算法真路径延迟导入）
```

## 算力三档

| tier | profile yaml | 入口 |
|------|--------------|------|
| low | `config/profiles/small.yaml` | `invoke(..., tier="low")` / `TIA_COMPUTE_PROFILE=low` |
| mid | `config/profiles/medium.yaml` | `tier="mid"` |
| high | `config/profiles/large.yaml` | `tier="high"` |

库内 API：`algorithms.list_tiers()` / `apply_tier()` / `python -m algorithms --list-tiers`

## 独立性约定

- **算法之间**：禁止互相 import（仅允许 `algorithms.base` / `contracts` / 本包）。
- **mock 路径**：不得依赖 `agent.*` / torch（可独立单测、可无权重演示）。
- **真推理路径**：允许 `from agent.inference...` 延迟导入（权重仍在本仓库 `models/`）。
- **Agent skills**：只允许 `algorithms.*` 再导出，不得反向成为实现源。

## 不要上传 / 勿当规范源

| 路径 | 原因 |
|------|------|
| `RT-DETR_onnx/` | 与 `examples/battlefield_rtdetr_detector_onnx` 重复，且体积大 |
| `models/checkpoints/*.pt` | 权重；用 LFS 或发布物 |
| `.venv/` | 本地环境 |
| 已删除的 `marl_dynamic_router` | 已由 `anti_jam_mcdm_router` 替代 |

## RT-DETR：Python 服务 ↔ ONNX

| 形态 | 路径 | 说明 |
|------|------|------|
| Python 算法 + HTTP | `algorithms/battlefield_rtdetr_detector` · `services/...` · `examples/battlefield_rtdetr_detector` | 整网：解码/letterbox/NMS/ODConv |
| **ONNX 骨干包** | `examples/battlefield_rtdetr_detector_onnx/1.0.0/` | `backend_type: onnx`，仅前向 |
| 默认权重 | `models/checkpoints/battlefield_rtdetr_unit17_coco25_ap_kaggle_best.pt` | 文件名不变；当前内容为 **unit17_ap_finetune best**（机场+飞机强化续训，97 类） |
| 别名权重 | `models/checkpoints/battlefield_rtdetr.pt` | 与上同内容，供健康检查 / 旧脚本 |
| 导出 | `scripts/export_rtdetr_onnx.py` | 默认读上述 kaggle best 文件名，写出 `models/battlefield_rtdetr.onnx` + examples 包 |

```powershell
.\.venv\Scripts\python.exe scripts\export_rtdetr_onnx.py --force
# 等价显式指定：
.\.venv\Scripts\python.exe scripts\export_rtdetr_onnx.py --force --weights models/checkpoints/battlefield_rtdetr_unit17_coco25_ap_kaggle_best.pt
```

换权重后务必 `--force` 重导 ONNX，再按需 `algolib.exe register/activate` 刷新本地算法库登记。

## 11 个算法 ID

`battlefield_rtdetr_detector` · `siamese_mask2former_damage` · `edl_evidential_verifier` · `motr_neural_kalman_tracker` · `marl_ppo_task_scheduler` · `imagebind_multimodal_encoder` · `multimodal_mamba_fusion` · `supcon_meta_classifier` · `synapse_rag_retriever` · `knowledge_semantic_comm` · `anti_jam_mcdm_router`
