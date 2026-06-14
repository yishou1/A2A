# A2A Agent 宕机恢复与统一接入说明

这份文档用于向老师汇报当前框架的 Agent 宕机恢复能力，也用于后续其他同学实现具体 Agent 时统一接入，保证代码合并后仍然能复用这套恢复机制。

## 1. 背景与目标

当前项目是一个 A2A 多智能体工作流框架。Commander 负责 workflow 编排，具体业务 Agent 由不同同学实现，例如侦察 Agent、火力 Agent、评估 Agent、突击 Agent 等。

多人协作后必须解决的问题是：

```text
如果某个 Agent 进程宕机、端口不可达、请求超时、ready=false，或者任务运行过程中突然停止心跳，Commander 后续应该怎么处理？
```

本轮完善目标：

```text
1. Commander 不把单个 Agent 宕机直接等同于整个 workflow 失败。
2. 同 role 下还有其他 idle Agent 时，自动重新指派同一个 work_item。
3. 宕机 Agent 会被标记为 unavailable，从可调度池隔离。
4. 运行中的 Agent 如果心跳丢失，也会触发自动切换。
5. 迟到返回的旧 Agent 结果不会覆盖备用 Agent 的结果。
6. trace、metadata、checkpoint 都保留恢复过程，便于汇报和排查。
7. 给后续 Agent 实现者一套统一接入规范。
```

## 2. 当前实现结论

现在框架已经支持 Agent 宕机后的自动重新指派，并且包含两类故障触发方式。

第一类是调用时立即失败：

```text
connection refused
connection reset
timeout
503 service unavailable
agent is not ready
requests.exceptions.RequestException
```

第二类是任务运行中断心跳：

```text
Commander 已经把任务派给某个 Agent
  -> 远程调用还没有返回
  -> Commander 按 A2A_LEASE_HEARTBEAT_CHECK_INTERVAL 定期检查该租约对应实例
  -> 如果 Nacos 中该实例 heartbeat_ts 超过 grace 时间没有刷新
  -> Commander 判定 heartbeat lost
  -> 当前 Agent 被释放为 unavailable
  -> 同一个 work_item 自动切换给下一个同 role 的 idle Agent
```

完整恢复流程：

```text
Commander 需要调用 role=recon
  -> 从 Nacos/Registry 查询 role=recon 且 status=idle 的实例
  -> AgentLeaseManager 领取一个实例租约，并把它标记为 busy
  -> Commander 调用该 Agent，同时启动租约心跳 watcher
  -> 如果调用失败或 watcher 检测到心跳丢失
  -> 释放租约时把该实例标记为 unavailable
  -> 记录 agent_heartbeat_lost / agent_marked_unavailable / agent_failover_reassigning
  -> 继续查找同 role 的下一个 idle Agent
  -> 备用 Agent 完成同一个 work_item
  -> 结果写入 workflow context 和 checkpoint
```

注意：这套机制负责“任务切换”和“故障隔离”。它不负责自动重启已经宕机的进程；进程级重启仍建议交给 Docker、systemd、Kubernetes、supervisor 或启动脚本。

## 3. 关键代码实现

### 3.1 Agent 心跳机制

文件：

```text
registry/nacos_manager.py
```

已实现能力：

```text
1. register_service() 默认给 metadata 写入 heartbeat_ts 和 heartbeat_at。
2. AgentHeartbeatSupervisor 后台线程会按 A2A_HEARTBEAT_INTERVAL 定期发送心跳。
3. 默认心跳间隔是 5s：
   A2A_HEARTBEAT_INTERVAL=5
4. 默认心跳宽限时间是 max(12s, heartbeat_interval * 2 + 2)：
   A2A_HEARTBEAT_GRACE_SECONDS
5. discover_service() 会过滤掉 heartbeat_ts 过期的实例。
6. find_instance() 可以查询某个具体实例的最新注册表快照。
7. is_instance_fresh() 对外提供健康判断。
```

这次额外修正了一个关键点：

```text
心跳线程发送 heartbeat 前会先合并 Nacos 中该实例的最新 metadata。
这样 Agent 持续心跳时只刷新 heartbeat_ts/heartbeat_at，
不会把 Commander 标记的 busy/unavailable/lease_* 状态覆盖回旧的 idle。
```

### 3.2 租约管理

