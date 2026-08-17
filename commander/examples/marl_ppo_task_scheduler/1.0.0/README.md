# MARL-PPO Task Scheduler

TIA `python_http_service` package for `marl_ppo_task_scheduler` (port **9024**).

## Role in the TIA Agent pipeline

```
RT-DETR → Siamese毁伤 → EDL → MOTR跟踪 → MARL-PPO调度 → 认知 → 通信路由
```

This package exposes the **MARL-PPO scheduling step** as a standalone algorithm library service.
It allocates sensors to targets and plans reattacks for under-damaged high-threat targets.

## Boundaries

| Does | Does not |
|------|----------|
| `sensor_assignments` | Trajectory prediction (`track_threat`) |
| `reattack_plan` | Fire-control / execution commands (`execution_control_planner`) |
| | Communication routing (`marl_dynamic_router`) |

When consumed inside the TIA Agent, outputs are converted to `TaskSchedulePlan` and
`resource_allocation` by `agent/skills/perception/schedule_adapter.py` for downstream agents.

## Run

```bash
python services/marl_ppo_task_scheduler/app/main.py
```

```bash
curl -X POST http://127.0.0.1:9024/predict -H "Content-Type: application/json" -d @examples/marl_ppo_task_scheduler/1.0.0/golden_cases/case_001_request.json
```
