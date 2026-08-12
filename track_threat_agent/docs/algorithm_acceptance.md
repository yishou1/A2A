# 算法交付与验收门禁

## 1. 文档目的

本文档用于区分“演示可运行”、“工程联调可用”和“可向甲方交付”三种状态。任何单一演示场景跑通都不等于算法已通过交付验收。

所有 `threat` / `risk` 输出仅表示态势关注优先级，不用于武器控制、攻击建议、制导、火控或交战决策。

## 2. 当前算法链

```text
检测帧
→ 卡尔曼状态预测和协方差门控
→ 全局最近邻关联（Hungarian assignment）
→ Kalman / alpha-beta 更新
→ 自适应 CV/CA/CT 物理预测回退 + ST-GNN 残差预测
→ 完整连接约束的编队识别
→ 预测路径与不确定性感知的威胁排序
→ DBN 时序平滑与 XAI 证据链
→ 保护资产影响与统一排序
```

## 3. 硬性验收指标

| 模块 | 指标 | 交付门槛 |
|---|---|---|
| 航迹关联 | 关联准确率 | 独立标注集 `>= 99%` |
| 航迹关联 | ID switch | 标准测试场景为 `0`，复杂场景单独报告 |
| 航迹管理 | 误建航迹、丢轨率、TTL | 不高于项目数据合同阈值；TTL 为 300 秒 |
| 飞机预测 | ADE / FDE | 相比最强物理基线均改善 `>= 10%` |
| 船舶预测 | ADE / FDE | 相比最强物理基线均改善 `>= 10%` |
| 不确定性 | 90% 区间覆盖率 | `85%–95%` |
| 编组识别 | Precision / Recall / F1 | 甲方标注集各项 `>= 0.90` |
| 编组稳定性 | group ID churn | 稳定编队场景不变号；短时漏报可恢复原 ID |
| 威胁排序 | NDCG@K / Top-K Recall | 需甲方或专家标注集，目标 `>= 0.90` |
| 风险概率 | Brier / ECE | 必须进行校准并随版本报告，不得只交付经验阈值 |
| 可解释性 | 证据完整率 | `100%` 排序项含 reason/evidence/factors/model trace |
| CPU 性能 | 200 节点 / 2000 边 P95 | `<= 200 ms` |
| 内存和模型 | 模型包与进程内存 | 单模型包 `<= 5 MB`；目标进程 `<= 512 MB` |
| 安全降级 | 模型缺失/超时/坏包 | `ST_GNN_REQUIRED=false` 时单帧回退自适应 CV/CA/CT，Agent 不中断 |
| 模型发布 | release gate | 验收/生产设置 `ST_GNN_ENFORCE_RELEASE_GATE=true`，candidate 模型不允许冒充 release 模型 |

## 4. 当前门禁状态

- 船舶 ST-GNN：已通过独立测试集 release gate，ADE/FDE 相比最强 CV 基线改善 53.25%/53.11%，90% 区间实际覆盖率 92.93%。
- 飞机 ST-GNN：已通过独立测试集 release gate，ADE/FDE 相比最强 IMM 基线改善 13.96%/18.43%，90% 区间实际覆盖率 90.26%。
- 在线航迹关联：已升级为物理距离 + 协方差 NIS 门控 + 全局最小代价分配。
- 编组识别：已阻止连通分量的链式误并，但尚缺甲方标注场景的 Precision/Recall/F1。
- 威胁排序：已消费 ST-GNN/自适应物理 `predicted_path`、预测置信度和不确定性；DBN 参数已版本化，仍需使用甲方或专家标注数据校准并冻结发布版本。
- CPU 模型性能（本次 Mac 开发机，200 节点/2000 边，50 次）：飞机 206,352 参数、866,796 字节、P95 2.574 ms；船舶 205,320 参数、862,700 字节、P95 2.465 ms。两者均小于 1 MB 且通过 200 ms 工程门限；甲方目标 CPU 仍需重复测试。
- 90 帧工程回归：584 次可见目标关联准确率 100%，ID switch=0，误建航迹=0，group ID churn=0，排序解释覆盖率 100%，本次开发机整体 CPU P95 为 3.835 ms。结果保存在 `artifacts/algorithm_pipeline_benchmark.json`；这是确定性工程场景结果，不代表甲方真实数据结果。

因此，当前版本已达到“工程联调与演示可用”，两个 ST-GNN 模型已通过现有数据门禁。但在编组标注集、威胁标注/校准集和甲方 CPU 现场复验完成前，不应宣称为“甲方数据验收已通过”。

## 5. 可执行工程回归

```bash
cd track_threat_agent
uv run --with-requirements requirements.txt --with-requirements ../requirements.txt \
  python scripts/benchmark_algorithm_pipeline.py \
  --frames 90 \
  --output artifacts/algorithm_pipeline_benchmark.json
```

脚本内置编队、船舶编组、交叉目标、检测乱序、坐标噪声和短时漏报，输出 JSON 门禁报告。该脚本是持续集成的工程回归，不代替甲方真实数据验收。

TorchScript 大图 CPU 性能单独执行：

```bash
uv run --with-requirements requirements.txt --with-requirements requirements-model.txt \
  python scripts/benchmark_st_gnn_runtime.py \
  --model-dir models/track_threat/st_gnn_ship_kaggle_v1
```

## 6. 交付前必须补齐的数据

1. 甲方传感器样例与时钟、坐标、速度、置信度定义。
2. 带真值 ID 的交叉、遮挡、漏报、重复检测和乱序帧。
3. 编队成立、拆分、合并和短时成员丢失的标注场景。
4. 专家给出的态势关注排序和 low/medium/high 标注，用于 DBN 校准。
5. 甲方 CPU 型号、内存上限、单帧最大目标/边数和延迟预算。

## 7. DBN 标注集验收

`scripts/evaluate_dbn_calibration.py` 接收 JSON 数组或 JSONL。每条至少包含 `sequence_id`、`track_id`、`object_type`、`base_score`、`factors` 和专家标注 `label=low|medium|high`。同一 `sequence_id` 按文件顺序保留 DBN 时序状态，不同序列相互隔离。

```bash
PYTHONPATH=.. uv run --with-requirements requirements.txt --with-requirements ../requirements.txt \
  python scripts/evaluate_dbn_calibration.py \
  --input /path/to/expert_labeled_attention.jsonl \
  --output artifacts/dbn_calibration_report.json \
  --max-brier 0.20 \
  --max-ece 0.08
```

报告输出 multiclass Brier、ECE、NLL、Accuracy、Macro-F1、置信度分箱和混淆矩阵，并记录 DBN 参数版本与 SHA256。在没有甲方或专家标注前，该工具可运行但不生成虚假“已校准”结论。