文件：

```text
commander_agent/agent_leases.py
```

已实现能力：

```text
acquire_one()   领取一个同 role idle Agent，并标记 busy
acquire_all()   并发场景领取多个同 role idle Agent
release()       成功时释放为 idle，失败时可释放为 unavailable
is_current()    判断租约是否仍然是当前有效租约
latest_instance() 查询租约对应实例的最新注册表状态
is_lease_fresh() 判断当前租约对应 Agent 心跳是否仍然新鲜
```

释放规则：

```text
Agent 成功完成任务
  -> release(status="idle")
  -> 清理 lease_* 和 unavailable_* 字段

Agent 宕机、不可达、超时、断心跳
  -> release(status="unavailable")
  -> 写入 unavailable_reason / unavailable_workflow_id / unavailable_work_item / unavailable_at
  -> 清理 lease_* 字段
```

### 3.3 Commander 主动切换

文件：

```text
commander_agent/main.py
```

核心机制：

```text
_delegate_task_with_lease()
  -> 获取 Agent 租约
  -> 调用 _delegate_leased_candidate()

_delegate_leased_candidate()
  -> 后台线程执行真实 A2A HTTP 调用
  -> 主线程按 A2A_LEASE_HEARTBEAT_CHECK_INTERVAL 检查租约心跳
  -> 调用先返回：按调用结果释放租约
  -> 心跳先丢失：标记 unavailable，并返回失败给外层调度

_delegate_task_with_lease()
  -> 看到失败后记录 agent_failover_reassigning
  -> 继续 acquire_one() 下一个同 role idle Agent
```

默认租约心跳检查间隔：

```text
A2A_LEASE_HEARTBEAT_CHECK_INTERVAL=1
```

为了防止旧 Agent 在切换后又迟到返回，结果写入前会再次检查：

```text
租约仍然是当前租约
且
该租约对应实例心跳仍然新鲜
```

如果不满足，Commander 记录：

```text
agent_late_response_ignored
```

并拒绝把旧结果写入 workflow context。

### 3.4 并发派发降级完成

对于 BPEL 中的 parallel activity，例如多个 artillery Agent 同时执行：

```text
1. 某个实例宕机，会被标记为 unavailable。
2. 如果至少一个同 role Agent 成功，且失败都是实例不可用类错误，activity 可以继续视为成功。
3. trace 记录 agent_parallel_degraded。
4. 如果失败是业务错误，而不是实例不可用，仍然会按失败处理。
```

## 4. 演示脚本

新增脚本：

```text
scripts/demo_agent_failover_reassignment.py
```

脚本不依赖真实 Nacos 和真实 HTTP Agent，而是用内存中的 `DemoRegistry` 模拟两个同 role Agent，并走真实的 Commander 调度、租约和 failover 逻辑。

模拟对象：

```text
Recon_Primary  10.0.0.11:8012  role=recon  status=idle
Recon_Backup   10.0.0.12:8012  role=recon  status=idle
```

脚本包含两个演示场景。

### 4.1 场景一：调用时发现主 Agent 宕机

故障模拟：

```text
Recon_Primary 调用时返回 connection refused
```

预期恢复：

```text
Recon_Primary -> status=unavailable
Recon_Backup  -> 接收重新指派任务并完成
workflow context 写入 Backup 返回结果
checkpoint 写入 /tmp/a2a-agent-failover-demo-state/demo-agent-failover.json
```

### 4.2 场景二：运行中突然断心跳

故障模拟：

```text
Recon_Primary 已经接到任务
远程调用还没有返回
heartbeat_ts 被模拟成过期
Commander 的 active lease heartbeat watcher 检测到 heartbeat lost
```

预期恢复：

```text
Commander 不等待原调用自然超时
Recon_Primary -> status=unavailable，原因是 heartbeat lost
Recon_Backup  -> 接收同一个 work_item 并完成
Recon_Primary 后续迟到返回不会覆盖 Backup 的结果
trace 中出现 agent_heartbeat_lost 和 agent_failover_reassigning
```

## 5. 演示运行命令

在项目根目录运行：

```bash
cd /home/wyw/A2A/A2A
conda run -n Agent python -u scripts/demo_agent_failover_reassignment.py --reset
```

如果当前机器路径显示为 `/home/yl/yl/wyw/A2A/A2A`，也可以在该真实路径下运行；两个路径指向的是同一份项目工作区。

