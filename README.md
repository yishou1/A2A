# A2A 多智能体协同作战系统：抢滩登陆场景 🏖️⚔️

本项目是一个基于多智能体（Multi-Agent）架构的模拟抢滩登陆作战系统。系统采用标准化的 **A2A (Agent-to-Agent) 通信协议** 实现各作战单位（Agent）之间的协同交互，并引入 **Nacos** 作为服务注册中心，支持作战任务的动态分发、战果评估以及动态重规划机制。

当前版本已经补齐了工作流持久化、恢复接管、附件引用、心跳检测和 failover 演示等能力，整体上不再只是一次性流程演示，而是更接近一个可以恢复和接管的工作流控制面。

为了让 GitHub 线上版本保持自洽，补充性的设计稿和周报不再作为仓库内容对外发布；本 README 只保留当前可运行的主流程、脚本入口和协作约定。

## ✨ 当前版本能力

- 工作流状态支持落盘保存，并可通过 `workflow_id` 恢复。
- Commander 支持动态加载预写 BPEL，并使用线程池把一个 activity 并发派发给多个同类型 Agent 实例。
- 每次 A2A 调用都会携带当前工作流的 `work_list`，Agent 可查询自己收到的任务列表快照。
- 恢复 API 支持在新进程、新端口上继续接管同一个 workflow。
- 附件统一使用对象存储引用，避免把大文件直接塞进消息体。
- Agent 注册后会发送 5 秒心跳，并按心跳时间过滤失联实例。
- 已补充进程重启恢复、Commander failover/resume 演示脚本和回归测试。

## 🎯 业务场景 (抢滩登陆)

在一场抢滩登陆战役中，面临复杂的敌防信息、火力配属和登陆时机等挑战。整个作战流程被分解给多个不同专长的智能体：
- **指控大脑 (Commander Agent)**：负责全局统筹、任务分解与下发。
- **侦察单位 (Reconnaissance Agent)**：负责收集敌军火力部署、气象水文信息。
- **火力打击单位 (Artillery Agent)**：负责实施精准的炮火覆盖与压制。
- **突击步兵单位 (Assault Agent)**：负责滩头阵地的抢占。
- **战果评估单位 (Evaluator Agent)**：负责对前序任务的执行效果进行评估。

---

## 🏗️ 核心架构体系

### 1. 注册中心 (Nacos)
系统所有的 Agent 都在启动时将自身能力、基础画像注入并注册到 Nacos 注册中心。
* **服务注册**：Agent 启动完成后，注册自身的服务名、IP与端口。
* **能力标签**：除了网络信息外，还会注册专长标签（例如：`role=recon`, `firepower=heavy` 等），用于动态发现。
* **心跳与健康过滤**：Agent 会按固定间隔发送心跳，Commander 在发现服务时会过滤掉过期实例，避免拿到已经失联的节点。

### 2. A2A (Agent-to-Agent) 通信协议
参考标准的 A2A 技术协议，Agent 之间的交互完全遵循以下四大步骤：
1. **Agent Discovery**：通过对方提供的 `/.well-known/agent-card` 接口获取 Agent Card，解析对方支持的能力和接口地址。
2. **Authentication**：解析 Agent Card 中的 `securitySchemes`（如 `openIdConnect`），根据授权中心（Auth Server）提供的 URIs 获取经过 JWT 签名的鉴权 Token。
3. **sendMessage API**：同步或异步任务下发，携带 JWT 发起 POST `/sendMessage` 请求，获取初步的 `Task Response`。
4. **sendMessageStream API**：通过 POST `/sendMessageStream` 获取任务执行进度数据流（如 `TaskStatusUpdateEvent` 和 `TaskArtifactUpdateEvent`），实时了解执行状态（Working -> Completed）。

---

## ⚙️ 核心机制说明

