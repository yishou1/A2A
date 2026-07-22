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
- 协方差 NIS 门控 + Hungarian 全局最近邻关联，降低交叉航迹换号和检测输入顺序影响。
- 未来 10 / 20 / 30 / 60 / 120 秒航线预测，并输出预测模型、置信度、不确定半径和时域类型。
- 计划书算法 Provider：默认使用 `PlanAlgorithmProvider` 暴露 ST-GNN、DBN 风险校准、保护资产影响分析、编队识别和 XAI 证据链。
- ST-GNN 动态实体跟踪与轨迹预测：安装 `requirements-model.txt` 后加载 Agent 内置、通过 schema/SHA256/golden I/O 校验的 TorchScript v2 模型包，飞机更新 10 / 20 / 30 / 60 秒预测点，船舶追加 600 / 1200 秒预测点。
- 自适应物理多假设预测：同时生成 `constant_velocity`、`constant_acceleration`、`coordinated_turn` 三类轨迹，依据观测到的加速度、转向率、航迹质量和异常状态归一化融合；该回退实现不冒充完整 IMM。
- 协方差 Kalman 滤波：`medium` 档使用 CV 状态空间 Kalman 更新，输出 state、covariance、innovation 和 kalman_gain。
- ADE/FDE 回看评估：下一帧到达后记录上一帧预测误差，summary 中输出聚合评估。
- DBN 动态态势关注校准：参数位于 `config/dbn_risk_model_v1.json`，输出 low / medium / high 后验概率、观测可信度、状态转移、可观测运动模式概率、参数版本和 SHA256。
- XAI 可解释封装：输出 `evidence_chain`、`factor_chain`、`dbn_transition_evidence`、`safety_chain` 和 `model_trace`，用于解释排序原因并声明安全边界。
- 下游决策 Agent 适配：artifact 中输出 `decision_risk_assessments`，字段对齐 lzh 决策规划 Agent 的 `RiskAssessment`，用于把咱们的风险排序交给下游知识/RAG/规划/合规模块。
- 疑似空中编队和海上编组识别，完整连接约束防止距离过远的链式误并，并使用 `tentative / confirmed / coasting` 生命周期稳定连续帧 `group_id`。
- 威胁排序直接使用 ST-GNN 或自适应物理预测路径、预测置信度和不确定性半径。
- 己方保护资产影响分析。
- 单体、群体、资产影响统一关注排序。
- A2A `sendMessage` / `sendMessageStream`。
- 兼容 Commander 传入的 `workflow_id` / `work_item` / `work_list` 任务信封字段；Agent 内部不运行工作流引擎。
- `work_item` 幂等缓存。
- `GET /workflows/{workflow_id}/work-list`。
- `GET /ready`、`POST /lifecycle/ready`、`GET /metrics`，适配 Commander 宕机恢复/ready=false 切换规范。
- `GET /schema/input`、`GET /schema/output`、`GET /state/summary`，用于上游/Gateway/Commander 自动读取协议版本和当前 Agent 状态。
- `GET /models` 返回 Agent 已加载的模型、版本和 ready/unavailable 状态。
- `GET /algorithms` 返回稳定算法 ID、版本、后端、模型绑定和 ready/partial/unavailable 状态；算法仍由 Agent 进程执行。
- `GET /resources` 返回主机与 Agent 进程 CPU、内存、磁盘和线程快照，供 Commander 调度观察。
- `POST /recovery/notify`、`GET /recovery/status` 接收并查询 Commander 重规划/恢复通知。
- Nacos role/status metadata 和 heartbeat_ts 心跳；心跳会保留 Commander 写入的 busy/unavailable/lease_* 状态。
- `AlgorithmProvider` 作为算法边界，默认主线已经切换到本地可运行的计划书算法栈。
- 本地 JSON 状态快照，支持演示环境重启后恢复航迹、最近 artifact、幂等缓存和 workflow work list。
- 独立 ST-GNN 模型包发现：默认发现 `models/track_threat` 下的内置模型包，也可通过 `ST_GNN_AIRCRAFT_MODEL_DIR`、`ST_GNN_SHIP_MODEL_DIR` 或旧 `ST_GNN_MODEL_DIR` 覆盖；模型不可用时安全回退。

当前工程不再只是“预留接口”。`PlanAlgorithmProvider` 会在 Agent 进程内直接执行协方差 Kalman 跟踪、自适应 CV/CA/CT 物理预测、TorchScript ST-GNN、版本化 DBN 态势关注校准、保护资产影响分析、编队识别和 XAI 证据链。知识库/RAG/方案规划/合规授权不放在本 Agent 中，交由下游 Agent 消费 `decision_risk_assessments` 后继续处理。公共算法库只作为算法源码、模型包和 schema 的交付仓库，不是本 Agent 的运行时 HTTP 依赖。

