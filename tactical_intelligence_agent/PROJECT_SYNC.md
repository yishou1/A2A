# 战术情报 Agent — 项目同步说明（接入与验收）

> 对照文档：《项目同步说明-新》（2026-06-01）  
> 基线 commit：`3d94a36 Add resident multi-workflow commander manager`  
> 分支：`feat/tactical-intelligence-agent`  
> 角色：`tactical_intelligence`

本文说明：**本 Agent 如何接入师兄 A2A-main 框架**、**哪些公共边界已对齐**、**文档中的验收项在代码里如何实现**，并附关键源码位置。

---

## 1. 在 A2A 三层架构中的位置

师兄文档将项目分为三层。我们**只实现业务层**，不修改公共控制面：

| 层级 | 目录/模块 | 本 Agent 是否改动 |
|------|-----------|-------------------|
| 常驻调度层 | `commander_agent/workflow_manager.py`、租约 | ❌ 未改 |
| 公共控制面 | `a2a_protocol/`、`workflow_state_store.py`、`registry/` | ❌ 未改，仅**复用** |
| 业务实现层 | `recon_agent/`、`artillery_agent/`、**本 Agent** | ✅ 新增 |

本 Agent 目录结构：

```
tactical_intelligence_agent/   ← 协议接入层（HTTP / Nacos / 幂等）
agent/                         ← 业务流水线（感知→认知→通信）
config/default.yaml            ← 推理配置
scripts/demo_tactical_intelligence_acceptance.py  ← 7 步验收演示
scripts/run_simulation.py      ← 铁谷红蓝四阶段仿真
```

---

## 2. 与现有 Agent 相同的启动模式

与 `recon_agent/main.py` 一致：**A2ABaseAgent + NacosRegistry + start()**。

Recon 参考实现：

```1:25:recon_agent/main.py
from a2a_protocol.server import A2ABaseAgent
from registry.nacos_manager import NacosRegistry, get_host_ip
import os

if __name__ == "__main__":
    port = int(os.environ.get("RECON_AGENT_PORT", "8002"))
    heartbeat_interval = float(os.environ.get("A2A_HEARTBEAT_INTERVAL", "5"))
    agent = A2ABaseAgent(
        name="Recon_Agent",
        description="Performs reconnaissance to gather enemy positions and weather.",
        role="recon",
        port=port
    )
    
    registry = NacosRegistry()
    ip = get_host_ip()
    
    registry.register_service(
        service_name="A2A-Agent",
        ip=ip,
        port=port,
        metadata={"role": "recon", "status": "idle"},
        heartbeat_interval=heartbeat_interval,
    )
    agent.start()
```

本 Agent 启动（`role=tactical_intelligence`，同样注册 `A2A-Agent`）：

```16:40:tactical_intelligence_agent/main.py
if __name__ == "__main__":
    port = int(os.environ.get("TIA_PORT", os.environ.get("TACTICAL_INTELLIGENCE_AGENT_PORT", "8015")))
    heartbeat_interval = float(os.environ.get("A2A_HEARTBEAT_INTERVAL", "5"))
    role = os.environ.get("TIA_A2A_ROLE", "tactical_intelligence")

    agent = TacticalIntelligenceCommanderAgent(port=port, role=role)

    if os.environ.get("TIA_NACOS_REGISTER", "1") == "1":
        registry = NacosRegistry()
        ip = get_host_ip()
        registry.register_service(
            service_name=os.environ.get("TIA_NACOS_SERVICE", "A2A-Agent"),
            ip=ip,
            port=port,
            metadata={
                "role": role,
                "status": "idle",
                "capability": "semantic_intelligence",
                "protocol": "http+a2a-commander",
            },
            heartbeat_interval=heartbeat_interval,
        )
        print(f"[NACOS] registered A2A-Agent at {ip}:{port} role={role}")

    agent.start()
```

**适配要点**：Nacos `metadata.status=idle`，可被 Manager 租约改为 `busy`；心跳间隔与公共层默认 5 秒一致。

---

## 3. 继承 A2ABaseAgent（公共协议基类）

`TacticalIntelligenceCommanderAgent` 继承师兄框架的 `A2ABaseAgent`，而非自建 HTTP 服务：

