# 任务调度智能体 — AMOS JSON 输入说明

独立 Agent：`task_scheduling_agent`。  
本期以 **JSON 文件** 接收 AMOS 态势，输出与 TIA `task_schedule` 对齐的调度结果；不接 Commander/A2A HTTP 外壳。

---

## 一、链路

```text
AMOS JSON 文件
    → amos_adapter（映射到 BattlefieldSchedulingState）
    → MARL-PPO / mock 启发式
    → sensor_assignments + reattack_plan
```

---

## 二、输入字段

样例：[`examples/amos_schedule_inputs/sample_amos_request.json`](../examples/amos_schedule_inputs/sample_amos_request.json)

| 字段 | 说明 |
|------|------|
| `mission_id` | 任务/想定 ID |
| `phase` | 阶段：`recon` / `contact` / `bda` 等 |
| `jamming_level` | 全局干扰 [0,1]；可与链路质量再合成 |
| `now` | 当前时刻（ISO8601），用于时间窗判断 |
| `tasks[]` | 待调度任务/目标 |
| `platforms[]` | 平台（传感器 / 打击） |
| `links[]` | 链路质量（可选） |

### `tasks[]`

| 字段 | → 调度状态 |
|------|------------|
| `target_id` / `task_id` | `SchedulingTarget.target_id` |
| `threat_score` / `priority` | 威胁；窗外降权，窗内临近截止抬升 |
| `damage_score` | 毁伤；过低且威胁高 → `needs_reattack` |
| `time_window.start/end` | 结合 `now` 做可用/紧迫编码 |
| `lat` / `lon` | 目标位置 |

### `platforms[]`

| 字段 | → 调度状态 |
|------|------------|
| `role=sensor` | `SchedulingSensor` |
| `role=strike` | `StrikeAsset` |
| `available` | 平台可用性 |
| `battery` | → `load = 1 - battery`；电量过低强制不可用 |
| `ammo` | → `remaining_ammo`（打击平台） |
| `link_quality` | 过低可强制不可用；并参与 `jamming_level` 合成 |
| `modality` / `asset_type` | 传感器模态 / 打击类型 |

### `links[]`

| 字段 | 说明 |
|------|------|
| `quality` | 与平台 `link_quality`、全局 `jamming_level` 合成有效干扰 |

---

## 三、输出（对齐 TIA `task_schedule`）

```json
{
  "mission_id": "amos-demo-001",
  "sensor_assignments": [
    {
      "sensor_id": "UAV-1",
      "target_id": "E-01",
      "task": "surveillance",
      "priority": "high",
      "rationale": "..."
    }
  ],
  "reattack_plan": [
    {
      "asset_id": "ARTY-1",
      "target_id": "E-02",
      "task": "reattack",
      "priority": "critical",
      "expected_damage": 0.65,
      "rationale": "..."
    }
  ],
  "covered_targets": ["E-01"],
  "reattack_targets": ["E-02"],
  "algorithm": "mock-heuristic",
  "task_schedule": { }
}
```

`task_schedule` 块与 [`agent/models/schemas.py`](../agent/models/schemas.py) 中 `TaskSchedulePlan` 一致，便于后续接入 Commander。

---

## 四、运行

```powershell
cd D:\a2a_project\A2A-main

.\.venv\Scripts\python.exe -m task_scheduling_agent `
  --input examples/amos_schedule_inputs/sample_amos_request.json `
  --output data/output/task_schedule/amos_demo.json `
  --use-mock

.\.venv\Scripts\python.exe scripts\smoke_task_scheduling_agent.py
```

去掉 `--use-mock` 时走 MARL-PPO 权重（若本机无 checkpoint，策略网络仍可随机初始化推理；建议验证用 mock）。

---

## 五、与 TIA 关系

- TIA 感知链**已移除**内嵌 MARL-PPO 调度；`task_schedule` 由本 Agent 单独产出。
- 本 Agent 与算法库服务仍可共享 `MARLPPOSchedulerNet` / 环境代码。
- TIA `intelligence_packet.task_schedule` 默认为空；调度结果请走本 Agent 输出 JSON。
