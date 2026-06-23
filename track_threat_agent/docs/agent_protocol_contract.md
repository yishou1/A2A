# Track Threat Agent Protocol Contract

本文档定义 `track_threat_agent` 作为下游 A2A/Nacos Agent 时的输入、输出、Nacos metadata 和 AMOS/态势前端事件格式。本文档面向上游感知/融合同学、A2A Gateway/Commander 同学、AMOS/态势展示同学和汇报验收。

安全边界：本文档中的 `threat`、`risk`、`impact` 只表示仿真态势关注优先级，不表示攻击建议、武器控制、打击决策、制导或交战意图。

## 1. Agent 定位

`track_threat_agent` 接收上游态势感知结果，输出：

- 多目标连续航迹；
- 未来航线预测；
- 疑似编队/编组；
- 己方保护资产影响分析；
- 单体、群体、资产影响统一关注排序；
- 可由 AMOS 或其他态势前端消费的事件。

推荐 A2A/Nacos 角色：

```text
SERVICE_NAME=A2A-Agent
AGENT_ROLE=track_threat
AGENT_STATUS=idle
```

## 2. 上游最小输入：perception_result

直接调试入口：

```http
POST /a2a/perception-result
Content-Type: application/json
```

最小结构：

```json
{
  "task_id": "task-001",
  "message_type": "perception_result",
  "algorithm_level": "medium",
  "scene": {
    "protected_zone_lat": 31.2304,
    "protected_zone_lon": 121.4737,
    "protected_radius_m": 30000,
    "protected_assets": []
  },
  "detections": [
    {
      "detection_id": "det-air-001",
      "object_type": "aircraft",
      "timestamp": 1781233000.0,
      "lat": 31.102,
      "lon": 121.318,
      "alt": 5200.0,
      "speed": 210.0,
      "heading": 72.0,
      "confidence": 0.92,
      "source_agent": "perception-fusion-agent",
      "metadata": {
        "affiliation": "unknown",
        "label": "fast_air_target",
        "threat_level": "medium"
      }
    }
  ]
}
```

必需字段：

```text
task_id
message_type
detections[].detection_id
detections[].object_type
detections[].timestamp
detections[].lat
detections[].lon
detections[].speed
detections[].heading
detections[].confidence
```

推荐字段：

```text
scene.protected_assets
detections[].alt
detections[].source_agent
detections[].metadata.affiliation
detections[].metadata.label
detections[].metadata.threat_level
detections[].metadata.knowledge_relations
```

`object_type` 取值：

```text
aircraft
ship
uav
unknown
```

`algorithm_level` 取值：

```text
small   最近邻关联 + alpha-beta 滤波
medium  最近邻关联 + 简化 Kalman-like 滤波
large   当前为接口占位，回退 medium，并在 metadata 标注 large_mock
```

## 3. A2A 工作流输入：sendMessage

近真实联调入口：

```http
POST /sendMessage
Authorization: Bearer <token>
Content-Type: application/json
```

