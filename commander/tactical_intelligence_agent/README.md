# 战术情报 Agent — 交付说明

> 角色：`tactical_intelligence`  
> 分支：`feat/tactical-intelligence-agent`  
> 基线：`3d94a36`（A2A-main main）

本文按《项目同步说明》**第十一节「建议的最终交付物」**组织：说明本 Agent **如何接入 A2A 项目**、**如何实现**，并在代码旁附简要解释，便于合并时直接验证。

---

## 本 Agent 做什么

在 A2A 工作流中，本 Agent 位于 **侦察（recon）与火力（artillery）之间**（BPEL 接入待师兄确认），负责：

- **输入**：Commander 下发的任务（`workflow_id`、`work_item`、侦察报告、对象存储附件、干扰等级等）
- **处理**：单体进程内串联 **感知 → 认知 → 通信** 三技能
- **输出**：语义情报包（目标列表、摘要、抗干扰路由），供下游 Agent 使用

接入方式与 `recon_agent`、`artillery_agent` 相同：**继承公共 `A2ABaseAgent`，注册 Nacos，暴露 HTTP 接口**，不修改 Commander / checkpoint / 租约等公共控制面。

---

## 如何接入 A2A 项目（总览）

```
Commander / A2AClient
    │  POST /sendMessage 或 /sendMessageStream（Bearer JWT）
    ▼
tactical_intelligence_agent/service.py   ← 协议层（A2A 接口、幂等、SSE）
    │  payload_adapter：Commander 载荷 → SensorBatch
    ▼
agent/orchestrator.py                    ← 业务层（三技能流水线）
    │
    ▼
SemanticIntelligencePacket → JSON 响应 / SSE Completed
```

| 层级 | 目录 | 职责 |
|------|------|------|
| 协议接入 | `tactical_intelligence_agent/` | HTTP、鉴权、Nacos、幂等、载荷转换 |
| 业务实现 | `agent/` | 感知 / 认知 / 通信算法与推理 |
| 配置 | `config/default.yaml` | 真实神经网络推理（`use_mock: false`） |

**设计原则**：协议层与业务层分离——师兄改 Commander 时，我们只需保证 HTTP 接口不变；我们改算法时，也不触碰 `commander_agent/` 等公共文件。

---

# 一、Agent 代码

## 1.1 启动入口（与 recon 同模式）

**做什么**：以独立进程启动 FastAPI 服务，并向 Nacos 注册，让 Commander / WorkflowManager 在 remote 模式下能发现本 Agent。

**为什么这样写**：完全对齐 `recon_agent/main.py` 的模式——同一套 `A2A-Agent` 服务名 + `metadata.role` 区分角色 + `status=idle` 供租约系统标记 busy/idle。师兄侧无需为 TIA 单独写发现逻辑。

**关键点**：
- `TIA_PORT`：HTTP 监听端口，避免与其他 Agent 冲突
- `TIA_NACOS_REGISTER=0`：本地仅测 HTTP 时可跳过 Nacos
- `A2A_HEARTBEAT_INTERVAL=5`：与主项目默认心跳一致

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

---

## 1.2 协议实现（四项最少接口）

同步说明要求每个 Agent 至少满足：**能被发现、能鉴权、能 sendMessage、能流式反馈**。以下代码即对此的实现。

### 继承公共基类

**做什么**：从师兄维护的 `a2a_protocol.server.A2ABaseAgent` 继承，自动获得 agent-card 模板、work_list 缓存、SSE 重放等公共能力。

**为什么**：避免重复实现 HTTP 路由和幂等框架；与 recon/artillery 使用同一基类，合并冲突面小。

```10:18:tactical_intelligence_agent/service.py
try:
    from a2a_protocol.server import A2ABaseAgent as _ServerBase
except ImportError:
    from a2a_protocol.commander_server import A2ABaseAgent as _ServerBase

from agent.models.schemas import SemanticIntelligencePacket
from agent.orchestrator import TacticalIntelligenceAgent
from tactical_intelligence_agent.bootstrap import create_engine, default_role
from tactical_intelligence_agent.payload_adapter import commander_payload_to_batch
```

### sendMessage + 鉴权 + 协议层幂等

**做什么**：
1. `Depends(verify_token)` — 无 Bearer JWT 直接 401
2. `_capture_work_list` — 缓存 Commander 下发的 work_list，便于恢复时对齐
3. `_task_response_cache` — 同一 `work_item` 第二次请求直接返回缓存，不重复执行业务

