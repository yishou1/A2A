# zh 分支相关 Agent 链路说明

本文档只说明和 `zh` 分支能力相关的部分。其他集成层能力，例如 AMOS 页面、Commander Gateway、基础启动脚本、Nacos、通用健康检查等，不在本文档展开。

当前本地运行分支是 `jzz/integrated`，它已经包含 `zh` 分支提交链；所以本文说的“zh 分支相关能力”，指的是从 `zh` 集成进当前 `jzz/integrated` 后仍在使用和本次继续修改的能力。

## 1. 范围

本文只覆盖这些 zh 相关模块：

| 中文模块 | English | 当前代码位置 | 来源/归属 |
|---|---|---|---|
| 战术情报 Agent | Tactical Intelligence Agent | `commander/local_runtime.py`, `commander/services/a2a_algorithms_common/tia_predictors.py` | `origin/cms/tactical-intelligence-agent` 后并入 `zh` |
| 跟踪与威胁 Agent | Track & Threat Agent | `commander/local_runtime.py`, `commander/services/a2a_algorithms_common/track_threat_algorithms.py` | `origin/wc/track-threat-agent` 后并入 `zh` |
| 任务调度 Agent | Task Scheduling Agent | `commander/task_scheduling_agent/`, `commander/local_runtime.py` | zh 链路相关能力 |
| 决策规划 Agent | Decision Planning Agent | `commander/decision_agents/decision_planning/` | `zh` |
| 合规授权 Agent | Compliance Authorization Agent | `commander/decision_agents/compliance_authorization/` | `zh` |
| 执行控制 Agent | Execution Control Agent | `commander/execution_control_agent/` | `zh` |
| 闭环优化 Agent | Closed-loop Optimization Agent | `commander/closed_loop_agent/` | `zh` |
| zh 链路标准映射层 | Standard Results Mapper | `commander/closed_loop_agent/agent_results_mapping.py` | 本次为 zh 链路重构 |
| Commander 输入组装 | Commander Payload Builder | `commander/commander_agent/main.py` | 本次为 zh 链路重构 |

不展开讲的部分：

- AMOS 前端和场景页面；
- Commander Gateway；
- Docker/Nacos/注册中心细节；
- legacy recon/artillery/assault/evaluator 的旧演示流程；
- 非 zh 链路必须的算法服务。

其中 `eval_score` 和 `execution_simulation_result` 会在七维特征里出现，但这里只把它们当作 zh 闭环所需的外部执行/评估证据，不展开讲对应 legacy Agent。

## 2. zh 链路现在怎么串起来

当前 zh 相关链路是：

```text
Tactical Intelligence Agent
  -> intelligence_packet

Track & Threat Agent
  -> tracking_result
  -> threat_assessment_result

Task Scheduling Agent
  -> task_scheduling_result
  -> scheduled_tasks / resources / planning_input

Decision Planning Agent
  -> decision_planning_result
  -> candidate_plans / recommended_plan

Compliance Authorization Agent
  -> compliance_authorization_result

Standard Results Mapper
  -> standard results

Execution Control Agent
  -> execution_control_result
  -> commands

Closed-loop Optimization Agent
  -> mission features
  -> mission completion
  -> closed-loop advice
```

这次修改后的关键变化是：

```text
下游不再直接假设自己必须要某些固定字段。
它会先看前置 zh Agent 已经产出了什么，
再由标准映射层转换成后续统一能读的 results。
```

所以现在核心中间层是：

```python
results = build_standard_results_from_context(context)
```

位置：

```text
commander/closed_loop_agent/agent_results_mapping.py
```

## 3. 每个 zh Agent 具体做什么

### 3.1 战术情报 Agent / Tactical Intelligence Agent

它负责把任务输入中的目标、证据和感知材料整理成战术情报包。

输入从哪里来：

| 输入字段 | 来源 | 说明 |
|---|---|---|
| `mission_input.contacts` | 任务输入 | 已知接触目标 |
| `mission_input.targets` | 任务输入 | 如果外部已经给了目标列表，则直接使用 |
| `mission_input.attachments` | 任务输入 | 图片、文档、证据附件 |
| `mission_input.evidence` | 任务输入 | 结构化证据 |
| `mission_input.perception_frames` | 任务输入 | 感知帧、多模态输入 |
| `mission_input.scene` | 任务输入 | 场景、区域、保护对象等 |

输出写到：

```text
context["intelligence_packet"]
```

典型输出：

```python
{
    "packet_id": "wf-001-tia",
    "schema_version": "intelligence_packet/v1",
    "mission_id": "wf-001",
    "scene": {...},
    "targets": [
        {
            "track_id": "T-1",
            "class": "surface_target",
            "geo": {"lat": 30.1, "lon": 114.1},
            "confidence": 0.91,
            "intent": "approach",
            "threat_score": 0.82,
            "metadata": {...}
        }
    ],
    "tracks": [...],
    "summary": "..."
}
```

它做了什么：