## 2.1 独立 ST-GNN 训练工程

离线数据准备、PyTorch ST-GNN 训练、评估和模型导出已经从在线 Agent 拆分到：

```text
/Users/mac/Desktop/st-gnn-trajectory-training
```

该目录包含约 22 GB ADS-B/AIS 数据、训练代码、飞机/船舶配置、训练测试、checkpoint 和评估报告。大型数据及 checkpoint 不进入 A2A 仓库。

在线 Agent 只保留轻量推理运行时和两个兆级模型包：

```text
models/track_threat/st_gnn_aircraft_kaggle_v1
models/track_threat/st_gnn_ship_kaggle_v1
```

`scripts/start_track_threat_agent.sh` 默认同时安装 `requirements-model.txt` 并在 CPU 上加载这两个 TorchScript bundle。只有明确要运行降级版时才设置 `TRACK_THREAT_ENABLE_TORCHSCRIPT=false`。

飞机和船舶模型均已通过当前 release gate。飞机模型相对最强 IMM 基线的 ADE/FDE 改善为 13.96%/18.43%，船舶模型相对最强 CV 基线约改善 53%。两个模型的不确定性系数均只使用 validation split 校准，最终门禁只在独立 test split 上评估。若要替换为新模型，可以通过以下环境变量覆盖：

```bash
export ST_GNN_AIRCRAFT_MODEL_DIR=/path/to/exported_models/st_gnn_aircraft_v1
export ST_GNN_SHIP_MODEL_DIR=/path/to/exported_models/st_gnn_ship_v1
```

模型包缺失、损坏或 schema 不兼容时，Agent 不会启动失败，而是继续使用 Kalman 和 CV/CA/CT 自适应多模型物理预测回退链路。未训练的固定矩阵图修正和旧 NumPy Ridge 模型不再进入 Agent 正式运行链。

训练命令、数据清单和服务器使用方法见独立工程的 `README.md` 与 `docs/`。

算法是否达到甲方交付标准不以单个演示场景为准，详细指标、当前过门状态和可执行回归命令见 `docs/algorithm_acceptance.md`。

## 3. 启动

轻量启动，不安装 PyTorch，模型链路会自动回退到 Kalman 与自适应 CV/CA/CT 物理预测，不执行 ST-GNN：

```bash
cd track_threat_agent
PYTHONPATH=.. uv run --with-requirements ../requirements.txt --with-requirements requirements.txt \
  uvicorn app.main:app --host 0.0.0.0 --port 8102
```

启用内置 TorchScript ST-GNN CPU 推理：

```bash
cd track_threat_agent
PYTHONPATH=.. uv run --with-requirements ../requirements.txt \
  --with-requirements requirements.txt \
  --with-requirements requirements-model.txt \
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
curl http://127.0.0.1:8102/resources
curl http://127.0.0.1:8102/models
curl http://127.0.0.1:8102/algorithms
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

### 4.1 Agent 本地模型执行

Agent 持有权威 `TrackStore`、任务幂等缓存、宕机恢复快照和模型实例。启动时从 `models/track_threat` 或 `ST_GNN_*_MODEL_DIR` 加载 TorchScript 模型，每帧航迹在本进程完成推理。`GET /models` 和 Nacos metadata 会公布 `models`、`models_ready`、`models_count` 和 `algorithm_deployment_status`。

Nacos 只发现 Agent、公布 skill/模型/健康状态，不调度算法、不传输模型输入、不承载逐帧航迹。模型超时、输入不足或 bundle 校验失败时，当前帧在 Agent 内回退自适应 CV/CA/CT 物理预测，不会转发给远程算法服务。

接入 Commander 宕机恢复时，本 Agent 遵守师兄统一规范：

- 服务名使用 `A2A-Agent`。
- metadata 至少包含 `role=track_threat` 和 `status=idle`。
- 存活时持续刷新 `heartbeat_ts` / `heartbeat_at`。
- 如果 Commander 把实例标记为 `busy`、`unavailable` 或写入 `lease_*` 字段，Agent 心跳不会用本地旧 metadata 覆盖这些调度状态。
- 单实例按真实状态能力注册 `max_concurrent_tasks=1`，并动态更新 `active_tasks`、`available_task_slots`、成功率、平均延迟和资源采样；多个航迹帧不会并发写同一个 TrackStore。
- 容量已满时不在 Agent 内无限排队，`/sendMessage` 返回 `AGENT_RESOURCE_EXHAUSTED`，Commander 可重试或改派同 role 实例。
- 心跳同时保留 Commander 写入的 `scheduling_score` 和 `scheduling_reason`。
- SDK 注册/心跳失败时自动回退 Nacos HTTP API；metadata PUT 遇到 Nacos Raft metadata 更新异常时，以幂等 POST 重新注册同一实例。
- 当 `/lifecycle/ready` 设置为 `ready=false` 时，`/sendMessage` 返回标准失败信封，`/sendMessageStream` 返回 503，Commander 可切换到同 role 其他 idle Agent。

本次实现参考 `lzh` 分支的分布式 Agent 运行时契约，但没有复制其远程算法库 `/run` 模式。航迹 Agent 是有状态服务，Kalman、ST-GNN、DBN、编组与资产影响算法全部在 Agent 进程内加载执行；Nacos 只发布发现、容量、心跳、资源、Skill 和模型摘要。`/sendMessage` 响应额外返回 `selected_algorithms` 与 `algorithm_duration_ms`，用于定位实际算法链和性能问题。

恢复通知示例：

```bash
curl -X POST http://127.0.0.1:8102/recovery/notify \
  -H 'Content-Type: application/json' \
  -d '{"workflow_id":"wf-demo-001","action":"resume","reason":"commander_replanned","reset_cache":true}'
