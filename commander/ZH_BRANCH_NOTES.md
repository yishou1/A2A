# zh 分支工作说明

> 更新时间：2026-07-17  
> 分支：`zh`（跟踪 `origin/zh`）  
> 相关提交：
> - `c963bb9` — merge: sync zh with latest main framework
> - `5843d99` — chore: align EC skills, drop bulky artifacts, document zh agents

本文档说明 `zh` 分支相对主框架的职责、本轮从 `main` 合入的内容，以及随后完成的优化改动，方便队友评审与后续合并。

---

## 1. 分支职责概览

`zh` 在公共 A2A 工作流控制面之上，主要贡献两类领域 Agent：

| 模块 | 角色 | 默认端口 | 核心能力 |
|---|---|---|---|
| `execution_control_agent/` | 执行控制 | `8017` | 关联规则匹配 + 线性回归运动预测，生成 strike/assault 指令 |
| `closed_loop_agent/` | 闭环优化 | `8016` | xBD 毁伤评估 + SC2LE 代理任务模型，闭环优化与需求判定 |

在 `beachhead_workflow.bpel` 中的插入顺序：

```text
recon
  -> execution_control (plan_strike_control)
  -> artillery
  -> evaluator
  -> [score >= 60] execution_control (plan_assault_control)
  -> assault
  -> closed_loop (closed_loop_optimization)
```

算法侧近期重点：

- 去掉 SC2LE **label leakage**
- 引入 **frozen proxy mission model**（`models/sc2le_proxy_mission_model.pkl`）
- 用 `mission_feature_schema` / `mission_feature_adapter` / `agent_results_mapping` 统一特征契约

---

## 2. 本轮从 main 合入的内容

提交：`c963bb9`

将 `origin/main` 最新框架能力合入 `zh`，冲突按「主框架基础设施 + zh 领域 Agent」原则处理：

### 2.1 保留 / 吸纳的 main 能力

- Commander Gateway（AMOS 数据包接入）
- 分布式智能体接口与轻量 `a2a_sdk`
- 资源监控（`psutil` / Prometheus / OpenTelemetry）
- 协议契约强化（`schema_version`、`required_skill` 等必填字段）
- 工作流编排 hardening、模型注册与幂等存储等

### 2.2 冲突处理原则

| 文件 | 处理方式 |
|---|---|
| `closed_loop_agent/*` | 以 zh 为准（泄漏检查、冻结代理模型、混合特征） |
| `execution_control` 相关 BPEL / Commander 载荷 | 保留 zh 的 EC → artillery/assault 链路 |
| `a2a_protocol/server.py` | 合并：main 的 protocolVersion / 监控端点 + zh 的 EC/CL skill |
| `artillery_agent` / `assault_agent` | zh 结构化输出 + main 的 model 注册 |
| `commander_agent/main.py` | zh 的角色载荷构建 + main 的多 input / schema 校验路径 |
| `requirements.txt` | 并集：框架监控依赖 + zh ML 依赖 |
| `tests/*` | 两侧用例合并，去重后保留 |

合入后本地未引入 `dispatchMode="parallel"` 到 `beachhead_workflow.bpel` 的 zh 主路径，避免破坏当前单派发时序假设。

---

## 3. 合入后的优化改动

提交：`5843d99`

### 3.1 Skill ID 与 BPEL / 发现层对齐

**问题：** BPEL 要求 `plan_strike_control` / `plan_assault_control`，但 Agent Card 原先只暴露 `generate_execution_commands`，且自定义 skill 缺少 `id`，远程按 skill 发现时可能匹配失败。

**改动：**

- `a2a_protocol/server.py`：`execution_control` 默认 skill 同时注册三个 ID
- `execution_control_agent/main.py`：Agent Card 同步注册三 skill，并转发 `**kwargs`（便于测试注入幂等库路径）
- `closed_loop_agent/main.py`：为 `closed_loop_optimization` 补齐 `id` / `tags`

兼容关系：

- `generate_execution_commands`：通用入口，用 `phase=strike|assault`
- `plan_strike_control` / `plan_assault_control`：BPEL 阶段专用，内部映射到对应 phase

### 3.2 大数据出仓与 `.gitignore`

**问题：** 仓库跟踪了约 60MB+ 的训练 CSV 和多份大型闭环结果 JSON，不利于 PR 与克隆。