结构：

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
    "scene": {},
    "detections": []
  }
}
```

字段说明：

- `workflow_id`：一次跨 Agent 工作流编号。
- `work_item`：幂等键。同一个 `work_item` 重试时返回缓存结果，不重复推进航迹历史。
- `command`：当前推荐 `analyze_perception_result`。
- `role`：当前 Agent 角色，应为 `track_threat`。
- `work_list`：工作流步骤列表，用于查询和汇报。
- `payload`：标准 `perception_result`。

### 3.1 sendMessage 统一响应信封

`/sendMessage` 按师兄 A2A 宕机恢复接入规范返回统一任务响应信封。Commander 判断成功/失败时读取 `status` 和 `error`；业务结果统一放在 `output` 内。

```json
{
  "workflow_id": "wf-demo-001",
  "work_item": "track-threat-step-001",
  "agent": "track-threat-group-agent",
  "role": "track_threat",
  "command": "analyze_perception_result",
  "status": "completed",
  "output": {
    "task_id": "task-001",
    "message_type": "track_threat_group_artifact",
    "artifact": {}
  },
  "metrics": {
    "latency_ms": 12.3,
    "duration_ms": 12.3,
    "track_count": 7,
    "group_count": 2,
    "ranking_count": 10
  },
  "error": null,
  "message": "Track/threat situation analysis completed",
  "attempts": 1,
  "cached": false
}
```

兼容说明：为了方便本地 curl 调试，响应仍保留顶层 `artifact` 和 `artifact_summary`，但上游/Commander 推荐读取 `output.artifact`。

ready=false 时返回标准失败信封：

```json
{
  "workflow_id": "wf-demo-001",
  "work_item": "track-threat-step-001",
  "agent": "track-threat-group-agent",
  "role": "track_threat",
  "command": "analyze_perception_result",
  "status": "failed",
  "output": {},
  "error": "agent is not ready",
  "error_code": "AGENT_NOT_READY",
  "message": "agent is not ready",
  "attempts": 1,
  "cached": false
}
```

## 4. 输出 Artifact

成功响应：

```json
{
  "task_id": "task-001",
  "message_type": "track_threat_group_artifact",
  "status": "completed",
  "artifact": {
    "protected_assets": [],
    "tracks": [],
    "threats": [],
    "asset_impacts": [],
    "groups": [],
    "unified_threat_ranking": [],
    "events": [],
    "summary": {}
  }
}
```

### 4.1 tracks

每条 track 包含：

```text
track_id
object_type
lat/lon/alt
speed
heading
vx/vy
track_quality
last_update_time
missed_count
history_path
predicted_path
metadata
```

`history_path` 最多保存 50 个点，防止内存无限增长。

### 4.2 predicted_path

每个预测点包含：

```json
{
  "dt_s": 10.0,
  "timestamp": 1781233010.0,
  "lat": 31.108,
  "lon": 121.331,
  "alt": 5200.0,
  "speed": 210.0,
  "heading": 72.0,
  "model_used": "imm_fused_graph_refined",
  "prediction_model": "imm_fused_graph_refined",
  "primary_model": "adaptive_constant_velocity",
  "model_probabilities": {
    "constant_velocity": 0.62,
    "constant_acceleration": 0.21,
    "coordinated_turn": 0.17
  },
  "prediction_confidence": 0.89,
  "uncertainty_radius_m": 86.4,
  "horizon_type": "short_term",
  "st_gnn_inspired": true,
  "st_gnn": {
    "algorithm": "ST-GNN",
    "contract": "dynamic_entity_tracking_and_trajectory_prediction",
    "runtime": "local_numpy_message_passing",
    "runtime_provider": "local_numpy_message_passing",
    "message_passing_layers": 2,
    "node_embedding": [0.12, -0.04, 0.31, 0.65],
    "edge_attention": [{"track_id": "trk-xxx", "attention_weight": 0.62}],
    "decoder_adjustment": {
      "vx_delta_mps": 1.7,
      "vy_delta_mps": -0.6,
      "accel_x_mps2": 0.02,
      "accel_y_mps2": 0.01
    },
    "trained_model_loaded": false,
    "graph_neighbor_count": 2,
    "graph_influence": 0.74
  },
  "graph_neighbor_count": 2,
  "graph_influence": 0.74
}
```

当前预测时域：

```text
10 秒  short_term
20 秒  short_term
30 秒  short_term
60 秒  medium_term
120 秒 medium_term
```

`model_used` 是推荐给下游读取的字段。`prediction_model` 保留用于兼容旧版本。

`uncertainty_radius_m` 表示仿真预测不确定半径，时间越长、机动越明显、航迹质量越低，该半径越大。

当前对外算法契约采用计划书中的 ST-GNN 动态实体跟踪与轨迹预测。本地运行时先用 `constant_velocity`、`constant_acceleration`、`coordinated_turn` 三类运动假设生成 IMM 基础预测线，再用 NumPy ST-GNN 消息传递根据邻近编队/编组关系修正短时预测。完整多假设结果保存在：

```text
tracks[].metadata.prediction.prediction_hypotheses
```

下一帧到达后，Agent 会把上一帧预测与当前检测位置进行回看比较，输出：

```text
tracks[].metadata.prediction_eval.ade_m
tracks[].metadata.prediction_eval.fde_m
artifact.summary.prediction_eval
```

### 4.3 threats

单体关注排序包含：

```text
threat_id
track_id
score
level
rank
factors
evidence
timestamp
metadata
```

`score` 范围为 0 到 1。`level` 为 `low`、`medium`、`high`。

### 4.4 groups

疑似编队/编组包含：

```text
group_id
group_type
member_track_ids
centroid
centroid_prediction
envelope
predicted_envelope
cohesion_score
group_threat_score
group_threat_level
evidence
timestamp
```

`group_type` 取值：

```text
air_formation
surface_group
mixed_group
unknown_group
```

### 4.5 asset_impacts

保护资产影响分析包含：

```text
impact_id
protected_asset_id
protected_asset_name
source_track_id
source_object_type
score
level
rank
closest_distance_m
predicted_closest_distance_m
factors
evidence
timestamp
```

该结果表示“哪些目标更值得关注，因为它们可能影响某个保护资产”，不是攻击建议。

## 5. Events

Agent 输出的 `events` 可由 A2A Gateway 或 AMOS Bridge 转发到态势前端。

主要事件：

```text
protected.asset.updated
asset.updated
asset.relationship.updated
track.updated
threat.updated
track.group.updated
threat.group.updated
asset.impact.updated
threat.ranking.updated
```

### 5.1 track.updated

```json
{
  "event_type": "track.updated",
  "track_id": "trk-xxx",
  "object_type": "aircraft",
  "current_position": {"lat": 31.1, "lon": 121.3, "alt": 5200},
  "speed": 210,
  "heading": 72,
  "history_path": [],
  "predicted_path": [],
  "track_quality": 0.91,
  "metadata": {},
  "timestamp": 1781233000.0
}
```

### 5.2 threat.ranking.updated

```json
{
  "event_type": "threat.ranking.updated",
  "ranking": [
    {
      "rank": 1,
      "item_type": "asset_impact",
      "item_id": "blue-coastal-radar",
      "score": 0.66,
      "level": "medium"
    }
  ]
}
```

## 6. Nacos Metadata

Agent 注册到 Nacos 的服务名：

```text
A2A-Agent
```

发现条件：

```text
role=track_threat
status=idle
```

关键 metadata：

```text
agent_id=track-threat-group-agent-01
role=track_threat
status=idle
send_message_endpoint=http://127.0.0.1:8102/sendMessage
send_message_stream_endpoint=http://127.0.0.1:8102/sendMessageStream
work_list_endpoint=http://127.0.0.1:8102/workflows/{workflow_id}/work-list
health_endpoint=http://127.0.0.1:8102/health
ready_endpoint=http://127.0.0.1:8102/ready
metrics_endpoint=http://127.0.0.1:8102/metrics
agent_card=http://127.0.0.1:8102/.well-known/agent-card.json
skills=trajectory_tracking,st_gnn_dynamic_entity_tracking,dynamic_bayesian_network_threat_assessment,kg_transformer_semantic_sitrep,group_detection,group_threat_ranking,protected_asset_impact_analysis,xai_evidence_generation
algorithm_family=st_gnn,dbn,kg_transformer,xai,imm,kalman
runtime_providers=local_numpy_st_gnn_message_passing,dbn_with_coa_probability_runtime,kg_transformer_self_attention_runtime,covariance_kalman_cv_filter
fallback_providers=baseline_motion_provider
algorithm_levels=small,medium,large
heartbeat_ts=<recent unix timestamp>
```

Nacos 只负责服务发现、健康和能力 metadata，不承载每一帧航迹数据。航迹数据通过 A2A HTTP/SSE 或 AMOS Bridge 传输。

心跳保护约定：

- Agent 存活时持续刷新 `heartbeat_ts` 和 `heartbeat_at`。
- Commander 领取租约后可能直接在 Nacos 写入 `status=busy`、`lease_workflow_id`、`lease_work_item`。
- Commander 判定宕机或断心跳后可能写入 `status=unavailable`、`unavailable_reason`、`unavailable_workflow_id`、`unavailable_work_item`。
- 本 Agent 每次发送心跳前会读取 Nacos 当前实例 metadata；如果发现上述调度状态，会保留这些字段，只刷新心跳时间，避免把 Commander 的 busy/unavailable 状态覆盖回本地旧 idle。

### 6.1 Ready / Metrics 接口

```http
GET /ready
POST /lifecycle/ready
GET /metrics
```

`/ready` 返回当前是否可接任务：

```json
{
  "ready": true,
  "agent": "track-threat-group-agent",
  "role": "track_threat",
  "agent_status": "idle",
  "active_tasks": 0
}
```

`POST /lifecycle/ready` 可临时切换接单状态：

```bash
curl -X POST http://127.0.0.1:8102/lifecycle/ready \
  -H "Content-Type: application/json" \
  -d '{"ready": false}'
