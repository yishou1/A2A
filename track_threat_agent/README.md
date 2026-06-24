# Track Threat Agent

`track_threat_agent` 是一个近真实 A2A/Nacos 下游态势分析 Agent Demo。它接收上游感知/融合模块输出的结构化 `perception_result`，维护连续航迹，预测未来短时航线，识别疑似编队/编组，分析保护资产影响，并输出统一关注排序和可供 AMOS/A2A 消费的事件。

安全边界：

- 只做仿真态势分析、航迹预测、编组识别和关注优先级排序。
- 不实现真实武器控制。
- 不生成攻击建议。
- 不做制导、火控、打击或交战决策。
- `threat` / `risk` / `impact` 在本 Demo 中只表示态势关注优先级。

## 1. A2A 角色

推荐注册角色：

```text
SERVICE_NAME=A2A-Agent
AGENT_ROLE=track_threat
AGENT_STATUS=idle
```

Commander / Gateway 可通过 Nacos 查询：

```text
serviceName=A2A-Agent
metadata.role=track_threat
metadata.status=idle
```

## 2. 当前能力

- 多目标航迹跟踪。
- 未来 10 / 20 / 30 / 60 / 120 秒航线预测，并输出预测模型、置信度、不确定半径和时域类型。
- 计划书算法 Provider：默认使用 `PlanAlgorithmProvider` 暴露 ST-GNN、DBN、KG+Transformer、XAI 四类正式算法契约。
- ST-GNN 动态实体跟踪与轨迹预测：本地 NumPy 消息传递运行时会构建目标图、计算边注意力、生成节点 embedding，并修正 10 / 20 / 30 / 60 / 120 秒预测点。
- IMM 多模型运动预测：同时生成 `constant_velocity`、`constant_acceleration`、`coordinated_turn` 三类假设，并按概率融合为基础预测线。
- 协方差 Kalman 滤波：`medium` 档使用 CV 状态空间 Kalman 更新，输出 state、covariance、innovation 和 kalman_gain。
- ADE/FDE 回看评估：下一帧到达后记录上一帧预测误差，summary 中输出聚合评估。
- DBN + COA 动态威胁状态评估：输出 low / medium / high 后验概率、COA 概率、dominant_coa 和 threat score。
- KG+Transformer 语义态势推理：把 TacticalIntelligenceAgent 语义字段和 knowledge relations 转成 KG token，通过本地 Transformer 自注意力输出语义态势因子、意图类别概率与证据链。
- TacticalIntelligenceAgent 语义字段消费：`threat_level`、`affiliation`、`label`、`knowledge_graph` 参与排序和资产影响分析。
- 疑似空中编队和海上编组识别。
- 己方保护资产影响分析。
- 单体、群体、资产影响统一关注排序。
- A2A `sendMessage` / `sendMessageStream`。
- `workflow_id` / `work_item` / `work_list` 工作流字段。
- `work_item` 幂等缓存。
- `GET /workflows/{workflow_id}/work-list`。
- `GET /ready`、`POST /lifecycle/ready`、`GET /metrics`，适配 Commander 宕机恢复/ready=false 切换规范。
- Nacos role/status metadata 和 heartbeat_ts 心跳；心跳会保留 Commander 写入的 busy/unavailable/lease_* 状态。
- `AlgorithmProvider` 作为算法边界，默认主线已经切换到本地可运行的计划书算法栈。
- 本地 JSON 状态快照，支持演示环境重启后恢复航迹、最近 artifact、幂等缓存和 workflow work list。

当前工程不再只是“预留接口”。`PlanAlgorithmProvider` 会实际调用本地算法：协方差 Kalman 跟踪、IMM 多模型预测、本地 ST-GNN 消息传递、KG+Transformer 自注意力语义推理、DBN+COA 后验评估和 XAI 证据链。后续公共算法库或 A100 训练版 ST-GNN 完成后，可以替换 `app/algorithm_provider.py` 后端实现和模型权重，但 A2A/Nacos 对外协议不需要改变。

## 3. 启动

```bash
cd track_threat_agent
PYTHONPATH=.. uv run --with-requirements ../requirements.txt --with-requirements requirements.txt \
  uvicorn app.main:app --host 0.0.0.0 --port 8102
```

也可以使用落地演示脚本：

```bash
cd track_threat_agent
./scripts/start_track_threat_agent.sh
```