```10:18:tactical_intelligence_agent/service.py
try:
    from a2a_protocol.server import A2ABaseAgent as _ServerBase
except ImportError:  # TIA 独立仓库无 server.A2ABaseAgent
    from a2a_protocol.commander_server import A2ABaseAgent as _ServerBase

from agent.models.schemas import SemanticIntelligencePacket
from agent.orchestrator import TacticalIntelligenceAgent
from tactical_intelligence_agent.bootstrap import create_engine, default_role
from tactical_intelligence_agent.payload_adapter import commander_payload_to_batch
```

基类提供的公共能力（缓存、work_list、路由模板）：

```16:27:a2a_protocol/server.py
class A2ABaseAgent:
    def __init__(self, name: str, description: str, role: str, port: int):
        self.name = name
        self.description = description
        self.role = role
        self.port = port
        self._task_response_cache = {}
        self._stream_response_cache = {}
        self._workflow_work_lists = {}
        self._state_lock = threading.RLock()
        self.app = FastAPI(title=name)
        self.setup_routes()
```

---

## 4. 四项最少接口行为（同步说明 §4.2）

### 4.1 Agent 能被发现 — `GET /.well-known/agent-card`

路由注册与 card 结构：

```53:55:tactical_intelligence_agent/service.py
        @target.get("/.well-known/agent-card")
        async def agent_card():
            return self.get_agent_card()
```

```29:46:a2a_protocol/server.py
    def get_agent_card(self):
        auth_server_base = os.environ.get("A2A_AUTH_SERVER_BASE", "http://127.0.0.1:8080")
        auth_server_base = auth_server_base.rstrip("/") + "/"
        return {
            "name": self.name,
            "description": self.description,
            "role": self.role,
            "securitySchemes": { ... },
            "sendMessageEndpoint": "/sendMessage",
            "sendMessageStreamEndpoint": "/sendMessageStream",
            "workListEndpoint": "/workflows/{workflow_id}/work-list",
        }
```

本 Agent 扩展 `capabilities` 字段：

```112:119:tactical_intelligence_agent/service.py
    def get_agent_card(self):
        card = super().get_agent_card()
        card["capabilities"] = [
            "semantic_intelligence",
            "multimodal_fusion",
            "anti_jam_routing",
        ]
        return card
```

**验收命令**：`curl http://127.0.0.1:8016/.well-known/agent-card`  
**演示脚本**：`scripts/demo_tactical_intelligence_acceptance.py` 步骤 2

---

### 4.2 能通过鉴权 — Bearer JWT

公共鉴权函数（所有 Agent 共用）：

```11:14:a2a_protocol/server.py
def verify_token(authorization: str = Header(None)):
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Unauthorized")
    return authorization.split("Bearer ")[1]
```

绑定到 sendMessage / sendMessageStream：

```66:67:tactical_intelligence_agent/service.py
        @target.post("/sendMessage")
        async def send_message(payload: dict, token: str = Depends(verify_token)):
```

**验收**：无 `Authorization` → HTTP 401；`Bearer mock-jwt-token-abcd` → 200（演示脚本步骤 3）

---

### 4.3 能处理 sendMessage — 统一返回结构

同步说明要求的返回格式：

```json
{
  "work_item": "...",
  "status": "Accepted",
  "role": "tactical_intelligence",
  "message": "..."
}
```

实现（含业务字段 `intelligence_packet_id`、`target_count`）：

```134:149:tactical_intelligence_agent/service.py
    def build_send_message_response(self, payload: dict, work_item: str) -> dict:
        try:
            packet = self._get_or_process(payload)
            return {
                "work_item": work_item,
                "workflow_id": payload.get("workflow_id"),
                "status": "Accepted",
                "role": self.role,
                "message": (
                    f"Tactical intelligence completed command={payload.get('command')}; "
                    f"targets={len(packet.targets)}; summary={packet.summary[:120]}"
                ),
                "intelligence_packet_id": packet.packet_id,
                "target_count": len(packet.targets),
                "work_list_size": len(self.get_work_list(payload.get("workflow_id"))),
            }
```

失败时返回 `status: Failed`（同文件 150–158 行）。

---

### 4.4 长任务流式 — `POST /sendMessageStream`（SSE）

三技能阶段 + Completed，与内部流水线对应：

