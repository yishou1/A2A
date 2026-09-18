# 规则执行仿真与闭环评估链路

## 定位

该链路用于没有实装执行器或外部仿真平台时的联调。它不会连接武器、设备或控制接口，也不会把规则计算结果描述成真实毁伤。所有执行结果均带有：

- `execution_mode=rule_based_simulation`
- `is_real_execution=false`
- `actuator_connected=false`
- `evidence.type=simulation`
- 可复算的证据 SHA-256

## 数据流

```text
前置 Agent 结果
  -> Execution Control Agent（规则匹配 + 运动预测 + 授权门控）
  -> Artillery/Assault Simulation Agent（确定性规则执行）
  -> Evaluator Agent（仿真效果投影为 0-100 分）
  -> Closed Loop Agent（七维任务特征 + 随机森林任务完成度 + 优化建议）
```

执行控制只在前置数据完整且授权通过时生成命令。规则执行 Agent 不使用固定态势默认值；以下字段必须由执行控制结果或调用方显式输入：

| 输入 | 首选来源 |
| --- | --- |
| 情报置信度 | `execution_control_result.output_data.situation.intel_confidence` |
| 资源就绪度 | `execution_control_result.output_data.situation.resource_readiness` |
| 通信质量 | `execution_control_result.output_data.situation.communication_quality` |
| 威胁压力 | `execution_control_result.output_data.situation.threat_score` |
| 授权结果 | `execution_control_result.output_data.authorization` |
| 目标和瞄准点 | `execution_command` |

任何必需字段缺失时返回 `insufficient_data`；授权未通过时返回 `blocked`，不会生成成功效果。

## 高中低配置

规则位于 `config/rule_simulation_profiles.json`：

- `low`：联调宽松档，最低输入门槛较低。
- `medium`：默认档，平衡输入质量和链路可运行性。
- `high`：严格档，对情报、资源和通信质量要求更高，允许的威胁压力更低。

档位只调整阈值和权重，不改变输入协议，也不绕过授权。相同输入、相同档位会得到相同分数、决策和证据摘要。

## 启动

在 `commander/.env` 中设置：

```bash
RULE_SIMULATION_PROFILE=medium
ARTILLERY_AGENT_PORT=8003
ASSAULT_AGENT_PORT=8004
EVALUATOR_AGENT_PORT=8015
```

然后使用项目现有启动入口：

```bash
cd commander
bash start_agents.sh
```

## 验证

```bash
python -m pytest -q \
  tests/test_rule_based_execution_pipeline.py \
  tests/test_execution_control_integration.py
```

测试覆盖缺失数据、授权拒绝、档位差异、确定性复现、执行证据校验、执行控制到规则执行再到 evaluator 和闭环评估的完整本地链路。