健康检查：

```bash
curl http://127.0.0.1:8102/health
```

Agent Card：

```bash
curl http://127.0.0.1:8102/.well-known/agent-card
```

ready 和 metrics：

```bash
curl http://127.0.0.1:8102/ready
curl http://127.0.0.1:8102/metrics
curl -X POST http://127.0.0.1:8102/lifecycle/ready \
  -H "Content-Type: application/json" \
  -d '{"ready": false}'
```

## 4. Nacos 配置

默认不强依赖 Nacos，便于本地单独运行。接入 A2A Commander 时可启用：

```bash
export NACOS_ENABLED=true
export NACOS_SERVER=127.0.0.1:8848
export NACOS_NAMESPACE=public
export SERVICE_NAME=A2A-Agent
export SERVICE_IP=127.0.0.1
export SERVICE_PORT=8102
export AGENT_ID=track-threat-group-agent-01
export AGENT_ROLE=track_threat
export AGENT_STATUS=idle
export HEARTBEAT_INTERVAL=5
```

完整示例见 `.env.example`。

完整 Nacos 联调步骤见 `docs/nacos_smoke_test.md`。该文档覆盖 Docker Compose 启动 Nacos、Agent 注册、师兄 `NacosRegistry` 发现、以及通过发现到的 `/sendMessage` endpoint 发起 A2A 调用。

接入 Commander 宕机恢复时，本 Agent 遵守师兄统一规范：

- 服务名使用 `A2A-Agent`。
- metadata 至少包含 `role=track_threat` 和 `status=idle`。
- 存活时持续刷新 `heartbeat_ts` / `heartbeat_at`。
- 如果 Commander 把实例标记为 `busy`、`unavailable` 或写入 `lease_*` 字段，Agent 心跳不会用本地旧 metadata 覆盖这些调度状态。
- 当 `/lifecycle/ready` 设置为 `ready=false` 时，`/sendMessage` 返回标准失败信封，`/sendMessageStream` 返回 503，Commander 可切换到同 role 其他 idle Agent。

## 5. 状态快照

Agent 默认会把可恢复状态保存到仓库根目录：

```text
.a2a_state/track_threat_agent_state.json
```

该目录已被 `.gitignore` 忽略，不会提交到仓库。状态快照保存：

- 当前 `tracks`；
- 当前 `groups`；
- 最近一次 `artifact`；
- `work_item` 幂等缓存；
- SSE 缓存；
- workflow work list；
- processed / failed 计数。

状态快照不保存：

- 当前锁；
- WebSocket 连接；
- 正在运行的 auto demo task；
- Nacos SDK client；
- 启动前未完成的 busy 状态。

服务重启后会恢复为 `idle`。如果上游任务未完成，应由 A2A Gateway / Commander 根据 `work_item` 重试。

如需指定状态文件位置：

```bash
export TRACK_THREAT_STATE_PATH=/tmp/track_threat_agent_state.json
```

## 6. A2A 调用

### 6.1 工作流入口

```http
POST /sendMessage
Authorization: Bearer <token>
Content-Type: application/json
```

示例：

```json
{
  "workflow_id": "wf-demo-001",
  "work_item": "track-threat-step-001",
  "command": "analyze_perception_result",
  "role": "track_threat",
  "work_list": [
    {"activity": "perception_fusion", "role": "recon"},
    {"activity": "track_threat_analysis", "role": "track_threat"},
    {"activity": "situation_display", "role": "commander"}
  ],
  "payload": {
    "task_id": "task-001",
    "message_type": "perception_result",
    "algorithm_level": "medium",
    "scene": {
      "protected_zone_lat": 31.2304,
      "protected_zone_lon": 121.4737,
      "protected_radius_m": 30000,
      "protected_assets": []
    },
    "detections": []
  }
}
```

`work_item` 是幂等键。同一个 `work_item` 重试时，Agent 会返回缓存 artifact，不会重复推进航迹历史、DBN 状态或编组状态。

`/sendMessage` 返回统一 A2A 任务响应信封，业务结果放在 `output` 中，同时为了兼容调试保留顶层 `artifact`：

```json
{
  "workflow_id": "wf-demo-001",
  "work_item": "track-threat-step-001",
  "agent": "track-threat-group-agent",
  "role": "track_threat",
  "command": "analyze_perception_result",
  "status": "completed",
  "output": {
    "message_type": "track_threat_group_artifact",
    "artifact": {}
  },
  "metrics": {"latency_ms": 12.3, "duration_ms": 12.3},
  "error": null,
  "cached": false
}
```

