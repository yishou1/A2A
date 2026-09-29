# 闭环优化 Agent 适配说明

## 适配结论

新增闭环优化能力可以适配到当前 `A2A` 框架中，但不能直接复用旧目录中的 MCP 服务入口。当前 `A2A` 框架以 `FastAPI + A2ABaseAgent + Nacos role` 为主，因此本次适配保留原有 agent 不变，新增 `closed_loop` 角色 agent，并把旧能力中的算法核心迁移为可被 A2A 调用的 Python 模块。

## 新增与修改内容

`closed_loop_agent/closed_loop_core.py`

闭环优化算法核心。包含逻辑回归损伤判别、K-Means 态势聚类、随机森林任务完成度回归、闭环控制策略，以及 xBD/SC2LE 特征表读取逻辑。

`closed_loop_agent/main.py`

目标框架下的新 agent 入口。继承 `A2ABaseAgent`，注册为 `role=closed_loop`，接收 `closed_loop_optimization` 命令并返回算法结果。

`scripts/extract_xbd_damage_features.py`

从 xBD 的 `images/` 和 `labels/` 中抽取建筑级特征，输出 `xbd_damage_features.csv`。

`scripts/run_xbd_closed_loop_demo.py`

端到端示例脚本。先抽取 xBD 特征，再调用闭环优化算法，生成结果 JSON。

`data/xbd/train`

从旧目录复制过来的 xBD 示例数据，包含 3 对灾前/灾后图像和对应标签。

`a2a_protocol/server.py`

新增可重写的 `handle_message` 方法，并支持在 Agent Card 中暴露 `skills` 字段。默认行为保持原样，原有 agent 不需要修改。

`local_runtime.py`

新增本地 `closed_loop` 角色，便于不启动 Nacos 和 HTTP 服务时直接验证闭环优化能力。

`requirements.txt`

新增 `Pillow`，用于读取和裁剪 xBD 图像。

`start_agents.sh`

新增 `CLOSED_LOOP_AGENT_PORT=8016` 和 `closed_loop_agent/main.py` 启动项。

## 调用方式

HTTP/A2A 调用时，请向 `closed_loop_agent` 发送：

```json
{
  "task_id": "closed-loop-demo-1",
  "command": "closed_loop_optimization",
  "input": {
    "target_count": 50,
    "cycles": 3,
    "dataset_paths": {
      "xbd_damage_csv": "data/xbd/processed/xbd_damage_features.csv"
    }
  }
}
```

返回结果在 `result.output_data` 中，主要包含：

`execution_control`：每个目标的执行控制建议。

`effect_assessment`：每个目标的损伤概率、是否损伤、态势标签。

`closed_loop_optimization`：每轮闭环优化历史、任务完成度初始值与最终值。

`performance_report`：任务完成度预测、延迟等性能指标。

`requirement_report`：是否满足协议中的验收指标。

## 当前缺少或需要后续补充的内容

SC2LE 原始回放到特征表的抽取脚本尚未实现。当前算法支持读取 `sc2le_task_csv`，但需要先把 SC2LE 数据处理成任务级特征表。

当前 commander 动态流程已把 `closed_loop` 插入默认状态机的最后一步。流程为 `recon -> artillery -> evaluator -> decision -> assault -> closed_loop -> end`。如果 commander 决策为 `RE-PLAN` 或 `ABORT`，流程会在决策后结束，不会进入 assault 和 closed-loop。

xBD 示例数据只有 15 条建筑样本，可以验证流程，但不能代表真实模型精度。要得到可信评估，需要使用更大的 xBD train/test 数据，并重新生成特征表。
