# BPEL 输入输出字段与 Schema 统一分析

## 1. 分析目标

本文档用于梳理当前 A2A 项目中 BPEL activity 的输入输出字段，分析现有 schema 是否清晰、是否适合真实 Agent 接入，并给出统一改进建议。

关注点：

```text
1. BPEL 中每个 activity 的 inputVariable / outputVariable 是否明确。
2. Commander 构造给 Agent 的 payload.input 是否和 BPEL 一致。
3. Agent 返回 output 后，Commander 如何写入 workflow context。
4. 当前 schema 是否支持多输入、多输出、真实 Agent 接入。
5. 后续需要统一哪些字段。
```

## 2. 当前 BPEL activity 字段

当前 BPEL `invoke` activity 主要使用以下字段：

| 字段 | 作用 |
| --- | --- |
| `name` | activity 名称 |
| `operation` | BPEL 层面的操作名 |
| `requiredSkill` | 当前 activity 需要的主技能 |
| `requiredSkills` | 当前 activity 需要的多个技能 |
| `inputVariable` | 输入变量 |
| `outputVariable` | 输出变量 |
| `dispatchMode` | 调度模式，`single` 或 `parallel` |
| `retryCount` / `maxRetries` | 重试次数 |
| `timeoutSeconds` / `timeout` | 超时时间 |
| `failurePolicy` | 失败策略 |
| `dependsOn` | 显式依赖关系 |

当前解析后的 activity 数据结构在 `bpel_workflow.py` 中：

```python
@dataclass
class BPELActivatity:
    activatity_id: str
    type: str
    name: str
    role: str | None = None
    partner_link: str | None = None
    operation: str | None = None
    command: str | None = None
    required_skill: str | None = None
    required_skills: list[str] = field(default_factory=list)
    dispatch_mode: str = "single"
    input_variable: str | None = None
    output_variable: str | None = None
    retry_count: int = 0
    timeout_seconds: float | None = None
    failure_policy: str = "pause"
    depends_on: list[str] = field(default_factory=list)
```

## 3. Commander 当前 payload 格式

Commander 执行 BPEL invoke 时，会构造发给 Agent 的 task payload。

关键代码在 `commander_agent/main.py`：

```python
def _build_bpel_task_payload(self, activatity: BPELActivatity, context: dict):
    input_key = self._context_key_for_bpel_variable(activatity.input_variable)
    input_payload = {}
    if input_key:
        input_payload[input_key] = self._context_input_value(
            context,
            input_key,
            activatity.input_variable,
        )

    return {
        "workflow_id": self.workflow_id,
        "workflow": self.workflow,
        "workflow_mode": self.mode,
        "work_item": item["work_item"],
        "parent_work_item": parent_item.get("work_item") if parent_item else None,
        "activity_id": activatity.activity_id,
        "activity_index": item["activity_index"],
        "activity_skill": dispatch_key,
        "activity": {
            "id": activatity.activity_id,
            "index": item["activity_index"],
            "name": activatity.name,
            "operation": activatity.operation,
            "skill": dispatch_key
        },
        "command": activatity.command,
        "required_skill": activatity.required_skill or activatity.command,
        "required_skills": list(activatity.required_skills),
        "input": input_payload,
        "context": self._context_snapshot(context),
        "attachments": attachment_snapshot(context.get("attachments", [])),
        "work_list": deepcopy(context.get("work_list", [])),
        "output_hint": self._context_key_for_bpel_variable(activatity.output_variable),
        "retry_policy": {
            "max_retries": activatity.retry_count if activatity.retry_count is not None else self.max_retries,
            "timeout_seconds": activatity.timeout_seconds or self.request_timeout,
            "failure_policy": activatity.failure_policy,
        },
    }
```

当前 Agent 收到的 payload 主要结构是：