## 6. 演示输出讲解重点

### PHASE 1：初始注册表

两个 Agent 都是 `idle`：

```json
[
  {
    "agent": "Recon_Primary",
    "address": "10.0.0.11:8012",
    "role": "recon",
    "status": "idle"
  },
  {
    "agent": "Recon_Backup",
    "address": "10.0.0.12:8012",
    "role": "recon",
    "status": "idle"
  }
]
```

可以说明：

```text
这里模拟 Nacos 中存在两个同类型侦察 Agent，Commander 可以从同 role 候选池中选择。
```

### PHASE 2：主 Agent 失败

调用失败场景的关键输出：

```text
[LEASE] demo-agent-failover acquired recon at 10.0.0.11:8012
[DOWN] Recon_Primary is down; simulated connection refused.
[LEASE] Released recon at 10.0.0.11:8012 as unavailable
```

断心跳场景的关键输出：

```text
[HEARTBEAT LOST] Recon_Primary stops heartbeating while task is running.
[LEASE] Released recon at 10.0.0.11:8012 as unavailable
[WARN] Candidate 10.0.0.11:8012 failed: heartbeat lost for 10.0.0.11:8012
```

可以说明：

```text
Commander 先领取主 Agent 的租约。发现调用失败或运行中断心跳后，没有直接终止 workflow，
而是将该实例释放为 unavailable。
```

### PHASE 3：切换备用 Agent

关键输出：

```text
[LEASE] demo-agent-failover acquired recon at 10.0.0.12:8012
[RECOVERED] Recon_Backup completed after heartbeat-triggered reassignment.
[LEASE] Released recon at 10.0.0.12:8012
```

可以说明：

```text
主 Agent 不可用后，Commander 自动选择同 role 的备用 Agent，并执行同一个 work_item。
```

### PHASE 4：恢复后的注册表

关键输出：

```json
[
  {
    "agent": "Recon_Primary",
    "status": "unavailable",
    "unavailable_reason": "heartbeat lost for 10.0.0.11:8012"
  },
  {
    "agent": "Recon_Backup",
    "status": "idle"
  }
]
```

可以说明：

```text
宕机实例已经从 idle 调度池中移除，后续任务不会继续选中它。
```

### PHASE 5：trace 事件

重点 trace：

```text
agent_call_failed
agent_heartbeat_lost
agent_marked_unavailable
agent_failover_reassigning
agent_late_response_ignored
agent_result_applied
```

可以说明：

```text
恢复过程不是黑盒，trace 里能看到故障检测、隔离、重新指派、迟到结果忽略和最终结果应用。
```

## 7. 自动化测试内容

### 7.1 单任务自动切换

文件：

```text
tests/test_bpel_workflow.py
```

测试点：

```text
第一个 recon Agent connection refused
Commander 标记它 unavailable
Commander 自动调用第二个 recon Agent
任务最终成功
```

### 7.2 带租约的自动切换

测试点：

```text
Commander 使用 AgentLeaseManager 领取主 Agent 租约
主 Agent 连接失败
释放租约时标记为 unavailable，而不是 idle
继续领取备用 Agent
最终无残留租约
```

### 7.3 运行中断心跳自动切换

测试点：

```text
第一个 recon Agent 已经开始执行任务
测试中把它的 heartbeat_ts 设置为过期
Commander 的 active lease heartbeat watcher 检测到 heartbeat lost
该 Agent 被标记为 unavailable
Commander 自动把同一个 work_item 指派给第二个 recon Agent
trace 包含 agent_heartbeat_lost 和 agent_failover_reassigning
```

### 7.4 并发派发降级完成

测试点：

```text
parallel activity 同时派发给两个 artillery Agent
其中一个宕机
另一个成功
activity 不被宕机实例拖垮
宕机实例被标记 unavailable
```

### 7.5 心跳 metadata 保护

文件：

```text
tests/test_agent_heartbeat.py
```

测试点：

```text
Agent 心跳线程发送 heartbeat 前合并最新注册表 metadata
如果 Commander 已把实例标记为 busy 并写入 lease_workflow_id
心跳不会把 status 覆盖回旧的 idle
```

### 7.6 租约释放状态

文件：

```text
tests/test_agent_leases.py
```

