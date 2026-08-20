# TIA Agent 接入主体框架与 I/O 外壳统一说明

**报告周期：** 2026-07-21 ～ 2026-07-24  
**工作主题：** 将战术情报 Agent（TIA）对齐 A2A 主体框架（lzh / Commander），并统一 Commander ↔ Agent 输入输出外壳  
**关联工程：** `d:\a2a_project\A2A-main`  
**Agent 定位：** 工作流**数据前端 / 感知前端**，产出 `intelligence_packet` 供航迹预测、决策规划、评估等下游 Agent 消费

---

## 一、本周工作目标

1. 把 TIA 从「独立流水线」接进项目主体：**协议层、注册心跳、技能广告、Commander 调度**  
2. 按飞书 / lzh 约定，统一 **Commander → Agent 输入外壳** 与 **Agent → Commander 输出外壳**  
3. 让 `output.intelligence_packet` 成为真正的下游标准输入（含 `tracks` + `history_path`、分层取数指引）  
4. 去掉 mock 兜底，用真实 UE / 本地传感器附件做联调验证  

---

## 二、本周完成情况总览

| 模块 | 状态 | 说明 |
|------|------|------|
| 平台包恢复与对齐 | ✅ | `a2a_protocol` / `a2a_sdk` / `commander_agent` 等与主体一致 |
| Agent 启动与 Nacos 心跳 | ✅ | `AgentRuntimeSDK.from_agent(...).serve()` |
| 技能广告 | ✅ | 主技能 `tactical_intelligence_analysis` |
| 输入外壳适配 | ✅ | `input.agent_request` + `attachments` → `SensorBatch` |
| 输出外壳统一 | ✅ | `output.intelligence_packet`（含 schema / tracks / targets 等） |
| Commander 落库键 | ✅ | `role=tactical_intelligence` → `context.intelligence_packet` |
| BPEL 角色映射 | ✅ | `TacticalIntelligenceAgent` → `tactical_intelligence` |
| 下游契约与适配器 | ✅ | `consumer_guide` + `downstream_adapter` |
| 真实场景两帧联调 | ✅ | UE `scene_000/001`，`history_path` 跨帧 1→2 |

---

## 三、如何接入项目主体框架

### 3.1 整体架构位置

```text
Commander / Gateway / BPEL
        │  sendMessage（统一外壳）
        ▼
┌───────────────────────────────────────┐
│  Tactical Intelligence Agent (TIA)    │
│  A2ABaseAgent + AgentRuntimeSDK       │
│  role = tactical_intelligence         │
│  skill = tactical_intelligence_analysis│
└───────────────────────────────────────┘
        │
        ├─ payload_adapter：外壳 → SensorBatch
        ├─ orchestrator：感知 → 认知 → 通信
        ├─ algorithm_library：POST /predict（9020–9030）
        └─ _build_output：→ output.intelligence_packet
                │
                ▼
        Commander context["intelligence_packet"]
                │
        ┌───────┼───────────────┐
        ▼       ▼               ▼
   航迹预测   决策/火力/评估   可视化/BDA
   (tracks)   (targets)        (attachments)
```

TIA 在流程中靠前：负责把传感器附件与侦察文本变成结构化情报包；**不替代**下游航迹预测 / 决策 Agent。

### 3.2 协议层接入（A2ABaseAgent）

实现类：`tactical_intelligence_agent/service.py` → `TacticalIntelligenceCommanderAgent`

| 能力 | 做法 |
|------|------|
| 统一任务信封 | 继承 `A2ABaseAgent`，走 `/sendMessage`、SSE 流式、幂等 `work_item` |
| 生命周期 | `/health` `/ready` `/lifecycle/ready` `/resources` `/recovery/notify` |
| 技能广告 | `build_tia_skills()`：主技能 + role 专业能力 |
| Agent Card | `output_hint=intelligence_packet`，`pipeline_stage=perception_front` |

主技能 ID（与飞书 / Commander 租约匹配）：

```text
required_skill = tactical_intelligence_analysis
output_hint    = intelligence_packet
command        = process_intelligence
role           = tactical_intelligence
```

### 3.3 启动与注册心跳（AgentRuntimeSDK）

入口：`tactical_intelligence_agent/main.py`

```powershell
# 本地不注册 Nacos
$env:TIA_NACOS_REGISTER="0"
$env:TIA_PORT="8016"
$env:TIA_SKIP_WARMUP="1"
.\.venv\Scripts\python.exe -m tactical_intelligence_agent.main
```

对齐点：

- `AgentRuntimeSDK.from_agent(agent, ...).serve(register=...)`
- 心跳元数据携带 `output_hint`、`algorithm_library=predict` 等
- 有 Nacos 时默认 `TIA_NACOS_REGISTER=1`

### 3.4 Commander / BPEL 调度接入