**改动：**

- 从 git 取消跟踪（本地文件仍可保留）：
  - `data/xbd/processed/xbd_damage_features_train.csv`
  - `data/xbd/processed/xbd_closed_loop_result*.json`
  - 对应 train report
- `.gitignore` 增加对原始数据集、embeddings、SC2 Pack、可再生中间结果的忽略规则
- **仍跟踪**：小规模 fixtures、`mined_rules.json`、冻结模型 `models/sc2le_proxy_mission_model.*`、小样本 CSV/报告

### 3.3 README 补齐 zh Agent 入口

在根 `README.md` 增加：

- 业务角色说明中的 Execution Control / Closed Loop
- 项目结构中的对应目录
- 独立小节：端口、启动方式、skill 约定、local 验证命令、特征/训练脚本入口
- 明确大数据集由脚本本地生成，勿提交

### 3.4 依赖与测试适配

- `requirements.txt`：框架依赖与 ML 依赖分组注释，便于「只跑框架」时识别可选包
- 集成测试补齐 main 协议必填字段：`schema_version`、`required_skill`
- Closed Loop 测试改用临时幂等库，避免共享 `.a2a_state` 缓存串扰
- 验证：`tests/test_execution_control_integration.py` + `test_closed_loop_integration.py` + `test_mission_feature_module.py` → **27 passed**

---

## 4. 关键文件清单（本轮）

```text
.gitignore
README.md
ZH_BRANCH_NOTES.md          # 本文档
a2a_protocol/server.py
closed_loop_agent/main.py
execution_control_agent/main.py
requirements.txt
tests/test_closed_loop_integration.py
tests/test_execution_control_integration.py
data/xbd/processed/*        # 大型产物取消跟踪
```

合入 main 时另有大量框架文件进入 `zh`（Gateway、SDK、监控、协议契约等），详见 `c963bb9` 的完整 diff。

---

## 5. 如何本地验证

```bash
# 协议 / Agent 单测
python -m pytest tests/test_execution_control_integration.py \
  tests/test_closed_loop_integration.py \
  tests/test_mission_feature_module.py -q

# Local 模式跑通含 EC + Closed Loop 的 BPEL
python -u commander_agent/main.py \
  --mode local \
  --workflow bpel \
  --workflow-file beachhead_workflow \
  --mock-eval-score 75

# 可选：闭环演示脚本
python scripts/run_xbd_closed_loop_demo.py
python scripts/run_sc2le_closed_loop_demo.py
```

---

## 6. 后续建议（未在本轮做完）

1. **拆分** `closed_loop_core.py`（约 2500 行）为 damage / mission / loop / io 子模块  
2. **上游 Agent** 统一输出结构化 `output_data`，减少闭环特征 fallback  
3. **SC2LE** 从 metadata 公式推进到 event-level 特征  
4. 为 EC / Closed Loop 补 **双实例 failover** 演示，与主框架能力对齐  
5. 增加最小 CI（至少跑上述三组测试）
6. algolib 模式下补齐闭环多 cycle 与完整训练指标回传（当前 algolib 路径为单轮服务编排）

---

## 7. 本轮算法库对接与心跳

详细修改说明见：

[`doc/ZH_ALGOLIB_HEARTBEAT_CHANGES.md`](doc/ZH_ALGOLIB_HEARTBEAT_CHANGES.md)

摘要：

- 新增 `algolib_bridge/`，支持 `local/algolib` 后端与 `direct/gateway` 传输
- EC 整包调用 `execution_control_planner`；CL 分调四个算法服务
- 默认仍 local；算法库失败默认可降级
- EC/CL/recon/artillery/assault 统一 `AgentRuntimeSDK` 注册与心跳

```powershell
python -m unittest tests.test_algolib_bridge tests.test_execution_control_integration tests.test_closed_loop_integration -q
```

---

## 8. 一句话总结

`zh` 已与最新 `main` 对齐，并在此基础上完成 skill 发现契约修复、仓库瘦身、文档补齐，以及 **local/algolib 双后端 + 双传输 + 心跳 SDK**；领域算法链路（执行控制 + 无泄漏闭环代理模型）保持可用，可按第 5/7 节与详细修改文档做回归验证。
