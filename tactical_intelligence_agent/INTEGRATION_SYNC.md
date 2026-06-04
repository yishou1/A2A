# A2A-main 分支合并指南（战术情报 Agent）

> 基线：`3d94a36 Add resident multi-workflow commander manager`  
> 分支建议：`feature/<name>-tactical-intelligence`  
> **本仓库为独立开发副本，合并前请勿直接修改 A2A-main 目录。**

## 1. 职责边界

| 层级 | 维护方 | 内容 |
|------|--------|------|
| 公共控制面 | 项目负责人 | Commander、WorkflowManager、checkpoint、附件协议、Nacos 心跳、租约 |
| 战术情报 Agent | 本分支 | `tactical_intelligence_agent/` + `agent/` 业务实现 |

本 Agent 为**单体进程**，内部串联感知 → 认知 → 通信三技能，**不拆成多个 skill Agent**。

## 2. 合并到 A2A-main 的文件清单

将以下内容复制/合并到 A2A-main 根目录（保持相对路径）：

```
tactical_intelligence_agent/
  __init__.py
  main.py              # 独立启动入口（与 recon_agent/main.py 同模式）
  bootstrap.py
  payload_adapter.py
  service.py
agent/                   # 完整算法与三技能目录（若主项目尚无）
workflow_payloads.py     # 若主项目已有则跳过（应完全一致）
tests/test_tactical_intelligence_agent.py
scripts/verify_commander_a2a.py
```

**依赖主项目已有模块（合并后改 import，不再复制）：**

- `a2a_protocol/server.py` → 使用主项目 `A2ABaseAgent`（本仓库对应 `a2a_protocol/commander_server.py`，API 一致）
- `registry/nacos_manager.py`
- `workflow_payloads.py`（主项目根目录已有）

合并后 `tactical_intelligence_agent/service.py` 建议改为：

```python
from a2a_protocol.server import A2ABaseAgent  # 主项目路径
```

## 3. 公共协议对齐

### 3.1 Agent 必须实现的接口

- [x] `GET /.well-known/agent-card`
- [x] Bearer JWT 鉴权（`Authorization: Bearer <token>`）
- [x] `POST /sendMessage`
- [x] `POST /sendMessageStream`（三技能阶段性 SSE 反馈）

### 3.2 固定字段

| 字段 | 本 Agent 取值 |
|------|----------------|
| `role` | `tactical_intelligence` |
| Nacos `service_name` | `A2A-Agent` |
| Nacos metadata.status | 启动 `idle`（由 Commander 租约改为 `busy`） |

### 3.3 sendMessage 返回示例

```json
{
  "work_item": "workflow-xxx:activatity-003-processintelligence",
  "workflow_id": "workflow-xxx",
  "status": "Accepted",
  "role": "tactical_intelligence",
  "message": "Tactical intelligence completed command=process_intelligence; targets=12; summary=...",
  "intelligence_packet_id": "uuid",
  "target_count": 12
}
```

### 3.4 sendMessageStream 阶段

| progress | stage | 说明 |
|----------|-------|------|
| 10% | perception | RT-DETR / Mask2Former / MOTR |
| 45% | cognition | ImageBind / Mamba / SynapseRAG |
| 75% | communication | 语义通信 / MARL 路由 |
| 100% | done | 含完整 `intelligence_packet` |

### 3.5 附件协议

- 任务 `attachments` **仅允许对象存储 URI**（s3/minio/https 等）
- 禁止在消息中内联 base64/bytes（与 `workflow_payloads.py` 一致）
- 本地联调可用 `attachment_uploader.py` 上传样例附件

### 3.6 幂等

- 同一 `work_item` 重复调用返回缓存结果（`A2ABaseAgent._task_response_cache` + 业务 `_result_cache`）

## 4. Commander 侧需增加的接入（合并 PR 时由负责人确认）

> **开发阶段不在 A2A-main 本地改代码**；以下为合并检查清单。