| 改动点 | 文件 | 作用 |
|--------|------|------|
| 默认输出键 | `commander_agent/main.py` | `tactical_intelligence` → `intelligence_packet` |
| 上下文集合 | 同上 | `context.intelligence_packet` 与 recon/strike 等并列落库 |
| BPEL 伙伴角色 | `bpel_workflow.py` | `TacticalIntelligenceAgent` → `tactical_intelligence` |
| BPEL 命令 | 同上 | `processIntelligence` → `process_intelligence` |
| BPEL 变量 | Commander | `IntelligencePacket` → `intelligence_packet` |

建议流程节点顺序（概念）：

```text
recon → tactical_intelligence → track_threat / trajectory → decision → ...
```

### 3.5 算法库接入方式（与决策 Agent 刻意并存）

| 侧 | 调用方式 | 说明 |
|----|----------|------|
| lzh 决策 Agent | 集中式 `POST /run`（约 8088） | 决策侧算法编排 |
| TIA 感知链路 | 各算法包 `POST /predict`（9020–9030） | `agent/algorithm_library` 编排 |

本周**不强制合并**两套调用：产品上决策用 `/run`，TIA 前端感知用 `/predict`。

本地无算法 HTTP 服务时，可用：

```powershell
$env:TIA_EXECUTION_MODE="in_process"
$env:TIA_COMPUTE_PROFILE="medium"
```

### 3.6 关键代码路径一览

| 路径 | 职责 |
|------|------|
| `tactical_intelligence_agent/main.py` | SDK 启动 / 心跳 |
| `tactical_intelligence_agent/service.py` | A2ABaseAgent、输出信封 |
| `tactical_intelligence_agent/payload_adapter.py` | 输入外壳 → SensorBatch |
| `agent/orchestrator.py` | 三技能流水线 + 航迹历史累积 |
| `agent/track_packet.py` | tracks / history_path / consumer_guide |
| `tactical_intelligence_agent/downstream_adapter.py` | 下游按层取数 |
| `commander_agent/main.py` | 结果落库键 |
| `bpel_workflow.py` | 伙伴角色与命令映射 |
| `docs/TIA_COMMANDER_IO_SHELL.md` | I/O 字段契约（细表） |

---

## 四、输入 / 输出外壳如何统一

### 4.1 统一原则（对齐飞书外壳）

| 方向 | 约定 |
|------|------|
| Commander → Agent | 业务字段放在 `input.agent_request`；传感器文件放在 `attachments[]`（对象存储 URI，禁止裸 base64） |
| Agent → Commander | 业务结果放在 `output.<output_hint>`，TIA 固定为 `output.intelligence_packet` |
| 协议外壳字段 | `schema_version` / `workflow_id` / `work_item` / `command` / `required_skill` / `status` / `metrics` / `error` 等与平台一致 |

适配入口：`payload_adapter.commander_payload_to_batch()`  
兼容：`input` 顶层字段与 `input.agent_request` 合并（飞书 / lzh 双写法）。

### 4.2 输入外壳（摘要）

```json
{
  "schema_version": "1.0",
  "workflow_id": "wf-001",
  "work_item": "wf-001:tactical_intelligence",
  "command": "process_intelligence",
  "required_skill": "tactical_intelligence_analysis",
  "output_hint": "intelligence_packet",
  "input": {
    "agent_request": {
      "recon_report": "...",
      "sector": "Sector_A",
      "coordinates": { "lat": 30.52, "lon": 114.39 }
    }
  },
  "attachments": [
    {
      "uri": "https://.../frame.png",
      "kind": "image",
      "checksum": { "algorithm": "sha256", "value": "..." },
      "meta": {
        "sensor_id": "EO-UAV-01",
        "modality": "eo_ir",
        "platform_lat": 30.521,
        "platform_lon": 114.392,
        "altitude_m": 3200.0
      }
    }
  ],
  "context": {
    "jamming_level": 0.0,
    "subscriber_agents": ["commander", "trajectory_predictor", "artillery"],
    "sensor_telemetry": {}
  }
}
```

**真实演练约束：** 无 mock 图像兜底；至少提供 `attachments`，或 `recon_report` / `sector` / `coordinates` 之一。本地调试可用 `TIA_ALLOW_LOCAL_FILE=1` + `local:///D:/...`。

### 4.3 输出外壳（摘要）

```json
{
  "status": "completed",
  "role": "tactical_intelligence",
  "output": {
    "intelligence_packet": {
      "schema_version": "1.0",
      "packet_id": "...",
      "mission_id": "wf-001",
      "summary": "...",
      "tracks": [ { "track_id": "T-0001", "history_path": [ ... ], "lat": 0, "lon": 0 } ],
      "targets": [ { "track_id": "T-0001", "threat_level": "high", "geo": {} } ],
      "task_schedule": {},
      "output_attachments": [],
      "consumer_guide": { "sections": { "tracks": {}, "targets": {} } },
      "routing": {},
      "provenance": {}
    },
    "track_count": 0,
    "target_count": 0,
    "summary": "...",
    "consumer_guide": {},
    "resource_allocation": {}
  }
}
```

