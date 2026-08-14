# TIA 与任务调度 Agent：小模型动态调用算法库（分体架构说明）

> **现状前提（以本地仓库为准）：**  
> 任务调度已从战术情报管线中拆出，成为独立的 **任务调度智能体**（`task_scheduling_agent`）。  
> **战术情报智能体（TIA）** 只负责感知 → 认知 → 通信，**不再**调用 `marl_ppo_task_scheduler`。  
> 两个智能体各自用小模型做「算法路由」，再通过同一套算法库网关真正执行算法。  
> 设计对齐 [lzh](https://github.com/yishou1/A2A/tree/lzh) 的「小模型选算法 + `/algorithms` + `/run`」范式。

---

## 1. 用文字先说清楚整套框架

### 1.1 现在有两个并列的智能体，而不是一条大管线

本地实现里，业务侧可以看成两条独立业务链，外加一层共享的算法服务底座：

第一，**战术情报智能体（TIA）**。它接收传感器批次（图像帧、上下文等），在内部按「感知 → 认知 → 通信」三技能推进，产出语义情报包（检测、轨迹、分类、摘要、路由等）。它关心的算法是检测、毁伤、EDL、跟踪、多模态、分类、RAG、语义压缩、抗干扰路由等；**明确不包含任务调度算法**。

第二，**任务调度智能体（Task Scheduling Agent）**。它接收 AMOS 风格态势 JSON（任务、平台电量/弹药/链路、时间窗等），产出传感器任务分配与再攻击计划。它关心的算法目前白名单里主要是 **`marl_ppo_task_scheduler`**，与 TIA 进程、TIA 三技能管线没有硬编码耦合。

第三，**共享算法库底座**。两边都不在 Agent 进程里直接加载大模型做业务推理（默认模式），而是访问统一的算法库网关（默认 `http://127.0.0.1:8088`）：先用 `GET /algorithms` 看当前活跃算法，再用 `POST /run` 让网关转发到各算法 HTTP 服务（如 `:9020` 检测、`:9024` 调度、`:9030` 路由）。小模型、Agent、网关、算法服务四层职责分离。

因此，当前框架的文字表述应是：

> **两个智能体分属不同业务；各自可选地请小模型从「本 Agent 允许的算法目录」里挑选本轮要跑的算法；真正计算一律经算法库网关执行。调度算法只属于任务调度智能体，TIA 规划时禁止点名调度算法。**

### 1.2 小模型在两边各自扮演什么角色

小模型（Tool LLM，如本机 Ollama）**不是**检测器，也**不是**调度器。它只做一件事：读「精简任务摘要 + 活跃算法目录」，输出一份 JSON 计划 `algorithm_calls[]`（选哪些 `algorithm_id`、可选 `params`、简短 reason）。

- 对 **TIA**：小模型回答「这一轮情报流水线里，哪些感知/认知/通信算法要开、哪些可跳过」。  
- 对 **任务调度智能体**：小模型回答「是否选用（以及如何配置）`marl_ppo_task_scheduler`」——在当前白名单下通常就是选中该调度算法。  

两端运行时都会：**校验白名单与版本、补齐必选、强制用真实业务数据覆盖 LLM 填写的 inputs**，避免幻觉进入情报包或调度表。

### 1.3 关闭小模型时各自怎么退化

- **TIA**：`algorithm_planner.mode=fixed` 时，直接跑固定默认管线（感知→认知→通信全量或配置约定集合），不访问小模型。  
- **任务调度**：不启用 LLM 规划时，默认仍调用 `marl_ppo_task_scheduler`（经 algolib 或本地 mock/权重，取决于 `--algolib` / backend）。  

也就是说：小模型是「可插拔路由器」；关掉后两个智能体仍可独立工作。

---

## 2. 分体架构总览（文字 + 结构图）

### 2.1 文字结构

可以把系统分成三层来记：

**业务智能体层（并列、互不吞并）**

- `tactical_intelligence_agent`：情报生产。入口常是 Commander 的 A2A `/sendMessage`，或本地编排器。  
- `task_scheduling_agent`：资源与任务分配。入口是 AMOS JSON 文件/接口，CLI 为 `python -m task_scheduling_agent`。  

两者可以先后被上层工作流调用（例如先情报后调度），但**代码路径已分开**：TIA 感知技能里写明调度已委托给独立 Agent，不再内嵌 MARL-PPO 步骤。

**规划与执行层（每边一套，模式相同）**

- TIA：`plan_algorithms`（`planner_runtime.py`）→ 三技能按 `plan.is_enabled` 执行 → `RemoteAlgorithmBackend`。  
- 调度：`run_with_algolib`（`algolib_runtime.py`）→ 选中后一次（或按计划）`/run`。  

**算法服务层（共享）**

- 网关 `tia_algolib_gateway`：目录与转发。  
- 各 `python_http_service`：9020–9030（调度 9024 主要给调度智能体用；TIA 用其余情报类端口）。  

### 2.2 结构图（按「两个智能体」画）

```text
                    ┌─────────────────────────────────────┐
                    │  小模型（可选，OpenAI 兼容）            │
                    │  ENABLE_LLM + TOOL_LLM_URL/NAME       │
                    └───────────────┬─────────────────────┘
                                    │ chat_json 只出 algorithm_calls
              ┌─────────────────────┴─────────────────────┐
              │                                           │
              ▼                                           ▼
┌─────────────────────────────┐           ┌─────────────────────────────┐
│ 智能体 A：战术情报 TIA         │           │ 智能体 B：任务调度             │
│ tactical_intelligence_agent │           │ task_scheduling_agent       │
│                             │           │                             │
│ 输入：传感器批次 SensorBatch  │           │ 输入：AMOS 态势 JSON           │
│ 规划：planner_runtime        │           │ 规划：algolib_runtime         │
│ 执行：感知→认知→通信           │           │ 执行：调度算法 /run             │
│ 输出：intelligence_packet    │           │ 输出：sensor/reattack 计划     │
│ 白名单：情报类算法（无调度）    │           │ 白名单：marl_ppo_task_scheduler│
└──────────────┬──────────────┘           └──────────────┬──────────────┘
               │  GET /algorithms                         │
               │  POST /run                               │
               └──────────────────┬───────────────────────┘
                                  ▼
                    ┌─────────────────────────────────────┐
                    │ 算法库网关 :8088                       │
                    │ tia_algolib_gateway                   │
                    └──────────────────┬──────────────────┘
                                       │ 转发 /predict
         ┌──────────────┬──────────────┼──────────────┬──────────────┐
         ▼              ▼              ▼              ▼              ▼
      :9020 检测     :9021 毁伤     :9023 跟踪     :9024 调度*    :9029/:9030 …
                                                   *主要服务调度智能体
```

说明：网关可以把调度算法也挂在 `/algorithms` 里供调度智能体发现；但 **TIA 的规划白名单刻意排除调度算法**，避免情报 Agent 再次「顺手做调度」。

---

## 3. 公共机制：小模型 → 目录 → 执行（两边一样）

无论 TIA 还是任务调度，文字上都可以拆成四步：

1. **取目录**：向算法库问「现在激活了哪些算法」（`GET /algorithms`），拿不到则用本地静态卡片兜底。  
2. **缩视图**：把任务上下文压成摘要（无原图、无大字段），交给小模型，控制 token。  
3. **要计划**：小模型只返回 JSON 计划；Agent 校验 `algorithm_id`、version、backend，并按本 Agent 规则补齐必选、排序。  
4. **真执行**：用业务侧真实输入调用 `POST /run`；**丢弃或覆盖** LLM 在 `inputs` 里乱写的内容。  

共享组件包括：

| 组件 | 作用 |
|------|------|
| `OpenAICompatibleClient` / `ToolLLMSettings` | 小模型 HTTP 对话，`chat_json` |
| `AlgorithmLibraryClient` | `list_algorithms` + `predict`/`run_algorithm` |
| `tia_algolib_gateway` | 集中 ` /algorithms` 与 `/run` |
| `config/default.yaml` 中 `tool_llm`、`algorithm_library` | 开关与地址 |

配置示例：

```yaml
tool_llm:
  enable: false          # 或 ENABLE_LLM=true
  url: ""                # TOOL_LLM_URL
  name: ""               # TOOL_LLM_NAME

algorithm_planner:
  mode: fixed            # TIA：fixed | llm
  fallback_to_fixed: true

algorithm_library:
  enabled: true
  call_mode: run
  base_url: http://127.0.0.1:8088
```

---

## 4. 智能体 A：战术情报 TIA（不含调度）

### 4.1 文字流程

TIA 收到一批传感器数据后，先做一次算法规划，再跑三技能。规划结果是一份「本轮启用算法集合」。感知、认知、通信在执行每个子算法前问：这个 `algorithm_id` 在不在计划里？在则经远程后端打算法库；不在则记 skip，并用降级逻辑保证下游仍能勉强前进（例如跳过毁伤则检测框不带 damage_score）。

整轮结束后，情报包的 `provenance` 里带上 `llm_plan` 与 `selected_algorithms`，便于追溯「小模型点了哪些菜、实际跑了哪些服务」。

### 4.2 规划入口代码

```python
# agent/orchestrator.py（节选）
plan = plan_algorithms(batch, config=self._config, llm_client=self._llm_client)

perception_out = self.perception.execute(batch, prior_tracks=prior, plan=plan)
cognition_out = self.cognition.execute(batch, perception_out, plan=plan)
packet = self.communication.execute(..., plan=plan)

packet.provenance = {
    **(packet.provenance or {}),
    "llm_plan": plan.to_dict(),
    "selected_algorithms": [c.algorithm_id for c in plan.algorithm_calls],
}
```

`plan_algorithms`（`planner_runtime.py`）行为用文字概括：

- `mode=fixed`：不调小模型，使用 `TIA_DEFAULT_PIPELINE`（**列表中已无** `marl_ppo_task_scheduler`）。  
- `mode=llm`：拉目录 → 过滤 TIA 白名单 → `chat_json` → 校验 → 注入必选（检测/EDL/跟踪）→ 按感知→认知→通信排序；失败可降级 fixed。  

Prompt 里写明：**禁止选择 `marl_ppo_task_scheduler`**，因为调度已是另一个智能体。

### 4.3 执行时如何「按计划开关」并打进算法库

```python
# agent/skills/perception/skill.py（节选）
def enabled(aid: str) -> bool:
    return plan is None or plan.is_enabled(aid)

if enabled("siamese_mask2former_damage"):
    damage_reports = self.damage.run({...})
else:
    trace[self.damage.name] = "skipped"
# 注释与实现一致：调度已委托 task_scheduling_agent，本技能不再跑 MARL-PPO
```

`execution_mode: algorithm_library` 时，工厂创建的是 `RemoteAlgorithmBackend`，内部：

```python
outputs = self._client.predict(self.algorithm_id, payload, params=params, ...)
# call_mode=run 时即 POST http://127.0.0.1:8088/run
```

### 4.4 TIA 端到端（文字）

Commander（或本地调用）把任务交给 TIA → TIA 用小模型或固定策略生成本轮算法计划 → 感知技能按计划调用检测/毁伤/EDL/跟踪等远程算法 → 认知、通信同理 → 汇总为情报包。  
**全程不会出现「在 TIA 内部再跑一遍任务调度算法」**；若上层还需要传感器分配与再攻击计划，应另一次调用任务调度智能体。

---

## 5. 智能体 B：任务调度（独立进程 / 独立入口）

### 5.1 文字流程

任务调度智能体不读传感器原图流水线，而读 AMOS JSON。开启 algolib 后端后：先问算法库有哪些活跃算法，筛出自己白名单里的调度算法；若启用了小模型，就让小模型在目录中确认选用 `marl_ppo_task_scheduler`（并可带 params）；然后将 AMOS 转成算法库认识的 inputs（tracks/detections/frames/batch_context，并附带原始 `amos_payload`），**强制写入**本次 `/run`，最后得到 `sensor_assignments`、`reattack_plan` 等，并附带 `llm_plan`。

未走 algolib 时，仍可本地 mock 启发式或本地加载 MARL-PPO 权重——那是调度智能体自己的「非算法库模式」，与 TIA 无关。

### 5.2 入口与分支

```text
python -m task_scheduling_agent --input amos.json [--algolib] [--llm]
```

```python
# engine.py 逻辑概要
if use_algolib_backend(cfg):
    return run_with_algolib(amos_payload, config=cfg)
# 否则本地 mock / 本地 MARL
```

### 5.3 `run_with_algolib` 五步（文字）

1. `GET /algorithms`（失败则用本地调度卡片兜底）。  
2. 只保留 `marl_ppo_task_scheduler`。  
3. `amos_to_algolib_inputs` 生成真实 inputs。  
4. 小模型开则 `chat_json` 选算法；否则默认调度算法。  
5. **覆盖 inputs** 后 `POST /run` → 网关 → `:9024/predict`。  

防幻觉：

```python
raw_call = {**raw_call, "inputs": real_inputs}  # LLM 写的 inputs 作废
```

### 5.4 与 TIA 的边界（必须分开理解）

| 问题 | 答案 |
|------|------|
| 调度还在不在 TIA 感知技能里？ | **不在。** 感知技能只委托说明，不执行 MARL-PPO。 |
| TIA 小模型能不能点名调度算法？ | **不能。** Prompt 与白名单双禁止。 |
| 调度智能体要不要跑检测/跟踪？ | **默认不要。** 白名单只有调度算法；态势来自 AMOS。 |
| 两者是否共用小模型服务？ | **可以共用同一 Ollama**，但是两次独立的 `chat_json`、两套 Prompt。 |
| 两者是否共用网关？ | **是**，同一 `:8088`；目录里调度算法主要给调度智能体用。 |

---

## 6. 对照表（按「两个智能体」整理）

| 维度 | 战术情报 TIA | 任务调度 Agent |
|------|--------------|----------------|
| 代码包 | `tactical_intelligence_agent/` + `agent/orchestrator.py` | `task_scheduling_agent/` |
| 业务输入 | 传感器批次 | AMOS JSON |
| 业务输出 | 语义情报包 | 传感器分配 + 再攻击计划 |
| 规划模块 | `planner_runtime.plan_algorithms` | `algolib_runtime.run_with_algolib` |
| Prompt | `tactical_intelligence_agent/llm/prompts.py` | `task_scheduling_agent/llm/prompts.py` |
| 算法白名单 | 情报管线算法（**排除调度**） | **仅** `marl_ppo_task_scheduler` |
| 执行形态 | 多步骤流水线，按 plan 跳过 | 以调度算法 `/run` 为主 |
| 与对方关系 | 不内嵌调度 | 不内嵌 TIA 三技能 |
| 关 LLM | 固定情报管线 | 默认仍调调度算法（algolib/本地） |

---

## 7. 算法库客户端（两边共用的「电话线」）

```python
# agent/algorithm_library/client.py 职责摘要
list_algorithms()  # 规划：给小模型看目录
run_algorithm()    # 执行：POST /run
predict()          # 统一入口：run 或直连 /predict
```

文字上记：

- 小模型**看不到**也**不需要**知道 `:9024` 这种端口；它只输出 `algorithm_id`。  
- Agent 拿着真实数据去打电话；网关按 id 转发。  
- 这样两个智能体才能安全地共用同一算法集群，又保持业务边界。

---

## 8. 环境变量与联调（分体视角）

| 开关 | 作用对象 | 含义 |
|------|----------|------|
| `ENABLE_LLM` + `TOOL_LLM_*` | 两边 | 小模型可达 |
| `TIA_ALGORITHM_PLANNER=llm` | 仅 TIA | TIA 用小模型选情报算法 |
| `--algolib` / `TASK_SCHEDULING_BACKEND=algolib` | 仅调度 | 调度走算法库而非纯本地 |
| `--llm` / `TASK_SCHEDULING_ALGORITHM_PLANNER=llm` | 仅调度 | 调度用小模型确认/选择调度算法 |
| `ALGOLIB_BASE_URL` | 两边 | 网关地址，默认 `:8088` |

推荐联调顺序（文字）：先起各算法 HTTP 与网关，再起小模型；然后**分别**验证 TIA 动态选情报算法、任务调度动态调用 `:9024`，不要假设「只启 TIA 就会自动出调度计划」。

```powershell
$env:TIA_USE_MOCK="1"
.\scripts\start_a2a_algorithm_services.ps1 -TiaOnly
.\scripts\start_tia_algolib_gateway.ps1

$env:ENABLE_LLM="true"
$env:TOOL_LLM_URL="http://127.0.0.1:11434/v1"
$env:TOOL_LLM_NAME="qwen2.5:7b"

# 智能体 A
$env:TIA_ALGORITHM_PLANNER="llm"
# 启动 tactical_intelligence_agent …

# 智能体 B（另一次调用）
.\.venv\Scripts\python.exe -m task_scheduling_agent `
  --input examples/amos_schedule_inputs/sample_amos_request.json `
  --algolib --llm
```

验收：TIA 回包有 `provenance.llm_plan` 且所选 id 不含调度；调度回包有 `selected_algorithms: ["marl_ppo_task_scheduler"]` 与分配结果；两边网关都能看到各自的 `/run`。

---

## 9. 设计原则（按分体架构重述）

1. **业务分体**：情报与调度是两个智能体，避免一个进程既出情报包又暗自改资源分配。  
2. **小模型只路由**：两边都禁止小模型直接写检测框或甘特式调度表。  
3. **目录 + 白名单**：同一网关，不同 Agent 看见/允许的算法集合不同。  
4. **真实输入由本 Agent 注入**：TIA 注入帧与中间结果；调度注入 AMOS 派生态势。  
5. **可关可降级**：无小模型时，TIA 固定情报管线，调度默认调度算法，系统仍可演示与联调。

---

## 10. 代码索引

| 说明 | 路径 |
|------|------|
| TIA 编排 | `agent/orchestrator.py` |
| TIA 规划 | `agent/algorithm_library/planner_runtime.py` |
| TIA Prompt（禁止调度算法） | `tactical_intelligence_agent/llm/prompts.py` |
| 感知技能（调度已委托） | `agent/skills/perception/skill.py` |
| 算法目录（TIA 排除调度 / 网关可含调度） | `agent/algorithm_library/catalog.py` |
| 算法库客户端 | `agent/algorithm_library/client.py` |
| 远程后端 | `agent/algorithm_library/remote_backend.py` |
| 任务调度包 | `task_scheduling_agent/` |
| 调度 algolib 运行时 | `task_scheduling_agent/algolib_runtime.py` |
| 调度 Prompt | `task_scheduling_agent/llm/prompts.py` |
| 网关 | `services/tia_algolib_gateway/app/main.py` |

---

## 11. 一句话总结（分体版）

当前本地架构是 **两个并列智能体共用算法库与可选小模型**：TIA 只规划并执行情报类算法、产出情报包；任务调度智能体独立消费 AMOS、只规划并执行调度算法、产出分配与再攻击计划。小模型在各自边界内做算法选择，真正计算一律走网关 `/run`，调度不再嵌在 TIA 流水线内部。