1. 从 `mission_input.contacts` 或 `mission_input.targets` 提取目标。
2. 给每个目标统一目标 ID，优先使用 `track_id/contact_id/target_id`。
3. 整理目标类型，例如 `class/classification/object_type`。
4. 保留位置 `geo`、置信度 `confidence`、意图 `intent`、威胁分数 `threat_score`。
5. 形成后续 Track Threat、Task Scheduling、Execution Control 可继续使用的目标列表。

可能用到的算法：

| 算法 ID | 做什么 |
|---|---|
| `battlefield_rtdetr_detector` | 从图像或感知帧中检测目标 |
| `edl_evidential_verifier` | 校验检测证据和不确定性 |
| `motr_neural_kalman_tracker` | 形成或更新目标轨迹 |
| `multimodal_mamba_fusion` | 融合多模态特征 |
| `supcon_meta_classifier` | 辅助目标分类和威胁识别 |

它给下游的关键字段：

| 字段 | 下游怎么用 |
|---|---|
| `targets[].track_id` | 后续所有目标关联主键 |
| `targets[].confidence` | 转为情报置信度 `intel_confidence` |
| `targets[].class` | 转为识别标签 |
| `targets[].threat_score` | 作为威胁压力候选值 |
| `targets[].geo` | 作为位置和展示辅助信息 |

### 3.2 跟踪与威胁 Agent / Track & Threat Agent

它负责基于情报目标继续做轨迹跟踪、风险评估和威胁排序。

输入从哪里来：

| 输入字段 | 来源 Agent | 说明 |
|---|---|---|
| `intelligence_packet.targets` | Tactical Intelligence Agent | 情报阶段目标列表 |
| `intelligence_packet.tracks` | Tactical Intelligence Agent | 情报阶段轨迹列表 |
| `tracking_result.tracks` | Track & Threat Agent 自身前一阶段 | 轨迹跟踪输出 |
| `mission_input.contacts` | 任务输入 | 目标补充信息 |

输出写到：

```text
context["tracking_result"]
context["threat_assessment_result"]
context["risk_assessments"]
context["target_histories"]
```

典型输出：

```python
{
    "schema_version": "threat_assessment_result/v1",
    "tracks": [
        {
            "track_id": "T-1",
            "object_type": "surface_target",
            "current_point": {...},
            "confidence": 0.91,
            "threat_score": 0.82
        }
    ],
    "risk_assessments": [
        {
            "target_id": "T-1",
            "priority": 1,
            "risk": "high",
            "threat_score": 82.0,
            "probability": 0.82,
            "rationale": "...",
            "triggered_rules": [...]
        }
    ],
    "unified_threat_ranking": [
        {
            "rank": 1,
            "item_id": "T-1",
            "score": 0.82,
            "level": "high"
        }
    ],
    "target_histories": [...]
}
```

它做了什么：

1. 将情报目标转换为可跟踪的 `tracks`。
2. 为每个目标生成风险评估 `risk_assessments`。
3. 给目标排序，形成 `unified_threat_ranking`。
4. 保留 `target_histories`，供任务调度和执行控制使用。

可能用到的算法：

| 算法 | 做什么 |
|---|---|
| `motr_neural_kalman_tracker` | 目标跟踪 |
| `supcon_meta_classifier` | 目标类型和威胁分类 |
| ST-GNN 轨迹模型 | 在独立 Track Threat 实现中用于时空轨迹预测 |
| linear fallback | 轨迹模型不可用时的线性预测补充 |

它给下游的关键字段：

| 字段 | 下游怎么用 |
|---|---|
| `risk_assessments[].target_id` | 执行控制命令目标 |
| `risk_assessments[].probability` | 威胁压力、命令优先级 |
| `risk_assessments[].priority` | 任务调度优先级 |
| `unified_threat_ranking[].score` | 闭环七维特征 `threat_pressure` |
| `target_histories` | 执行控制轨迹预测输入 |

### 3.3 任务调度 Agent / Task Scheduling Agent

它负责把威胁目标转成任务，并分配可用资源。

输入从哪里来：

| 输入字段 | 来源 Agent | 说明 |
|---|---|---|
| `threat_assessment_result.risk_assessments` | Track & Threat Agent | 哪些目标有风险、优先级是多少 |
| `threat_assessment_result.tracks` | Track & Threat Agent | 目标位置、类别和置信度 |
| `mission_input.friendly_platforms` | 任务输入 | 可用平台、传感器、打击资源 |
| `mission_input.environment.network` | 任务输入 | 网络退化、通信环境 |
| `planning_objectives` | Commander context | 调度目标 |

输出写到：

```text
context["task_scheduling_result"]
context["scheduled_tasks"]
context["resources"]
context["planning_input"]
```

典型输出：

```python
{
    "mission_id": "wf-001",
    "scheduled_tasks": [
        {
            "id": "TASK-001",
            "target_id": "T-1",
            "priority": 1,
            "task_type": "reattack",
            "required_resource_types": ["sensor", "strike"],
            "assigned_resources": ["SENSOR-1", "FIRE-1"]
        }
    ],
    "resources": [
        {
            "id": "FIRE-1",
            "type": "strike",
            "status": "available",
            "capacity": 1.0,
            "attributes": {...}
        }
    ],
    "risk_assessments": [...],
    "target_histories": [...],
    "planning_objectives": [...]
}
```