组装位置：`TacticalIntelligenceCommanderAgent._build_output()`。

### 4.4 packet 分层：下游该读哪一块

| 字段 | 消费者 | 用途 |
|------|--------|------|
| `tracks[]` + `history_path[]` | 航迹预测、跟踪更新、图关系推理 | 机器可读时空航迹（含 speed/heading） |
| `targets[]` | 决策规划、火力、评估 | 威胁 / 敌我 / 类别等语义目标 |
| `task_schedule` | 调度、Commander | 传感器分配与再攻击规划 |
| `output_attachments[]` | 可视化、BDA | 标注图等产物 URI |
| `summary` / `routing` | Commander、通信 | 摘要与路由建议 |
| `consumer_guide` | 全体下游 | 自描述：各 section 给谁用 |
| `schema_version` | 全体下游 | 契约版本（当前 `1.0`） |

代码辅助：

```python
from tactical_intelligence_agent.downstream_adapter import (
    to_trajectory_predictor_input,  # → {"tracks": [...]}
    to_decision_targets,
    to_task_schedule,
    to_output_attachments,
)
```

跨帧时 `agent/orchestrator.py` 通过 `accumulate_track_history()` 累积 `history_path`，保证航迹预测有足够时间序列。

---

## 五、与 lzh 主体对齐对照表

| 能力 | lzh / 主体做法 | TIA 本周对齐 |
|------|----------------|--------------|
| 启动 / 心跳 | `AgentRuntimeSDK.from_agent().serve()` | ✅ `main.py` |
| 技能匹配 | `required_skill` + 心跳 `skill_ids` | ✅ `tactical_intelligence_analysis` |
| 任务协议 | `A2ABaseAgent` 统一信封 | ✅ `service.py` |
| 输入业务字段 | `input.agent_request` | ✅ `payload_adapter` |
| 输出业务字段 | `output.<output_hint>` | ✅ `intelligence_packet` |
| Commander 落库 | role → context 键 | ✅ `intelligence_packet` |
| 算法调用 | 决策侧 `/run` | 保留 TIA `/predict`（并存） |

更细的字段表见：`docs/TIA_COMMANDER_IO_SHELL.md`。  
简要启动备忘见：`docs/接入说明文档.md`。

---

## 六、验证与联调结果

### 6.1 单测

```powershell
.\.venv\Scripts\python.exe -m unittest tactical_intelligence_agent.test_tactical_intelligence_agent -v
```

覆盖：payload 适配、无 mock 空输入拒绝、输出信封含 `tracks` / `schema_version`、`/sendMessage` 统一响应。

### 6.2 真实两帧 smoke

脚本：`scripts/smoke_tia_packet_tracks.py`  
数据：`examples/ue_naval_scenario/export/OP-IRON-SEA-001/battlefield/scene_000.png`、`scene_001.png`

```powershell
$env:TIA_ALLOW_LOCAL_FILE="1"
$env:TIA_EXECUTION_MODE="in_process"
$env:TIA_COMPUTE_PROFILE="medium"
.\.venv\Scripts\python.exe scripts/smoke_tia_packet_tracks.py
```

结果摘要：

- frame0：5 tracks，`history_path` 长度 1  
- frame1：3 tracks，同 ID（如 `T-0003`）`history_path` 长度 **2**  
- 产物：`data/output/smoke_tia_packet/`（含 `trajectory_request.json`）

---

## 七、本周结论与下周建议

**结论：** TIA 已按主体框架完成「可被 Commander 发现、可被统一外壳调用、可把结果写入 `context.intelligence_packet`」的接入；输出 packet 按层划分，下游可明确取 `tracks` / `targets` / `task_schedule` / `output_attachments`。

**建议下周：**

1. 在正式 BPEL 流程里插入 TIA 节点，端到端：TIA → 航迹预测 Agent  
2. 生产环境统一对象存储 URI（MinIO/S3），关闭 `TIA_ALLOW_LOCAL_FILE`  
3. 雷达/遥测 contacts 进一步并入感知 `prior_tracks`，减少「只靠 EO」断层  
4. 与师兄决策侧约定 `intelligence_packet/v1` 兼容策略（只增字段、不改语义）

---

## 八、相关文档索引

| 文档 | 内容 |
|------|------|
| 本文 | 本周接入主体框架 + I/O 统一说明 |
| `docs/TIA_COMMANDER_IO_SHELL.md` | 输入输出字段完整契约 |
| `docs/接入说明文档.md` | 启动与 Commander 调用速查 |
| `docs/TIA_ALGORITHMS_INTEGRATION.md` | 算法库 `/predict` 集成 |
| `docs/TIA_ALGORITHM_PACKAGE_ARCHITECTURE.md` | 算法包架构 |
