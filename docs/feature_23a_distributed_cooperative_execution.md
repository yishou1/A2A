# 23.a 分布式多智能体协同执行实现与整合说明

## 1. 功能边界

23.a 位于现有“任务调度”和“人工授权/执行控制”之间，只负责形成协同编组与任务分配结果。它不替代 23 一级项已有的 Actor-Critic 决策，不修改 ROE、人工授权、fire command 或武器执行状态机，也不会直接下发执行命令。

```text
任务调度结果
    -> 23.a 各 Agent 本地竞标与赢家表一致性
    -> 带锁定资源的 scheduled_tasks
    -> 原候选方案生成
    -> 原合规与人工授权
    -> 原执行控制和 fire command
```

该能力默认关闭。只有任务输入显式设置 `cooperative_execution.enabled=true` 时才进入协同分配；关闭时原链路行为不变。启用后若没有可用参与者或在最大轮次内不能达成一致，流程失败关闭，不会降级为绕过协同或授权的执行。

## 2. 算法设计

实现采用确定性的简化 CBBA（Consensus-Based Bundle Algorithm）。每个执行 Agent 都维护自己的任务视图、资源状态、候选任务包、本地报价和赢家表。Commander 仅发现参与者、启动通信轮次并比较赢家表摘要是否一致，不集中计算最终赢家。

每个 Agent 的处理顺序为：

1. 接收相同的任务或逻辑任务槽位集合，并读取自身实时资源和可用状态。
2. 先做硬约束过滤，包括 Agent 是否可用、剩余任务槽位是否大于零、能力是否覆盖任务要求、资源类型是否匹配、最低资源指标是否满足。
3. 对可执行任务计算本地效用，按效用从高到低建立本地任务包。
4. 通过现有 A2A 客户端向其他 Agent 请求赢家表，并合并收到的竞标信息。
5. 多个 Agent 竞争同一槽位时，报价高者获胜；报价完全相同时，Agent ID 字典序较小者获胜。
6. Agent 丢失任务后，从第一个失去的槽位开始裁剪任务包，再基于当前赢家表重新竞标。
7. 各 Agent 的赢家表摘要一致时结束；Agent 失效后，其未完成槽位从赢家表移除，由剩余 Agent 重新竞标。

本地效用只使用项目已有字段，不需要新训练模型：

```text
utility = priority * 100
        + capability_match * 30
        + readiness * 20
        + capacity_ratio * 10
        - load * 20
        - task_cost
```

其中 `priority`、`readiness`、`capacity_ratio` 和 `load` 归一化到 0 到 1；`capability_match` 表示任务要求能力的覆盖率；`task_cost` 使用 Agent 已有的任务代价。硬约束不满足时效用为负无穷，该 Agent 不能获得任务。排序、槽位命名、报价精度和同分规则均固定，因此同一输入可以稳定复现同一结果。

## 3. 多槽位任务

普通任务默认展开为一个逻辑槽位：`TASK-1#slot-001`，最终只能有一个赢家。需要多个执行单元并行参与时设置 `required_slots`，例如 `required_slots: 3` 会展开为三个独立槽位。每个槽位仍只有一个赢家；默认 `distinct_agents=true`，同一 Agent 不能同时获得同一父任务的多个槽位，因此三个槽位可由三个不同 Agent 并行承担。

## 4. 输入示例

协同配置随任务输入传入：

```json
{
  "cooperative_execution": {
    "enabled": true,
    "coordination_id": "CBBA-MISSION-001",
    "max_rounds": 16,
    "participant_roles": ["artillery", "assault", "recon"],
    "task_overrides": {
      "TASK-1": {
        "required_capabilities": ["engage"],
        "required_resource_types": ["strike"],
        "required_slots": 3,
        "distinct_agents": true
      }
    }
  }
}
```

参与 Agent 的本地资源输入主要包括 `agent_id`、`available/status`、`capabilities`、`resource_types`、`readiness`、`available_task_slots`、`max_concurrent_tasks`、`load` 和可选的 `task_costs`。这些字段由 Agent 自己的实时资源状态提供，不由 Commander 为全部 Agent 统一伪造。

## 5. A2A 协调消息