```163:208:tactical_intelligence_agent/service.py
    async def execute_stream(self, payload: dict) -> AsyncIterator[str]:
        work_item = self._work_item_from_payload(payload)
        ...
        yield self._sse_event(status="Working", progress="10%", stage="perception", ...)
        ...
        yield self._sse_event(status="Working", progress="45%", stage="cognition", ...)
        ...
        yield self._sse_event(status="Working", progress="75%", stage="communication", ...)
        packet = self._get_or_process(payload)
        yield self._sse_event(
            status="Completed",
            progress="100%",
            stage="done",
            intelligence_packet=packet.model_dump(mode="json"),
            ...
        )
```

SSE 缓存与重放由基类 `_cached_stream` 实现：

```72:87:a2a_protocol/server.py
    async def _cached_stream(self, payload):
        self._capture_work_list(payload)
        work_item = self._work_item_from_payload(payload)
        with self._state_lock:
            cached_events = self._stream_response_cache.get(work_item)
        if cached_events is not None:
            async for event in self._replay_stream(cached_events):
                yield event
            return
        buffered_events = []
        async for event in self.execute_stream(payload):
            buffered_events.append(event)
            yield event
        with self._state_lock:
            self._stream_response_cache[work_item] = buffered_events
```

**验收**：演示脚本步骤 5，检查 4 条 SSE 且末条含 `intelligence_packet`

---

## 5. 公共字段与附件协议（同步说明 §4.1）

### 5.1 固定字段映射

| 字段 | 代码处理位置 |
|------|-------------|
| `workflow_id` | `payload_adapter.py` → `SensorBatch.mission_id` |
| `work_item` | `A2ABaseAgent._work_item_from_payload()`，幂等键 |
| `attachments` | `normalize_attachments()` 校验后转 SensorFrame |
| `role` | 构造时 `default_role()` → `"tactical_intelligence"` |
| `status` | sendMessage / SSE 事件中返回 |
| `work_list` | `_capture_work_list()` 缓存，响应中带 `work_list_size` |

work_item 提取：

```54:55:a2a_protocol/server.py
    def _work_item_from_payload(self, payload):
        return payload.get("work_item") or payload.get("task_id", "work-item-001")
```

work_list 捕获：

```57:62:a2a_protocol/server.py
    def _capture_work_list(self, payload):
        workflow_id = payload.get("workflow_id")
        work_list = payload.get("work_list")
        if workflow_id and isinstance(work_list, list):
            with self._state_lock:
                self._workflow_work_lists[workflow_id] = deepcopy(work_list)
```

### 5.2 附件仅允许对象存储 URI

主项目 `workflow_payloads.py` 禁止内联二进制、禁止 `file://`：

```29:30:workflow_payloads.py
INLINE_ATTACHMENT_FIELDS = {"data", "base64", "bytes", "content", "buffer", "raw", "payload"}
SUPPORTED_ATTACHMENT_SCHEMES = {"s3", "gs", "oss", "minio", "cos", "azblob", "http", "https"}
```

```61:70:workflow_payloads.py
def _ensure_object_storage_uri(uri: Any) -> str:
    ...
    if parsed.scheme == "file":
        raise ValueError("attachment uri must not use the file:// scheme")
```

本 Agent 适配层调用公共校验：

```110:111:tactical_intelligence_agent/payload_adapter.py
    attachments = normalize_attachments(payload.get("attachments"))
    frames = [_frame_from_attachment(item, index) for index, item in enumerate(attachments)]
```

附件转传感器帧（只存 URI，不内联 bytes）：

```39:43:tactical_intelligence_agent/payload_adapter.py
    payload: dict[str, Any] = {"attachment_ref": attachment}
    if modality == SensorModality.TEXT_REPORT:
        payload["text"] = meta.get("text") or f"attachment:{attachment['uri']}"
    else:
        payload["image_uri"] = attachment["uri"]
```

上游 BPEL 变量（如 recon 报告）写入 input 帧：

```53:64:tactical_intelligence_agent/payload_adapter.py
def _frames_from_input(input_payload: dict[str, Any]) -> list[SensorFrame]:
    frames: list[SensorFrame] = []
    recon_report = input_payload.get("recon_report")
    if recon_report:
        frames.append(
            SensorFrame(
                sensor_id="RECON-TEXT",
                modality=SensorModality.TEXT_REPORT,
                payload={"text": str(recon_report)},
                metadata={"source": "recon_report"},
            )
        )
```

