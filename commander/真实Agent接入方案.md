# A2A 真实 Agent 接入方案

> 面向对象：需要把真实业务 Agent 接入 Commander 编排系统的开发者  
> 核心原则：Agent 不再按固定 role 被调用，而是按自身声明的 skills 被 Commander 动态发现和调度。

## 1. 总体逻辑

真实 Agent 接入后的运行链路如下：

```text
真实 Agent 启动
-> 读取自身配置和算法库
-> 根据可调用算法声明 skills
-> 向 Nacos 注册 serviceName=A2A-Agent
-> 在 Nacos metadata 中写入 status=idle、skills、心跳信息
-> 暴露 Agent Card 和任务执行接口
-> 等待 Commander 下发 activity
-> 调用自身算法库执行任务
-> 返回标准 task response
-> Commander 写入 workflow context 并推进后续 BPEL activity
```

当前项目中的 `recon_agent`、`artillery_agent`、`evaluator_agent`、`assault_agent` 主要是 demo。真实接入时，Agent 可以叫任意名字，也可以拥有多个技能。Commander 只关心：

```text
1. Agent 是否注册到 Nacos
2. Agent 是否 idle
3. Agent metadata.skills 是否包含 BPEL activity.requiredSkill
4. Agent 是否心跳新鲜
5. Agent 是否能通过 A2A HTTP 接口执行任务
```

## 2. Agent 必须实现的接口

真实 Agent 至少需要提供以下 HTTP 接口。

| 接口 | 方法 | 作用 |
| --- | --- | --- |
| `/.well-known/agent-card` | `GET` | 返回 Agent 能力描述、skills、认证方式和任务接口地址 |
| `/sendMessage` | `POST` | 接收 Commander 下发的普通任务 |
| `/health` | `GET` | 健康检查 |
| `/ready` | `GET` | 返回当前是否可接收任务 |

建议额外支持：

| 接口 | 方法 | 作用 |
| --- | --- | --- |
| `/sendMessageStream` | `POST` | 长任务流式返回阶段性进度 |
| `/metrics` | `GET` | 返回任务数量、耗时、错误信息等指标 |
| `/workflows/{workflow_id}/work-list` | `GET` | 返回 Agent 收到的工作列表，便于排查 |
| `/lifecycle/ready` | `POST` | 手动切换 Agent ready 状态 |

## 3. Agent Card 规范

Agent Card 是 Commander 和其他系统了解 Agent 能力的入口。

示例：

```json
{
  "name": "TerrainAnalysisAgent",
  "description": "负责地形探测、目标识别和风险评估的真实业务 Agent。",
  "role": "generalist",
  "skills": [
    {
      "id": "scan_beach_defenses",
      "name": "Beach Defense Scan",
      "description": "探测滩头防御、障碍物、火力点和敌方阵地。",
      "tags": ["scan", "detect", "recon", "探测", "侦察"]
    },
    {
      "id": "target_identification",
      "name": "Target Identification",
      "description": "识别目标类型、坐标和威胁等级。",
      "tags": ["target", "identify", "目标识别"]
    }
  ],
  "securitySchemes": {
    "openIdConnect": {
      "type": "openIdConnect",
      "authorizationUrl": "http://127.0.0.1:8080/auth",
      "tokenUrl": "http://127.0.0.1:8080/post"
    }
  },
  "sendMessageEndpoint": "/sendMessage",
  "sendMessageStreamEndpoint": "/sendMessageStream",
  "healthEndpoint": "/health",
  "readyEndpoint": "/ready",
  "metricsEndpoint": "/metrics"
}
```

### skills 字段说明

`skills` 是 Agent 接入的核心字段。每个 skill 对应 Agent 背后算法库中的一个可调用能力。

| 字段 | 必填 | 说明 |
| --- | --- | --- |
| `id` | 是 | 技能唯一标识，必须和 BPEL 的 `requiredSkill` 对齐 |
| `name` | 建议 | 技能展示名 |
| `description` | 建议 | 技能说明 |
| `tags` | 建议 | 便于检索的关键词 |

命名建议：

```text
使用小写 snake_case
例如 scan_beach_defenses、target_identification、damage_assessment
不要频繁改 skill id
```

## 4. Nacos 注册规范

Agent 启动后必须注册到 Nacos。

固定服务名：

```text
serviceName = A2A-Agent
```