```json
{
  "workflow_id": "workflow-xxx",
  "work_item": "workflow-xxx:activity-002-scanbeachdefenses",
  "activity_id": "activity-002-scanbeachdefenses",
  "activity_index": 2,
  "activity_skill": "scan_beach_defenses",
  "activity": {
    "id": "activity-002-scanbeachdefenses",
    "index": 2,
    "name": "ScanBeachDefenses",
    "operation": "scanBeachDefenses",
    "skill": "scan_beach_defenses"
  },
  "command": "scan_beach_defenses",
  "required_skill": "scan_beach_defenses",
  "required_skills": ["scan_beach_defenses"],
  "input": {
    "sector": "Sector_A"
  },
  "context": {},
  "attachments": [],
  "work_list": [],
  "output_hint": "recon_report",
  "retry_policy": {
    "max_retries": 0,
    "timeout_seconds": 5.0,
    "failure_policy": "pause"
  }
}
```

## 4. BPEL 变量到 context key 的映射

当前 Commander 会把 BPEL 变量名转换成内部 context key。

虽然映射函数未在本文展开，但当前实际表现如下：

| BPEL 变量 | context key / output key |
| --- | --- |
| `Sector_A` | `sector` |
| `StrikeCoordinates` | `coordinates` |
| `ReconReport` | `recon_report` |
| `StrikeResult` | `strike_result` |
| `EvalScore` | `eval_score` |
| `CommanderDecision` | `commander_decision` |
| `AssaultResult` | `assault_result` |

因此：

```xml
inputVariable="Sector_A"
```

会进入 payload：

```json
{
  "input": {
    "sector": "Sector_A"
  }
}
```

而：

```xml
outputVariable="ReconReport"
```

会生成：

```json
{
  "output_hint": "recon_report"
}
```

Agent 应尽量按照 `output_hint` 返回：

```json
{
  "output": {
    "recon_report": "..."
  }
}
```

## 5. 当前三个 BPEL 的 activity 输入输出梳理

### 5.1 BeachheadAssaultWorkflow

文件：`beachhead_workflow.bpel`

| 顺序 | activity / operation | requiredSkill | inputVariable | payload.input key | outputVariable | output key | dispatchMode |
| --- | --- | --- | --- | --- | --- | --- | --- |
| 1 | `scanBeachDefenses` | `scan_beach_defenses` | `Sector_A` | `sector` | `ReconReport` | `recon_report` | `single` |
| 2 | `suppressBeachSector` | `suppress_beach_sector_A` | `StrikeCoordinates` | `coordinates` | `StrikeResult` | `strike_result` | `parallel` |
| 3 | `evaluateStrike` | `evaluate_strike` | `StrikeCoordinates` | `coordinates` | `EvalScore` | `eval_score` | `single` |
| 4 | `analyzeAndReplanning` | `analyze_and_replanning` | `ReconReport + StrikeResult` | 当前存在问题 | `CommanderDecision` | `commander_decision` | `single` |
| 5 | `captureBeachhead` | `capture_beachhead` | `StrikeCoordinates` | `coordinates` | 缺失 | 当前依赖兜底 | `single` |

观察：

```text
1. Recon 输入输出清晰。
2. Artillery 输入输出清晰，但真实任务通常还需要 ReconReport，目前 BPEL 没显式声明。
3. Evaluator 只声明 StrikeCoordinates，但代码实际还会从 context 额外塞 recon_report、strike_result。
4. analyzeAndReplanning 的 inputVariable 使用 "ReconReport + StrikeResult"，当前 parser 会把它当作一个字符串变量处理，不是真正的多输入。
5. Assault 没有 outputVariable，导致 output_hint 为 None，结果写入依赖 Commander 兜底逻辑。
```

### 5.2 QuickStrikeWorkflow

文件：`quick_strike_workflow.bpel`

| 顺序 | activity / operation | requiredSkill | inputVariable | payload.input key | outputVariable | output key | dispatchMode |
| --- | --- | --- | --- | --- | --- | --- | --- |
| 1 | `scanBeachDefenses` | `scan_beach_defenses` | `Sector_A` | `sector` | `ReconReport` | `recon_report` | `single` |
| 2 | `suppressBeachSector` | `suppress_beach_sector_A` | `StrikeCoordinates` | `coordinates` | `StrikeResult` | `strike_result` | `parallel` |
| 3 | `captureBeachhead` | `capture_beachhead` | `StrikeCoordinates` | `coordinates` | 缺失 | 当前依赖兜底 | `single` |

观察：