它做了什么：

1. 读取威胁评估中的目标和优先级。
2. 将每个高风险目标转换成任务。
3. 读取友方平台，生成资源列表 `resources`。
4. 根据传感器和打击资源情况生成 `assigned_resources`。
5. 保留 `risk_assessments` 和 `target_histories` 给后续模块继续用。

用到的算法：

| 算法 | 做什么 |
|---|---|
| `marl_ppo_task_scheduler` | 多智能体强化学习任务调度和资源分配 |
| local fallback scheduler | Algolib 不可用时，用本地启发式调度保证链路可解释地继续 |

它给下游的关键字段：

| 字段 | 下游怎么用 |
|---|---|
| `scheduled_tasks[].target_id` | 执行控制在没有轨迹预测时生成命令 |
| `scheduled_tasks[].priority` | 命令优先级 fallback |
| `scheduled_tasks[].required_resource_types` | 计算通信/协同覆盖质量 |
| `resources[].status` | 判断资源是否可用 |
| `resources[].capacity` | 计算资源就绪度 |
| `resources[].type` | 判断任务需求是否被覆盖 |

### 3.4 决策规划 Agent / Decision Planning Agent

它负责从任务和资源中生成候选计划，并推荐一个计划。

输入从哪里来：

| 输入字段 | 来源 Agent | 说明 |
|---|---|---|
| `scheduled_tasks` | Task Scheduling Agent | 已调度任务 |
| `resources` | Task Scheduling Agent | 可用资源 |
| `risk_assessments` | Track & Threat Agent / Task Scheduling Agent | 目标风险 |
| `planning_objectives` | Commander context | 任务目标 |
| `constraints` | Commander context | 约束 |
| `authorization` | Commander context | 操作授权信息 |

输出写到：

```text
context["decision_planning_result"]
context["candidate_plans"]
```

典型输出：

```python
{
    "candidate_plans": [
        {
            "id": "PLAN-001",
            "target_ids": ["T-1"],
            "assigned_resources": ["FIRE-1"],
            "actions": ["precision_strike"],
            "score": 0.86,
            "status": "candidate"
        }
    ],
    "recommended_plan_id": "PLAN-001",
    "recommended_plan": {...},
    "plan_scores": [...],
    "method": "template_generation_logistic_lstm_scoring"
}
```

它做了什么：

1. 基于 `scheduled_tasks` 和 `resources` 生成多个候选计划。
2. 对每个计划计算覆盖率、风险匹配、资源效率、约束适配。
3. 根据评分选择 `recommended_plan`。
4. 把候选计划交给合规授权 Agent 检查。

用到的算法：

| 算法 | 做什么 |
|---|---|
| template generation | 生成候选计划 |
| multi-factor scoring | 按覆盖率、风险匹配、资源效率、约束适配打分 |
| logistic scoring | 对计划成功倾向做逻辑回归式评分 |
| LSTM-like trend scoring | 根据目标历史趋势修正计划评分 |
| structured rule RAG adjustment | 用规则知识和检索证据调整计划 |
| ONNX optional | 可选 ONNX，失败回退 medium |

它给下游的关键字段：

| 字段 | 下游怎么用 |
|---|---|
| `recommended_plan.target_ids` | 执行控制生成命令的首选目标来源 |
| `recommended_plan.actions` | 执行控制命令动作来源之一 |
| `recommended_plan.assigned_resources` | 仿真和展示使用 |
| `candidate_plans` | 合规授权检查 |
| `recommended_plan_id` | 标记最终推荐计划 |

### 3.5 合规授权 Agent / Compliance Authorization Agent

它负责检查计划是否满足规则、授权和合规要求。

输入从哪里来：

| 输入字段 | 来源 Agent | 说明 |
|---|---|---|
| `candidate_plans` | Decision Planning Agent | 待检查的候选计划 |
| `authorization` | Commander context | 操作员授权或任务授权 |
| `constraints` | Commander context | 约束 |
| `planning_objectives` | Commander context | 任务目标 |

输出写到：

```text
context["compliance_authorization_result"]
context["authorization"]
context["compliance_decision"]
```

典型输出：

```python
{
    "decision": "approved",
    "approved_for_demo_handoff": True,
    "requires_human_approval": False,
    "risk_probability": 0.12,
    "compliance_probability": 0.88,
    "selected_plan_id": "PLAN-001",
    "per_plan_results": [...]
}
```

它做了什么：

1. 选择推荐计划或候选计划进行检查。
2. 用规则表和关键词判断是否存在阻断项。
3. 检索规则证据，形成可解释的合规依据。
4. 用 logistic 风险校准得到 `risk_probability` 和 `compliance_probability`。
5. 输出是否允许交给后续仿真执行。

用到的算法：

| 算法 | 做什么 |
|---|---|
| keyword authorization check | 检查计划动作和授权文本 |
| structured rule table | 结构化规则命中 |
| RAG evidence retrieval | 检索规则、授权、法则证据 |
| logistic risk calibration | 合规风险概率校准 |
| ONNX optional | 可选 ONNX，失败回退 |