**对应验收**：「能鉴权后调用」「重复 work_item 保持幂等」

```66:78:tactical_intelligence_agent/service.py
        @target.post("/sendMessage")
        async def send_message(payload: dict, token: str = Depends(verify_token)):
            self._capture_work_list(payload)
            work_item = self._work_item_from_payload(payload)
            with self._state_lock:
                cached_response = self._task_response_cache.get(work_item)
            if cached_response is not None:
                return cached_response

            response = self.build_send_message_response(payload, work_item)
            with self._state_lock:
                self._task_response_cache[work_item] = response
            return response
```

### 统一返回结构

**做什么**：在基类要求的 `work_item / status / role / message` 之外，附加业务字段 `intelligence_packet_id`、`target_count`，供 Commander 写入 checkpoint 或传给 artillery。

**为什么 `status=Accepted`**：与师兄文档示例及 recon/artillery 一致；失败时同函数返回 `Failed` 和错误信息（见 service.py 150–158 行）。

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

### 流式 SSE（三技能阶段）

**做什么**：长任务通过 SSE 推送进度。前三条事件对应内部三技能（感知 10% → 认知 45% → 通信 75%），最后一条 `Completed` 携带完整 `intelligence_packet` JSON。

**为什么分阶段**：满足同步说明「流式任务输出阶段性事件」；Commander / 前端可展示进度条；与 `agent/orchestrator.py` 的三步流水线一一对应。

**幂等**：基类 `_cached_stream` 会缓存 SSE 事件序列，同一 `work_item` 重放时逐条输出相同内容（Commander resume 场景）。

```163:208:tactical_intelligence_agent/service.py
    async def execute_stream(self, payload: dict) -> AsyncIterator[str]:
        work_item = self._work_item_from_payload(payload)
        ...
        yield self._sse_event(status="Working", progress="10%", stage="perception", ...)
        yield self._sse_event(status="Working", progress="45%", stage="cognition", ...)
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

---

## 1.3 Commander 载荷适配（附件协议 + 公共字段）

**做什么**：把 Commander 下发的 JSON 任务包，转成内部统一的 `SensorBatch` 结构，再交给 `agent/orchestrator.py` 处理。

**为什么需要这一层**：Commander 用的是公共任务信封（`workflow_id`、`attachments`、`input`）；业务流水线用的是传感器批次（多模态帧 + context）。适配层是「接入 A2A」与「跑算法」之间的桥梁。

### 附件校验与帧构建

**做什么**：
- 调用主项目 `normalize_attachments()`，拒绝 base64 内联、拒绝 `file://`
- 每个附件变成一条 `SensorFrame`（EO/SAR/文本等）
- 把 `input.recon_report` 等 BPEL 上游变量也转成文本帧

**对应同步说明**：「attachments 只允许对象存储引用」

```100:111:tactical_intelligence_agent/payload_adapter.py
def commander_payload_to_batch(payload: dict[str, Any], *, allow_mock_fallback: bool = True) -> SensorBatch:
    workflow_id = payload.get("workflow_id") or payload.get("work_item") or "WF-UNKNOWN"
    command = payload.get("command") or "process_intelligence"

    attachments = normalize_attachments(payload.get("attachments"))
    frames = [_frame_from_attachment(item, index) for index, item in enumerate(attachments)]

    input_payload = dict(payload.get("input") or {})
    frames.extend(_frames_from_input(input_payload))
```

### 上游 recon 报告接入

**做什么**：recon Agent 产出的文字报告通过 `input.recon_report` 传入，这里转成 `TEXT_REPORT` 模态帧，供认知/通信技能做 RAG 和语义压缩。

**典型链路**：BPEL 中 recon 输出变量 → Commander 填入 `input` → 本 Agent 读取并融合进批次。