---

## 6. Commander 载荷 → 业务流水线

完整适配入口：

```100:149:tactical_intelligence_agent/payload_adapter.py
def commander_payload_to_batch(payload: dict[str, Any], *, allow_mock_fallback: bool = True) -> SensorBatch:
    workflow_id = payload.get("workflow_id") or payload.get("work_item") or "WF-UNKNOWN"
    command = payload.get("command") or "process_intelligence"
    attachments = normalize_attachments(payload.get("attachments"))
    ...
    batch_context: dict[str, Any] = {
        "command": command,
        "work_item": payload.get("work_item"),
        "jamming_level": float(upstream_context.get("jamming_level", 0.0)),
        "subscriber_agents": upstream_context.get("subscriber_agents") or [...],
        ...
    }
    return SensorBatch(mission_id=str(workflow_id), frames=frames, context=batch_context)
```

三技能串联（业务层，与 HTTP 无关）：

```42:60:agent/orchestrator.py
    def process(self, batch: SensorBatch) -> SemanticIntelligencePacket:
        mission_id = batch.mission_id
        prior = self._track_state.get(mission_id, [])

        perception_out = self.perception.execute(batch, prior_tracks=prior)
        self._track_state[mission_id] = perception_out.tracks

        cognition_out = self.cognition.execute(batch, perception_out)

        jamming = float(batch.context.get("jamming_level", 0.0))
        subscribers = batch.context.get("subscriber_agents") or []

        return self.communication.execute(
            mission_id, perception_out, cognition_out,
            subscriber_agents=subscribers, jamming_level=jamming,
        )
```

service 层调用：

```121:132:tactical_intelligence_agent/service.py
    def _get_or_process(self, payload: dict) -> SemanticIntelligencePacket:
        work_item = self._work_item_from_payload(payload)
        with self._result_lock:
            cached = self._result_cache.get(work_item)
        if cached is not None:
            return cached

        batch = commander_payload_to_batch(payload)
        packet = self._engine.process(batch)
        with self._result_lock:
            self._result_cache[work_item] = packet
        return packet
```

**数据流图**：

```
Commander payload
  → payload_adapter.commander_payload_to_batch()
  → TacticalIntelligenceAgent.process()   # agent/orchestrator.py
  → SemanticIntelligencePacket
  → sendMessage / SSE Completed
```

---

## 7. 幂等实现（同步说明 §5.2）

同一 `work_item` 三层缓存，满足「重复提交不重复产生副作用」：

| 层级 | 变量 | 作用 |
|------|------|------|
| 协议层 | `_task_response_cache` | sendMessage 响应重放 |
| 协议层 | `_stream_response_cache` | SSE 事件重放 |
| 业务层 | `_result_cache` | 神经网络推理只执行一次 |

sendMessage 协议层缓存：

```66:78:tactical_intelligence_agent/service.py
        @target.post("/sendMessage")
        async def send_message(payload: dict, token: str = Depends(verify_token)):
            ...
            with self._state_lock:
                cached_response = self._task_response_cache.get(work_item)
            if cached_response is not None:
                return cached_response
            response = self.build_send_message_response(payload, work_item)
            with self._state_lock:
                self._task_response_cache[work_item] = response
            return response
```

单元测试验证业务幂等：

```56:67:tactical_intelligence_agent/test_tactical_intelligence_agent.py
    def test_send_message_idempotent(self):
        agent = TacticalIntelligenceCommanderAgent(port=0)
        payload = { ... }
        first = agent.build_send_message_response(payload, payload["work_item"])
        second = agent.build_send_message_response(payload, payload["work_item"])
        self.assertEqual(first["intelligence_packet_id"], second["intelligence_packet_id"])
        self.assertEqual(first["target_count"], second["target_count"])
```

**恢复边界说明**：

- **workflow checkpoint** 由 `workflow_state_store.py` / Commander 负责，本 Agent **不**持久化 workflow 状态。
- Commander **resume** 后若重复下发同一 `work_item`，本 Agent 返回缓存，避免重复推理。

---

## 8. 合并 PR 七项验收（同步说明 §7）

