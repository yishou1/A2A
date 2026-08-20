# TIA 适配 lzh「小模型动态调用算法库」方案与验证

**对照分支：** [yishou1/A2A@lzh](https://github.com/yishou1/A2A/tree/lzh)  
**对照模块：** `decision_agents/common/algolib_runtime.py`、`llm/client.py`、`algolib_client.py`  
**本地对象：** 战术情报 Agent（TIA）+ `agent/algorithm_library/`（`POST /predict`）

---

## 一、lzh 分支：Agent 如何用小模型动态调算法库

### 1.1 核心链路（决策 Agent）

```text
用户输入 / AgentRequest
        │
        ▼
┌───────────────────────────────┐
│ ENABLE_LLM=true               │
│ OpenAICompatibleClient        │  ← TOOL_LLM_URL / TOOL_LLM_NAME（小模型）
│ chat_json(system, user)       │
└───────────────────────────────┘
        │ 返回 JSON plan
        │ {
        │   "intent": "...",
        │   "algorithm_calls": [{
        │      "algorithm_id", "version", "backend_type",
        │      "inputs", "params", "reason"
        │   }],
        │   "missing_fields": [],
        │   "explanation": "..."
        │ }
        ▼
┌───────────────────────────────┐
│ AlgorithmLibraryClient        │
│ GET  /algorithms              │  ← 拉活跃算法目录（约 8088）
│ POST /run                     │  ← 按 plan 执行选中算法
└───────────────────────────────┘
        │
        ▼
AgentResponse（含 selected_algorithms、llm_plan、algolib outputs）
```

关键文件：

| 文件 | 职责 |
|------|------|
| `decision_agents/common/algolib_runtime.py` | `run_agent_with_algolib`：选算法 + 调 `/run` |
| `decision_agents/common/algolib_client.py` | `list_algorithms` / `run_algorithm` |
| `decision_agents/common/llm_enhancer.py` | NL→Request、结果解释（可选） |
| `llm/client.py` | OpenAI 兼容小模型客户端（`chat_json`） |
| `decision_agents/*/prompts.py` | `ALGOLIB_SYSTEM_PROMPT` + catalog 拼装 |

### 1.2 小模型在链路里具体做什么

1. **拉目录**：`GET {ALGOLIB_BASE_URL}/algorithms`  
2. **缩请求视图**：`_llm_request_view` 裁剪大字段（如历史只留末步），控制 token  
3. **缩目录**：`_llm_algorithm_catalog` 只保留本 Agent **白名单**算法  
4. **JSON 规划**：小模型只输出「调哪个算法 + 原因」，**不直接产出业务结论**  
5. **强制用真实输入**：无论 LLM 写了什么 `inputs`，运行时用完整 `AgentRequest.model_dump()` **覆盖**（防幻觉改输入）  
6. **校验**：`algorithm_id`∈白名单、version/backend 与活跃卡一致  
7. **执行**：`POST /run`；失败则 `ALGORITHM_RUNTIME_ERROR` / `LLM_PROVIDER_ERROR`

关闭 LLM 时（`ENABLE_LLM=false`）：走 `AGENT_DEFAULT_ALGORITHMS` 默认算法，仍调 `/run`。

### 1.3 环境开关（lzh）

| 变量 | 含义 |
|------|------|
| `ENABLE_LLM` | 是否用小模型做算法选择 / NL 解析 |
| `TOOL_LLM_URL` / `TOOL_LLM_NAME` | 小模型 OpenAI 兼容端点与模型名 |
| `DECISION_AGENT_BACKEND=algolib` | 走算法库而非本地纯函数 |
| `ALGOLIB_BASE_URL` | 默认 `http://127.0.0.1:8088` |
| `TOOL_LLM_ALLOWED_MODELS` | 可选模型白名单 |

### 1.4 与 TIA 现状的差异

| 维度 | lzh 决策 Agent | 本地 TIA |
|------|----------------|----------|
| 算法入口 | 集中式 `GET /algorithms` + `POST /run` | 分散式 `POST /predict`（9020–9030） |
| 编排方式 | **小模型动态选** 1 个算法 | **固定流水线**（检测→毁伤→EDL→跟踪→调度→…） |
| 算法选择 | LLM plan + 白名单校验 | `factory.py` 按技能硬编码 |
| 小模型角色 | Tool-calling / 选算法 | 未接入（感知链不依赖 LLM） |
| 关闭 LLM | 默认算法 ID | `use_mock` / `in_process` / 固定后端 |

结论：lzh 的「小模型」是 **算法路由器（planner）**；TIA 当前是 **确定性技能管线**。适配时不应把整条感知链改成「每次 LLM 随便挑算法」，而应：**在可配置边界内，用小模型决定本轮启用哪些算法 / 跳过哪些 / 选哪套变体**，执行仍走现有 `/predict`。

---

## 二、本地 TIA 适配方法

### 2.1 适配原则

1. **保留**三技能固定顺序骨架（感知→认知→通信），保证情报包契约稳定。  
2. **新增**可选「算法计划层」：小模型输出本轮 `algorithm_calls[]`（有序）。  
3. **执行**仍用现有 `AlgorithmLibraryClient.predict()`（或未来统一网关 `/run`）。  
4. **安全**：白名单 + 必选算法 + 输入由编排器注入，LLM 不可改传感器原始帧。  
5. **可关**：`ENABLE_LLM=false` 时退回今天的固定管线（默认行为不变）。

### 2.2 推荐架构

```text
Commander sendMessage
        │
        ▼
TIA service / orchestrator
        │
        ├─ (可选) ToolLLMPlanner.plan(batch, catalog)
        │         → { algorithm_calls: [ {algorithm_id, params, reason}, ... ] }
        │
        ├─ PipelineExecutor.run(plan)
        │         → 按 plan 顺序 RemoteBackend.predict / 跳过未选中步骤
        │
        └─ CommunicationSkill → intelligence_packet
                  （tracks / targets / provenance.llm_plan）
```

### 2.3 建议新增模块（放本地仓库）

```text
tactical_intelligence_agent/
  llm/
    client.py              # 可从 lzh llm/client.py 移植 OpenAICompatibleClient
    planner.py             # 对应 algolib_runtime._llm_plan / _select_algorithm_call
    prompts.py             # TIA 专用 ALGOLIB / 管线计划 prompt
  algorithm_catalog.py     # 把 endpoints.py 11 算法写成「目录卡片」给 LLM
```

或更贴近 lzh 命名：

```text
agent/algorithm_library/
  catalog.py               # list 本地 11 卡 / 可选聚合 GET /algorithms
  planner_runtime.py       # plan + validate + execute predict
```

### 2.4 TIA 算法白名单与默认管线

与 lzh 的 `AGENT_ALLOWED_ALGORITHMS` 对应，建议：

```python
TIA_ALLOWED_ALGORITHMS = {
    "battlefield_rtdetr_detector",
    "siamese_mask2former_damage",
    "edl_evidential_verifier",
    "motr_neural_kalman_tracker",
    "marl_ppo_task_scheduler",
    "imagebind_multimodal_encoder",
    "multimodal_mamba_fusion",
    "supcon_meta_classifier",
    "synapse_rag_retriever",
    "knowledge_semantic_comm",
    "marl_dynamic_router",
}

# 关闭 LLM 时的默认有序管线（与现 skill 一致）
TIA_DEFAULT_PIPELINE = [
    "battlefield_rtdetr_detector",
    "siamese_mask2former_damage",
    "edl_evidential_verifier",
    "motr_neural_kalman_tracker",
    "marl_ppo_task_scheduler",
    "imagebind_multimodal_encoder",
    "multimodal_mamba_fusion",
    "supcon_meta_classifier",
    "synapse_rag_retriever",
    "knowledge_semantic_comm",
    "marl_dynamic_router",
]

# 不可被 LLM 跳过的核心步骤（保证 packet 有 tracks）
TIA_REQUIRED_ALGORITHMS = {
    "battlefield_rtdetr_detector",
    "edl_evidential_verifier",
    "motr_neural_kalman_tracker",
}
```

小模型允许的动态范围示例：

- 跳过毁伤评估（无参考帧时）  
- 跳过 RAG（无 knowledge_base）  
- 选择是否跑语义压缩 / 路由  
- `params`：置信度门限、`horizons`、scheduler 优先级等  

不允许：编造检测框、改写 attachment URI、输出不在白名单的 `algorithm_id`。

### 2.5 Prompt 契约（对齐 lzh JSON 形）

小模型返回：

```json
{
  "intent": "eo_recon_tracking",
  "algorithm_calls": [
    {
      "algorithm_id": "battlefield_rtdetr_detector",
      "version": "1.0.0",
      "backend_type": "python_http_service",
      "params": { "confidence_threshold": 0.25 },
      "reason": "有 EO 附件，需要检测"
    },
    {
      "algorithm_id": "motr_neural_kalman_tracker",
      "version": "1.0.0",
      "backend_type": "python_http_service",
      "params": {},
      "reason": "需要跨帧航迹供下游预测"
    }
  ],
  "missing_fields": [],
  "explanation": "本轮以检测+跟踪为主，跳过毁伤与 RAG"
}
```

注意：与 lzh 一样，**执行时的 `inputs` 由编排器根据当前帧 / 上一步 outputs 组装**，不要信任 LLM 填的 `inputs`。

### 2.6 与 `/run` vs `/predict` 的两种落地

| 方案 | 做法 | 适用 |
|------|------|------|
| **A. 最小改动（推荐先做）** | Planner 只产出有序 `algorithm_id` 列表；执行仍 `POST :port/predict` | 现有 11 服务已就绪 |
| **B. 完全对齐 lzh** | 前面加聚合网关 `GET /algorithms` + `POST /run`，内部再转各 `/predict` | 要与决策 Agent 共用同一 algolib |

建议：**先 A，再视需要做 B**。方案 B 可把 `ALGOLIB_BASE_URL` 指到统一网关，TIA 与决策 Agent 共用同一套「小模型选算法」运行时。

### 2.7 配置项（建议写入 `config/default.yaml` + 环境变量）

```yaml
tool_llm:
  enable: false                 # 对应 ENABLE_LLM
  url: ""                       # TOOL_LLM_URL
  name: ""                      # TOOL_LLM_NAME
  timeout_seconds: 30
  temperature: 0
  json_mode: false
  strip_thinking: true
  allowed_models: []

algorithm_planner:
  mode: fixed                   # fixed | llm
  required_algorithms: [...]
  allow_skip: true
```

环境变量对齐 lzh 命名，便于共用部署：

```powershell
$env:ENABLE_LLM="true"
$env:TOOL_LLM_URL="http://127.0.0.1:8000/v1"
$env:TOOL_LLM_NAME="qwen2.5-7b-instruct"
$env:TIA_ALGORITHM_PLANNER="llm"   # fixed | llm
```

### 2.8 接入 orchestrator 的改动点

在 `agent/orchestrator.py` 的 `process()` 中：

```text
1. prepare_batch_for_inference(batch)
2. plan = planner.plan(batch)          # fixed 或 llm
3. perception / cognition / communication
   - 各 skill 根据 plan 决定是否调用对应 RemoteBackend
   - 或抽一层 PipelineExecutor 按 algorithm_calls 顺序执行
4. packet.provenance["llm_plan"] = plan
5. 照常写出 tracks / targets / consumer_guide
```

`payload_adapter`、Commander 外壳、`intelligence_packet` 契约**不必改**；只是 `provenance` 多记一笔 `llm_plan`。

### 2.9 实施分期

| 阶段 | 内容 | 产出 |
|------|------|------|
| P0 | 移植 `OpenAICompatibleClient` + FakeLLM 单测 | 可测 JSON 计划 |
| P1 | `fixed`/`llm` Planner + 白名单校验 + 必选算法 | 关 LLM 行为与现网一致 |
| P2 | orchestrator 按 plan 跳过可选算法 | 真实 EO 联调 |
| P3（可选） | 聚合 `/algorithms`+`/run` 网关 | 与 lzh 决策侧完全同构 |

---

## 三、验证方法

### 3.1 单元测试（不依赖真 LLM / 真 GPU）

仿 lzh `tests/test_decision_agents_algolib_runtime.py`：

| 用例 | 断言 |
|------|------|
| `test_fixed_planner_default_pipeline` | `ENABLE_LLM=false` → 完整默认 11 步顺序 |
| `test_llm_planner_selects_whitelist` | FakeLLM 返回合法 `algorithm_calls` → 校验通过 |
| `test_llm_rejects_unknown_algorithm` | 计划含白名单外 ID → `AlgorithmLibraryError` / `input_required` |
| `test_llm_cannot_skip_required` | 跳过 detector/tracker → 自动补回或报错 |
| `test_execution_inputs_not_from_llm` | 执行时 `inputs` 来自 batch/上一步，不来自 LLM 伪造字段 |
| `test_packet_still_has_tracks` | 最小 plan（detect+edl+motr）仍产出 `tracks`+`history_path` |

建议文件：`tests/test_tia_algorithm_planner.py`

### 3.2 Mock 集成测试（FakeLLM + FakePredict）

```text
1. patch OpenAICompatibleClient.chat_json → 固定 plan（跳过 damage + rag）
2. patch AlgorithmLibraryClient.predict → 按 algorithm_id 返回桩 outputs
3. 调 TacticalIntelligenceAgent.process(真实结构 SensorBatch)
4. 断言：
   - 只调用了 plan 中的算法
   - packet.provenance["llm_plan"] 存在
   - packet.schema_version / tracks / consumer_guide 仍在
```

### 3.3 真小模型烟测（有 TOOL_LLM）

```powershell
$env:ENABLE_LLM="true"
$env:TOOL_LLM_URL="http://<your-llm>/v1"
$env:TOOL_LLM_NAME="qwen2.5-7b-instruct"
$env:TIA_ALGORITHM_PLANNER="llm"
$env:TIA_EXECUTION_MODE="algorithm_library"   # 或 in_process
$env:TIA_ALLOW_LOCAL_FILE="1"
$env:TIA_COMPUTE_PROFILE="medium"

# 先起算法服务（若走 algorithm_library）
# .\scripts\start_a2a_algorithm_services.ps1 -TiaOnly

.\.venv\Scripts\python.exe scripts/smoke_tia_llm_planner.py
```

`smoke_tia_llm_planner.py` 建议打印：

1. LLM 原始 plan JSON  
2. 校验后的 `algorithm_calls`  
3. 实际调用的 algorithm_id 列表  
4. `intelligence_packet.tracks` 数量与 `history_path` 长度  

### 3.4 对照 lzh 行为的验收清单

| 检查项 | 通过标准 |
|--------|----------|
| LLM 关 | 与当前固定管线结果结构一致（字段级） |
| LLM 开 + Fake | 只执行白名单算法；inputs 不被 LLM 篡改 |
| 未知算法 | 失败码清晰，不静默跑偏 |
| 缺必选算法 | 拒绝或自动补齐后仍有 tracks |
| 下游契约 | `output.intelligence_packet` 仍含 schema/tracks/targets/consumer_guide |
| 超时 | LLM 超时 → 可配置降级到 `fixed` 或返回 `LLM_PROVIDER_ERROR` |

### 3.5 与现有 smoke 衔接

已有：`scripts/smoke_tia_packet_tracks.py`（验证 packet / history）。  
LLM 适配完成后：

1. 先跑原 smoke（`TIA_ALGORITHM_PLANNER=fixed`）确认回归  
2. 再跑 LLM smoke（`=llm`）对比 `provenance.llm_plan` 与调用次数  

---

## 四、一句话对照

| | lzh | 本地 TIA 适配后 |
|--|-----|-----------------|
| 小模型干什么 | 从目录里选 **1 个**决策算法并组 `/run` | 从 11 卡目录里规划 **本轮有序算法子集** 并组 `/predict` |
| 算法库 | 集中 `8088/run` | 先分散 `902x/predict`，可选再聚合 |
| 默认无 LLM | 默认 `*_core` 算法 | 默认完整感知→认知→通信管线 |
| 输出 | `AgentResponse` | 仍是 `intelligence_packet`（多 `llm_plan` 溯源） |

---

## 五、相关代码索引（lzh）

```text
decision_agents/common/algolib_runtime.py   # 小模型选算法 + /run
decision_agents/common/algolib_client.py    # GET /algorithms, POST /run
decision_agents/common/llm_enhancer.py      # NL 解析 / 解释
decision_agents/*/prompts.py                # ALGOLIB_SYSTEM_PROMPT
llm/client.py                               # OpenAICompatibleClient.chat_json
tests/test_decision_agents_algolib_runtime.py
```

本地对照：

```text
agent/algorithm_library/client.py           # POST /predict
agent/algorithm_library/endpoints.py        # 11 算法端口
agent/orchestrator.py                       # 固定管线入口
tactical_intelligence_agent/service.py      # Commander 外壳
docs/TIA本周接入主体框架与IO外壳统一说明.md
```

---

## 六、落地状态（已实现）

### 6.1 小模型规划（P0–P2）

| 模块 | 路径 |
|------|------|
| LLM 客户端 | `tactical_intelligence_agent/llm/client.py` |
| Prompt | `tactical_intelligence_agent/llm/prompts.py` |
| 算法目录 | `agent/algorithm_library/catalog.py` |
| 规划运行时 | `agent/algorithm_library/planner_runtime.py` |
| 编排接入 | `agent/orchestrator.py`（`provenance.llm_plan`） |
| 技能跳过 | `agent/skills/{perception,cognition,communication}/skill.py` |

### 6.2 集中式调用（P3，对齐 lzh）

| 模块 | 路径 |
|------|------|
| 网关 | `services/tia_algolib_gateway/app/main.py`（`GET /algorithms` + `POST /run`） |
| 客户端 | `agent/algorithm_library/client.py`（`list_algorithms` / `run_algorithm` / `predict→/run`） |
| 远程后端 | `agent/algorithm_library/remote_backend.py`（默认走 `/run`） |
| 启动 | `scripts/start_tia_algolib_gateway.ps1`；`start_a2a_algorithm_services.ps1` 会顺带拉起 |

调用链（与 lzh 同构）：

```text
TIA Planner ──GET /algorithms──► Gateway:8088
TIA Skill   ──POST /run────────► Gateway:8088 ──► 902x/predict
```

配置（`config/default.yaml`）：

```yaml
algorithm_library:
  call_mode: run
  base_url: http://127.0.0.1:8088
```

环境变量：`ALGOLIB_BASE_URL`、`TIA_ALGOLIB_CALL_MODE=run|predict`、`TIA_ALGORITHM_PLANNER=llm`、`ENABLE_LLM`。

验证：

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s tests -p "test_tia_algorithm_planner.py" -v
.\.venv\Scripts\python.exe -m unittest discover -s tests -p "test_tia_algolib_run_client.py" -v
$env:TIA_LLM_SMOKE_FAKE="1"; .\.venv\Scripts\python.exe scripts/smoke_tia_llm_planner.py
```

---

## 七、运维启动顺序

```powershell
# 1) 各算法 /predict（9020–9030）+ 可选网关
.\scripts\start_a2a_algorithm_services.ps1 -TiaOnly

# 或单独起网关
.\scripts\start_tia_algolib_gateway.ps1

# 2) TIA Agent
$env:ALGOLIB_BASE_URL="http://127.0.0.1:8088"
$env:TIA_ALGOLIB_CALL_MODE="run"
$env:TIA_ALGORITHM_PLANNER="llm"   # 或 fixed
$env:ENABLE_LLM="true"             # llm 模式需要
python -m tactical_intelligence_agent.main
```