metadata 必须包含：

```json
{
  "status": "idle",
  "skills": "scan_beach_defenses,target_identification,探测,目标识别"
}
```

建议包含：

```json
{
  "agent_name": "TerrainAnalysisAgent",
  "role": "generalist",
  "version": "1.0.0",
  "algorithm_profile": "terrain-v1",
  "status": "idle",
  "skills": "scan_beach_defenses,target_identification,探测,目标识别",
  "heartbeat_ts": 1783044011,
  "heartbeat_at": "2026-07-03T02:00:11Z"
}
```

### status 取值

| status | 含义 |
| --- | --- |
| `idle` | 空闲，可以被 Commander 调度 |
| `busy` | 正在执行任务，暂时不能被其他 workflow 占用 |
| `unavailable` | 不可用，可能连接失败、心跳丢失、熔断打开 |

### skills metadata 的作用

`metadata.skills` 是给 Commander 快速筛选用的能力摘要。

Agent Card 中是结构化格式：

```json
"skills": [
  {
    "id": "scan_beach_defenses",
    "name": "Beach Defense Scan",
    "tags": ["探测", "侦察"]
  }
]
```

Nacos metadata 中压缩为字符串：

```json
{
  "skills": "scan_beach_defenses,Beach Defense Scan,探测,侦察"
}
```

Commander 当前只按 skill 匹配，不再按 role 兜底。因此如果 Agent 没有注册 `skills`，即使 `role=recon`，也不会被选中。

## 5. Commander 如何选择 Agent

BPEL activity 示例：

```xml
<invoke name="SkillOnlyRecon"
        requiredSkill="scan_beach_defenses"
        operation="scanBeachDefenses"
        inputVariable="Sector_A"
        outputVariable="ReconReport"/>
```

匹配逻辑：

```text
Commander 读取 requiredSkill=scan_beach_defenses
-> 从 Nacos 查询 serviceName=A2A-Agent 且 status=idle 的实例
-> 读取每个实例 metadata.skills
-> 只保留包含 scan_beach_defenses 的 Agent
-> 申请租约
-> 如启用 Redis，则申请分布式锁
-> 检查熔断器
-> 调用 Agent /sendMessage
```

多技能 activity 示例：

```xml
<invoke name="ComplexRecon"
        requiredSkills="scan_beach_defenses,target_identification"
        operation="complexRecon"
        inputVariable="Sector_A"
        outputVariable="ReconReport"/>
```

多技能匹配要求 Agent 同时具备所有技能：

```text
scan_beach_defenses AND target_identification
```

## 6. Commander 下发的任务格式

Commander 调用 `/sendMessage` 时，会发送类似 payload：

```json
{
  "workflow_id": "workflow-abc123",
  "workflow": "bpel",
  "workflow_mode": "remote",
  "work_item": "workflow-abc123:activatity-002-skillonlyrecon",
  "parent_work_item": "workflow-abc123:activatity-001-sequence",
  "activatity_id": "activatity-002-skillonlyrecon",
  "activatity_index": 2,
  "activatity_role": "scan_beach_defenses",
  "command": "scan_beach_defenses",
  "required_skill": "scan_beach_defenses",
  "required_skills": ["scan_beach_defenses"],
  "input": {
    "sector": "Sector_A"
  },
  "context": {
    "workflow_id": "workflow-abc123",
    "workflow_status": "running",
    "sector": "Sector_A",
    "coordinates": "120.5E, 35.1N",
    "battle_log": []
  },
  "attachments": [],
  "work_list": [],
  "output_hint": "recon_report",
  "retry_policy": {
    "max_retries": 1,
    "timeout_seconds": 5.0,
    "failure_policy": "pause"
  }
}
```

字段说明：

| 字段 | 说明 |
| --- | --- |
| `workflow_id` | 整个 workflow 的唯一 ID |
| `work_item` | 当前 activity 的唯一任务 ID，Agent 应用它做幂等 |
| `command` | 当前希望执行的命令，通常和 requiredSkill 对齐 |
| `required_skill` | 当前 activity 需要的主技能 |
| `required_skills` | 当前 activity 需要的技能列表 |
| `input` | 当前 activity 的输入数据 |
| `context` | workflow 上下文快照，只读使用 |
| `attachments` | 附件引用，只允许对象存储引用，不要内嵌二进制 |
| `output_hint` | Commander 希望 Agent 输出写入的 key |
| `retry_policy` | Commander 对该任务的重试和超时策略 |

