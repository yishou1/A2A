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
object_types=aircraft,ship,uav,unknown
ranking_item_types=track,group,asset_impact
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

协议发现入口：

```bash
curl http://127.0.0.1:8102/schema/input
curl http://127.0.0.1:8102/schema/output
curl http://127.0.0.1:8102/schema/state
curl http://127.0.0.1:8102/state/summary
```

当前协议版本：

```text
input_schema_version=perception_result/v1
artifact_schema_version=track_threat_group_artifact/v1
state_summary_schema_version=1
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

## 3. A2A 任务信封输入：sendMessage

近真实联调入口：

```http
POST /sendMessage
Authorization: Bearer <token>
Content-Type: application/json
```

结构：

```json
{
  "schema_version": "1.0",
  "workflow_id": "wf-demo-001",
  "work_item": "track-threat-step-001",
  "command": "trajectory_tracking",
  "required_skill": "trajectory_tracking",
  "required_skills": ["trajectory_tracking"],
  "input": {
    "scene": {"protected_assets": []},
    "detections": []
  },
  "context": {"algorithm_level": "medium"},
  "attachments": [],
  "work_list": [],
  "output_hint": "tracking_result"
}
```

字段说明：

- `workflow_id`：Commander 分配的跨 Agent 任务关联编号，不表示本 Agent 内部运行工作流引擎。
- `work_item`：幂等键。同一个 `work_item` 重试时返回缓存结果，不重复推进航迹历史。
- `schema_version`：当前固定为 `1.0`。
- `command`：任务动作；自动拆解时分别为 `trajectory_tracking`、`threat_ranking`。
- `required_skill` / `required_skills`：Commander 用于按 Skill 发现和调度 Agent。
- `input`：当前 Skill 的业务输入，可包含 `detections`、`cognition_result` 或 `tracking_result`。
- `output_hint`：强约束的响应键；成功响应必须包含 `output[output_hint]`。
- `work_list`：工作流步骤列表，用于查询和汇报。
- `payload`：仅为旧版兼容入口；新 Commander 应使用 `input`。

最新版 Commander 的两阶段调用为：

```text
CognitionResult
→ trajectory_tracking / output.tracking_result
→ threat_ranking / output.threat_assessment_result
```

`trajectory_tracking` 支持 `input.cognition_result` 上下文条目数组；Agent 会解包最后一个条目的 `value`。`threat_ranking` 支持 `input.tracking_result` 上下文条目数组，并直接读取 TrackState 进行排序，不会把同一批航迹重新当 Detection 更新一次。

### 3.1 sendMessage 统一响应信封

`/sendMessage` 按师兄 A2A 宕机恢复接入规范返回统一任务响应信封。Commander 判断成功/失败时读取 `status` 和 `error`；业务结果统一放在 `output` 内。

```json
{
  "schema_version": "1.0",
  "workflow_id": "wf-demo-001",
  "work_item": "track-threat-step-001",
  "agent": "track-threat-group-agent",
  "role": "track_threat",
  "command": "analyze_perception_result",
  "status": "completed",
  "output": {
    "task_id": "task-001",
    "message_type": "track_threat_group_artifact",
    "artifact": {},
    "tracking_result": {
      "schema_version": "tracking_result/v1",
      "scene": {},
      "tracks": [],
      "protected_assets": [],
      "summary": {}
    }
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

当 `output_hint=threat_assessment_result` 时，`output.threat_assessment_result` 包含 `threats`、`groups`、`asset_impacts`、`unified_threat_ranking`、`decision_risk_assessments` 和 AMOS 事件。完整流水线建议使用 `output_hint=track_threat_group_artifact`。

兼容说明：为了方便旧版脚本和本地 curl 调试，响应仍保留 `output.artifact`、顶层 `artifact` 和 `artifact_summary`；最新版 Commander 必须读取 `output[output_hint]`。

成功响应还包含：

- `selected_algorithms`：本次 Skill 实际选择的稳定算法 ID，例如 `covariance_kalman_cv_filter`、`st_gnn_dynamic_entity_tracking`、`dynamic_bayesian_network`。
- `algorithm_duration_ms`：跟踪预测、威胁评估/XAI、编组、保护资产影响和统一排序的分阶段耗时。

该追踪只描述 Agent 进程内执行，不表示通过 HTTP 调用远程算法服务。

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

该 Agent 的 TrackStore、编组生命周期和预测回看均有连续状态，因此单实例容量固定为 `max_concurrent_tasks=1`。实例忙时返回：

```json
{
  "status": "failed",
  "error": "agent task capacity is full",
  "error_code": "AGENT_RESOURCE_EXHAUSTED"
}
```

Commander 应重试或改派其他 `role=track_threat,status=idle` 实例，不应向同一实例并发写入两帧态势。

## 4. 输出 Artifact

成功响应：

```json
{
  "task_id": "task-001",
  "message_type": "track_threat_group_artifact",
  "status": "completed",
  "artifact": {
    "task_id": "task-001",
    "artifact_schema_version": "track_threat_group_artifact/v1",
    "input_schema_version": "perception_result/v1",
    "trace": {
      "task_id": "task-001",
      "message_type": "perception_result",
      "algorithm_level": "medium",
      "detection_count": 7,
      "processed_at": 1781233000.0,
      "agent": "track-threat-group-agent",
      "role": "track_threat"
    },
    "protected_assets": [],
    "tracks": [],
    "threats": [],
    "asset_impacts": [],
    "groups": [],
    "unified_threat_ranking": [],
    "decision_risk_assessments": [],
    "events": [],
    "summary": {}
  }
}
```

### 4.0 trace 与 schema

`artifact.trace` 用于跨 Agent 联调排错，包含 `task_id`、输入消息类型、算法等级、输入 detection 数量、处理时间、Agent 名称和角色。

`artifact.summary.schema` 会重复输出 `input_schema_version` 与 `artifact_schema_version`，方便只读取 summary 的 Gateway、Commander 或展示层快速判断兼容性。

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

本 Agent 不输出 `metadata.semantic_sitrep`。KG/RAG/规则推理和方案规划属于下游决策/合规 Agent；本 Agent 通过 `decision_risk_assessments` 交付它们需要的风险摘要。

`threats[].metadata.dbn` 对应版本化 DBN 态势关注校准输出。参数来自 `config/dbn_risk_model_v1.json`，启动时校验概率矩阵和权重，输出携带版本与 SHA256：

```json
{
  "algorithm": "DBN",
  "contract": "dynamic_bayesian_network_situation_attention_calibration",
  "parameter_model": {
    "schema_version": "dbn_risk_model/v1",
    "model_version": "dbn-risk-attention-v1",
    "sha256": "64-character-sha256"
  },
  "dbn_posterior": {"low": 0.12, "medium": 0.34, "high": 0.54},
  "risk_state_probabilities": {"low": 0.12, "medium": 0.34, "high": 0.54},
  "observation_reliability": 0.86,
  "state_transition": {
    "previous_high": 0.22,
    "prior_high": 0.30,
    "posterior_high": 0.54,
    "high_delta": 0.32
  },
  "posterior_entropy": 1.21,
  "risk_pattern_probabilities": {
    "protected_zone_approach": 0.46,
    "sustained_presence": 0.11,
    "coordinated_motion": 0.21,
    "anomalous_motion": 0.08,
    "non_closing_motion": 0.14
  },
  "risk_pattern_transition": {
    "previous_dominant_pattern": "sustained_presence",
    "dominant_pattern": "protected_zone_approach",
    "dominant_changed": true
  },
  "dominant_risk_pattern": "protected_zone_approach",
  "risk_pattern_model": {
    "algorithm": "DBN observable-pattern calibration",
    "contract": "situation_awareness_observable_pattern_probability",
    "utility_scores": {}
  }
}
```

`metadata.xai` 对应计划书中的 XAI 可解释封装：

```json
{
  "algorithm": "XAI",
  "contract": "situation_attention_explainable_evidence_chain",
  "language": "zh-CN",
  "evidence_chain": [],
  "factor_chain": [{"factor": "closing", "contribution": 0.18}],
  "dbn_transition_evidence": {},
  "safety_chain": [
    "该结果仅表示仿真态势关注优先级",
    "不包含武器控制、制导、交战或打击建议"
  ]
}
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
  "model_used": "st_gnn_torchscript",
  "prediction_model": "st_gnn_torchscript",
  "primary_model": "adaptive_constant_velocity",
  "model_probabilities": {
    "constant_velocity": 0.62,
    "constant_acceleration": 0.21,
    "coordinated_turn": 0.17
  },
  "prediction_confidence": 0.89,
  "uncertainty_radius_m": 86.4,
  "horizon_type": "short_term",
  "model_version": "aircraft-release-v2",
  "baseline_model": "adaptive_ctra_fusion",
  "inference_latency_ms": 3.8,
  "fallback_reason": null
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

当前对外算法契约采用计划书中的 ST-GNN 动态实体跟踪与轨迹预测。Agent 先生成 `constant_velocity`、`constant_acceleration`、`coordinated_turn` 三类自适应物理预测；通过 bundle schema、SHA256、golden I/O 和 release gate 的 TorchScript ST-GNN 再预测相对物理基线的残差。未加载正式模型时只输出 `adaptive_multi_model_fused`，不会运行固定矩阵或 NumPy 伪 ST-GNN。完整物理假设保存在：

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
metadata.lifecycle_state
metadata.hit_count
metadata.consecutive_hit_count
metadata.missed_count
metadata.member_change
```

`group_type` 取值：

```text
air_formation
surface_group
mixed_group
unknown_group
```

群组生命周期：

- `tentative`：首次满足物理关联门限，尚待连续帧确认。
- `confirmed`：连续命中达到门限，作为稳定群组输出。
- `coasting`：本帧未满足关联门限，但在有限漏检窗口内保留原 `group_id`。
- 超过 `max_missed_frames` 后群组从活动集合移除；当前实现不无限保存群组历史。

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
predicted_min_distance_margin_m
closest_time_s
eta_to_protected_radius_s
will_enter_protection_radius
factors
evidence
timestamp
```

字段含义：

- `predicted_min_distance_margin_m`：预测最近距离相对保护半径的裕度。大于 0 表示仍在保护半径外，小于 0 表示预测路径进入保护半径。
- `closest_time_s`：预测路径上距离保护资产最近的时间偏移。
- `eta_to_protected_radius_s`：如果预测会进入保护半径，表示预计进入保护半径的时间偏移；如果不进入则为 `null`。
- `will_enter_protection_radius`：布尔值，表示预测航线是否进入该保护资产的保护半径。

该结果表示“哪些目标更值得关注，因为它们可能影响某个保护资产”，不是攻击建议、拦截建议或交战决策。

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
      "level": "medium",
      "reason": "中等关注：预测航线接近保护资产，建议持续监视。",
      "evidence": ["预测最近距离低于保护半径阈值"],
      "factors": {
        "distance_factor": 0.72,
        "asset_priority_factor": 0.8
      },
      "eta_to_protected_radius_s": 60,
      "will_enter_protection_radius": true,
      "predicted_min_distance_margin_m": -420.5,
      "predicted_closest_distance_m": 4580.0
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
resources_endpoint=http://127.0.0.1:8102/resources
algorithms_endpoint=http://127.0.0.1:8102/algorithms
recovery_endpoint=http://127.0.0.1:8102/recovery/notify
recovery_status_endpoint=http://127.0.0.1:8102/recovery/status
state_summary_endpoint=http://127.0.0.1:8102/state/summary
input_schema_url=http://127.0.0.1:8102/schema/input
output_schema_url=http://127.0.0.1:8102/schema/output
state_schema_url=http://127.0.0.1:8102/schema/state
capability_version=track_threat_agent_v1
artifact_schema_version=track_threat_group_artifact/v1
input_schema_version=perception_result/v1
algorithm_profile=kalman_stgnn_dbn_group_asset_xai
model_status=no_model / model_loaded / model_training / model_error
agent_card=http://127.0.0.1:8102/.well-known/agent-card.json
skills=trajectory_tracking,trajectory_prediction,threat_ranking,group_detection,group_threat_ranking,protected_asset_impact_analysis
algorithm_family=kalman,st_gnn,dbn,asset_impact,group_detection,xai
runtime_providers=covariance_kalman_cv_filter,torchscript_st_gnn,dbn_risk_state_calibration_runtime,physical_relation_complete_link_clustering,predicted_path_asset_proximity
fallback_providers=adaptive_cv_ca_ct_physics
algorithm_levels=small,medium,large
object_types=aircraft,ship,uav,unknown
input_message_types=perception_result,tactical_intelligence_result,a2a_task
output_message_types=track_threat_group_artifact,track.updated,threat.updated,track.group.updated,threat.group.updated,threat.ranking.updated,asset.impact.updated
ranking_item_types=track,group,asset_impact
scene_contract=protected_zone_lat,protected_zone_lon,protected_radius_m,protected_assets
minimum_detection_fields=detection_id,object_type,timestamp,lat,lon,speed,heading,confidence
dbn_parameter_schema=dbn_risk_model/v1
dbn_parameter_model=dbn-risk-attention-v1
group_lifecycle_states=tentative,confirmed,coasting
tracking_lifecycle_states=tentative,confirmed,coasting,lost
algorithm_boundary=tracking,prediction,group_detection,risk_ranking,protected_asset_impact,xai
upstream_boundary=sensor_processing,perception_fusion
downstream_boundary=semantic_reasoning,mission_planning,engagement_decision
models=track_state_kalman_cv,trajectory_adaptive_multi_model_physics,...
models_ready=<Agent 已成功加载的模型>
models_count=<模型数量>
algorithm_deployment_status=ready / partial / unavailable
algorithm_execution_location=agent_process
algorithm_library_transport=none
algorithm_loading_mode=agent_local_model_bundle
remote_algorithm_execution=false
algorithm_contract_version=track_threat_algorithms/v1
internal_workflow_engine=false
active_tasks=0
max_concurrent_tasks=1
available_task_slots=1
task_execution_status=idle
quality_tasks_completed=0
quality_tasks_failed=0
quality_success_rate=1.000000
quality_avg_latency_ms=0.000
resource_cpu_percent=<latest sample>
resource_memory_percent=<latest sample>
heartbeat_ts=<recent unix timestamp>
```

Nacos 只负责 Agent 发现、健康、skill 和模型部署摘要，不承载每一帧航迹数据，也不调度算法执行。航迹数据通过 A2A HTTP/SSE 或 AMOS Bridge 传输，模型由 Agent 本进程加载和执行。

心跳保护约定：

- Agent 存活时持续刷新 `heartbeat_ts` 和 `heartbeat_at`。
- 优先使用 Nacos SDK 注册和 heartbeat；SDK 缺失或 heartbeat 失败时使用 Nacos HTTP API。metadata PUT 失败时使用同实例幂等 POST 重新注册，Agent 服务本身不退出。
- Commander 领取租约后可能直接在 Nacos 写入 `status=busy`、`lease_workflow_id`、`lease_work_item`。
- Commander 判定宕机或断心跳后可能写入 `status=unavailable`、`unavailable_reason`、`unavailable_workflow_id`、`unavailable_work_item`。
- 本 Agent 每次发送心跳前会读取 Nacos 当前实例 metadata；如果发现上述调度状态，会保留 `lease_*`、`circuit_*`、`unavailable_*`、容量字段以及 `scheduling_score/scheduling_reason`，避免把 Commander 的状态覆盖回本地旧值。
- Agent 的 TrackStore 是有序有状态存储，当前真实并发能力固定为一个 Slot：`max_concurrent_tasks=1`。横向并发应启动多个独立 Agent 实例，而不是并发写同一个 TrackStore。
- 每次调用后更新成功数、失败数、成功率和平均延迟；心跳刷新 CPU、内存、磁盘资源采样。

### 6.1 Ready / Metrics 接口

```http
GET /ready
POST /lifecycle/ready
GET /metrics
GET /resources
GET /algorithms
POST /recovery/notify
GET /recovery/status
```

`/ready` 返回当前是否可接任务：

```json
{
  "ready": true,
  "agent": "track-threat-group-agent",
  "role": "track_threat",
  "agent_status": "idle",
  "active_tasks": 0,
  "max_concurrent_tasks": 1,
  "available_task_slots": 1,
  "task_execution_status": "idle"
}
```

`/resources` 只报告原始资源快照，调度阈值由 Commander 决定。`/algorithms` 公布稳定算法 ID、实际本地模型绑定、版本和后端。它不是远程算法执行入口，不提供 `/run`。

Commander 重规划或恢复后可通知：

```json
POST /recovery/notify
{
  "workflow_id": "wf-demo-001",
  "action": "resume",
  "reason": "commander_replanned",
  "reset_cache": true
}
```

Agent 会记录最近 100 条通知、恢复 `ready=true`，并可按 workflow 清除幂等缓存；`GET /recovery/status` 返回最近一次和完整通知列表。

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
predicted_path[].model_version
predicted_path[].baseline_model
predicted_path[].inference_latency_ms
predicted_path[].fallback_reason
```

## 9. 当前限制

- 当前仍是 Demo，不是生产系统。
- 飞机和船舶 ST-GNN 均已有通过独立测试集 release gate 的内置模型包；严格 release gate 仍会拒绝未过门的外部替换模型。
- DBN 参数已完成 schema、版本和哈希管理，但正式概率仍需使用甲方或专家标注样本校准。
- 知识库/RAG/规划/合规能力由下游 Agent 负责，本 Agent 只输出可消费的风险摘要。
- 状态使用本地 JSON 快照恢复，不是生产级数据库或分布式状态存储。
- 单实例采用串行处理；并发建议通过多个 Agent 实例注册到 Nacos。
- `threat` / `risk` / `impact` 只表示仿真关注优先级。

## 10. Commander Skill 契约

Agent Card 和 Nacos metadata.skills 统一发布：

```text
track_threat_situation_analysis
trajectory_tracking
trajectory_prediction
group_detection
threat_ranking
group_threat_ranking
protected_asset_impact_analysis
```

Commander 可以在 `/sendMessage` 或 `/sendMessageStream` 请求中使用：

```json
{
  "schema_version": "1.0",
  "required_skill": "trajectory_tracking",
  "required_skills": ["trajectory_tracking"],
  "input": {"detections": []},
  "context": {"scene": {}, "algorithm_level": "medium"},
  "output_hint": "tracking_result"
}
```

未声明 Skill 时默认执行 `track_threat_situation_analysis` 完整流水线。不支持的 Skill 返回 `error_code=UNSUPPORTED_SKILL`。字符串 `output_hint` 是严格输出契约；旧版对象形式的 `output_hint` 只保留兼容确认，不用于最新版 Commander 校验。
