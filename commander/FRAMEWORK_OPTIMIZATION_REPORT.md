# A2A 多 Agent 框架优化汇报

## 1. 优化目标

本轮优化的重点不是新增具体业务算法，而是把当前项目从“流程能跑的演示系统”提升为“便于接入真实 Agent 和多人代码的框架底座”。

优化前，Commander 已经可以基于 BPEL 推进流程，也有 Nacos 注册发现、心跳、租约、checkpoint 和恢复能力。但仍存在几个框架层问题：

- Agent 返回结果没有真正进入 workflow context，Commander 里仍有较多模拟写死结果。
- `/sendMessage` 返回格式不统一，不利于后续接入不同同学的 Agent。
- 错误、超时、重试策略较弱，失败后主要依赖暂停流程。
- Agent 生命周期观测不足，只能通过 Nacos metadata 间接判断。
- 日志以 `print` 为主，缺少结构化事件和 workflow trace。
- Manager API 只能看 workflow 和 leases，缺少 agents、trace、work-list、checkpoint 等状态面。
- BPEL 里 activity 命名存在 `activatity` 拼写问题，且缺少 retry/timeout/failurePolicy 等执行语义。

本轮优化围绕四个方向展开：

```text
可接入性：统一任务响应，Agent output 写入 workflow context
稳定性：重试、超时、失败策略、Agent ready 状态
可观测性：结构化日志、workflow trace、Manager 状态 API
兼容性：修正 activity 命名，同时兼容旧 checkpoint 字段
```

## 2. 标准化 A2A 任务响应

新增文件：

```text
a2a_protocol/messages.py
```

新增统一任务响应信封：

```json
{
  "workflow_id": "wf-001",
  "work_item": "wf-001:activatity-003-suppressbeachsector",
  "agent": "Artillery_Agent",
  "role": "artillery",
  "command": "suppress_beach_sector_A",
  "status": "completed",
  "output": {
    "strike_result": "Suppression barrage executed on 120.5E, 35.1N."
  },
  "metrics": {
    "latency_ms": 12.3
  },
  "error": null,
  "message": "Artillery_Agent completed command=suppress_beach_sector_A",
  "attempts": 1,
  "cached": false
}
```

这个格式解决的问题：

- Agent 返回结果统一放入 `output`。
- Commander 可以统一判断 `status/error`。
- 每次调用可以携带 `metrics`，便于后续统计耗时、成功率。
- 失败响应也统一，不再只靠异常字符串。

## 3. Agent 返回结果进入 Workflow Context

优化前，Commander 在 `apply_agent_result` 里写死结果，例如：

```python
context["recon_report"] = "Sector_A is heavily fortified..."
context["strike_result"] = "Suppression barrage executed..."
context["eval_score"] = 40
```

优化后，Commander 优先读取 Agent 标准响应：

```text
agent_results[work_item].output
```

然后根据 `output_hint` 写入 workflow context：

```text
recon_report
strike_result
eval_score
assault_result
```

如果某个旧 Agent 还没有返回标准 `output`，Commander 会保留旧的模拟值作为兜底，保证已有 demo 不被破坏。

新增 context 字段：

```json
{
  "agent_results": {
    "wf-001:activatity-003-suppressbeachsector": {
      "status": "completed",
      "output": {
        "strike_result": "..."
      }
    }
  }
}
```

并发场景下，同一个 work_item 可能由多个同类型 Agent 返回结果，框架会聚合到：

```json
{
  "parallel_results": [
    {"agent": "Artillery_Agent_1", "output": {}},
    {"agent": "Artillery_Agent_2", "output": {}}
  ]
}
```

## 4. Agent 生命周期管理

在 `A2ABaseAgent` 中新增生命周期与观测接口：

```text
GET  /health
GET  /ready
POST /lifecycle/ready
GET  /metrics
```

### /health

用于判断进程是否存活：

```json
{
  "status": "ok",
  "agent": "Recon_Agent",
  "role": "recon",
  "uptime_seconds": 51.2
}
```

### /ready

用于判断 Agent 是否可接任务：

```json
{
  "ready": true,
  "agent": "Recon_Agent",
  "role": "recon",
  "active_tasks": 0
}
```

### /lifecycle/ready

用于手动切换 Agent 接任务状态，后续可用于宕机/维护/过载模拟：

