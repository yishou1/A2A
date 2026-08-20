# A2A 作战工作流 (Workflow Specification)

在传统企业级架构中，诸如 **BPEL (Business Process Execution Language)** 或 **BPMN (Business Process Model and Notation)** 通常被用来对复杂的分布式业务活动进行建模抽象和编排执行。

随着大模型和多智能体时代的到来，我们同样可以使用工作流语言将系统中的 Agent 协同抽象化：让各个 Agent 成为服务节点（Partner Links），而全局上下文变量就是战场态势图。

## 1. BPEL 形式的工作流表示

为本项目编写的一个标准宏观编排描述已存放在： `beachhead_workflow.bpel` 中。

在这个 XML 描述标准中，我们可以看到早期工作流引擎的核心思想是如何映射到我们今天的 Agent A2A 设计上的：

- **`<process>` & `<sequence>` (执行序列)**：定义必须按顺序推进的活动。
- **同类 Agent 并发**：不同角色保持顺序依赖。需要并发协作时，在单个 `<invoke>` 上使用 `dispatchMode="parallel"`，Commander 会把任务并发派发给多个同类型 Agent 实例。
- **`<invoke partnerLink="...">` (服务调用)**：对应我们在 Commander (指挥官) 中书写的动态寻址逻辑。早期的 BPEL 会去 UDDI (早年间的服务发现中心) 寻找伙伴；而现在我们去 **Nacos** 找到如 `recon_agent` 或 `artillery_agent`。
- **`<variables>` (上下文状态机)**：流转于各个 Agent 之间的信息状态。例如侦察兵探明的机枪阵地报告（ReconReport），传递给了火力打击与最后的大模型。
- **`<switch> ... <case>` (网关与决策分支)**：对应着战果评估后（毁伤率40%）是否终止原定计划的判断分流。如果是现代 Agentic Workflow ，这一步将交给 GPT/LLM （大语言模型）作为决策网关动态判断。

当前 Commander 会在启动时扫描项目中的 `.bpel` 文件，并支持按文件路径、文件名、文件 stem 或 `<process name>` 动态加载工作流。加载后会生成 `work_list`，其中每个活动都有稳定的 `activatity_id` 和 `work_item`。这些字段会进入 checkpoint，也会随 A2A 消息发送给 Agent。

当前抢滩登陆工作流中的关键并发结构是：

```xml
<invoke partnerLink="ArtilleryAgent"
        operation="suppressBeachSector"
        dispatchMode="parallel"/>
```

因此 recon 完成后才会进入 artillery。Commander 会从 Nacos 查询全部 `role=artillery` 且 `status=idle` 的实例，并行下发火力任务；全部炮兵任务完成后才会调用 Evaluator。

## 2. 可选择的预定义 BPEL

项目允许提前维护多套 `.bpel` 工作流，并在 Commander 启动时按需选择：

| BPEL | Process Name | 特点 |
|---|---|---|
| `beachhead_workflow.bpel` | `BeachheadAssaultWorkflow` | 基础登陆方案；炮兵同类并发；评估阈值为 60 |
| `reinforced_beachhead_workflow.bpel` | `ReinforcedBeachheadWorkflow` | 强化登陆方案；侦察、炮兵、突击均支持同类并发；评估阈值为 80 |
| `quick_strike_workflow.bpel` | `QuickStrikeWorkflow` | 简化突击方案；省略评估与条件分支；炮兵同类并发 |

查看和选择方式：

```bash
./venv/bin/python commander_agent/main.py --list-workflows
./venv/bin/python commander_agent/main.py \
  --mode local \
  --workflow bpel \
  --workflow-file ReinforcedBeachheadWorkflow \
  --mock-eval-score 85
```

## 3. 现代云原生版的演进表示 (Serverless Workflow 风格)

对于现在和未来的多智能体微服务架构，XML由于冗长往往会被 **YAML/JSON 的极简 DSL (如 CNCF Serverless Workflow)** 替代。以本项目的场景为例，其现代画的工作流描述可以表示为如下 YAML 格式：

```yaml
id: beachhead-assault-workflow
name: Beachhead Operation Multi-Agent Flow
version: '1.0'
start: ReconnaissancePhase

states:
  - name: ReconnaissancePhase
    type: operation
    actions:
      - functionRef: # Nacos 查找 role=recon
          refName: ReconAgent
          arguments:
            sector: "Sector_A"
    transition: ArtilleryStrikePhase

  - name: ArtilleryStrikePhase
    type: operation
    actions:
      - functionRef: # Nacos 查找 role=artillery
          refName: ArtilleryAgent
          arguments:
            coordinates: "120.5E, 35.1N"
    transition: EvaluateOutcomePhase

  - name: EvaluateOutcomePhase
    type: operation
    actions:
      - functionRef: # Nacos 查找 role=evaluator
          refName: EvaluatorAgent
    transition: CommanderDecisionGateway

  - name: CommanderDecisionGateway # 早期化身为 Switch，在这里化身为大模型网关
    type: switch
    dataConditions:
      - condition: "${ .eval_score < 60 }"
        transition: ReplanningPhase  # 转去重规划
    defaultCondition:
      transition: AssaultPhase       # 直接抢滩登陆

  - name: ReplanningPhase
    type: operation
    actions:
      - functionRef: # 调用 LLM 决策 Agent
          refName: LLMCommander
    end: true

  - name: AssaultPhase
    type: operation
    actions:
      - functionRef: 
          refName: AssaultAgent
    end: true
```

### 总结
无论是古老而严谨的 BPEL 标准，还是轻量级的云原生 Workflow DSL，其本质都是将**“任务执行者（Agents）”**与**“协作流程控制（Commander/Engine）”**进行解耦脱钩管理。这正是您在这套A2A协议架构中引入 Nacos 作为注册中心的意义所在：指挥大脑只关注这套**“图（Workflow）”**的运转流向，而执行者只需要按照自身技能被唤醒并打工。
