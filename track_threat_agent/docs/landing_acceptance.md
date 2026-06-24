# Track Threat Agent 落地验收说明

本文用于本周把 `track_threat_agent` 作为近真实 A2A/Nacos 下游 Agent 落地验收。所有能力仍限定为仿真态势分析、航迹预测、关注排序和保护资产影响分析，不做武器控制、打击建议、制导或交战决策。

## 1. 本周落地目标

Agent 需要完成以下闭环：

```text
上游 perception_result / A2A task
  -> Track Threat Agent
  -> Kalman 航迹更新
  -> IMM 基础预测
  -> ST-GNN 图关系预测修正
  -> KG+Transformer 语义态势推理
  -> DBN+COA 威胁状态评估
  -> GroupDetector 编队/编组识别
  -> AssetImpactAnalyzer 保护资产影响分析
  -> XAI 证据链
  -> A2A artifact + AMOS-style events
```

## 2. 启动 Agent

```bash
cd /Users/mac/Desktop/多阈协同作战/yishou1-A2A/track_threat_agent
./scripts/start_track_threat_agent.sh
```

如果需要启用 Nacos：

```bash
export NACOS_ENABLED=true
export NACOS_SERVER=127.0.0.1:8848
export SERVICE_NAME=A2A-Agent
export SERVICE_IP=127.0.0.1
export SERVICE_PORT=8102
export AGENT_ROLE=track_threat
export AGENT_STATUS=idle
./scripts/start_track_threat_agent.sh
```

## 3. 健康检查

```bash
cd /Users/mac/Desktop/多阈协同作战/yishou1-A2A/track_threat_agent
PYTHONPATH=.. uv run --with-requirements requirements.txt --with-requirements ../requirements.txt \
  python scripts/check_track_threat_agent.py
```

验收点：

- `health.status=ok`
- `ready.ready=true`
- `agent_card.role=track_threat`
- `capabilities` 中包含 ST-GNN、DBN、KG+Transformer、资产影响分析和 XAI。

## 4. A2A 调用演示

```bash
cd /Users/mac/Desktop/多阈协同作战/yishou1-A2A/track_threat_agent
PYTHONPATH=.. uv run --with-requirements requirements.txt --with-requirements ../requirements.txt \
  python scripts/send_track_threat_demo_task.py --frame 45
```

验收点：

- 返回 `status=completed`
- `summary.track_count=7`
- `summary.group_count>=1`
- `summary.asset_impact_count>0`
- `ranking_top_3` 有单体、群体或资产影响排序项。

## 5. 长序列场景

导出 90 帧长序列：

```bash
cd /Users/mac/Desktop/多阈协同作战/yishou1-A2A/track_threat_agent
PYTHONPATH=.. uv run --with-requirements requirements.txt --with-requirements ../requirements.txt \
  python scripts/export_long_sequence.py --frames 90 --output sample_data/coastal_operation_90_frames.json
```

场景阶段：

- `0-14`：初始发现。
- `15-34`：航迹稳定。
- `35-44`：保护资产监控，低空 UAV 改变航向。
- `45-74`：unknown 航向/速度突变，触发 anomaly。
- `75-89`：持续监视和排序稳定输出。

场景目标：

- 3 架 `aircraft` 形成空中编队。
- 2 艘 `ship` 形成海上编组。
- 1 架 `uav` 绕飞保护资产。
- 1 个 `unknown` 在第 45 帧后出现异常机动。
- 4 个 protected assets：指挥节点、岸基雷达、后勤码头、医疗集结点。

## 6. 预测评估

```bash
cd /Users/mac/Desktop/多阈协同作战/yishou1-A2A/track_threat_agent
PYTHONPATH=.. uv run --with-requirements requirements.txt --with-requirements ../requirements.txt \
  python -m eval.prediction_eval --frames 90 --output eval/reports/prediction_eval_90_frames.json
```

报告包含：

- `baseline`：`Kalman+IMM`
- `enhanced`：`Kalman+IMM+ST-GNN`
- `mean_ade_m`
- `mean_fde_m`
- `rmse_m`
- `uncertainty_hit_rate`
- `st_gnn_delta`

说明：

- `mean_ade_delta_m < 0` 表示 ST-GNN 修正降低平均预测误差。
- `mean_fde_delta_m < 0` 表示 ST-GNN 修正降低远期预测误差。
- 如果某次仿真中 delta 为正，说明当前固定权重 ST-GNN 在该场景不如 baseline，需要进入后续训练权重优化阶段。

## 7. 全量测试

Agent 内测试：

```bash
cd /Users/mac/Desktop/多阈协同作战/yishou1-A2A/track_threat_agent
uv run --with-requirements requirements.txt --with-requirements ../requirements.txt pytest -q
```

仓库级测试：

```bash
cd /Users/mac/Desktop/多阈协同作战/yishou1-A2A
PYTHONPATH=. uv run --with-requirements requirements.txt --with pytest --with httpx pytest -q
```

## 8. 汇报口径

可以这样汇报：

> 本周重点不是继续扩展新算法，而是把 Track Threat Agent 落地成可运行、可注册、可调用、可评估的近真实下游 Agent。当前 Agent 能接收 A2A perception_result，维护连续航迹，输出未来航线预测、疑似编队/编组、保护资产影响、统一关注排序和 AMOS-style events。算法链路包括 Kalman、IMM、ST-GNN、KG+Transformer、DBN+COA 和 XAI。我们还补充了 90 帧长序列仿真和预测评估脚本，可以对比 `Kalman+IMM` 与 `Kalman+IMM+ST-GNN` 的 ADE/FDE/RMSE。

## 9. 当前限制

- 当前 ST-GNN 是本地 NumPy 消息传递运行时，尚未接训练权重。
- KG+Transformer 是本地 token self-attention，尚未接 Neo4j/LLM 服务。
- 评估数据来自仿真长序列，不代表真实战场性能。
- 所有 `threat` / `risk` / `impact` 只表示态势关注优先级。