```bash
curl -X POST http://127.0.0.1:8012/lifecycle/ready \
  -H 'Content-Type: application/json' \
  -d '{"ready": false}'
```

当 Agent `ready=false` 时，`/sendMessage` 会返回标准失败响应，`/sendMessageStream` 会返回 503。

### /metrics

用于查看 Agent 运行指标：

```json
{
  "tasks_received": 3,
  "tasks_completed": 3,
  "tasks_failed": 0,
  "stream_requests": 1,
  "cache_hits": 0,
  "active_tasks": 0,
  "last_error": null,
  "last_work_item": "wf-001:activatity-003-suppressbeachsector",
  "agent": "Artillery_Agent",
  "role": "artillery",
  "ready": true,
  "uptime_seconds": 122.4
}
```

## 5. 错误处理、超时与重试策略

Commander 新增框架级参数：

```bash
--max-retries 1
--retry-backoff 0.2
--request-timeout 5
```

含义：

- `max_retries`：每个候选 Agent 失败后最多重试次数。
- `retry_backoff`：重试等待基准时间。
- `request_timeout`：A2A discover/auth/sendMessage 的 HTTP 超时。

远程调用流程现在会记录：

```text
agent_call_attempt
agent_call_completed
agent_call_failed
```

BPEL activity 也支持在 XML 上配置执行策略：

```xml
<invoke partnerLink="ArtilleryAgent"
        operation="suppressBeachSector"
        dispatchMode="parallel"
        retryCount="2"
        timeoutSeconds="5"
        failurePolicy="pause"/>
```

当前支持的 `failurePolicy`：

```text
pause：默认策略，失败后 workflow 暂停
skip：失败后跳过该 activity，继续后续流程
fail：失败后向上抛错，流程失败
```

## 6. Workflow Trace 与结构化日志

新增文件：

```text
observability.py
```

新增两类观测能力：

### 结构化日志

运行时会输出统一事件：

```text
[A2A_EVENT] {"event_type": "agent_call_attempt", "workflow_id": "...", ...}
```

相比普通 print，这类日志更适合后续做检索、统计和展示。

### Workflow Trace

每个 workflow context 中新增：

```json
{
  "trace": [
    {
      "ts": "2026-06-07T...",
      "event_type": "workflow_started",
      "workflow_id": "wf-001"
    },
    {
      "ts": "2026-06-07T...",
      "event_type": "activity_status_changed",
      "activity_id": "activatity-003-suppressbeachsector",
      "status": "running"
    },
    {
      "ts": "2026-06-07T...",
      "event_type": "agent_result_applied",
      "role": "artillery"
    }
  ]
}
```

trace 会随 checkpoint 一起保存，因此 workflow 恢复后仍可查看历史执行过程。

## 7. Manager API 增强

原有 Manager 已支持：

```text
GET  /health
GET  /workflows
POST /workflows
GET  /workflows/{workflow_id}
POST /workflows/{workflow_id}/resume
GET  /leases
```

本轮新增：

```text
GET /agents
GET /workflows/{workflow_id}/checkpoint
GET /workflows/{workflow_id}/work-list
GET /workflows/{workflow_id}/trace
```

### /agents

查看当前 Nacos 中可用 Agent 实例。

### /workflows/{workflow_id}/work-list

查看 BPEL activity 执行状态：

```json
{
  "workflow_id": "wf-001",
  "work_list": [
    {
      "activity_id": "activatity-002-scanbeachdefenses",
      "role": "recon",
      "status": "completed"
    }
  ]
}
```

### /workflows/{workflow_id}/trace

查看 workflow 事件流，便于调试、展示、复盘。

### /workflows/{workflow_id}/checkpoint

单独查看完整 checkpoint，便于恢复和问题定位。

## 8. BPEL 动态加载与并发派发

本周还把流程定义从代码里的固定顺序，进一步抽象为可动态加载的 BPEL 文件。Commander 不再只能按写死的 `recon -> artillery -> evaluator -> assault` 逻辑推进，而是可以在启动时选择不同的 `.bpel` 文件，由解析器把 XML 流程转换成内部 `work_list` 后执行。

### 8.1 动态加载 `.bpel`

相关实现文件：

```text
bpel_workflow.py
commander_agent/main.py
```