它给下游的关键字段：

| 字段 | 下游怎么用 |
|---|---|
| `decision` | 执行控制判断是否阻断 |
| `approved_for_demo_handoff` | 是否允许进入仿真执行 |
| `requires_human_approval` | 是否需要人工确认 |
| `risk_probability` | 风险展示和解释 |
| `compliance_probability` | 合规可信度展示 |

### 3.6 执行控制 Agent / Execution Control Agent

它负责把前面 zh Agent 的结果转成可执行命令。

输入：

```python
{
    "phase": "strike" | "assault",
    "results": build_standard_results_from_context(context)
}
```

它读取的不是零散前置字段，而是标准 `results`：

| 标准字段 | 实际来源 |
|---|---|
| `perception_detection.detections` | Tactical Intelligence Agent 的 `intelligence_packet.targets` |
| `threat_evaluation.priority_score/ranked_targets` | Track & Threat Agent 的 `risk_assessments` / `unified_threat_ranking` |
| `resource_allocation.readiness/resources/scheduled_tasks` | Task Scheduling Agent |
| `communication.delivery_rate` | Task Scheduling Agent 的任务-资源覆盖情况 |
| `plan_decision.recommended_plan` | Decision Planning Agent |
| `compliance_authorization.decision` | Compliance Authorization Agent |
| `data_fusion.track_history` | Track & Threat Agent 或任务输入中的历史轨迹 |

它先构建态势：

```python
situation = {
    "phase": "strike",
    "threat_score": 0.82,
    "intel_confidence": 0.91,
    "resource_readiness": 1.0,
    "communication_quality": 1.0,
    "commander_decision": "ASSAULT"
}
```

然后做三步：

1. 关联规则匹配。

   ```text
   situation -> discretize_situation -> match_rules -> choose_primary_rule
   ```

   它会根据威胁、情报置信度、资源状态、通信质量、阶段，选择适合的执行规则。

2. 轨迹预测。

   如果 `data_fusion.track_history` 足够完整，就做线性预测：

   ```text
   x(t) = vx * t + bx
   y(t) = vy * t + by
   ```

   输出 `aim_point` 和 `execute_at`。

3. 计划兜底命令生成。

   这是本次修改重点。现在如果没有足够轨迹，执行控制不会直接断，而是继续看 zh 前置 Agent 是否已经给了计划或任务：

   ```text
   如果有 prediction_details:
       用轨迹预测生成命令
   否则如果有 decision_planning_result.recommended_plan.target_ids:
       用推荐计划生成命令，source=decision_plan
   否则如果有 task_scheduling_result.scheduled_tasks[].target_id:
       用调度任务生成命令，source=scheduled_task
   否则:
       返回 insufficient_data
   ```

命令字段具体来源：

| 命令字段 | 来源 |
|---|---|
| `target_id` | `recommended_plan.target_ids` 或 `scheduled_tasks[].target_id` |
| `action` | 规则 consequent，或 `recommended_plan.actions[0]`，或 `scheduled_tasks[].task_type` |
| `executor_role` | `phase=strike` 时默认 `artillery`，`phase=assault` 时默认 `assault`，规则可覆盖 |
| `priority` | 优先取 `risk_assessments[].probability` / `ranked_targets[].score`；没有则取任务 priority |
| `rule_id` | 关联规则匹配结果 |
| `source` | `decision_plan` 或 `scheduled_task` |

任务优先级解释也在本次修正：

```text
priority=1 -> 1.0
priority=2 -> 0.5
priority=3 -> 0.3333
```

这样 `priority=1` 会被理解成最高优先级，而不是百分比 `0.01`。

### 3.7 闭环优化 Agent / Closed-loop Optimization Agent

它负责在执行后做任务完成度评估、损伤评估和下一步建议。

输入：

```python
{
    "target_count": len(targets),
    "cycles": 3,
    "feature_mode": "strict",
    "results": build_standard_results_from_context(context),
    "targets": [...]
}
```

其中 `targets` 由 Commander 组装：

```text
优先从执行命令拿 target_id
  -> execution_simulation_result.commands[].target_id

再补目标信息
  -> mission_input.contacts
  -> intelligence_packet.targets

再补效果信息
  -> eval_score 或目标自带 damage_probability / xBD 特征
```

示例：

```python
{
    "target_id": "T-1",
    "target_class": "surface_target",
    "threat_score": 0.82,
    "detection_confidence": 0.91,
    "damage_probability": 0.70,
    "damage_probability_source": "evaluator.eval_score",
    "execution_command": {...}
}
```

它做了什么：

1. 用 `mission_feature_adapter` 把标准 `results` 和 `targets` 转成七维任务特征。
2. 用 `mission_completion_scorer` 计算任务完成度。
3. 对每个目标调用或使用 `xbd_damage_assessor` 估计损伤概率。
4. 用 `closed_loop_decision_advisor` 给每个目标生成下一步动作建议。
5. 输出 `requirement_report`，说明任务完成度、损伤准确率、执行证据是否满足要求。