```text
1. QuickStrike 是简化流程，没有 Evaluator。
2. Assault 仍缺少 outputVariable。
3. Artillery 同样没有显式声明 ReconReport 输入。
```

### 5.3 ReinforcedBeachheadWorkflow

文件：`reinforced_beachhead_workflow.bpel`

| 顺序 | activity / operation | requiredSkill | inputVariable | payload.input key | outputVariable | output key | dispatchMode |
| --- | --- | --- | --- | --- | --- | --- | --- |
| 1 | `scanBeachDefenses` | `scan_beach_defenses` | `Sector_A` | `sector` | `ReconReport` | `recon_report` | `parallel` |
| 2 | `suppressBeachSector` | `suppress_beach_sector_A` | `StrikeCoordinates` | `coordinates` | `StrikeResult` | `strike_result` | `parallel` |
| 3 | `evaluateStrike` | `evaluate_strike` | `StrikeCoordinates` | `coordinates` | `EvalScore` | `eval_score` | `single` |
| 4 | `analyzeAndReplanning` | `analyze_and_replanning` | `ReconReport + StrikeResult` | 当前存在问题 | `CommanderDecision` | `commander_decision` | `single` |
| 5 | `captureBeachhead` | `capture_beachhead` | `StrikeCoordinates` | `coordinates` | 缺失 | 当前依赖兜底 | `parallel` |

观察：

```text
1. Reinforced 允许 Recon、Artillery、Assault 并行派发。
2. 多个 Agent 写同一个 outputVariable 时，Commander 当前会把结果收集成列表。
3. analyzeAndReplanning 多输入表达不规范。
4. Assault 缺少 outputVariable。
```

## 6. 当前主要问题

### 6.1 inputVariable 只支持单变量

当前 BPEL 使用：

```xml
inputVariable="StrikeCoordinates"
```

这种单输入变量能正常工作。

但下面这种写法存在问题：

```xml
inputVariable="ReconReport + StrikeResult"
```

当前解析器不会真正把它拆成两个输入，而是会把它当成一个变量名。

风险：

```text
真实 Agent 接入后，Agent 可能拿不到 recon_report 和 strike_result 两个独立字段。
```

### 6.2 部分 activity 缺少 outputVariable

例如：

```xml
<invoke requiredSkill="capture_beachhead"
        inputVariable="StrikeCoordinates"/>
```

没有：

```xml
outputVariable="AssaultResult"
```

当前 Commander 有兜底逻辑，但真实 Agent 接入时不建议依赖兜底。

风险：

```text
Agent 不知道应该返回什么 key。
Commander 写 context 时不够明确。
后续 activity 难以稳定引用 assault_result。
```

### 6.3 BPEL 声明输入与代码实际输入不一致

例如 Evaluator：

```xml
<invoke operation="evaluateStrike"
        inputVariable="StrikeCoordinates"
        outputVariable="EvalScore"/>
```

但 Commander 实际业务上还会需要：

```text
recon_report
strike_result
mock_eval_score
```

这说明当前 BPEL 没有完整表达 Agent 的真实输入需求。

风险：

```text
BPEL 看起来只依赖 StrikeCoordinates，但实际运行依赖 ReconReport 和 StrikeResult。
调试、迁移、真实 Agent 接入时容易误解。
```

### 6.4 字段命名统一为 activity

当前标准字段统一使用：

```text
activity_id
activity_index
activity_skill
```

含义如下：

```text
activity_id：当前 activity 的唯一 ID。
activity_index：当前 activity 在 work_list 中的顺序号。
activity_skill：当前 activity 调度 Agent 时使用的技能 key。
```

历史字段只做兼容旧 checkpoint / 旧 demo Agent：

```text
activatity_id
activatity_index
activatity_role
```

真实 Agent 新接入时不要再读取 `activatity_*`，应优先读取 `activity` 对象或 `activity_*` 字段。

### 6.5 output 类型缺少 schema 约束

目前 BPEL 变量只写：

```xml
<variable name="ReconReport" type="String"/>
```

但真实 Agent 可能返回结构化对象：

```json
{
  "summary": "...",
  "targets": [],
  "confidence": 0.91
}
```

如果继续声明成 `String`，语义不够准确。

## 7. 建议统一的 BPEL schema

建议后续统一成以下格式。