测试点：

```text
release(..., status="unavailable") 可以正确清理 lease 字段，并保留 unavailable_reason
```

## 8. 测试运行命令

在 `Agent` 虚拟环境中运行：

```bash
cd /home/wyw/A2A/A2A
conda run -n Agent python -m pytest -q
```

本次功能相关测试：

```bash
conda run -n Agent python -m pytest tests/test_agent_heartbeat.py tests/test_agent_leases.py tests/test_bpel_workflow.py -q
```

当前验证结果：

```text
33 passed, 1 warning
```

其中 warning 来自 FastAPI/Starlette 的 TestClient 依赖提示，不影响当前功能。

演示脚本也已验证通过：

```text
Down Agent was isolated and the task was reassigned.
Active heartbeat loss triggered reassignment before the original call returned.
Recon_Primary late result was rejected by lease/heartbeat guard.
```

## 9. 后续其他同学实现 Agent 的统一接入规范

为了保证宕机恢复机制在多人合并后仍然可用，所有业务 Agent 必须遵守下面的框架约定。

### 9.1 服务注册规范

所有 Agent 注册到 Nacos 时统一使用服务名：

```text
A2A-Agent
```

metadata 至少包含：

```json
{
  "role": "recon",
  "status": "idle"
}
```

字段含义：

```text
role    能力类型，例如 recon / artillery / evaluator / assault
status  调度状态，idle 表示可接任务，busy 表示已被租用，unavailable 表示当前不可调度
```

后续新增 Agent 类型时，只需要新增 role，例如：

```json
{
  "role": "drone_recon",
  "status": "idle"
}
```

然后 BPEL 或 Commander 侧能映射到这个 role 即可。

### 9.2 心跳接入规范

如果业务 Agent 使用项目已有的 `NacosRegistry.register_service()`，会自动获得心跳能力：

```python
registry.register_service(
    service_name="A2A-Agent",
    ip=ip,
    port=port,
    metadata={"role": "recon", "status": "idle"},
    heartbeat_interval=5,
)
```

推荐环境变量：

```text
A2A_HEARTBEAT_INTERVAL=5
A2A_HEARTBEAT_GRACE_SECONDS=12
A2A_LEASE_HEARTBEAT_CHECK_INTERVAL=1
```

要求：

```text
1. Agent 存活时必须持续刷新 heartbeat_ts。
2. 心跳间隔应小于 heartbeat_grace_seconds。
3. 自定义 Agent 如果不使用 NacosRegistry，也必须提供等价 heartbeat_ts 更新。
4. 不要在业务代码中手动把 busy/unavailable 改回 idle；状态释放由 Commander 租约逻辑负责。
```

### 9.3 推荐继承 A2ABaseAgent

最推荐的做法是继承现有基础类：

```python
from a2a_protocol.server import A2ABaseAgent


class MyReconAgent(A2ABaseAgent):
    def execute_task(self, payload):
        output_hint = payload.get("output_hint") or "recon_report"
        sector = payload.get("input", {}).get("sector", "unknown")
        return {
            output_hint: f"{sector} recon result"
        }, "recon completed"
```

这样可以自动获得：

```text
GET  /health
GET  /ready
POST /lifecycle/ready
GET  /metrics
GET  /.well-known/agent-card
POST /sendMessage
POST /sendMessageStream
GET  /workflows/{workflow_id}/work-list
```

### 9.4 统一响应格式

Agent 返回应使用统一任务响应信封：

```json
{
  "workflow_id": "workflow-001",
  "work_item": "workflow-001:1:recon",
  "agent": "Recon_Agent_1",
  "role": "recon",
  "command": "scan_beach_defenses",
  "status": "completed",
  "output": {
    "recon_report": "..."
  },
  "metrics": {
    "latency_ms": 12.3
  },
  "error": null,
  "message": "completed",
  "attempts": 1,
  "cached": false
}
```

字段要求：

```text
status=completed/succeeded/success/accepted -> Commander 视为成功
status=failed/error/rejected/timeout 或 error 非空 -> Commander 视为失败
output -> 业务结果必须放这里
work_item -> 必须原样返回，用于幂等和追踪
workflow_id -> 必须原样返回，用于 checkpoint 和 trace
```

如果继承 `A2ABaseAgent` 并只重写 `execute_task()`，基础类会自动包装这个响应。