`BPELWorkflowCatalog` 会扫描项目根目录和 `workflows/` 目录下的 `.bpel` 文件：

```python
class BPELWorkflowCatalog:
    def __init__(self, project_root: str | Path):
        root = Path(project_root).resolve()
        self.search_dirs = [root / "workflows", root]

    def discover(self) -> list[Path]:
        ...

    def load(self, workflow_ref: str | None = None) -> BPELWorkflowDefinition:
        ...
```

加载时支持多种引用方式：

```text
文件路径
文件名
文件 stem
BPEL process name
```

例如下面几种都可以定位同一个工作流：

```bash
--workflow-file beachhead_workflow
--workflow-file beachhead_workflow.bpel
--workflow-file BeachheadAssaultWorkflow
```

Commander 初始化时会根据 `workflow_file` 动态加载：

```python
self.workflow_catalog = BPELWorkflowCatalog(PROJECT_ROOT)

if self.workflow == "bpel" or self.workflow_file:
    self.bpel_definition = self.workflow_catalog.load(self.workflow_file)
    self.workflow = "bpel"
```

这样做的价值是：同一套 Commander 框架可以运行多套预案，不需要为了换流程修改 Python 代码。

### 8.2 XML 节点转换为 work_list

BPEL 解析器会读取 XML 节点，并把它们转换成内部 activity 对象，再生成 `work_list`。

目前支持的主要节点包括：

```text
sequence   顺序执行
invoke     调用 Agent
assign     变量赋值
switch     条件分支
case       条件分支
otherwise  默认分支
throw      抛出故障
flow       并行块
```

例如 BPEL 中的：

```xml
<invoke partnerLink="ArtilleryAgent"
        operation="suppressBeachSector"
        dispatchMode="parallel"/>
```

会被解析成类似下面的 work_list 项：

```json
{
  "activity_id": "activatity-004-suppressbeachsector",
  "type": "invoke",
  "role": "artillery",
  "operation": "suppressBeachSector",
  "command": "suppress_beach_sector_A",
  "dispatch_mode": "parallel",
  "status": "pending"
}
```

`work_list` 是 workflow 执行过程中的步骤清单。后续每个 activity 执行时，状态会在下面几种状态之间流转：

```text
pending -> running -> completed
pending -> running -> failed
pending -> skipped
```

### 8.3 dispatchMode="parallel" 的并发派发

这里需要区分两个并发层次：

```text
Manager 负责多条 Workflow 并发
CommanderAgent 负责单条 Workflow 内部的 Agent 并发
```

也就是说，`dispatchMode="parallel"` 不是 Manager 直接派发，而是某条 workflow 内部的 `CommanderAgent` 在执行到这个 activity 时并发派发。

核心逻辑在 `CommanderAgent._execute_bpel_invoke`：

```python
payload, stream = self._build_bpel_task_payload(activatity, context)
if activatity.dispatch_mode == "parallel":
    success = self.delegate_parallel_task(activatity.role, payload, stream=stream)
else:
    success = self.delegate_task(activatity.role, payload, stream=stream)
```

如果是远程模式并启用了租约管理，最终会进入：

```python
_delegate_parallel_task_with_lease()
```

该函数会先领取多个同类型空闲 Agent 的租约：

```python
leases = self.lease_manager.acquire_all(role_needed, self.workflow_id, work_item)
```

然后使用线程池并发调用这些已经领取到租约的 Agent：

```python
with ThreadPoolExecutor(
    max_workers=min(self.max_workers, len(leases)),
    thread_name_prefix=f"a2a-{role_needed}",
) as executor:
    futures = {
        executor.submit(
            self._delegate_leased_candidate,
            lease,
            role_needed,
            task_payload,
            stream,
        ): lease.target
        for lease in leases
    }
```

每个并发调用结束后都会在 `finally` 中释放租约，避免 Agent 长期卡在 `busy` 状态。

### 8.4 控制参数

BPEL 动态加载与并发派发涉及两个主要参数：

```text
--max-workflows
  控制 Manager 同时推进多少条 workflow。

--max-workers
  控制单条 workflow 内部，一个 parallel activity 最多并发调用多少个 Agent。
```

所以二者不是一回事：

```text
max_workflows = 流程级并发
max_workers   = 单个 activity 内部的 Agent 调用并发
```

