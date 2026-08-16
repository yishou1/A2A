# MARL-PPO Task Scheduler

TIA `python_http_service` package for `marl_ppo_task_scheduler` (port **9024**).

## Role in the TIA Agent pipeline

```
RT-DETR → Siamese毁伤 → EDL → MOTR跟踪 → MARL-PPO调度 → 认知 → 通信路由
```

This package exposes the **MARL-PPO scheduling step** as a standalone algorithm library service.
It allocates sensors to targets and plans reattacks for under-damaged high-threat targets.

The small CPU profile loads the trained
`models/checkpoints/marl_ppo_scheduler_s.safetensors` artifact. It uses a
parameter-shared Actor-Critic policy with per-agent identity, resource
availability, valid-target, and duplicate-assignment masks. Missing or invalid
weights fail the real-mode health gate instead of using a random network.

On 256 deterministic synthetic holdout scenarios, the trained policy reached
mean total reward 3.500993, high-threat coverage 0.706510, and reattack coverage
0.627474. The constrained random baseline reached 2.066525, 0.510352, and
0.340495 respectively. These synthetic results validate the implementation
pipeline only and are not operational battlefield performance claims.

## Boundaries

| Does | Does not |
|------|----------|
| `sensor_assignments` | Trajectory prediction (`track_threat`) |
| `reattack_plan` | Fire-control / execution commands (`execution_control_planner`) |
| | Communication routing (`marl_dynamic_router`) |

When consumed inside the TIA Agent, outputs are converted to `TaskSchedulePlan` and
`resource_allocation` by `agent/skills/perception/schedule_adapter.py` for downstream agents.

## Run

Install `services/requirements.txt`, then use real mode:

```powershell
$env:TIA_USE_MOCK="0"
$env:TIA_COMPUTE_PROFILE="small"
python services/marl_ppo_task_scheduler/app/main.py
```

```bash
curl -X POST http://127.0.0.1:9024/predict -H "Content-Type: application/json" -d @examples/marl_ppo_task_scheduler/1.0.0/golden_cases/case_001_request.json
```

Reproduce the deterministic training artifact with:

```powershell
python scripts/train_marl_ppo_scheduler.py
```