| # | 验收项 | 实现代码 | 验证方式 |
|---|--------|----------|----------|
| 1 | 代码能跑 | `main.py` + `bootstrap.py` | `import tactical_intelligence_agent.main` |
| 2 | Agent 能被发现 | `get_agent_card()` | 步骤 2 / `curl /.well-known/agent-card` |
| 3 | 能接收任务 | `verify_token` + `send_message` | 步骤 3：401 / 200 |
| 4 | 返回结构符合协议 | `build_send_message_response()` | 步骤 4：含 work_item/status/role/message |
| 5 | 流式输出正常 | `execute_stream()` + `_cached_stream()` | 步骤 5：4 条 SSE |
| 6 | 恢复后继续工作 | 双层/三层缓存 | 步骤 6：同一 work_item 两次响应一致 |
| 7 | 不破坏已有测试 | 未改 `commander_agent/`、`tests/` 公共用例 | `python -m unittest discover -s tests` |

**分步可视化验收**（推荐录屏/答辩）：

```powershell
# 终端 1
$env:PYTHONPATH="."; $env:TIA_CONFIG="config\default.yaml"
$env:TIA_ALLOW_INLINE_FRAMES="1"; $env:TIA_NACOS_REGISTER="0"; $env:TIA_PORT="8016"
.\.venv\Scripts\python.exe tactical_intelligence_agent\main.py

# 终端 2
$env:PYTHONPATH="."; $env:TIA_PORT="8016"
.\.venv\Scripts\python.exe scripts\demo_tactical_intelligence_acceptance.py --pause
```

详见 [`DEMO_GUIDE.md`](DEMO_GUIDE.md)、[`MERGE_CHECKLIST.md`](MERGE_CHECKLIST.md)。

---

## 9. 建议交付物（同步说明 §11）

| 交付物 | 路径 |
|--------|------|
| Agent 代码 | `tactical_intelligence_agent/` + `agent/` |
| 对应测试 | `tactical_intelligence_agent/test_tactical_intelligence_agent.py` |
| 说明文档 | 本文、`INTEGRATION_SYNC.md`、`DEMO_GUIDE.md` |
| 最小 demo | `demo_tactical_intelligence_acceptance.py` / `run_simulation.py` |

**单元测试运行**：

```powershell
$env:PYTHONPATH="."; $env:TIA_CONFIG="config\default.yaml"
.\.venv\Scripts\python.exe tactical_intelligence_agent\test_tactical_intelligence_agent.py
```

**业务仿真（铁谷红蓝四阶段）**：

```powershell
$env:PYTHONPATH="."; $env:TIA_CONFIG="config\default.yaml"
.\.venv\Scripts\python.exe scripts\run_simulation.py
```

---

## 10. 我们未改动的公共模块（合并安全）

以下目录/文件**未在本 PR 中修改**，避免与师兄负责的控制面冲突：

- `commander_agent/`（含 WorkflowManager、租约、resume）
- `workflow_state_store.py`、`bpel_workflow.py`
- `a2a_protocol/`（仅 import）
- `registry/nacos_manager.py`（仅调用）

---

## 11. 待师兄侧完成的 Commander 接入（合并后联调）

本 PR 已具备**独立运行 + 协议验收**。编入完整 BPEL 工作流需师兄确认后在 Commander 侧增加：

| 项 | 建议改动 | 状态 |
|----|----------|------|
| BPEL | recon → **tactical_intelligence** → artillery | ⏳ 待接入 |
| `commander_agent/main.py` | role 映射 `tactical_intelligence` | ⏳ 待接入 |
| `local_runtime.py` | 本地 mock 条目 | ⏳ 可选 |
| `start_agents.sh` | 启动 `tactical_intelligence_agent/main.py` | ⏳ 可选 |

预期 BPEL 片段（示例）：

```xml
<invoke partnerLink="TacticalIntelligenceAgent" operation="processIntelligence"
        inputVariable="ReconReport" outputVariable="IntelligencePacket"/>
```

---

## 12. 给师兄的一句话摘要

公共控制面未改动。本 Agent 按 `A2ABaseAgent` 协议实现 `role=tactical_intelligence`，复用 Nacos 心跳与附件校验，支持 agent-card / JWT / sendMessage / sendMessageStream 与 work_item 幂等；业务上在 `agent/orchestrator.py` 串联感知→认知→通信。分支 `feat/tactical-intelligence-agent` 可用 `demo_tactical_intelligence_acceptance.py --pause` 逐步验收。
