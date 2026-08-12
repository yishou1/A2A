# Task Scheduling Agent

独立任务调度 / 资源分配智能体。输入为 AMOS 风格 JSON 文件，输出传感器任务分配与打击/再攻击计划。

## 快速运行

```powershell
cd D:\a2a_project\A2A-main

.\.venv\Scripts\python.exe -m task_scheduling_agent `
  --input examples/amos_schedule_inputs/sample_amos_request.json `
  --output data/output/task_schedule/amos_demo.json `
  --use-mock
```

## 说明

- 字段约定见 `docs/任务调度智能体_AMOS_JSON输入说明.md`
- 默认推荐 `--use-mock`；有 `marl_ppo_scheduler.pt` 时可用 `--real`
- 本期不接 Commander / A2A HTTP 外壳