### 6.2 SSE 进度流

```http
POST /sendMessageStream
Authorization: Bearer <token>
Content-Type: application/json
```

返回 `text/event-stream`，包含 `Working`、`Artifact`、`Completed` 等事件。

### 6.3 查询 workflow work list

```bash
curl http://127.0.0.1:8102/workflows/wf-demo-001/work-list
```

## 7. 直接调试入口

保留直接态势输入接口，便于 curl 和本地联调：

```bash
curl -X POST http://127.0.0.1:8102/a2a/perception-result \
  -H "Content-Type: application/json" \
  --data @sample_data/group_scene.json
```

发送 A2A `sendMessage` 演示任务：

```bash
cd track_threat_agent
PYTHONPATH=.. uv run --with-requirements requirements.txt --with-requirements ../requirements.txt \
  python scripts/send_track_threat_demo_task.py --frame 45
```

检查健康、ready 和 Agent Card：

```bash
cd track_threat_agent
PYTHONPATH=.. uv run --with-requirements requirements.txt --with-requirements ../requirements.txt \
  python scripts/check_track_threat_agent.py
```

导出 90 帧长序列场景：

```bash
cd track_threat_agent
PYTHONPATH=.. uv run --with-requirements requirements.txt --with-requirements ../requirements.txt \
  python scripts/export_long_sequence.py --frames 90 --output sample_data/coastal_operation_90_frames.json
```

运行预测评估，对比 `Kalman+IMM` 与 `Kalman+IMM+ST-GNN`：

```bash
cd track_threat_agent
PYTHONPATH=.. uv run --with-requirements requirements.txt --with-requirements ../requirements.txt \
  python -m eval.prediction_eval --frames 90 --output eval/reports/prediction_eval_90_frames.json
```

## 8. 输出

返回 `track_threat_group_artifact`，包含：

- `tracks`：连续航迹、历史路径、预测路径。
- `threats`：单体关注排序。
- `groups`：疑似编队/编组。
- `asset_impacts`：保护资产影响分析。
- `unified_threat_ranking`：单体、群体、资产影响统一排序。
- `events`：`track.updated`、`threat.updated`、`track.group.updated`、`threat.group.updated`、`threat.ranking.updated`、`asset.impact.updated` 等事件。

每个 `tracks[].predicted_path[]` 预测点包含：

- `dt_s`：预测时间差，当前为 10、20、30、60、120 秒。
- `horizon_type`：`short_term` 或 `medium_term`。
- `model_used`：推荐下游读取的模型字段，例如 `adaptive_ctra_turn_graph_refined`。
- `prediction_model`：兼容旧版本的模型字段，内容与 `model_used` 保持一致。
- `prediction_confidence`：预测置信度，时间越长通常越低。
- `uncertainty_radius_m`：仿真预测不确定半径。

更完整的输入、输出、事件和 Nacos metadata 说明见 `docs/agent_protocol_contract.md`。

## 9. 测试

```bash
cd track_threat_agent
uv run --with-requirements requirements.txt --with-requirements ../requirements.txt pytest -q
```

当前测试覆盖：

- 多目标跟踪。
- 历史路径长度控制。
- 威胁排序。
- 编队/编组识别。
- 保护资产影响分析。
- AMOS/A2A 事件适配。
- ST-GNN 动态实体跟踪与轨迹预测，本地 NumPy 消息传递运行时会输出 embedding 和 edge attention。
- KG+Transformer 本地自注意力语义推理。
- DBN+COA 威胁状态后验概率评估。
- 90 帧长序列仿真场景。
- 预测评估脚本和 baseline / enhanced 对比报告。
- `work_item` 幂等。
- `work_list` 查询。

## 10. 当前限制

- 当前是 Demo，不是生产系统。
- 训练版 ST-GNN 权重尚未随仓库提供，当前使用本地 NumPy ST-GNN 消息传递运行时。
- KG+Transformer 当前使用本地 token self-attention，尚未接 Neo4j/LLM 服务。
- 当前使用本地 JSON 文件做轻量状态快照，还不是生产级数据库或分布式状态存储。
- 单实例串行处理，多并发建议通过多 Agent 实例水平扩展。