```

当 `ready=false` 时，`/sendMessage` 返回 `status=failed,error=agent is not ready`，`/sendMessageStream` 返回 503。Commander 可将该实例视为不可用并切换到同 role 其他 idle Agent。

## 7. 状态快照和重试语义

Agent 默认把可恢复状态写入：

```text
.a2a_state/track_threat_agent_state.json
```

可通过环境变量覆盖：

```bash
export TRACK_THREAT_STATE_PATH=/path/to/track_threat_agent_state.json
```

状态快照保存：

```text
tracks
groups
last_artifact
task_response_cache
stream_response_cache
workflow_work_lists
processed_task_count
failed_task_count
```

状态快照不保存：

```text
processing_lock
websocket connections
auto_demo_task
Nacos client
current busy work item
```

重启恢复策略：

- Agent 从快照恢复航迹、最近 artifact 和幂等缓存。
- Agent 启动后状态统一回到 `idle`。
- 如果上游没有收到上一次响应，应使用相同 `work_item` 重试。
- 如果该 `work_item` 已经完成并被缓存，Agent 返回缓存 artifact，且 `cached=true`。
- 如果该 `work_item` 未完成，Agent 会重新处理。

## 8. AMOS/态势前端对接建议

AMOS 或其他态势前端至少需要消费：

```text
track.updated.predicted_path
track.updated.history_path
track.group.updated.envelope
track.group.updated.centroid_prediction
protected.asset.updated
asset.impact.updated
threat.ranking.updated
```

如果前端要显示预测可信度，可以读取：

```text
predicted_path[].model_used
predicted_path[].prediction_confidence
predicted_path[].uncertainty_radius_m
predicted_path[].horizon_type
```

## 9. 当前限制

- 当前仍是 Demo，不是生产系统。
- 训练版 ST-GNN 权重尚未随仓库提供；当前使用本地 NumPy ST-GNN 消息传递运行时。
- KG+Transformer 当前使用本地 token self-attention，尚未接 Neo4j/LLM 服务。
- 状态使用本地 JSON 快照恢复，不是生产级数据库或分布式状态存储。
- 单实例采用串行处理；并发建议通过多个 Agent 实例注册到 Nacos。
- `threat` / `risk` / `impact` 只表示仿真关注优先级。