用到的算法：

| 算法 ID | 做什么 |
|---|---|
| `mission_feature_adapter` | 标准结果转七维任务特征 |
| `mission_completion_scorer` | 七维特征转任务完成度 |
| `xbd_damage_assessor` | 目标损伤概率评估 |
| `closed_loop_decision_advisor` | 生成闭环动作建议 |

本地核心还包含：

| 算法 | 做什么 |
|---|---|
| ResNet18 ROI embeddings + logistic regression | 图像/区域损伤评估 |
| K-Means | 态势聚类 |
| SC2LE proxy random forest | 任务完成度代理模型 |
| rule-constrained receding-horizon control | 基于损伤、威胁、不确定性、任务完成度做滚动闭环控制 |

## 4. 标准映射层具体做什么

标准映射层是 zh 链路这次修改的核心胶水层。

文件：

```text
commander/closed_loop_agent/agent_results_mapping.py
```

它的目标是把多个 zh Agent 的输出整理为后置模块统一可读的结构：

```python
results = {
    "perception_detection": {...},
    "recognition": {...},
    "data_fusion": {...},
    "threat_evaluation": {...},
    "resource_allocation": {...},
    "communication": {...},
    "plan_decision": {...},
    "compliance_authorization": {...},
    "execution_control": {...}
}
```

### 4.1 感知检测 `perception_detection`

来源优先级：

1. `context["structured_detections"]`
2. Tactical Intelligence Agent 的 `intelligence_packet.targets`
3. `mission_input.contacts`

字段转换：

| 原字段 | 标准字段 |
|---|---|
| `track_id` / `target_id` / `contact_id` | `detections[].track_id` |
| `confidence` / `metadata.confidence` | `detections[].conf` |
| `class` / `classification` / `object_type` | `detections[].class_name` |

### 4.2 识别结果 `recognition`

来源：

```text
perception_detection.detections
```

做法：

```text
把 detection 列表同步放到 recognition.labels，兼容后续识别类消费者。
```

### 4.3 数据融合 `data_fusion`

来源优先级：

1. `context["structured_track_history"]`
2. 执行控制或仿真执行输出中的 `tracks`
3. `planning_input.target_histories`
4. Task Scheduling Agent 的 `target_histories`
5. `mission_input.contacts[].metadata.history_path`

字段转换：

| 原字段 | 标准字段 |
|---|---|
| `history_path[].timestamp/sim_time/t` | `history[].t` |
| `history_path[].lng/lon/x` | `history[].x` |
| `history_path[].lat/y` | `history[].y` |
| `weapon_prep_sec` | 原样保留 |
| `flight_time_sec` | 原样保留 |

### 4.4 威胁评估 `threat_evaluation`

来源：

| 来源字段 | 来源 Agent |
|---|---|
| `risk_assessments[].probability` | Track & Threat Agent |
| `risk_assessments[].threat_score` | Track & Threat Agent |
| `unified_threat_ranking[].score` | Track & Threat Agent |
| `intelligence_packet.targets[].threat_score` | Tactical Intelligence Agent |
| `mission_input.contacts[].threat_score` | 任务输入补充 |

计算：

```text
priority_score = max(所有可归一化到 0-1 的威胁值)
```

输出还会保留明细：

```python
{
    "priority_score": 0.82,
    "risk_assessments": [...],
    "ranked_targets": [...]
}
```

### 4.5 资源分配 `resource_allocation`

来源：

| 来源字段 | 来源 Agent |
|---|---|
| `task_scheduling_result.resources` | Task Scheduling Agent |
| `task_scheduling_result.scheduled_tasks` | Task Scheduling Agent |
| `planning_input.resources` | Commander 从调度结果整理 |
| `planning_input.scheduled_tasks` | Commander 从调度结果整理 |

计算资源就绪度：

```text
如果 resource.status 属于 available/ready/idle/online/active:
    resource_score = resource.capacity，如果 capacity 缺失则视为 1.0
否则:
    resource_score = 0.0

readiness = mean(resource_score)
```

计算资源压力：

```text
supply_pressure = scheduled_task_count / (scheduled_task_count + resource_count)
```

### 4.6 通信质量 `communication`

这里没有引入额外通信 Agent，而是从 Task Scheduling Agent 的结果中推导“任务-资源覆盖质量”。

来源：

| 来源字段 | 来源 Agent |
|---|---|
| `scheduled_tasks[].required_resource_types` | Task Scheduling Agent |
| `resources[].type` | Task Scheduling Agent |
| `resources[].status` | Task Scheduling Agent |

计算：

```text
available_resource_types = 所有可用资源的 type

如果某个任务 required_resource_types 为空:
    认为 covered
否则如果任意 required_resource_type 在 available_resource_types 中:
    认为 covered
否则:
    不 covered

delivery_rate = covered_task_count / total_task_count
```

输出：

```python
{
    "delivery_rate": 1.0
}
```

注意：这里的 `comm_quality` 不是物理通信链路质量，而是 zh 当前链路里可解释的“调度任务能不能被可用资源覆盖”的协同质量。