### 9.5 ready 状态规范

Agent 临时不可接任务时，不应该假装成功。可以通过：

```bash
curl -X POST http://127.0.0.1:8012/lifecycle/ready \
  -H 'Content-Type: application/json' \
  -d '{"ready": false}'
```

当 `ready=false`：

```text
/sendMessage 返回标准失败响应：agent is not ready
/sendMessageStream 返回 503
```

Commander 会把这类错误识别为不可用，并切到同 role 其他 Agent。

### 9.6 幂等规范

后续真实 Agent 需要注意：

```text
同一个 work_item 可能因为网络异常、调用超时、断心跳被重试或重新指派。
```

因此 Agent 最好做幂等处理：

```text
1. 收到同一个 work_item 时，如果已经完成过，直接返回缓存结果。
2. 不要因为重试导致重复写数据库、重复扣资源或重复执行不可逆动作。
3. A2ABaseAgent 目前已有基于 work_item 的内存缓存，真实生产场景可替换为持久化缓存。
```

## 10. 后期合并代码检查清单

每个同学提交 Agent 代码前建议自查：

```text
[ ] 是否继承 A2ABaseAgent，或至少实现同等 A2A 接口
[ ] 是否注册到 A2A-Agent 服务名
[ ] metadata 是否包含 role 和 status=idle
[ ] role 是否和 BPEL/Commander 期望一致
[ ] 是否启用心跳，且 heartbeat_ts 会持续刷新
[ ] 心跳是否不会覆盖 Commander 写入的 busy/unavailable/lease_* 状态
[ ] /health 是否能访问
[ ] /ready 是否能访问
[ ] /sendMessage 是否返回统一响应信封
[ ] output 是否放在 response["output"] 内
[ ] workflow_id 和 work_item 是否原样透传
[ ] ready=false 或服务不可用时是否返回失败而不是假成功
[ ] 是否考虑同一个 work_item 的幂等
```

## 11. 给老师汇报时可以这样总结

可以按下面的话术说明：

```text
我们把宕机恢复做在 Commander 框架层，而不是写死到某个具体 Agent。

当 Commander 调用某个 Agent 失败时，会区分这是业务失败还是实例不可用。
如果是连接失败、超时、503、ready=false，或者运行中检测到 heartbeat lost，
框架会把该实例标记为 unavailable，释放它的租约，并继续查找同 role 的其他 idle Agent
来接管同一个 work_item。

为了避免切换后旧 Agent 迟到返回污染结果，Commander 在写入结果前会确认租约仍有效且心跳仍新鲜。
如果旧结果已经失效，会记录 agent_late_response_ignored，不写入 workflow context。

后续不同同学实现自己的 Agent 时，只要统一注册 role/status、持续心跳，并遵守 A2A 接口和响应格式，
就可以自动获得这套故障切换能力。

演示脚本 demo_agent_failover_reassignment.py 覆盖了两种情况：
一种是调用时 connection refused，另一种是任务运行中 heartbeat lost。
两种情况下 Commander 都能隔离主 Agent，并切换到备用 Agent。
```

## 12. 当前边界与后续可扩展点

当前已经完成：

```text
Agent 不可用错误识别
Agent 心跳定期上报
运行中租约心跳 watcher
Agent unavailable 标记
租约释放状态区分
同 role 备用 Agent 自动重新指派
迟到响应忽略
并发派发降级完成
trace 记录
checkpoint 保存
演示脚本
自动化测试
```

仍可继续增强：

```text
Agent 进程自动重启：交给 Docker/systemd/K8s 或后续 supervisor 模块
unavailable 自动恢复为 idle：可增加健康探测器，检测 Agent 恢复后重置状态
跨 Manager 分布式锁：当前租约主要适合单 Manager 进程内协调
更细粒度失败策略：例如 parallel activity 最少成功数、按权重选择备用 Agent
恢复过程可视化：把 trace 接入前端或日志平台
```

## 13. 一句话结论

```text
这次完善后，框架已经能在 Agent 调用失败或运行中断心跳时自动隔离故障实例，
并把同一个 work_item 重新指派给同 role 的健康 Agent。
后续其他同学只要按统一 A2A 接口、role/status metadata、心跳机制和响应信封接入，
就可以复用这套宕机恢复机制。
```
