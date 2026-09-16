# 独立算法包（解耦自两个本地智能体）

两个智能体各自**编排**算法，但算法实现已抽到本目录，可单独使用。

**目录与上传约定**：见 [LAYOUT.md](./LAYOUT.md)。

| 智能体 | 算法 |
|--------|------|
| 战术情报 TIA | 检测 / 毁伤 / EDL / 跟踪 / ImageBind / 融合 / 分类 / RAG / 语义压缩 / 路由 |
| 任务调度 | `marl_ppo_task_scheduler` |

## 方案 A：契约优先

每个算法只认自己的 **I/O 契约**，不认整条 TIA 流水线。

| 层级 | 路径 | 职责 |
|------|------|------|
| 契约 | `algorithms/contracts/` | required/optional 输入、输出键、别名、校验、`invoke()` |
| JSON Schema | `algorithms/contracts/schemas/<id>/` | 独立调用的 input/output 文档 |
| 适配器 | `agent/adapters/pipeline.py` | Skill 把流水线上下文翻译成契约输入 |
| 算法包 | `algorithms/<id>/` | 只实现 `predict` / `Backend.run` |

```python
from algorithms import invoke, get_contract, contract_summary

# 推荐：带契约校验的单独调用
out = invoke(
    "battlefield_rtdetr_detector",
    {"frames": [{"sensor_id": "s1", "modality": "eo_ir"}]},
    use_mock=True,
)

# 查看契约
print(get_contract("edl_evidential_verifier").required_inputs)
```

```bash
python -m algorithms --list-contracts
python -m algorithms battlefield_rtdetr_detector --inputs inputs.json --mock
```

HTTP 方式不变：`POST http://127.0.0.1:<port>/predict`（9020–9030）。

## 单独调用（兼容）

```python
from algorithms.battlefield_rtdetr_detector import predict

out = predict({"frames": [{"sensor_id": "s1", "modality": "eo_ir"}]}, use_mock=True)
# {"detections": [...], "count": N}
```

## 边界说明

- **已解耦（方案 A）**：契约冻结；编排侧经 adapter 拼装输入；算法不互相 import。
- **仍由 Agent 编排**：TIA Skill 负责上下游拼接（产品流水线耦合，不是算法实现耦合）。
- **权重/推理**：真实推理仍复用 `agent.inference.*`（后续方案 B 可再下沉）。

## 算力三档（识别同权重，推理加减负）

**已纳入算法库**：`algorithms.compute_tiers` / `list_tiers()` / `invoke(..., tier=...)`。  
配置源：`config/profiles/{small,medium,large}.yaml`（`param_tier`: low/mid/high）。

**原则**：检测 / 跟踪用**同一套已训权重**（97 类 best + MOTR battlefield），不换不会识别的小模型。  
低/中档只省算力：更小 `imgsz`、FP16、可选关闭 ODConv。

| 档位 | 别名 | 检测权重 | imgsz | half | ODConv | 跟踪权重 | `motr_variant` |
|------|------|----------|-------|------|--------|----------|----------------|
| 低 | `small`/`low` | 97 类 best | 416 | 开 | 关 | MOTR battlefield | high（同结构） |
| 中 | `medium`/`mid` | 97 类 best | 640 | 开 | 开 | MOTR battlefield | high |
| 高 | `large`/`high` | 97 类 best | 1280 | 关 | 开 | MOTR battlefield | high |

```python
from algorithms import list_tiers, invoke, apply_tier, tier_summary

list_tiers()                          # 查看三档卡片
cfg = apply_tier({}, tier="low")      # 合并 small.yaml
invoke("battlefield_rtdetr_detector", {"frames": [...]}, tier="mid", use_mock=True)
```

```bash
python -m algorithms --list-tiers
python -m algorithms battlefield_rtdetr_detector --inputs in.json --tier low --mock
# 或: set TIA_COMPUTE_PROFILE=low
```

### RT-DETR / MOTR / 毁伤 / 编码 / EDL / 路由 近期对齐点

- **检测**：默认权重文件名仍为 `battlefield_rtdetr_unit17_coco25_ap_kaggle_best.pt`（三档 profile）；内容已替换为 **unit17_ap_finetune best**（机场+飞机强化）。`odconv_enable` / `detection_half` / `detection_max_det`；输出 `rtdetr_confidence`、`odconv_confidence`；大框（面积≥20%）与 ODConv 轻融合。ONNX 骨干：`examples/battlefield_rtdetr_detector_onnx`（`scripts/export_rtdetr_onnx.py --force` 重导）。
- **跟踪**：`MOTRTracker(variant=…)` 支持 low/mid/high 结构；现网 profile 仍固定 `motr_variant: high` + battlefield 权重。
- **毁伤**：`damage_backend` + `param_tier` → low 轻量 CNN / mid-high Mask2Former；输出含 `mask_pixels`。
- **编码**：`embed_backend` 三档（MobileNet / ResNet18 / ImageBind）；`predict` 回传 `embed_backend`/`embed_dim`/`param_tier`。
- **EDL**：三态门控；默认阈值 `0.35` / `0.45`；完整 `assessments` 报告。
- **路由**：`anti_jam_mcdm_router` 默认 SAW；可选 PPO（已移除 `marl_dynamic_router`）。
- 契约与 examples schema：`algorithms/contracts/schemas/` 与上述字段同步。

毁伤 / 编码等非识别主路径仍可在低档用轻量后端（不影响检测类别）。  
若以后要「真·更小参数且精度接近」，需对 97 类数据做**蒸馏**或量化，而不是直接换 YOLO-nano。

切换：`TIA_COMPUTE_PROFILE=low|mid|high` 或 `invoke(..., tier="low|mid|high")`。

## 成熟度

| 级别 | 算法 |
|------|------|
| production | `battlefield_rtdetr_detector` |
| stable | 毁伤、EDL、跟踪、调度、ImageBind、SupCon、语义压缩 |
| research（默认确定性主路径） | Mamba 融合、SynapseRAG；神经网络需 `prefer_deterministic: false` |
| stable（工业可审计） | `anti_jam_mcdm_router`：默认 SAW（Hwang & Yoon 1981），可选 PPO |

见 `algorithms.ALGORITHM_MATURITY`。