### ① 动态发现：谁来执行下一步任务？
在动态战场环境中，Agent 不会硬编码下一个执行者的地址。
1. **意图解析**：Commander Agent 或上游 Agent 解析当前的作战需求（如“需要压制敌方坐标A的火力点”）。
2. **Nacos 服务发现**：根据能力要求，向 Nacos 查询匹配的健康智能体（查询标签 `role=artillery` 且 `status=idle`）。
3. **Agent Card 握手**：锁定某个推荐的 Agent 后，通过 `GET /.well-known/agent-card` 拉取它的详细协议手册。
4. **验证与调用**：通过 Auth Server 鉴权后，将火力打击任务派发给该 Agent。

### ② 动态评估与流程重规划
战场环境瞬息万变，一旦任务效果不佳，需要快速变更策略。
1. **任务评估机制**：每次火力覆盖或突破任务完成后，均会触发 **Evaluator Agent** 的后置评估。
2. **熔断与替换**：
   - 如果评估返回 `success_rate < 60%`，说明原定计划失败或执行 Agent 能力不足（可能被“压制”或“战损”）。
   - **重规划逻辑**：Commander Agent 收到低评估报告后，会立刻向 Nacos 请求替换为另一个健康的同类型 Agent 或者更高规格的 Agent（例如从“常规炮兵”升级为“航空轰炸Agent”）。
3. **流程变更**：如果多次火力打击均失败，Commander 将变更规划流程，暂停突击兵 (Assault) 的登陆，优先派遣“干扰型 Agent”或呼叫更多侦察支持。

---

## 📂 项目结构

```text
A2A/
├── README.md                # 项目说明文档
├── WORKFLOW_DESIGN.md       # 工作流设计说明
├── a2a_protocol/            # A2A 通信协议标准实现库
├── commander_agent/         # 决策大脑中心
├── recon_agent/             # 侦察兵 Agent
├── artillery_agent/         # 火力打击 Agent
├── assault_agent/           # 登陆突击 Agent
├── evaluator_agent/         # 战果评估 & 策略重算 Agent
├── registry/                # Nacos 相关配置与客户端封装
├── scripts/                 # 恢复 / failover 演示脚本
├── tests/                   # 回归测试
├── attachment_uploader.py   # 本地文件上传成附件引用
├── bpel_workflow.py         # BPEL 动态发现、解析与 work_list 生成
├── beachhead_workflow.bpel  # 可动态加载的抢滩登陆 BPEL
├── local_runtime.py         # 本地模拟运行时
├── workflow_payloads.py     # 附件与任务信封规范
├── workflow_state_store.py  # 工作流 checkpoint 存储
├── timing_probe.py          # 阶段耗时测试工具
└── docker-compose.yml       # 环境部署文件
```

## 🔄 工作流恢复与接管

这一版的核心变化，是把 Commander 从“只在内存里推进流程”升级成“可以落盘、可以恢复、可以接管”的工作流控制器。

- `workflow_id`：整条工作流的唯一标识，用来定位 checkpoint。
- `work_item`：单个工作项的唯一标识，用来做幂等和追踪。
- `activatity`：checkpoint 中统一使用的活动字段命名，例如 `current_activatity`、`workflow_activatity`。
- `workflow_state_store.py`：负责把工作流状态保存到 `.a2a_state/workflows/`。
- `commander_agent/recovery_api.py`：提供 `/health`、`/workflows/{workflow_id}`、`/resume`、`/takeover` 等恢复接口。
- `workflow_payloads.py`：规定附件必须是对象存储引用，避免内联大文件。
- `attachment_uploader.py`：把本地文件上传后转换成标准附件引用。

## 🧪 Local 模式

如果只想验证 Commander Workflow，不想启动 Nacos 和各 Agent HTTP 服务，可以使用 Local 模式：

```bash
cd /home/yl/yl/jzz/A2A
./venv/bin/python -u commander_agent/main.py --mode local --workflow bpel --workflow-file beachhead_workflow --mock-eval-score 40
./venv/bin/python -u commander_agent/main.py --mode local --workflow bpel --workflow-file beachhead_workflow --mock-eval-score 75
```