### 4.7 计划决策 `plan_decision`

来源：

```text
Decision Planning Agent -> decision_planning_result
```

保留字段：

```python
{
    "candidate_plans": [...],
    "recommended_plan_id": "...",
    "recommended_plan": {...},
    "decision": "..."
}
```

### 4.8 合规授权 `compliance_authorization`

来源：

```text
Compliance Authorization Agent -> compliance_authorization_result
```

保留字段：

```python
{
    "decision": "approved",
    "approved_for_demo_handoff": True,
    "requires_human_approval": False
}
```

### 4.9 执行控制证据 `execution_control`

来源：

```text
Execution Control Agent -> execution_control_result
```

如果当前链路已经走过仿真执行，则也会读取外部执行证据：

```text
execution_simulation_result
```

这里只把它当作 zh 闭环后续需要的执行证据来源，不展开讲仿真执行 Agent。

保留字段：

```python
{
    "commands": [...],
    "latency_ms": 120.0,
    "execution_mode": "simulation"
}
```

## 5. 七维任务特征从哪里来

闭环优化使用 `mission_features_v2` 七维特征：

```text
damage_rate
asset_readiness
control_timeliness
intel_confidence
threat_pressure
ammo_pressure
comm_quality
```

下面逐项说明它是不是能从 zh 前置 Agent 拿到，以及具体从哪里拿。

### 5.1 总表

| 中文特征 | English Feature | 能否从 zh 链路拿到 | 具体字段来源 | 计算方式 |
|---|---|---|---|---|
| 损伤率 | `damage_rate` | 不能从纯前置情报/调度阶段直接拿；需要执行后评估证据 | `targets[].damage_probability`、`damage_confirmation`、`xbd_damage_assessor`、外部 `eval_score` | 目标损伤概率均值，或 `confirmed_destroyed / engaged_targets` |
| 资源就绪度 | `asset_readiness` | 能 | Task Scheduling Agent 的 `task_scheduling_result.resources` | 可用资源 `capacity` 平均 |
| 控制及时性 | `control_timeliness` | 能，但必须等 Execution Control Agent 跑完 | `execution_control_result.output_data.latency_ms` | `1 - latency_ms / 2000` |
| 情报置信度 | `intel_confidence` | 能 | Tactical Intelligence Agent 的 `intelligence_packet.targets[].confidence` | detection confidence 平均 |
| 威胁压力 | `threat_pressure` | 能 | Track & Threat Agent 的 `risk_assessments` / `unified_threat_ranking` | ranking/risk score 平均或 priority score |
| 弹药/资源压力 | `ammo_pressure` | 能派生 | Task Scheduling Agent 的 `scheduled_tasks` 和 `resources` | `scheduled_task_count / (scheduled_task_count + resource_count)` |
| 通信质量 | `comm_quality` | 能派生 | Task Scheduling Agent 的任务资源覆盖情况 | `covered_task_count / total_task_count` |

### 5.2 损伤率 `damage_rate`

含义：

```text
当前目标被执行动作影响后的损伤程度。
```

字段来源：

| 优先级 | 字段 | 来源 |
|---|---|---|
| 1 | `targets[].damage_probability` | Closed-loop 输入 targets，可能由 Commander 从外部 `eval_score` 或目标自带损伤字段组装 |
| 2 | `damage_confirmation.confirmed_destroyed` / `engaged_targets` | 如果后续接入明确损伤确认结果，则直接使用 |
| 3 | `xbd_damage_assessor.damage_probability` | Closed-loop 内部损伤评估算法 |

计算：

```text
如果 targets 中有 damage_probability:
    damage_rate = mean(targets[].damage_probability)
否则如果有 damage_confirmation:
    damage_rate = confirmed_destroyed / engaged_targets
否则如果 xBD 损伤评估器能对目标评估:
    damage_rate = mean(xBD damage probabilities)
否则:
    missing_fields += ["damage_rate"]
```

关键说明：

```text
damage_rate 不是 Tactical Intelligence / Track Threat / Task Scheduling 这几个纯前置 Agent 一开始就能提供的字段。
它需要执行后证据或目标损伤证据。
本次修改后，如果已有 eval_score 或目标 damage_probability，就用它；没有就明确报缺失，不造默认值。
```

### 5.3 资源就绪度 `asset_readiness`

含义：

```text
当前资源是否可用、能力是否足够。
```

字段来源：

```text
Task Scheduling Agent
  -> task_scheduling_result.resources
  -> resource_allocation.readiness
  -> asset_readiness
```

源字段：

```python
{
    "id": "FIRE-1",
    "type": "strike",
    "status": "available",
    "capacity": 1.0
}
```

计算：

```text
available 状态包括 available/ready/idle/online/active。

如果资源 available:
    resource_score = capacity，如果 capacity 缺失则为 1.0
否则:
    resource_score = 0

asset_readiness = mean(resource_score)
```

例子：

```text
资源 A: available, capacity=1.0
资源 B: available, capacity=0.8
资源 C: offline, capacity=1.0

asset_readiness = (1.0 + 0.8 + 0.0) / 3 = 0.6
```

