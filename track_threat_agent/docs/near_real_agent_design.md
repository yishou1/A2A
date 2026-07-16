# Near-Real Track Threat Agent Demo Design

本文说明当前 Demo 如何从“本地演示服务”升级为“近真实 A2A/Nacos Agent Demo”。它仍然是仿真项目，不接真实武器系统，不做攻击建议，不做制导、火控或交战决策。

## 1. 当前定位

`track-threat-group-agent-demo` 是一个下游态势分析 Agent。上游感知或融合模块向它发送 `perception_result`，本 Agent 负责：

- 多目标航迹维护；
- 未来 10/20/30/60/120 秒航线预测；
- ST-GNN 动态实体跟踪与轨迹预测契约；
- 疑似空中编队和海上编组识别；
- 己方保护资产影响分析；
- 单体、群体、资产影响统一关注排序；
- 生成 A2A / AMOS 可消费的 artifact 和 events。

## 2. 本地算法实现与后续模型替换点

当前阶段已经把 Demo 规则升级为本地可运行的计划书算法栈。默认 provider 为：

```text
PlanAlgorithmProvider
```

它把计划书中的算法作为主契约：

- `st_gnn_dynamic_entity_tracking`：动态实体跟踪与轨迹预测；
- `dynamic_bayesian_network`：风险状态后验概率校准；
- `xai_evidence_chain`：证据链与模型轨迹封装；
- `asset_track_relation_graph`：保护资产与目标关系图分析。

当前本地运行时包括：

```text
covariance_kalman_cv_filter
imm_multi_model_motion_prediction
local_numpy_st_gnn_message_passing
dbn_risk_state_calibration_runtime
xai_evidence_runtime
```

实际流程是：`medium` 档先用协方差 Kalman 更新 track 状态，再用 IMM 生成三类运动假设；ST-GNN 本地运行时把目标构成动态图，通过两层消息传递输出节点 embedding、边注意力和速度/加速度修正；DBN 模块在 low/medium/high 风险状态上做时序后验更新，并输出风险模式概率。知识库/RAG/规划/合规能力交由下游 lzh Agent 消费 `decision_risk_assessments` 后处理。

训练版 ST-GNN 通过 TorchScript bundle 交付到 Agent，由 Agent 启动时加载并在 CPU 上直接推理。师兄公共算法库作为源码、schema 和模型包的交付边界，不注册成本 Agent 依赖的远程算法工作流。替换模型包时 A2A/Nacos/AMOS 协议不需要改变。

## 3. A2A 任务信封兼容

除了直接调用 `POST /a2a/perception-result`，本 Agent 也支持 Commander 的 A2A 任务信封：

```text
POST /sendMessage
POST /sendMessageStream
GET  /workflows/{workflow_id}/work-list
```

`workflow_id` 仅用于跨 Agent 任务关联，`work_item` 用于幂等。航迹 Agent 内部是一次进程内分析调用，不运行工作流引擎，不逐算法发起 HTTP 请求。

推荐 A2A payload：

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

`work_item` 用于幂等处理。如果同一个 `work_item` 因恢复、重试或网络抖动重复到达，Agent 会直接返回缓存结果，不会重复推进航迹状态。

## 4. 并发策略

本 Agent 是有状态 Agent，内部维护：

- `tracker.tracks`
- `history_path`
- DBN 风险状态
- group 状态
- `last_artifact`

当前 Demo 采用实例级串行处理：

```text
一个 Agent 实例同一时刻只处理一个任务
```

代码中通过 `processing_lock` 保护主处理流程。若需要并发，推荐启动多个 Agent 实例并全部注册到 Nacos，由 Commander 根据 `role=track_threat,status=idle` 选择空闲实例。

## 5. Nacos 注册建议

接入师兄 A2A 仓库时，建议使用统一服务名：

```bash
SERVICE_NAME=A2A-Agent
AGENT_ROLE=track_threat
AGENT_STATUS=idle
```

关键 metadata：

```text
role=track_threat
status=idle
agent_id=track-threat-group-agent-01
send_message_endpoint=http://{ip}:{port}/sendMessage
send_message_stream_endpoint=http://{ip}:{port}/sendMessageStream
work_list_endpoint=http://{ip}:{port}/workflows/{workflow_id}/work-list
agent_card=http://{ip}:{port}/.well-known/agent-card
health_endpoint=http://{ip}:{port}/health
skills=trajectory_tracking,trajectory_prediction,threat_ranking,group_detection,protected_asset_impact_analysis
```

Nacos 只负责服务发现、健康和 metadata，不负责传输每一帧航迹数据。帧数据仍通过 A2A HTTP/SSE 或 AMOS Bridge 调用本 Agent。

## 6. Health 可观测性

`GET /health` 会返回近真实 Agent 运行态字段：

```json
{
  "status": "ok",
  "agent_status": "idle",
  "active_track_count": 7,
  "active_group_count": 2,
  "processed_task_count": 1,
  "failed_task_count": 0,
  "cached_work_item_count": 1,
  "current_workflow_id": null,
  "current_work_item": null,
  "algorithm_provider": "agent_local_model_runtime"
}
```

这些字段用于演示 Agent 是否空闲、是否正在处理任务、是否存在缓存结果、当前算法提供者是什么。

## 7. 与真实 Agent 的差距

当前仍是 Demo，主要限制如下：

- 飞机候选和船舶 ST-GNN TorchScript 模型已随 Agent 提供，未达到发布门禁的模型会保留 candidate 标识；
- 知识库/RAG/规划/合规能力由下游 Agent 负责，本 Agent 只输出可消费的风险摘要；
- 状态保存在内存中，服务重启后不恢复历史航迹；
- 单实例串行处理，多工作流并发建议通过多实例水平扩展；
- Nacos 注册是可选 best-effort，不依赖 Nacos 也能启动。

## 8. 汇报表述

可以这样概括：

> 当前版本已按近真实 Agent 方式封装。它支持 A2A Agent Card、sendMessage、sendMessageStream、任务幂等、Nacos role/status/model metadata 和健康检查。算法和 TorchScript 模型由 Agent 自身加载并在进程内执行，不把内部分析设计成远程工作流；输出的 `decision_risk_assessments` 可由下游决策/合规 Agent 继续消费。