Agent Card 新增 `coordinationEndpoint=/coordination/cbba`。该端点沿用现有 Bearer Token 鉴权，但协调消息不会进入业务任务计数、Commander 工作流状态或武器执行状态机。

```json
{
  "schema_version": "cbba-coordination/v1",
  "message_type": "initialize | state_request | synchronize | merge | reconcile | close",
  "coordination_id": "CBBA-MISSION-001",
  "participant_id": "AGENT-A",
  "round": 1,
  "active_agent_ids": ["AGENT-A", "AGENT-B"],
  "tasks": [],
  "peers": [],
  "winner_table": {}
}
```

Agent 返回 `candidate_bundle`、`local_bids`、`winner_table`、`winner_table_digest`、`resource_state` 和当前轮次。最终聚合输出包括 `assignments`、参与者状态、失败参与者、收敛轮次，并始终明确返回 `authorization_required=true`、`execution_dispatched=false`。

## 6. 输出与原链路衔接

协同结果写回每个 `scheduled_task`：

```json
{
  "assigned_resources": ["AGENT-A", "AGENT-B", "AGENT-C"],
  "assignment_slot_ids": [
    "TASK-1#slot-001",
    "TASK-1#slot-002",
    "TASK-1#slot-003"
  ],
  "assignment_source": "deterministic_cbba",
  "assignment_locked": true,
  "coordination_id": "CBBA-MISSION-001"
}
```

候选方案生成会保留这些已锁定资源，不再由中央规划器覆盖。之后仍必须经过现有 `compliance_authorization`；授权为 pending 或 denied 时执行控制不会产生执行命令，只有 approved 后才将分配结果带入原执行接口。

## 7. 关键代码

| 范围 | 文件 | 关键对象 |
|---|---|---|
| 通用算法 | `algorithmrepo/services/a2a_algorithms_common/distributed_cbba.py` | `CBBAParticipant`、`run_local_cbba_consensus`、`converge_participants` |
| Commander 镜像 | `commander/services/a2a_algorithms_common/distributed_cbba.py` | 与算法库文件保持一致 |
| Agent 本地状态 | `commander/distributed_coordination/agent_state.py` | `AgentCoordinationStore` |
| A2A 轮次协调 | `commander/distributed_coordination/orchestrator.py` | `A2ACBBAOrchestrator` |
| 调度衔接 | `commander/distributed_coordination/integration.py` | `coordinate_task_schedule` |
| A2A 通信 | `commander/a2a_protocol/server.py`、`client.py` | `/coordination/cbba`、`exchange_coordination` |
| 授权前后传递 | `commander/decision_support/planning.py`、`execution_control_agent/execution_control_core.py` | 锁定分配保留与命令字段透传 |
| 测试 | `commander/tests/test_distributed_cbba_execution.py` | 9 个专项场景 |

## 8. 整合验证

```powershell
cd commander
python -m pytest tests\test_distributed_cbba_execution.py -q
python -m pytest tests\test_task_scheduling_amos_adapter.py tests\test_task_scheduling_algolib.py tests\test_a2a_sdk.py tests\test_distributed_agent_interfaces.py tests\test_scheduling_policy.py tests\test_bpel_workflow.py tests\test_decision_agents_a2a.py tests\test_decision_support_formulas.py tests\test_algolib_orchestration.py tests\test_closed_loop_integration.py -q -k "not test_demo_script_runs_both_workflows"
cd ..
powershell -ExecutionPolicy Bypass -File scripts\check_zh_algorithm_vendor.ps1
```

当前基线因已移除 `artillery_agent`、缺少部分模型权重、Windows SQLite 临时文件锁及既有断言差异存在 14 项已知失败。本改动前基线为 `364 passed, 4 skipped, 14 failed`，改动后为 `373 passed, 4 skipped, 14 failed`；失败集合一致，新增 9 项测试全部通过。

AMOS 平台全套在 WSL `a2a` 环境中的结果为 `239 passed, 7 failed`。7 项失败均位于既有场景媒体 checksum、默认几何锚点和传感器俯角数据一致性检查，本次改动未修改 `amos-platform` 或对应场景数据。因此当前仓库不能声明完整 E2E 全绿，但 23.a 专项链路及其相关 Commander 回归全部通过。