curl http://127.0.0.1:8102/recovery/status
```

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
- rejected 计数和最近 100 条恢复通知。

状态快照不保存：

- 当前锁；
- Nacos SDK client；
- 启动前未完成的 busy 状态。

服务重启后会恢复为 `idle`。如果上游任务未完成，应由 A2A Gateway / Commander 根据 `work_item` 重试。

如需指定状态文件位置：

```bash
export TRACK_THREAT_STATE_PATH=/path/to/track_threat_agent_state.json
```

## 6. A2A 调用

### 6.1 A2A 任务入口

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

`workflow_id` 和 `work_list` 只是 Commander 传入的跨 Agent 任务关联信息。本 Agent 收到任务后会作为一次本地分析调用执行，不在内部创建子工作流或网络算法步骤。

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

标准联调场景：

```bash
curl -X POST http://127.0.0.1:8102/a2a/perception-result \
  -H "Content-Type: application/json" \
  --data @sample_data/scene_01_normal_tracking.json

curl -X POST http://127.0.0.1:8102/a2a/perception-result \
  -H "Content-Type: application/json" \
  --data @sample_data/scene_02_asset_approach.json

curl -X POST http://127.0.0.1:8102/a2a/perception-result \
  -H "Content-Type: application/json" \
  --data @sample_data/scene_03_group_maneuver.json
```

三套场景分别用于验证：

- `scene_01_normal_tracking.json`：普通多目标跟踪、预测、基础排序。
- `scene_02_asset_approach.json`：目标预测航线接近保护资产，验证 `asset_impacts`。
- `scene_03_group_maneuver.json`：空中编队、海上编组和 unknown 异常运动，验证 group 生命周期、物理协同识别和统一排序。

协议和状态查询：

```bash
curl http://127.0.0.1:8102/schema/input
curl http://127.0.0.1:8102/schema/output
curl http://127.0.0.1:8102/state/summary
```

发送 A2A `sendMessage` 演示任务：

```bash
cd track_threat_agent
PYTHONPATH=.. uv run --with-requirements requirements.txt --with-requirements ../requirements.txt \
  python scripts/send_track_threat_demo_task.py --frame 45
```

一键联调 smoke test，会检查 `/health`、`/ready`、Agent Card、输入/输出 schema，并通过 `/sendMessage` 发送一帧标准任务：

```bash
cd track_threat_agent
PYTHONPATH=.. uv run --with-requirements requirements.txt --with-requirements ../requirements.txt \
  python scripts/smoke_track_threat_agent.py
```

导出 90 帧长序列场景：

```bash
cd track_threat_agent
PYTHONPATH=.. uv run --with-requirements requirements.txt --with-requirements ../requirements.txt \
  python scripts/export_long_sequence.py --frames 90 --output sample_data/coastal_operation_90_frames.json
```

运行预测评估，对比 `Kalman+自适应物理多假设` 与 `Kalman+物理基线+ST-GNN`：

```bash
cd track_threat_agent
PYTHONPATH=.. uv run --with-requirements requirements.txt --with-requirements ../requirements.txt \
  python -m eval.prediction_eval --frames 90 --output eval/reports/prediction_eval_90_frames.json