### 7.1 单输入单输出

```xml
<invoke name="ScanBeach"
        requiredSkill="scan_beach_defenses"
        operation="scanBeachDefenses"
        inputVariables="Sector_A"
        outputVariable="ReconReport"
        outputKey="recon_report"
        failurePolicy="pause"
        timeoutSeconds="10"/>
```

说明：

```text
inputVariables：统一使用复数，即使只有一个输入。
outputVariable：BPEL 变量名。
outputKey：Agent output 中建议返回的 key。
```

### 7.2 多输入单输出

```xml
<invoke name="EvaluateStrike"
        requiredSkill="evaluate_strike"
        operation="evaluateStrike"
        inputVariables="ReconReport,StrikeResult,StrikeCoordinates"
        outputVariable="EvalScore"
        outputKey="eval_score"
        failurePolicy="pause"/>
```

生成 payload：

```json
{
  "input": {
    "recon_report": [...],
    "strike_result": [...],
    "coordinates": "120.5E, 35.1N"
  },
  "output_hint": "eval_score"
}
```

### 7.3 多技能 activity

```xml
<invoke name="ComplexRecon"
        requiredSkills="scan_beach_defenses,target_identification"
        operation="complexRecon"
        inputVariables="Sector_A"
        outputVariable="ReconReport"
        outputKey="recon_report"/>
```

含义：

```text
Agent 必须同时具备 scan_beach_defenses 和 target_identification。
```

### 7.4 建议变量定义支持 object

建议变量定义从：

```xml
<variable name="ReconReport" type="String"/>
```

改成更准确的：

```xml
<variable name="ReconReport" type="Object"/>
<variable name="StrikeResult" type="Object"/>
<variable name="EvalScore" type="Integer"/>
<variable name="AssaultResult" type="Object"/>
```

或者后续引入 schemaRef：

```xml
<variable name="ReconReport"
          type="Object"
          schemaRef="schemas/recon_report.schema.json"/>
```

## 8. 建议统一的 Agent payload schema

Commander 下发给 Agent 的 payload 建议稳定为：

```json
{
  "workflow_id": "workflow-xxx",
  "work_item": "workflow-xxx:activity-002-scan",
  "activity": {
    "id": "activity-002-scan",
    "index": 2,
    "name": "ScanBeach",
    "type": "invoke",
    "operation": "scanBeachDefenses",
    "required_skill": "scan_beach_defenses",
    "required_skills": ["scan_beach_defenses"],
    "dispatch_mode": "single"
  },
  "input": {
    "sector": "Sector_A"
  },
  "output": {
    "variable": "ReconReport",
    "key": "recon_report"
  },
  "context": {},
  "attachments": [],
  "retry_policy": {
    "max_retries": 1,
    "timeout_seconds": 10,
    "failure_policy": "pause"
  }
}
```

兼容当前字段：

```text
required_skill
required_skills
input
output_hint
work_item
```

但长期建议新增嵌套结构：

```text
activity
output
```

让真实 Agent 更容易理解任务含义。

## 9. 建议统一的 Agent response schema

Agent 返回建议统一为：

```json
{
  "workflow_id": "workflow-xxx",
  "work_item": "workflow-xxx:activity-002-scan",
  "agent": "TerrainAnalysisAgent",
  "skill": "scan_beach_defenses",
  "command": "scan_beach_defenses",
  "status": "completed",
  "output": {
    "recon_report": {
      "summary": "发现三处防御工事",
      "targets": [],
      "confidence": 0.91
    }
  },
  "metrics": {
    "duration_ms": 1200,
    "algorithm": "terrain-v1"
  },
  "error": null,
  "message": "scan_beach_defenses completed",
  "attempts": 1,
  "cached": false
}
```

字段要求：

| 字段 | 要求 | 说明 |
| --- | --- | --- |
| `workflow_id` | 必填 | workflow ID |
| `work_item` | 必填 | activity 唯一任务 ID |
| `agent` | 必填 | Agent 名称 |
| `skill` | 建议 | 实际执行的 skill |
| `command` | 建议 | 执行命令 |
| `status` | 必填 | `completed` / `failed` |
| `output` | 必填 | 结果对象 |
| `metrics` | 建议 | 耗时、算法版本、置信度 |
| `error` | 失败时必填 | 错误原因 |
| `cached` | 建议 | 是否幂等缓存结果 |

