# Agent Runtime / SDK 接入说明

## 1. 目标

真实 Agent 接入 crowd 模式时，不应该重复实现 TaskPool / Supervisor 协议。

推荐接入方式是：

```text
业务 Agent 只实现 execute_task(payload)
框架 Agent Runtime 负责注册、心跳、claim、续租、提交结果、错误包装和指标采集
```

这样下游 Agent 只关心自己的算法、模型、工具调用或业务逻辑，不需要理解完整调度控制面。

## 2. Crowd Agent 的运行职责

在 crowd 模式里，一个 Agent 不只是被动 HTTP 服务，而是主动 worker。

Agent Runtime 需要负责：

```text
1. 启动 HTTP 服务，暴露 health / ready / metrics 等接口。
2. 注册到 Supervisor。
3. 周期性上报 heartbeat、ready、resources、active_tasks。
4. 周期性向 TaskPool 请求 claim-next。
5. claim 成功后执行 task payload。
6. 长任务执行期间自动 renew claim lease。
7. 执行完成后 submit result 到 TaskPool。
8. 执行异常时包装 failed result 或记录 last_error。
9. 进程退出时停止 worker loop。
```

## 3. 最小接入示例

```python
from a2a_protocol.server import A2ABaseAgent


class TargetDetectAgent(A2ABaseAgent):
    def execute_task(self, payload):
        input_data = payload.get("input", {})
        image_uri = input_data.get("image_uri")

        # 这里替换成真实模型、算法或工具调用。
        result = run_target_detection(image_uri)

        return {
            "target_report": result,
        }, "target detection completed"


if __name__ == "__main__":
    agent = TargetDetectAgent(
        name="TargetDetect_Agent",
        description="Detect targets from reconnaissance images.",
        role="target_detection",
        port=18020,
        agent_id="target-detect-01",
        crowd_worker_enabled=True,
        crowd_claim_interval=1.0,
    )
    agent.start()
```

## 4. 必要环境变量

Agent 进程需要知道 Supervisor 和 TaskPool 的局域网地址：

```bash
export A2A_SUPERVISOR_URL=http://127.0.0.1:8030
export A2A_TASK_POOL_URL=http://127.0.0.1:8040
export A2A_SUPERVISOR_REQUIRED=true
export A2A_CROWD_WORKER_ENABLED=true
export A2A_CROWD_CLAIM_INTERVAL=1
```

如果启用鉴权：

```bash
export A2A_SUPERVISOR_AUTH_TOKEN=supervisor-token
export A2A_TASK_POOL_AUTH_TOKEN=task-pool-token
export A2A_AUTH_TOKEN=a2a-agent-token
```

## 5. Agent 注册信息

Agent Runtime 会向 Supervisor 注册：

```json
{
  "agent_id": "target-detect-01",
  "name": "TargetDetect_Agent",
  "role": "target_detection",
  "endpoint": "http://127.0.0.1:18020",
  "skills": ["target_identification"],
  "ready": true,
  "active_tasks": 0,
  "max_concurrency": 1,
  "resources": {
    "resource_state": "ok",
    "system": {
      "cpu_percent": 20.0,
      "memory_percent": 50.0
    }
  }
}
```

Supervisor 会用这些信息判断 Agent 是否能领取任务。

## 6. Task Payload 约定

Agent 的 `execute_task(payload)` 会收到 Commander 发布的 activity task。

常见字段：

```json
{
  "workflow_id": "workflow-abc",
  "work_item": "workflow-abc:activity-002-targetdetect",
  "activity_id": "activity-002-targetdetect",
  "activity_skill": "target_identification",
  "required_skills": ["target_identification"],
  "input": {
    "image_uri": "/data/recon/image-001.png"
  },
  "output_hint": "target_report",
  "retry_policy": {
    "max_retries": 1,
    "timeout_seconds": 30,
    "failure_policy": "pause"
  },
  "completionPolicy": {
    "type": "first_success"
  },
  "resource_requirements": {
    "min_gpu_count": 1,
    "min_gpu_vram_gb": 8
  }
}
```

Agent 应重点关注：

```text
input：业务输入
activity_skill：任务技能
output_hint：建议输出字段名
workflow_id / work_item：用于日志和排障
```

## 7. Result 返回约定

`execute_task()` 返回：