```53:64:tactical_intelligence_agent/payload_adapter.py
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

## 1.4 三技能业务流水线

**做什么**：在 `agent/` 目录内完成全部算法逻辑，与 HTTP 无关。三步严格顺序执行，输出一个 `SemanticIntelligencePacket`。

| 步骤 | 技能 | 主要能力 |
|------|------|----------|
| 1 | PerceptionSkill | 检测、毁伤、EDL 验证、MOTR 跟踪 |
| 2 | CognitionSkill | 多模态嵌入、Mamba 融合、RAG、分类 |
| 3 | CommunicationSkill | 语义通信压缩、MARL 抗干扰路由 |

**为什么单体不拆三个 Agent**：同步说明按「一个同学负责一个 Agent 能力面」分工；我们的能力面是「战术情报整包」，内部三技能是流水线阶段，不是三个独立 Nacos 服务。

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

### 业务层幂等（不重复跑神经网络）

**做什么**：即使协议层缓存未命中，同一 `work_item` 的推理结果也只计算一次，存入 `_result_cache`。

**为什么需要两层缓存**：协议层缓存 HTTP 响应格式；业务层缓存重推理开销大的 `SemanticIntelligencePacket`。resume 重复下发同一 work_item 时，不会重复加载 YOLO / CLIP 等模型推理。

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

---

## 1.5 代码目录清单

```
tactical_intelligence_agent/
  main.py              启动 + Nacos
  service.py           A2A 协议 + SSE + 幂等
  payload_adapter.py   Commander 载荷 → SensorBatch
  bootstrap.py         加载 config，创建推理引擎
agent/
  orchestrator.py      三技能串联
  skills/              perception / cognition / communication
  inference/           神经网络实现
config/default.yaml    推理配置
scripts/
  demo_tactical_intelligence_acceptance.py
  run_simulation.py
  build_situation.py
```

---

# 二、对应测试

文件：`tactical_intelligence_agent/test_tactical_intelligence_agent.py`

同步说明要求：「至少一条能证明可被接入的测试」，覆盖发现、鉴权、结构、流式、幂等。下表为对应关系：

| 测试项 | 验证什么 | 对应同步说明 |
|--------|----------|--------------|
| `test_commander_payload_to_batch` | Commander JSON → SensorBatch 转换正确 | 公共字段、附件协议 |
| `test_execute_stream_completed` | SSE ≥4 条，末条 Completed + intelligence_packet | 流式阶段性事件 |
| `test_send_message_idempotent` | 同一 work_item 两次响应 id/count 相同 | 幂等 / resume 重放 |

### 载荷转换测试

**解释**：构造带对象存储附件 + recon_report 的标准 payload，断言 `mission_id`、帧数量、`command` 写入 context——证明适配层符合公共协议，业务层能收到正确输入。

```16:35:tactical_intelligence_agent/test_tactical_intelligence_agent.py
class PayloadAdapterTest(unittest.TestCase):
    def test_commander_payload_to_batch(self):
        attachment = build_attachment_ref(
            "https://minio.example.local/a2a/recon/frame-001.jpg",
            sha256="deadbeef",
            kind="image",
            attachment_id="att-1",
        )
        payload = {
            "workflow_id": "wf-001",
            "work_item": "wf-001:activatity-001",
            "command": "process_intelligence",
            "input": {"recon_report": "Enemy positions observed."},
            "attachments": [attachment],
            "context": {"jamming_level": 0.2},
        }
        batch = commander_payload_to_batch(payload)
        self.assertEqual(batch.mission_id, "wf-001")
        self.assertGreaterEqual(len(batch.frames), 2)
        self.assertEqual(batch.context["command"], "process_intelligence")
```

### 流式 + 幂等测试

**解释**：
- `test_execute_stream_completed`：不启 HTTP，直接调 `execute_stream()`，验证四阶段事件与最终情报包——等价于 sendMessageStream 的核心逻辑
- `test_send_message_idempotent`：连续两次 `build_send_message_response`，断言 `intelligence_packet_id` 不变——证明业务层缓存生效

```38:67:tactical_intelligence_agent/test_tactical_intelligence_agent.py
class TacticalIntelligenceCommanderAgentTest(unittest.IsolatedAsyncioTestCase):
    async def test_execute_stream_completed(self):
        agent = TacticalIntelligenceCommanderAgent(port=0)
        ...
        self.assertGreaterEqual(len(events), 4)
        last = json.loads(events[-1].removeprefix("data: ").strip())
        self.assertEqual(last["status"], "Completed")
        self.assertIn("intelligence_packet", last)

    def test_send_message_idempotent(self):
        ...
        first = agent.build_send_message_response(payload, payload["work_item"])
        second = agent.build_send_message_response(payload, payload["work_item"])
        self.assertEqual(first["intelligence_packet_id"], second["intelligence_packet_id"])
        self.assertEqual(first["target_count"], second["target_count"])
