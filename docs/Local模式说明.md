# A2A Local 模式说明

Local 模式用于在不启动 Nacos、不启动各 Agent HTTP 服务的情况下，直接在 Commander 进程内模拟完整 Workflow。

它适合：

- 本地调试 Commander 编排逻辑。
- 验证动态 Workflow 分支。
- 在没有 Nacos/Docker/网络环境时演示系统流程。
- 快速复现 RE-PLAN 与 ASSAULT 两类结果。

## 1. 运行方式

进入项目目录：

```bash
cd /home/yl/yl/jzz/A2A
```

低评估分，触发 RE-PLAN：

```bash
./venv/bin/python -u commander_agent/main.py \
  --mode local \
  --mock-eval-score 40 \
  --max-steps 10
```

高评估分，触发 ASSAULT：

```bash
./venv/bin/python -u commander_agent/main.py \
  --mode local \
  --mock-eval-score 75 \
  --max-steps 10
```

强制 Commander 决策：

```bash
./venv/bin/python -u commander_agent/main.py \
  --mode local \
  --mock-decision ASSAULT
```

## 2. Local 与 Remote 的区别

| 项目 | Local 模式 | Remote 模式 |
| :--- | :--- | :--- |
| Nacos | 不需要 | 需要 |
| Agent HTTP 服务 | 不需要 | 需要 |
| A2A Client/Server 网络调用 | 不走网络 | 真实 HTTP 调用 |
| 流式 Artillery 反馈 | 本地模拟 | SSE 接口返回 |
| Commander 决策 | 默认本地 mock | 可调用 LLM |
| 适用场景 | 开发、测试、演示 Workflow | 分布式 A2A 协议验证 |

## 3. 已支持的 Local Agent

Local 模式通过 `local_runtime.py` 模拟以下角色：

| Role | Local Agent |
| :--- | :--- |
| `recon` | `Local_Recon_Agent` |
| `artillery` | `Local_Artillery_Agent` |
| `evaluator` | `Local_Evaluator_Agent` |
| `assault` | `Local_Assault_Agent` |

## 4. 当前行为

Local 模式仍保留 A2A 的核心阶段感：

```text
Local Discovery -> Local Auth -> Local Send/Stream -> Commander 状态机推进
```

因此它不是简单跳过 Agent，而是在本地模拟完整交互语义。

## 5. 测试

```bash
PYTHONPATH=/home/yl/yl/jzz/A2A ./venv/bin/python -m unittest discover -s tests
```