```

## 8. 输出

返回 `track_threat_group_artifact`，包含：

- `artifact_schema_version`：当前为 `track_threat_group_artifact/v1`。
- `trace`：包含 `task_id`、`algorithm_level`、输入 detection 数量和处理时间。
- `tracks`：连续航迹、历史路径、预测路径。
- `threats`：单体关注排序。
- `groups`：疑似编队/编组，`metadata.lifecycle_state` 标明待确认、已确认或短时保持状态。
- `asset_impacts`：保护资产影响分析，包含预测最近距离、保护半径距离裕度、是否进入保护半径和预计进入时间。
- `unified_threat_ranking`：单体、群体、资产影响统一排序，包含 `reason`、`evidence`、`factors`，方便前端或 Commander 解释排序原因。
- `events`：`track.updated`、`threat.updated`、`track.group.updated`、`threat.group.updated`、`threat.ranking.updated`、`asset.impact.updated` 等事件。

每个 `tracks[].predicted_path[]` 预测点包含：

- `dt_s`：飞机默认包含 10、20、30、60、120 秒；启用船舶模型后追加 600、1200 秒。
- `horizon_type`：`short_term` 或 `medium_term`。
- `model_used`：推荐下游读取的模型字段，例如 `adaptive_multi_model_fused` 或 `st_gnn_torchscript`。
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
- ST-GNN TorchScript 模型包校验、CPU 推理、超时与物理回退。
- DBN 参数 schema、状态矩阵校验、版本和 SHA256 追踪。
- 航迹与群组 `tentative / confirmed / coasting / lost` 生命周期。
- DBN 风险状态后验概率评估。
- 下游 `decision_risk_assessments` 风险摘要适配。
- 90 帧长序列仿真场景。
- 预测评估脚本和 baseline / enhanced 对比报告。
- `work_item` 幂等。
- `work_list` 查询。

## 10. ST-GNN v2 模型交付

大规模训练在独立工程 `st-gnn-trajectory-training` 完成。只有通过独立 test set 指标门禁的模型才能导出：

```text
model.ts
model_manifest.json
normalization.json
metrics.json
golden_io.json
sha256sums.json
```

启用模型运行时：

```bash
cd track_threat_agent
uv run --with-requirements requirements.txt \
  --with-requirements requirements-model.txt \
  --with-requirements ../requirements.txt \
  uvicorn app.main:app --host 0.0.0.0 --port 8102
```

```bash
export ST_GNN_AIRCRAFT_MODEL_DIR=/models/st_gnn_aircraft_v1
export ST_GNN_SHIP_MODEL_DIR=/models/st_gnn_ship_v1
export ST_GNN_REQUIRED=false
export ST_GNN_ENFORCE_RELEASE_GATE=false
export ST_GNN_MAX_INFERENCE_MS=200
```

这些环境变量是可选覆盖项；不配置时会使用 `models/track_threat` 的内置 bundle。Agent 会校验 schema、SHA256 和 golden I/O。验收/生产部署建议设置 `ST_GNN_ENFORCE_RELEASE_GATE=true`；未通过 `release_gate` 的外部模型会被拒绝并回退。飞机模型更新 10/20/30/60 秒点并保留 120 秒物理预测点；船舶模型保留短期物理预测并追加 600/1200 秒点。运行时会应用模型包中的 validation 不确定性校准系数。历史不足、模型损坏、超时或输出异常时逐帧回退，不中断 Agent。

## 11. Skill 调用

Agent Card 与 Nacos 使用同一组 snake_case Skill ID：

```text
track_threat_situation_analysis
trajectory_tracking
trajectory_prediction
group_detection
threat_ranking
group_threat_ranking
protected_asset_impact_analysis
```

`/sendMessage` 和 `/sendMessageStream` 支持 `required_skill`、`required_skills`、`input`、`context`、`output_hint`。不支持的 Skill 返回 `UNSUPPORTED_SKILL`。

最新版 Commander 使用 `schema_version=1.0` 和字符串 `output_hint`。`trajectory_tracking` 返回 `output.tracking_result`；后续 `threat_ranking` 可直接消费上下文条目形式的 `input.tracking_result` 并返回 `output.threat_assessment_result`，不会重复推进航迹历史。完整调用使用 `output.track_threat_group_artifact`。算法均在 Agent 进程内执行，Nacos 只负责发现、调度状态和 Slot 信息。

## 12. 当前限制

- 当前是 Demo，不是生产系统。
- 飞机和船舶 ST-GNN 已通过现有独立测试集 release gate；仍需在甲方实际 CPU 和现场数据上复验。
- DBN 参数结构已版本化，但正式概率仍需使用甲方或专家标注数据校准并冻结新版本。
- 知识库/RAG/规划/合规能力由下游 Agent 负责，本 Agent 只输出可消费的风险摘要。
- 当前使用本地 JSON 文件做轻量状态快照，还不是生产级数据库或分布式状态存储。
- 单实例串行处理，多并发建议通过多 Agent 实例水平扩展。

## 13. 文档索引

- `docs/agent_protocol_contract.md`：输入、输出、事件、Skill、状态和 Nacos metadata 的完整协议。
- `docs/a2a_integration.md`：A2A Gateway / Commander 发现与调用说明。
- `docs/nacos_smoke_test.md`：Docker Nacos 注册、发现和调用联调步骤。
- `docs/amos_bridge_contract.md`：AMOS Bridge 事件回写和地图字段约定。
- `docs/st_gnn_v2_integration.md`：TorchScript 模型包、在线构图、降级和性能验收。