```python
return output, message
```

其中 `output` 应该是 dict：

```python
{
    "target_report": {
        "targets": [
            {"type": "vehicle", "confidence": 0.92}
        ]
    }
}
```

Agent Runtime 会自动包装成标准 task response：

```json
{
  "workflow_id": "workflow-abc",
  "work_item": "workflow-abc:activity-002-targetdetect",
  "agent": "TargetDetect_Agent",
  "role": "target_detection",
  "status": "completed",
  "output": {
    "target_report": {
      "targets": []
    }
  },
  "metrics": {
    "duration_ms": 1234.5
  },
  "message": "target detection completed"
}
```

## 8. 失败处理建议

业务失败和系统失败要区分。

推荐错误分类：

```text
AGENT_BUSINESS_ERROR：业务上无法完成，例如输入图片无目标。
AGENT_PROTOCOL_ERROR：payload 或 result schema 不合法。
AGENT_TIMEOUT：算法或外部依赖超时。
AGENT_UNAVAILABLE：Agent 内部依赖不可用。
AGENT_RESOURCE_EXHAUSTED：资源不足，例如 GPU 显存不足。
```

后续可以在 Agent Runtime 中统一提供异常包装工具，让业务 Agent 抛异常即可。

## 9. 独立进程 E2E

当前项目提供了独立 Agent 进程版 demo：

```bash
python3 scripts/demo_crowd_service_mode.py --agent-processes --timeout 10 --claim-interval 0.05
```

它会启动：

```text
Supervisor 进程
TaskPool 进程
Recon Agent 进程
Artillery Agent 进程
Assault Agent 进程
Commander workflow
```

这个 demo 更接近真实部署形态，真实 Agent 接入前建议优先跑通这一条链路。

## 10. TaskPool 事件查询

TaskPool 会记录任务生命周期事件，便于后续接监控、审计和 Dashboard。

当前事件包括：

```text
task.created
task.claimed
task.completed
task.failed
```

服务化 TaskPool 可以通过 HTTP 查询：

```bash
curl -H "Authorization: Bearer ${A2A_TASK_POOL_AUTH_TOKEN}" \
  "http://127.0.0.1:8040/events?workflow_id=workflow-abc"
```

SDK 内部也可以通过 `TaskPoolClient.list_events()` 查询：

```python
events = task_pool.list_events(
    workflow_id="workflow-abc",
    event_type="task.completed",
)
```

## 11. completionPolicy

`completionPolicy` 用来描述一个 TaskPool task 在多人 claim 时什么时候算完成。

当前支持：

```text
first_success：默认策略，首个成功结果即完成。
wait_all：等待所有 claim 都返回，全部成功才完成。
min_results：达到指定成功结果数即完成。
majority_vote：按 max_claims 计算多数阈值，目前作为 quorum 策略使用。
best_score：等待所有 claim 返回后，从成功结果里选 score 最高的响应。
```

示例：

```json
{
  "completionPolicy": {
    "type": "min_results",
    "min_results": 2
  }
}
```

## 12. TaskPool 存储抽象

TaskPool 业务逻辑现在依赖 `TaskPoolStateStore`，默认实现是 `JsonFileTaskPoolStateStore`。

这意味着后续替换 Redis / DB 时，优先实现新的 state store，而不是改 publish / claim / submit_result 的调度逻辑。

当前已有：

```text
JsonFileTaskPoolStateStore：默认 JSON 文件存储，适合 demo 和单机验证。
InMemoryTaskPoolStateStore：进程内存存储，适合单测和嵌入式 demo。
```

## 13. 接入 Checklist

```text
1. 定义 Agent role 和 skills。
2. 继承 A2ABaseAgent。
3. 实现 execute_task(payload)。
4. 确保 output 字段符合 output_hint 或约定 schema。
5. 设置 Supervisor / TaskPool URL。
6. 设置鉴权 token。
7. 启动 Agent，确认 Supervisor /agents 能看到 online + ready。
8. 运行 crowd workflow，确认 Agent 能 claim 和 submit result。
9. 查看 /metrics 和 TaskPool task 状态。
10. 通过 TaskPool /events 查询任务生命周期事件。
11. 按任务并发语义设置 completionPolicy。
12. 补充真实 Agent 的单元测试和 E2E 测试。
```
