# 分布式协同执行运行时

## 实现边界

该模块把 23.a 的 CBBA 分配结果接到多个真实独立的 Agent 进程。这里的“真实”指真实进程、HTTP A2A、Nacos 注册、主机资源采样、SQLite 状态和外部事件证据，不代表接入武器或自动生成执行效果。

系统没有内置致动器，也不会生成命中、毁伤、弹药消耗或“执行成功”等模拟结果。没有经过白名单的数据源回传证据时，任务只会停在 `synchronized`，不会自动进入 `executing` 或 `completed`。

## 运行链路

```text
任务调度
  -> CBBA 各 Agent 独立竞标并锁定槽位
  -> 原有合规检查和人工授权
  -> 执行控制生成带 assigned_resources/slot_ids 的命令
  -> Dispatcher 按已锁定结果寻找对应 Agent
  -> 每个 Agent 独立 reserve -> ready
  -> Agent 通过 A2A 查询对端状态并进入 synchronized
  -> 外部训练平台/测试台上报带校验值的 started/completed/failed 事件
  -> 每个 Agent 独立更新 executing/completed/failed
```

Commander 不重新计算任务赢家。任何已分配 Agent 不在线、授权不通过、槽位未锁定、开始时间不一致或对端未就绪都会失败关闭。

## Agent 状态机

```text
reserved -> ready -> synchronized -> executing -> completed
    |          |           |              |
    +----------+-----------+--------------+-> failed / aborted
```

`reserved` 要求：

- `authorization.decision` 为 `approved` 或 `authorized`；
- 带非空授权引用；
- `assignment_source=deterministic_cbba`；
- `assignment_locked=true`；
- 当前 Agent 必须是该槽位赢家。

后续准备、事件记录和中止必须使用与预留时相同的授权引用。

## 真实数据来源

| 数据 | 实际来源 |
|---|---|
| CPU、内存、网络、进程状态 | `ResourceMonitor` 调用本机 `psutil` |
| 在线和忙闲状态 | Agent 心跳及当前活动任务数 |
| Agent 身份、资源类型、能力 | 运维维护的显式部署配置 |
| CBBA 分配 | 各 Agent 本地报价和 A2A 赢家表一致性 |
| 执行状态 | 白名单外部训练平台或测试台事件 |
| 事件完整性 | 事件 ID、来源、观测时间、证据 URI、SHA-256 |
| 状态和审计 | 每个 Agent 自己的 SQLite 数据库 |

外部事件不会被项目自动构造。事件来源未加入 `allowed_event_sources`、缺少 URI/SHA-256、状态转换顺序错误或授权引用变化时，Agent 会拒绝数据且不写入事件库。

## 部署配置

部署文件必须符合 `commander/config/cooperative_execution_agents.schema.json`。下面仅表示字段结构，实例名称和能力必须替换成实际接入资源，不能直接当作真实部署数据：

```json
{
  "agents": [
    {
      "agent_id": "<registered-platform-id-1>",
      "host": "<bind-ip-1>",
      "port": 18121,
      "resource_types": ["<actual-resource-type>"],
      "capabilities": ["<actual-capability>"],
      "allowed_event_sources": ["<approved-source-id>"],
      "max_concurrent_tasks": 1,
      "state_db": "<writable-state-db-path>"
    },
    {
      "agent_id": "<registered-platform-id-2>",
      "host": "<bind-ip-2>",
      "port": 18122,
      "resource_types": ["<actual-resource-type>"],
      "capabilities": ["<actual-capability>"],
      "allowed_event_sources": ["<approved-source-id>"],
      "max_concurrent_tasks": 1,
      "state_db": "<writable-state-db-path>"
    }
  ]
}
```

先只校验配置：

```bash
cd commander
python scripts/start_cooperative_execution_agents.py --config /absolute/path/agents.json --check
```

单独启动多实例：

```bash
python scripts/start_cooperative_execution_agents.py --config /absolute/path/agents.json
```

随完整栈启动：

```bash
export COOP_EXECUTION_AGENTS_CONFIG=/absolute/path/agents.json
./start_agents.sh
```

每个实例以 `role=cooperative_execution` 注册到 `A2A-Agent`，metadata 包含 `agent_id`、真实资源采样、能力、活动会话数、`actuator_connected=false`。

## 任务开关

任务必须明确开启自动准备，并提供未来统一开始时间：

```json
{
  "cooperative_execution": {
    "enabled": true,
    "auto_prepare_execution": true,
    "planned_start_at": "<future-ISO-8601-timestamp>",
    "participant_roles": ["cooperative_execution"]
  }
}
```

该配置只推进到同步就绪。`ready_for_external_execution=true` 不等于已执行；返回中始终明确包含：

```json
{
  "actuator_connected": false,
  "execution_dispatched": false
}
```

## 外部事件契约

外部训练平台或测试台通过 Agent 的原有 `/sendMessage` 提交 `record_execution_event`。事件至少包含：

```json
{
  "event_id": "<globally-unique-event-id>",
  "event_type": "started | completed | failed | aborted",
  "source": "<allowlisted-source-id>",
  "observed_at": "<ISO-8601-with-timezone>",
  "evidence": {
    "uri": "<immutable-evidence-uri>",
    "sha256": "<64-character-sha256>"
  }
}
```

同一 `event_id` 重复提交时按幂等事件处理。事件写入与状态转换在同一个 SQLite 事务中完成，非法事件不会留下半条审计记录。

## 验证

```bash
cd commander
python -m pytest tests/test_cooperative_execution_runtime.py -q
python -m pytest tests/test_distributed_cbba_execution.py -q
```

测试覆盖授权、槽位归属、状态机、双 Agent A2A 就绪同步、失联失败关闭、多槽位、外部事件白名单、证据校验、幂等和无致动边界。