```

**运行命令**（使用 `default.yaml` 真实推理）：

```powershell
cd D:\a2a_project\A2A-main
$env:PYTHONPATH = "."
$env:TIA_CONFIG = "config\default.yaml"
.\.venv\Scripts\python.exe tactical_intelligence_agent\test_tactical_intelligence_agent.py
```

**HTTP 端到端验收**（含鉴权 401 等，适合录屏）：`scripts/demo_tactical_intelligence_acceptance.py --pause`（见第四节）。

---

# 三、简短说明（本文档 + 补充）

| 文档 | 内容 |
|------|------|
| **本文 `README.md`** | 交付物总览、接入方式、代码位置与解释、运行命令 |
| [`DEMO_GUIDE.md`](DEMO_GUIDE.md) | 分步可视化演示 |
| [`PROJECT_SYNC.md`](PROJECT_SYNC.md) | 与《项目同步说明》逐条对照 |

**输入 / 输出摘要**

| 方向 | 内容 |
|------|------|
| 输入 | `workflow_id`、`work_item`、`command`、`input.recon_report`、`attachments[]`（URI）、`context.jamming_level` |
| 输出 | `status`、`role`、`message`、`intelligence_packet_id`、`target_count`；SSE 末条含完整 `intelligence_packet` |

**未改动的主项目模块**（避免合并冲突）：`commander_agent/`、`workflow_state_store.py`、`a2a_protocol/`（仅 import）、`registry/`（仅调用）。

**待师兄合并后接入**：BPEL 中增加 `recon → tactical_intelligence → artillery`；`commander_agent/main.py` 增加 role 映射。当前 PR 已可独立启动并完成协议验收。

---

# 四、最小 Demo / 运行命令

## 4.1 协议验收 Demo（HTTP，推荐合并验证）

验证「Agent 能被 Commander 发现和调用」的完整链路，共 7 步（agent-card → 鉴权 → sendMessage → SSE → 幂等）。

**终端 1 — 启动 Agent**

说明：`TIA_NACOS_REGISTER=0` 跳过 Nacos，仅测 HTTP；`TIA_ALLOW_INLINE_FRAMES=1` 允许 demo 附带真实 bus.jpg 视觉帧。

```powershell
cd D:\a2a_project\A2A-main
$env:PYTHONPATH = "."
$env:TIA_CONFIG = "config\default.yaml"
$env:TIA_ALLOW_INLINE_FRAMES = "1"
$env:TIA_NACOS_REGISTER = "0"
$env:TIA_PORT = "8016"
.\.venv\Scripts\python.exe tactical_intelligence_agent\main.py
```

**终端 2 — 七步验收**

```powershell
$env:PYTHONPATH = "."
$env:TIA_PORT = "8016"
.\.venv\Scripts\python.exe scripts\demo_tactical_intelligence_acceptance.py --pause
```

加 `--pause` 每步暂停，便于截图/录屏提交 PR。

## 4.2 业务仿真 Demo（无需 HTTP）

说明：不经过 Commander，直接用铁谷红蓝场景跑四阶段仿真，验证三技能流水线与输出 JSON。适合展示「业务产出」。

```powershell
$env:PYTHONPATH = "."
$env:TIA_CONFIG = "config\default.yaml"
.\.venv\Scripts\python.exe scripts\run_simulation.py
```

输出：`data/output/campaign/OP-IRON-VALLEY-2026-<时间>/`（含 intelligence/、downstream/latest_for_agents.json）

## 4.3 仅导出态势（不跑推理）

```powershell
$env:PYTHONPATH = "."
.\.venv\Scripts\python.exe scripts\build_situation.py
```

## 4.4 环境变量

| 变量 | 默认 | 说明 |
|------|------|------|
| `TIA_CONFIG` | `config/default.yaml` | 推理配置 |
| `TIA_PORT` | `8015` | HTTP 端口 |
| `TIA_NACOS_REGISTER` | `1` | `0` 跳过 Nacos |
| `TIA_ALLOW_INLINE_FRAMES` | `0` | HTTP demo 附真实图像时设 `1` |

---

## 合并验证清单（师兄可直接勾选）

- [ ] `tactical_intelligence_agent/main.py` 能启动，Uvicorn 监听正常
- [ ] `GET /.well-known/agent-card` 返回 `role: tactical_intelligence`
- [ ] 无 JWT → 401；有 JWT → sendMessage 200
- [ ] 响应含 `work_item`、`status`、`role`、`message`
- [ ] sendMessageStream 返回 4 条 SSE，末条 `Completed` + `intelligence_packet`
- [ ] 同一 `work_item` 重复调用结果一致
- [ ] `test_tactical_intelligence_agent.py` 通过
- [ ] `python -m unittest discover -s tests` 原有测试仍通过