### 5.4 控制及时性 `control_timeliness`

含义：

```text
执行控制响应速度是否满足闭环要求。
```

字段来源：

```text
Execution Control Agent
  -> execution_control_result.output_data.latency_ms
  -> control_timeliness
```

如果已经进入仿真执行，也可从：

```text
execution_simulation_result.output_data.latency_ms
```

计算：

```text
control_timeliness = clamp(1.0 - latency_ms / LATENCY_REFERENCE_MS)
LATENCY_REFERENCE_MS = 2000.0
```

例子：

```text
latency_ms = 120
control_timeliness = 1 - 120 / 2000 = 0.94
```

关键说明：

```text
control_timeliness 不是战术情报、威胁评估、任务调度阶段直接能拿的。
它必须等 Execution Control Agent 运行后，才有真实 latency。
```

### 5.5 情报置信度 `intel_confidence`

含义：

```text
当前目标情报可信程度。
```

字段来源：

```text
Tactical Intelligence Agent
  -> intelligence_packet.targets[].confidence
  -> perception_detection.detections[].conf
  -> intel_confidence
```

备用来源：

```text
mission_input.contacts[].confidence
data_fusion.fused_track.det_conf
targets[].detection_confidence
```

计算：

```text
intel_confidence = mean(all detection conf)
```

例子：

```text
T-1 confidence=0.91
T-2 confidence=0.87

intel_confidence = 0.89
```

### 5.6 威胁压力 `threat_pressure`

含义：

```text
当前目标集合整体威胁有多高。
```

字段来源：

```text
Track & Threat Agent
  -> threat_assessment_result.risk_assessments[].probability
  -> threat_assessment_result.unified_threat_ranking[].score
  -> threat_evaluation
```

备用来源：

```text
Tactical Intelligence Agent
  -> intelligence_packet.targets[].threat_score
```

标准映射层先计算：

```text
priority_score = max(
    risk_assessments[].probability,
    risk_assessments[].threat_score,
    unified_threat_ranking[].score,
    intelligence_packet.targets[].threat_score,
    mission_input.contacts[].threat_score
)
```

闭环特征层再计算：

```text
如果有 ranked_targets:
    threat_pressure = mean(ranked_targets[].score)
否则:
    threat_pressure = priority_score
```

### 5.7 弹药/资源压力 `ammo_pressure`

含义：

```text
当前任务量相对资源量是否紧张。
```

字段来源：

```text
Task Scheduling Agent
  -> task_scheduling_result.scheduled_tasks
  -> task_scheduling_result.resources
  -> resource_allocation.supply_pressure
  -> ammo_pressure
```

计算：

```text
supply_pressure = scheduled_task_count / (scheduled_task_count + resource_count)
ammo_pressure = supply_pressure
```

例子：

```text
scheduled_task_count = 3
resource_count = 5

ammo_pressure = 3 / (3 + 5) = 0.375
```

说明：

```text
如果上游已经显式给了 supply_pressure / ammo_pressure / resource_pressure，就优先使用显式字段。
否则用任务数和资源数派生。
```

### 5.8 通信质量 `comm_quality`

含义：

```text
当前任务和资源之间是否能形成有效协同覆盖。
```

字段来源：

```text
Task Scheduling Agent
  -> scheduled_tasks[].required_resource_types
  -> resources[].type/status
  -> communication.delivery_rate
  -> comm_quality
```

计算：

```text
available_resource_types = 所有 status 可用的 resources[].type

对每个 task:
    如果 required_resource_types 为空:
        covered = True
    否则如果任意 required_resource_type 在 available_resource_types 中:
        covered = True
    否则:
        covered = False

comm_quality = covered_task_count / total_task_count
```

例子：

```text
resources:
  SENSOR-1 type=sensor status=available
  FIRE-1 type=strike status=available

tasks:
  TASK-1 required_resource_types=["sensor"]
  TASK-2 required_resource_types=["strike"]

comm_quality = 2 / 2 = 1.0
```

说明：

```text
这里的通信质量不是通信物理层指标，而是 zh 当前链路中能从 Task Scheduling Agent 输出稳定推导出的任务-资源协同覆盖质量。
```

## 6. 缺失数据策略

当前策略：

```text
能从 zh 前置 Agent 输出推导的，就推导；
推导必须有明确字段来源；
不能推导时，返回 insufficient_data 和 missing_fields；
不再使用无来源默认值。
```

典型情况：

| 情况 | 当前行为 |
|---|---|
| 没有轨迹历史，但有 `recommended_plan.target_ids` | 执行控制生成命令，`source=decision_plan` |
| 没有推荐计划，但有 `scheduled_tasks[].target_id` | 执行控制生成命令，`source=scheduled_task` |
| 没有推荐计划，也没有调度任务 | 执行控制返回 `insufficient_data` |
| 没有 `damage_confirmation`，但有 `targets.damage_probability` | 闭环可以计算 `damage_rate` |
| 没有任何损伤或评估证据 | 闭环返回 `missing_fields=["damage_rate"]` |
| 合规授权未批准 | 执行控制阻断后续执行 |