### 4.1 BPEL 工作流

在 `recon` 与 `artillery` 之间插入战术情报步骤，例如：

```xml
<invoke partnerLink="TacticalIntelligenceAgent" operation="processIntelligence"
        inputVariable="ReconReport" outputVariable="IntelligencePacket"/>
```

### 4.2 `commander_agent/main.py`

- `_build_dynamic_task_payload` / BPEL role 映射增加 `tactical_intelligence`
- `apply_agent_result` 写入 `context["intelligence_packet"]` 或 `context["tactical_intelligence_summary"]`
- `rule_next_step`：recon 完成后优先调度 `tactical_intelligence`

### 4.3 `local_runtime.py`

```python
"tactical_intelligence": {
    "name": "Local_Tactical_Intelligence_Agent",
    "description": "Local tactical intelligence unit.",
    "role": "tactical_intelligence",
},
```

并在 `send_message_stream` 中返回三阶段 + Completed 事件。

### 4.4 `start_agents.sh`

```bash
./venv/bin/python -u tactical_intelligence_agent/main.py &
export TIA_PORT=8015
```

## 5. 本地验证（本仓库）

### 5.1 启动服务

```powershell
cd d:\a2a_project\TacticalIntelligenceAgent
$env:TIA_CONFIG = "config\mock.yaml"
python run.py
```

### 5.2 Commander 协议验证

```powershell
python scripts\verify_commander_a2a.py
```

### 5.3 单元测试

```powershell
python -m unittest tests.test_tactical_intelligence_agent -v
```

### 5.4 与 A2A-main Commander 联调（可选）

1. 启动 Nacos  
2. 本仓库：`$env:TIA_NACOS_REGISTER="1"; python tactical_intelligence_agent/main.py`  
3. A2A-main：`commander_agent/main.py --mode remote`（需 BPEL 已接入 TIA 步骤）

## 6. 环境变量

| 变量 | 默认 | 说明 |
|------|------|------|
| `TIA_PORT` / `TACTICAL_INTELLIGENCE_AGENT_PORT` | `8080` / `8015` | 服务端口 |
| `TIA_A2A_ROLE` | `tactical_intelligence` | Nacos role |
| `TIA_CONFIG` | `config/default.yaml` | 推理配置 |
| `TIA_NACOS_REGISTER` | `0`（run.py）/ `1`（main.py） | 是否注册 Nacos |
| `A2A_HEARTBEAT_INTERVAL` | `5` | 心跳秒数 |

## 7. 验收标准（合并 PR）

完整清单与分支步骤见 **[`MERGE_CHECKLIST.md`](MERGE_CHECKLIST.md)**。

一键验收：

```powershell
python scripts\run_merge_acceptance.py
```

| # | 验收项 | 状态 |
|---|--------|------|
| 1 | 代码能跑 | `test_main_module_importable` |
| 2 | Agent 能被发现 | `test_agent_card_discovery` |
| 3 | 能接收任务 + 鉴权 | `test_send_message_accepts_task` |
| 4 | 返回结构符合协议 | `test_send_message_response_schema` |
| 5 | 流式输出正常 | `test_stream_progress_events` |
| 6 | resume 幂等 / SSE 重放 | `test_idempotent_task_and_stream_replay` |
| 7 | 不破坏已有测试 | 合并后在 A2A-main 跑全量 `unittest discover` |

## 8. 数据流摘要

```
Commander task payload
  ├─ workflow_id / work_item / command
  ├─ input.recon_report / sector / coordinates
  └─ attachments[] (对象存储 URI)
           │
           ▼
  payload_adapter.commander_payload_to_batch()
           │
           ▼
  TacticalIntelligenceAgent.process()  # 单 Agent 三技能
           │
           ▼
  SemanticIntelligencePacket → SSE Completed / sendMessage message
```

下游 Agent（artillery / evaluator）从 `intelligence_packet.targets`、`routing`、`summary` 读取决策输入。