当前代码里仍使用 `role` 字段承载执行标识。由于现在已经是 skill-only 调度，建议后续逐步改为：

```text
role -> skill
```

兼容期可以两个字段都返回：

```json
{
  "role": "scan_beach_defenses",
  "skill": "scan_beach_defenses"
}
```

## 10. 每个 activity 建议改进结果

### 10.1 scan_beach_defenses

建议 BPEL：

```xml
<invoke name="ScanBeach"
        requiredSkill="scan_beach_defenses"
        operation="scanBeachDefenses"
        inputVariables="Sector_A"
        outputVariable="ReconReport"
        outputKey="recon_report"/>
```

建议 payload.input：

```json
{
  "sector": "Sector_A"
}
```

建议 Agent output：

```json
{
  "recon_report": {
    "summary": "发现三处防御工事",
    "targets": [],
    "confidence": 0.91
  }
}
```

### 10.2 suppress_beach_sector_A

建议 BPEL：

```xml
<invoke name="SuppressBeach"
        requiredSkill="suppress_beach_sector_A"
        operation="suppressBeachSector"
        dispatchMode="parallel"
        inputVariables="StrikeCoordinates,ReconReport"
        outputVariable="StrikeResult"
        outputKey="strike_result"/>
```

建议 payload.input：

```json
{
  "coordinates": "120.5E, 35.1N",
  "recon_report": [...]
}
```

建议 Agent output：

```json
{
  "strike_result": {
    "status": "suppressed",
    "effects": [],
    "confidence": 0.87
  }
}
```

### 10.3 evaluate_strike

建议 BPEL：

```xml
<invoke name="EvaluateStrike"
        requiredSkill="evaluate_strike"
        operation="evaluateStrike"
        inputVariables="ReconReport,StrikeResult,StrikeCoordinates"
        outputVariable="EvalScore"
        outputKey="eval_score"/>
```

建议 payload.input：

```json
{
  "recon_report": [...],
  "strike_result": [...],
  "coordinates": "120.5E, 35.1N"
}
```

建议 Agent output：

```json
{
  "eval_score": 75,
  "evaluation": {
    "summary": "压制效果基本达标",
    "risk_level": "medium"
  }
}
```

说明：

```text
如果 output_hint 是 eval_score，Commander 当前会优先读取 output.eval_score。
额外的 evaluation 可以保留在 output 中供后续扩展。
```

### 10.4 analyze_and_replanning

建议 BPEL：

```xml
<invoke name="AnalyzeAndReplanning"
        requiredSkill="analyze_and_replanning"
        operation="analyzeAndReplanning"
        inputVariables="ReconReport,StrikeResult,EvalScore"
        outputVariable="CommanderDecision"
        outputKey="commander_decision"/>
```

建议 payload.input：

```json
{
  "recon_report": [...],
  "strike_result": [...],
  "eval_score": [...]
}
```

建议 Agent output：

```json
{
  "commander_decision": {
    "decision": "REPLAN",
    "reason": "压制效果不足",
    "next_required_skills": ["scan_beach_defenses"]
  }
}
```

### 10.5 capture_beachhead

建议 BPEL：

```xml
<invoke name="CaptureBeachhead"
        requiredSkill="capture_beachhead"
        operation="captureBeachhead"
        inputVariables="StrikeCoordinates,ReconReport,StrikeResult,EvalScore,CommanderDecision"
        outputVariable="AssaultResult"
        outputKey="assault_result"/>
```

建议 payload.input：

```json
{
  "coordinates": "120.5E, 35.1N",
  "recon_report": [...],
  "strike_result": [...],
  "eval_score": [...],
  "commander_decision": [...]
}
```

建议 Agent output：

```json
{
  "assault_result": {
    "status": "captured",
    "objective": "beachhead",
    "loss_estimate": "low"
  }
}
```

## 11. 是否需要改进

结论：**需要改进。**

当前系统已经能跑通，但为了真实 Agent 接入和长期维护，建议尽快统一 BPEL schema。

优先级如下：

### P0：必须改