- `40` 会触发 `RE-PLAN` 分支。
- `75` 会触发 `ASSAULT` 分支。

`beachhead_workflow.bpel` 中不同角色严格按 `recon -> artillery -> evaluator -> assault` 顺序推进。炮兵节点使用 `dispatchMode="parallel"`，Commander 会把同一个火力任务并发派发给多个 `role=artillery` 实例。`--max-workers` 控制最大并发数。

项目中可以提前保存多套 BPEL，并在运行前选择：

```bash
./venv/bin/python -u commander_agent/main.py --list-workflows

# 基础登陆方案：炮兵同类并发，毁伤率阈值 60
./venv/bin/python -u commander_agent/main.py \
  --mode local \
  --workflow bpel \
  --workflow-file beachhead_workflow \
  --mock-eval-score 75

# 强化登陆方案：侦察、炮兵、突击均支持同类并发，毁伤率阈值 80
./venv/bin/python -u commander_agent/main.py \
  --mode local \
  --workflow bpel \
  --workflow-file reinforced_beachhead_workflow \
  --mock-eval-score 85

# 简化突击方案：省略评估分支，直接执行侦察、炮击和突击
./venv/bin/python -u commander_agent/main.py \
  --mode local \
  --workflow bpel \
  --workflow-file quick_strike_workflow
```

Agent 收到任务后可以查看当前 workflow 的任务列表快照：

```bash
curl http://127.0.0.1:8012/workflows/<workflow_id>/work-list
```

恢复和本地模式的更细说明已经合并到上面的流程描述中，直接按脚本运行即可。

恢复和 failover 演示脚本：

```bash
cd /home/yl/yl/jzz/A2A
./venv/bin/python scripts/demo_bpel_workflows.py
./venv/bin/python scripts/demo_resume_after_restart.py --reset
./venv/bin/python scripts/demo_commander_failover_resume.py --reset
```

- `demo_bpel_workflows.py` 用于展示并运行两套可选择的 BPEL。
- `demo_resume_after_restart.py` 用于演示同一个 workflow 在进程重启后继续执行。
- `demo_commander_failover_resume.py` 用于演示主 Commander 宕机后，在新端口启动备用 Commander 并 resume。

## 🤝 团队协作与合并

如果你打算把这个仓库作为小组代码合并的基础，建议先统一接口、状态和测试口径，核心原则如下：

- 哪些模块适合作为公共基础层；
- 哪些协议字段必须稳定；
- 如何减少多人并行开发时的冲突；
- 合并前应该跑哪些测试和脚本。

如果你是在本地维护这份仓库，仍然可以在工作区内保留更细的设计稿和周报，但线上仓库会保持精简，只保留代码与主流程说明。

## ⏱️ 阶段耗时测试

项目提供了 `timing_probe.py` 用于测量 A2A 调用链路中的关键耗时：

- `route_discovery`：通过 Nacos 发现目标 Agent 的路由耗时。
- `agent_card_discovery`：访问 `/.well-known/agent-card` 的耗时。
- `authentication`：模拟鉴权耗时。
- `task_submit_ack`：调用 `/sendMessage` 后收到接收响应的耗时。
- `task_time_to_first_event`：调用 `/sendMessageStream` 后收到首个 SSE 事件的耗时。
- `task_stream_completion`：流式任务从开始到完成的耗时。
- `phase_total`：单个阶段端到端总耗时。

启动 Nacos 和各 Agent 后运行：

```bash
cd /home/yl/yl/jzz/A2A
docker compose up -d
./start_agents.sh
```

在另一个终端执行耗时测试：

```bash
cd /home/yl/yl/jzz/A2A
./timing_probe.py --roles recon,artillery,evaluator --iterations 3
```

如果只想测试路由、Agent Card、鉴权和同步提交响应，不等待流式任务完成：

```bash
./timing_probe.py --roles recon,artillery,evaluator --no-stream
```

输出 JSON 便于后续分析：

```bash
./timing_probe.py --roles recon,artillery,evaluator,assault --iterations 5 --json
```