## 7. 本次修改的 zh 相关文件

| 文件 | 做了什么 |
|---|---|
| `commander/closed_loop_agent/agent_results_mapping.py` | 将 zh 前置 Agent 的真实输出统一映射成标准 `results` |
| `commander/execution_control_agent/execution_control_core.py` | 支持从 `recommended_plan` / `scheduled_tasks` 生成执行命令 |
| `commander/services/a2a_algorithms_common/execution_planner.py` | 服务侧执行控制 planner 同步本地逻辑 |
| `commander/closed_loop_agent/mission_feature_adapter.py` | 七维特征改为先提取真实字段，最后再判断缺失 |
| `commander/services/a2a_algorithms_common/mission_feature_adapter.py` | 服务侧七维特征适配器同步 |
| `commander/commander_agent/main.py` | 为 zh 执行控制和闭环优化组装标准输入 |

## 8. 一条 zh 链路示例

前置 Agent 输出：

```python
intelligence_packet = {
    "targets": [
        {"track_id": "T-1", "class": "surface_target", "confidence": 0.91, "threat_score": 0.82}
    ]
}

threat_assessment_result = {
    "risk_assessments": [
        {"target_id": "T-1", "probability": 0.82, "priority": 1}
    ],
    "unified_threat_ranking": [
        {"item_id": "T-1", "score": 0.82}
    ]
}

task_scheduling_result = {
    "scheduled_tasks": [
        {"target_id": "T-1", "priority": 1, "task_type": "engage", "required_resource_types": ["strike"]}
    ],
    "resources": [
        {"id": "FIRE-1", "type": "strike", "status": "available", "capacity": 1.0}
    ]
}

decision_planning_result = {
    "recommended_plan_id": "PLAN-1",
    "recommended_plan": {
        "id": "PLAN-1",
        "target_ids": ["T-1"],
        "actions": ["engage"],
        "assigned_resources": ["FIRE-1"]
    }
}

compliance_authorization_result = {
    "decision": "approved",
    "approved_for_demo_handoff": True
}
```

标准映射得到：

```python
results = {
    "perception_detection": {
        "output_data": {
            "detections": [{"track_id": "T-1", "conf": 0.91, "class_name": "surface_target"}]
        }
    },
    "threat_evaluation": {
        "output_data": {
            "priority_score": 0.82,
            "risk_assessments": [...],
            "ranked_targets": [...]
        }
    },
    "resource_allocation": {
        "output_data": {
            "readiness": 1.0,
            "supply_pressure": 0.5,
            "scheduled_tasks": [...],
            "resources": [...]
        }
    },
    "communication": {
        "output_data": {
            "delivery_rate": 1.0
        }
    },
    "plan_decision": {
        "output_data": {
            "recommended_plan_id": "PLAN-1",
            "recommended_plan": {...}
        }
    },
    "compliance_authorization": {
        "output_data": {
            "decision": "approved",
            "approved_for_demo_handoff": True
        }
    }
}
```

执行控制在没有轨迹预测时仍能生成：

```python
{
    "assessment_status": "ready",
    "commands": [
        {
            "command_id": "CMD-STR-001",
            "executor_role": "artillery",
            "action": "precision_strike",
            "target_id": "T-1",
            "priority": 0.82,
            "source": "decision_plan"
        }
    ]
}
```

如果执行后存在 `latency_ms=120` 和 `damage_probability=0.70`，闭环七维特征就是：

```python
{
    "damage_rate": 0.70,
    "asset_readiness": 1.0,
    "control_timeliness": 0.94,
    "intel_confidence": 0.91,
    "threat_pressure": 0.82,
    "ammo_pressure": 0.5,
    "comm_quality": 1.0
}
```

逐项来源：

| 特征 | 值 | 来源 |
|---|---:|---|
| `damage_rate` | 0.70 | 执行/评估后的 `targets.damage_probability` |
| `asset_readiness` | 1.00 | Task Scheduling Agent 的 `resources[].capacity/status` |
| `control_timeliness` | 0.94 | Execution Control Agent 的 `latency_ms=120` |
| `intel_confidence` | 0.91 | Tactical Intelligence Agent 的 `targets[].confidence` |
| `threat_pressure` | 0.82 | Track & Threat Agent 的 `unified_threat_ranking[].score` |
| `ammo_pressure` | 0.50 | 1 个任务 / 1 个任务 + 1 个资源 |
| `comm_quality` | 1.00 | 任务要求 strike，资源中有 available strike |

## 9. 验证结果

本次 zh 相关改动已验证：

```text
py_compile: passed
test_algolib_orchestration.py: 8 passed
test_mission_feature_module.py: 9 passed
test_a2a_algorithm_services.py: 13 passed
```

行为检查结果：

```text
没有轨迹历史，但存在 intelligence_packet、threat_assessment_result、
task_scheduling_result、decision_planning_result、compliance_authorization_result。

execution_control assessment_status = ready
commands[0].source = decision_plan
closed_loop feature_status = ready
missing_fields = []
```