1. 所有 invoke 都必须显式写 `outputVariable`。
2. 不再使用 `inputVariable="A + B"` 这种表达多输入。
3. 新增并统一使用 `inputVariables`。
4. 明确规定 Agent 必须按 `output_hint` 返回 output key。

### P1：建议改

1. 新增 `outputKey`，显式声明 Agent output key。
2. payload 中新增嵌套 `activity` 和 `output` 结构。
3. response 中新增 `skill` 字段，逐步弱化 `role`。
4. BPEL variable 类型从简单 `String` 扩展到 `Object` / `Array`。

### P2：后续增强

1. 引入 JSON Schema 校验输入输出。
2. 支持多个 outputVariable / outputMappings。
3. 支持 requiredSkills 的 OR / AND 策略。
4. 支持 activity 级别输入字段重命名。

## 12. 推荐后的统一示例

推荐 BPEL：

```xml
<process name="UnifiedBeachheadWorkflow" targetNamespace="http://a2a.workflow">
  <variables>
    <variable name="Sector" type="String"/>
    <variable name="ReconReport" type="Object"/>
    <variable name="StrikeCoordinates" type="String"/>
    <variable name="StrikeResult" type="Object"/>
    <variable name="EvalScore" type="Integer"/>
    <variable name="CommanderDecision" type="Object"/>
    <variable name="AssaultResult" type="Object"/>
  </variables>

  <sequence>
    <invoke name="ScanBeach"
            requiredSkill="scan_beach_defenses"
            operation="scanBeachDefenses"
            inputVariables="Sector"
            outputVariable="ReconReport"
            outputKey="recon_report"/>

    <invoke name="SuppressBeach"
            requiredSkill="suppress_beach_sector_A"
            operation="suppressBeachSector"
            dispatchMode="parallel"
            inputVariables="StrikeCoordinates,ReconReport"
            outputVariable="StrikeResult"
            outputKey="strike_result"/>

    <invoke name="EvaluateStrike"
            requiredSkill="evaluate_strike"
            operation="evaluateStrike"
            inputVariables="ReconReport,StrikeResult,StrikeCoordinates"
            outputVariable="EvalScore"
            outputKey="eval_score"/>

    <invoke name="CaptureBeachhead"
            requiredSkill="capture_beachhead"
            operation="captureBeachhead"
            inputVariables="StrikeCoordinates,ReconReport,StrikeResult,EvalScore"
            outputVariable="AssaultResult"
            outputKey="assault_result"/>
  </sequence>
</process>
```

推荐 Agent payload：

```json
{
  "workflow_id": "workflow-xxx",
  "work_item": "workflow-xxx:activity-004-capture",
  "activity": {
    "id": "activity-004-capture",
    "name": "CaptureBeachhead",
    "operation": "captureBeachhead",
    "required_skill": "capture_beachhead",
    "required_skills": ["capture_beachhead"],
    "dispatch_mode": "single"
  },
  "input": {
    "coordinates": "120.5E, 35.1N",
    "recon_report": [],
    "strike_result": [],
    "eval_score": []
  },
  "output": {
    "variable": "AssaultResult",
    "key": "assault_result"
  },
  "output_hint": "assault_result",
  "context": {},
  "attachments": [],
  "retry_policy": {
    "max_retries": 1,
    "timeout_seconds": 10,
    "failure_policy": "pause"
  }
}
```

## 13. 总结

当前 BPEL 输入输出机制已经具备基础能力：

```text
inputVariable -> payload.input
outputVariable -> output_hint
Agent output -> workflow context
```

但对于真实 Agent 接入还不够规范，主要问题是：

```text
1. 多输入表达不标准。
2. 部分 activity 缺少 outputVariable。
3. BPEL 声明和实际 payload 输入不完全一致。
4. output 类型缺少 schema 约束。
5. role/skill 字段仍处于兼容过渡期。
```

建议下一步统一为：

```text
inputVariables + outputVariable + outputKey + requiredSkill
```

并在 payload 中明确：

```text
activity
input
output
retry_policy
```

这样真实 Agent 只需要根据统一 schema 读取输入、执行算法、按 output.key 返回结果，就可以稳定接入 Commander 编排系统。
