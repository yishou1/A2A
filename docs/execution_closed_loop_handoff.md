# 执行控制与闭环评估算法交接说明

## 1. 交接基线

本次改动已经迁移到最新集成分支之上，不需要从旧的 `jzz/integrated` 分支整体合并。

| 项目 | 内容 |
| --- | --- |
| 上游分支 | `origin/integration/maritime-algolib` |
| 上游基准提交 | `a44351912b99f239b09526b4059fd37b0a4059c9` |
| 交接分支 | `zh-execution-closed-loop-handoff` |
| 算法库提交 | `673a22f` |
| Commander 集成提交 | `68ae9a6` |

合并顺序固定为：算法库提交 -> Commander 集成提交 -> 本交接说明提交。

## 2. 本次改动范围

本次只交接执行控制和闭环评估相关改动，没有带入 AMOS、全局启动脚本、测试压缩包、其他算法数据集等无关修改。

执行控制侧将轨迹预测、规则匹配和命令生成从 Agent 调用层中拆出，统一由 `algorithmrepo/services/a2a_algorithms_common` 提供算法实现。轨迹预测不再使用手写拟合，低配采用 scikit-learn 线性回归，中配和高配采用 FilterPy 常速度卡尔曼滤波；关联规则改为 mlxtend Apriori。Commander 仅负责读取前置 Agent 结果、选择配置、调用算法服务并整理输出。

闭环评估侧将七维任务特征转换、任务完成度评分和闭环建议分别拆成独立模块。任务完成度模型改为 scikit-learn `RandomForestRegressor`，模型文件和训练元数据随算法库提交。闭环建议仍采用可审计的规则决策，输入来自已经存在的前置 Agent 输出，不在缺少关键数据时伪造默认业务指标。

## 3. 高中低配置

| 配置 | 轨迹预测 | 任务完成度随机森林 | 执行控制输入限制 | 用途 |
| --- | --- | ---: | --- | --- |
| `low` | scikit-learn 线性回归 | 32 棵树 | 最多 16 条航迹、3 条匹配规则 | 资源有限、优先低延迟 |
| `medium` | FilterPy 卡尔曼，Q=0.05、R=1.0 | 96 棵树 | 完整输入 | 默认平衡配置 |
| `high` | FilterPy 卡尔曼，Q=0.01、R=0.25 | 192 棵树 | 完整输入 | 资源充足、优先模型容量 |

配置入口：

```env
EXECUTION_CONTROL_ALGORITHM_PROFILE=medium
CLOSED_LOOP_ALGORITHM_PROFILE=medium
```

请求参数中的 `profile` 或 `algorithm_profile` 可以覆盖环境变量。未指定时使用 `medium`。非法配置会直接报错，不会静默回退为其他配置。

需要注意：`high` 表示使用更多计算资源和更低的观测噪声假设，并不保证在所有真实噪声条件下都比 `medium` 准确。部署时应依据真实传感器噪声和性能压测选择。

## 4. 交接责任边界

### 康凡：算法库实现

重点接收提交 `673a22f`，检查以下内容：

- `algorithmrepo/services/a2a_algorithms_common/`
- `algorithmrepo/services/execution_control_planner/`
- `algorithmrepo/services/mission_feature_adapter/`
- `algorithmrepo/services/mission_completion_scorer/`
- `algorithmrepo/services/closed_loop_decision_advisor/`
- `algorithmrepo/scripts/train_mission_completion_sklearn.py`
- `algorithmrepo/models/sc2le_proxy_mission_model.*`
- `algorithmrepo/tests/python/`

依赖版本范围已经写入 `algorithmrepo/services/requirements.txt`：scikit-learn 1.4-1.7、FilterPy 1.4.5、mlxtend 0.23-0.24、pandas 2.2。

### 思龙：高中低配置

重点检查提交 `673a22f` 中的：

- `algorithmrepo/services/a2a_algorithms_common/algorithm_profiles.py`
- `algorithmrepo/tests/python/test_execution_closed_loop_profiles.py`
- `algorithmrepo/tests/python/test_zh_execution_closed_loop_chain.py`

Commander 的环境变量入口和请求覆盖逻辑位于提交 `68ae9a6`。配置名称固定为 `low`、`medium`、`high`，默认值固定为 `medium`。

### 郑州：项目集成

按顺序合并 `673a22f` 和 `68ae9a6`。第二个提交包含：

- `commander/execution_control_agent/`
- `commander/closed_loop_agent/`
- `commander/services/a2a_algorithms_common/`
- Commander 模型镜像、依赖和环境变量
- `scripts/check_zh_algorithm_vendor.ps1`
- Commander 集成测试

算法库目录是标准实现，Commander 下的同名算法目录是本地运行兜底镜像。执行 `scripts/check_zh_algorithm_vendor.ps1` 可以检测两份代码是否漂移。

## 5. 已完成验证

验证环境：Python `D:\tools\python\python.exe`，scikit-learn 1.7.2，FilterPy 1.4.5，mlxtend 0.23.4，pandas 2.3.3。

| 测试范围 | 结果 |
| --- | --- |
| 算法库三级配置、服务预测器、执行到闭环链路、轨迹预测 | `29 passed` |
| Commander 算法编排、闭环集成、任务特征转换 | `29 passed` |
| 算法库与 Commander 镜像一致性 | 通过 |
| 合计 | `58 passed` |

复测命令：

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\check_zh_algorithm_vendor.ps1

cd algorithmrepo
$env:PYTHONPATH="$PWD\services;$PWD\..\commander"
python -m pytest tests\python\test_execution_closed_loop_profiles.py tests\python\test_zh_execution_closed_loop_chain.py tests\python\test_a2a_algorithm_services.py tests\python\test_trajectory_linear_predictor.py -q

cd ..\commander
python -m pytest tests\test_algolib_orchestration.py tests\test_closed_loop_integration.py tests\test_mission_feature_module.py -q
```

## 6. 当前限制

任务完成度随机森林目前使用 SC2LE 代理数据训练，适合验证接口、训练流程和算法链路，但不能把当前指标当作真实业务场景最终精度。后续获得真实标注数据后，应继续使用现有训练脚本重新训练并更新模型元数据。

三级配置已经在功能测试中验证，但尚未在郑州最终部署机器上完成 CPU、内存和端到端延迟压测。合并后不影响默认 `medium` 配置运行。