### 8.5 当前已有 BPEL 示例

项目中已经有三套可动态加载的 BPEL：

```text
beachhead_workflow.bpel
  基础抢滩流程，炮兵阶段并发。

reinforced_beachhead_workflow.bpel
  强化抢滩流程，侦察、炮兵、突击阶段都可并发。

quick_strike_workflow.bpel
  快速突击流程，省略评估分支。
```

可以通过以下命令查看：

```bash
python commander_agent/main.py --list-workflows
```

也可以指定某个 BPEL 运行：

```bash
python commander_agent/main.py \
  --mode local \
  --workflow bpel \
  --workflow-file quick_strike_workflow
```

### 8.6 本功能的价值

这一部分让框架从“代码写死流程”变成了“流程文件驱动执行”：

```text
1. 流程预案可以通过 .bpel 文件切换。
2. 同一个 Commander 可以运行不同任务链路。
3. activity 的顺序、分支、并发属性可以由 XML 描述。
4. work_list 可以被 Manager API 查询，便于展示流程进度。
5. dispatchMode="parallel" 让同类型 Agent 可以协同执行同一阶段任务。
```

后续如果要接入新的作战链路或算法链路，可以优先新增 BPEL 文件，而不是修改 Commander 主流程代码。

## 9. Activity 命名兼容与修正

历史代码中使用了拼写错误字段：

```text
activatity_id
activatity_index
current_activatity
active_activatities
workflow_activatity
```

本轮没有直接删除旧字段，因为旧 checkpoint 和现有测试仍依赖这些字段。采用兼容迁移策略：

```text
保留旧字段：保证旧流程、旧 checkpoint 可恢复
新增新字段：activity_id、activity_index、current_activity、active_activities、workflow_activity
保存 checkpoint 时自动同步新旧字段
读取 checkpoint 时同时识别新旧字段
```

这样后续新代码可以逐步切换到正确的 `activity` 命名，而不会破坏已有数据。

## 10. 本轮修改的主要文件

```text
a2a_protocol/messages.py          标准任务响应信封
a2a_protocol/server.py            Agent 生命周期、metrics、统一响应
a2a_protocol/client.py            HTTP 错误、失败响应、超时
local_runtime.py                  Local 模式统一响应格式
commander_agent/main.py           结果流转、重试、trace、activity 兼容
commander_agent/workflow_manager.py Manager 状态聚合
commander_agent/manager_api.py    新增 agents/trace/work-list/checkpoint API
bpel_workflow.py                  activity 别名、retry/timeout/failurePolicy 解析
observability.py                  结构化日志和 workflow trace
```

## 11. 验证情况

已完成：

```bash
python -m compileall a2a_protocol commander_agent local_runtime.py bpel_workflow.py observability.py workflow_state_store.py
```

结果：语法编译通过。

当前环境执行完整 local workflow 时仍有依赖阻塞：

```text
ModuleNotFoundError: No module named 'nacos'
```

原因是当前 shell 使用的 Python 环境未安装 `requirements.txt` 中的 `nacos-sdk-python`，而 `commander_agent/main.py` 顶层会导入 `registry.nacos_manager`。这不是本轮代码语法问题，而是运行环境依赖未安装。后续在正确虚拟环境中安装依赖后可继续跑完整回归。

## 12. 明天组会可以这样总结

本轮我主要没有继续堆具体业务 Agent，而是把框架底座做了工程化增强：

```text
1. 统一了 Agent 响应格式，所有 Agent 以后都通过 output 返回结果。
2. Commander 已经能把 Agent output 写入 workflow context，不再只依赖写死模拟值。
3. 增加了重试、超时、失败策略，让远程调用更稳定。
4. 增加了 Agent 生命周期接口和 metrics，便于后续做宕机和过载模拟。
5. 增加了结构化日志和 workflow trace，便于调试、展示和复盘。
6. Manager API 增加 agents、work-list、trace、checkpoint 状态面。
7. 修正 activity 命名问题，同时兼容旧 checkpoint。
```

下一步建议：

```text
1. 先修运行环境依赖，保证所有测试能在统一 venv 中通过。
2. 再把 start_agents.sh 的硬编码路径去掉，提升可运行性。
3. 然后在现有框架上接入新的能力型 Agent 和算法库。
```
