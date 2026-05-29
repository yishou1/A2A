# A2A 多智能体协同作战系统：抢滩登陆场景 🏖️⚔️

本项目是一个基于多智能体（Multi-Agent）架构的模拟抢滩登陆作战系统。系统采用标准化的 **A2A (Agent-to-Agent) 通信协议** 实现各作战单位（Agent）之间的协同交互，并引入 **Nacos** 作为服务注册中心，支持作战任务的动态分发、战果评估以及动态重规划机制。

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

## 📂 项目结构规划 (待建)

```text
A2A/
├── README.md               # 项目说明文档
├── registry/               # Nacos 相关配置与客户端封装
├── a2a_protocol/           # A2A 通信协议标准实现库 (Discovery, Auth, sendMessage)
├── commander_agent/        # 决策大脑中心
├── recon_agent/            # 侦察兵 Agent
├── artillery_agent/        # 火力打击 Agent
├── assault_agent/          # 登陆突击 Agent
├── evaluator_agent/        # 战果评估 & 策略重算 Agent
└── docker-compose.yml      # 环境部署文件 (启动 Nacos, Auth Server等)
```

## 🚀 下一步开发计划
1. 初始化 Nacos 服务环境并构建基础的 Agent 服务注册与心跳接口。
2. 封装 `a2a_protocol` 标准库，提供 `agent-card` 暴露能力及 HTTP 通信流解析能力。
3. 实现各个业务 Agent 的具体控制逻辑。

## 🧪 Local 模式

如果只想验证 Commander Workflow，不想启动 Nacos 和各 Agent HTTP 服务，可以使用 Local 模式：

```bash
cd /home/yl/yl/jzz/A2A
./venv/bin/python -u commander_agent/main.py --mode local --mock-eval-score 40
./venv/bin/python -u commander_agent/main.py --mode local --mock-eval-score 75
```

- `40` 会触发 `RE-PLAN` 分支。
- `75` 会触发 `ASSAULT` 分支。

详细说明见 `docs/Local模式说明.md`。

## 🤝 团队协作与合并

如果你打算把这个仓库作为小组代码合并的基础，建议先统一接口、状态和测试口径。详细的合并接入规范见 `docs/小组代码合并规范.md`，里面整理了：

- 哪些模块适合作为公共基础层；
- 哪些协议字段必须稳定；
- 如何减少多人并行开发时的冲突；
- 合并前应该跑哪些测试和脚本。

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