Agent 需要重点读取：

```text
work_item
required_skill / required_skills
input
output_hint
```

## 7. Agent 返回结果规范

成功返回示例：

```json
{
  "workflow_id": "workflow-abc123",
  "work_item": "workflow-abc123:activatity-002-skillonlyrecon",
  "agent": "TerrainAnalysisAgent",
  "role": "scan_beach_defenses",
  "command": "scan_beach_defenses",
  "status": "completed",
  "output": {
    "recon_report": {
      "summary": "目标区域发现三处防御工事。",
      "targets": [
        {
          "type": "bunker",
          "coordinate": "120.5E,35.1N",
          "confidence": 0.91
        }
      ]
    }
  },
  "metrics": {
    "duration_ms": 1280,
    "algorithm": "terrain-v1",
    "confidence": 0.91
  },
  "error": null,
  "message": "scan_beach_defenses completed",
  "attempts": 1,
  "cached": false
}
```

失败返回示例：

```json
{
  "workflow_id": "workflow-abc123",
  "work_item": "workflow-abc123:activatity-002-skillonlyrecon",
  "agent": "TerrainAnalysisAgent",
  "role": "scan_beach_defenses",
  "command": "scan_beach_defenses",
  "status": "failed",
  "output": {},
  "metrics": {
    "duration_ms": 300
  },
  "error": "input image is missing",
  "error_code": "AGENT_BUSINESS_ERROR",
  "message": "input image is missing",
  "attempts": 1,
  "cached": false
}
```

### output 要求

Agent 最好使用 Commander 下发的 `output_hint` 作为输出 key。

例如：

```json
{
  "output_hint": "recon_report"
}
```

则返回：

```json
{
  "output": {
    "recon_report": "..."
  }
}
```

这样 Commander 可以稳定把结果写入 workflow context。

## 8. 幂等要求

Agent 必须把 `work_item` 当成幂等键。

原因：

```text
Commander 可能因为超时、网络抖动、failover、重试而重复发送同一个 work_item
```

要求：

```text
同一个 work_item 不要重复产生不可逆副作用
如果已经执行过，优先返回缓存结果
```

推荐内部缓存结构：

```python
task_cache = {
    "workflow-abc123:activatity-002-skillonlyrecon": {
        "status": "completed",
        "output": {...}
    }
}
```

## 9. ready / health 要求

`/health` 示例：

```json
{
  "status": "ok",
  "agent": "TerrainAnalysisAgent",
  "uptime_seconds": 120.5
}
```

`/ready` 示例：

```json
{
  "ready": true,
  "agent": "TerrainAnalysisAgent",
  "active_tasks": 0
}
```

如果 Agent 当前算法库未加载完成、模型不可用、资源不足，应返回：

```json
{
  "ready": false
}
```

同时 `/sendMessage` 应返回标准失败：

```json
{
  "status": "failed",
  "error_code": "AGENT_NOT_READY",
  "error": "agent is not ready"
}
```

## 10. 心跳和状态同步

Agent 注册到 Nacos 后应保持心跳。

建议：

```text
heartbeat interval = 5s
heartbeat metadata 保持最新 status、skills、heartbeat_ts、heartbeat_at
```

状态变化：

```text
Agent 初始注册：status=idle
Commander 租用后：Commander 会更新 status=busy
任务完成后：Commander 会释放为 status=idle
异常或熔断：Commander 可能标记为 status=unavailable
```

Agent 自己不要随意覆盖 Commander 写入的租约字段：

```text
lease_workflow_id
lease_work_item
lease_acquired_at
lease_lock_backend
lease_lock_key
```

## 11. 最小 Python 接入示例

