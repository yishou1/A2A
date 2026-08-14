# Task Scheduling Agent

独立任务调度 / 资源分配智能体。输入为 AMOS 风格 JSON，输出传感器任务分配与打击/再攻击计划。

支持两种后端（与 [lzh](https://github.com/yishou1/A2A/tree/lzh) 决策 Agent / TIA 算法规划对齐）：

| 模式 | 说明 |
|------|------|
| **local**（默认） | 进程内 mock 启发式或本地 MARL-PPO |
| **algolib** | 小模型选算法 → `GET /algorithms` → `POST /run` |

## 快速运行（本地 mock）

```powershell
cd D:\a2a_project\A2A-main

.\.venv\Scripts\python.exe -m task_scheduling_agent `
  --input examples/amos_schedule_inputs/sample_amos_request.json `
  --output data/output/task_schedule/amos_demo.json `
  --use-mock
```

## 算法库动态调用（对齐 lzh）

```powershell
# 1) 启动算法 HTTP 服务（至少 9024 marl_ppo）
$env:TIA_USE_MOCK="1"
.\scripts\start_a2a_algorithm_services.ps1 -TiaOnly

# 2) 启动集中网关 :8088
.\scripts\start_tia_algolib_gateway.ps1

# 3a) 关闭 LLM：默认算法 marl_ppo_task_scheduler
.\.venv\Scripts\python.exe -m task_scheduling_agent `
  --input examples/amos_schedule_inputs/sample_amos_request.json `
  --output data/output/task_schedule/amos_algolib.json `
  --algolib

# 3b) 开启小模型选算法（Ollama / OpenAI 兼容）
$env:ENABLE_LLM="true"
$env:TOOL_LLM_URL="http://127.0.0.1:11434/v1"
$env:TOOL_LLM_NAME="qwen2.5:7b"
.\.venv\Scripts\python.exe -m task_scheduling_agent `
  --input examples/amos_schedule_inputs/sample_amos_request.json `
  --output data/output/task_schedule/amos_algolib_llm.json `
  --algolib --llm
```

环境变量：

| 变量 | 含义 |
|------|------|
| `TASK_SCHEDULING_BACKEND=algolib` | 走算法库 |
| `ALGOLIB_BASE_URL` | 默认 `http://127.0.0.1:8088` |
| `ENABLE_LLM` / `TOOL_LLM_URL` / `TOOL_LLM_NAME` | 小模型规划 |
| `TASK_SCHEDULING_ALGORITHM_PLANNER=llm` | 强制 LLM 规划模式 |

## 说明

- 字段约定见 `docs/任务调度智能体_AMOS_JSON输入说明.md`
- algolib 路径会把 AMOS JSON 转成 `marl_ppo_task_scheduler` 输入，并**强制覆盖** LLM 填写的 `inputs`（防幻觉）
- 白名单算法：`marl_ppo_task_scheduler`