```python
from a2a_protocol.server import A2ABaseAgent, skills_metadata
from registry.nacos_manager import NacosRegistry, get_host_ip


class TerrainAnalysisAgent(A2ABaseAgent):
    def execute_task(self, payload):
        skill = payload.get("required_skill")
        output_hint = payload.get("output_hint") or "result"
        task_input = payload.get("input", {})

        if skill == "scan_beach_defenses":
            result = run_scan_algorithm(task_input)
        elif skill == "target_identification":
            result = run_target_identification(task_input)
        else:
            raise ValueError(f"Unsupported skill: {skill}")

        return {output_hint: result}, f"{skill} completed"


def run_scan_algorithm(task_input):
    return {
        "summary": "发现疑似防御工事。",
        "input": task_input,
    }


def run_target_identification(task_input):
    return {
        "targets": [],
        "input": task_input,
    }


if __name__ == "__main__":
    skills = [
        {
            "id": "scan_beach_defenses",
            "name": "Beach Defense Scan",
            "description": "探测滩头防御和敌方阵地。",
            "tags": ["scan", "detect", "探测", "侦察"],
        },
        {
            "id": "target_identification",
            "name": "Target Identification",
            "description": "识别目标类型和威胁等级。",
            "tags": ["target", "identify", "目标识别"],
        },
    ]

    agent = TerrainAnalysisAgent(
        name="TerrainAnalysisAgent",
        description="真实地形分析 Agent",
        role="generalist",
        port=8010,
        skills=skills,
    )

    registry = NacosRegistry()
    registry.register_service(
        service_name="A2A-Agent",
        ip=get_host_ip(),
        port=8010,
        metadata={
            "agent_name": agent.name,
            "role": agent.role,
            "status": "idle",
            "version": "1.0.0",
            **skills_metadata(agent.skills),
        },
    )

    agent.start()
```

## 12. BPEL 编排示例

只按 skill 编排，不依赖固定 role：

```xml
<process name="SkillOnlyWorkflow" targetNamespace="http://a2a.test/workflow">
  <variables>
    <variable name="ReconReport" type="String"/>
  </variables>

  <sequence>
    <invoke name="SkillOnlyRecon"
            requiredSkill="scan_beach_defenses"
            operation="scanBeachDefenses"
            inputVariable="Sector_A"
            outputVariable="ReconReport"/>
  </sequence>
</process>
```

多技能 activity：

```xml
<invoke name="ComplexRecon"
        requiredSkills="scan_beach_defenses,target_identification"
        operation="complexRecon"
        inputVariable="Sector_A"
        outputVariable="ReconReport"/>
```

## 13. 接入检查清单

接入前请确认：

- Agent 能启动 HTTP 服务。
- `/.well-known/agent-card` 能返回 `skills`。
- Agent 能注册到 Nacos，服务名为 `A2A-Agent`。
- Nacos metadata 中有 `status=idle`。
- Nacos metadata 中有 `skills`，且包含 BPEL 的 `requiredSkill`。
- `/sendMessage` 能处理 Commander payload。
- 返回结果包含 `workflow_id`、`work_item`、`status`、`output`。
- 输出 key 尽量使用 `output_hint`。
- 同一个 `work_item` 重复调用时能幂等返回。
- Agent 算法不可用时返回 `AGENT_NOT_READY` 或标准失败。

## 14. 常见错误

### 1. Agent 有 role 但没有 skills

现在 Commander 只按 skill 匹配。只有：

```json
{
  "role": "recon",
  "status": "idle"
}
```

不会被选中。必须有：

```json
{
  "status": "idle",
  "skills": "scan_beach_defenses"
}
```

### 2. BPEL requiredSkill 和 Agent skill id 不一致

BPEL：

```xml
requiredSkill="scan_beach_defense"
```

Agent：

```json
"skills": "scan_beach_defenses"
```

这两个不完全一致，可能无法匹配。建议统一维护 skill id 表。

### 3. output 没有使用 output_hint

如果 Commander 下发：

```json
"output_hint": "recon_report"
```

Agent 却返回：

```json
"output": {
  "result": "..."
}
```

Commander 仍可能兜底取第一个值，但不推荐。最好直接返回：

```json
"output": {
  "recon_report": "..."
}
```

### 4. 没有幂等处理

如果 Commander 重试同一个 `work_item`，Agent 不应重复执行不可逆操作。必须缓存或识别重复任务。

## 15. 推荐分工

Commander 侧负责：

```text
BPEL 编排
skills 匹配
Nacos 发现
租约和分布式锁
failover
checkpoint
workflow context
```

真实 Agent 侧负责：

```text
声明 skills
注册 Nacos
维护自身 ready/health
接收 /sendMessage
根据 requiredSkill 调用自身算法库
返回标准 task response
保证 work_item 幂等
```

这样各业务 Agent 可以独立演进算法库，Commander 只基于统一协议进行编排和调度。
