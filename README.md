# A2A + AMOS 一体化演示

本目录以尽量少的架构改动联通了 AMOS 仿真平台与 A2A Commander。当前可实际运行
“海上编队护航与要地防空”剧本：AMOS 产生因果仿真快照，Gateway 传给 Commander，
Commander 通过 Nacos 发现并协调 7 个独立 HTTP Agent，Agent 经 AlgoLib 加载并调用算法，
结果再投影回 AMOS 前端。模拟攻击必须由操作员明确授权。

源仓库、分支和提交版本见 [config/SOURCES.md](config/SOURCES.md)。

## 运行结构

| 组件 | 运行方式 | 地址 |
| --- | --- | --- |
| AMOS 仿真与前端 | WSL 原生 Python 进程 | <http://127.0.0.1:5000/> |
| Commander | WSL 原生 Python 进程 | <http://127.0.0.1:8021/supervisor> |
| AMOS/Commander Gateway | WSL 原生 Python 进程 | <http://127.0.0.1:8030/gateway/v1/health> |
| AlgoLib | WSL 原生 C++ HTTP 进程 | <http://127.0.0.1:8088/health> |
| 7 个 Agent | 7 个独立 WSL HTTP 进程 | 8102、10200–10205 |
| Nacos | Docker Desktop 容器 | <http://127.0.0.1:8848/nacos/> |
| 认证 mock | Docker Desktop 容器 | <http://127.0.0.1:8080/get> |

Docker 只用于固定 Nacos/Java 环境和认证 mock 的状态边界，Agent 本身不在容器中。
Windows Docker Desktop 开启 WSL Integration 后，WSL 中的客户端会连接 Windows 上的
Docker Engine，不需要在 WSL 中再运行一套 daemon。可用 `docker info` 验证连接。

## 首次安装

推荐在 Windows 11 + WSL2 环境中运行。新电脑需要预先安装：

- Git 和 Miniforge/Anaconda；
- Docker Desktop，并为当前 WSL 发行版启用 WSL Integration；
- 可访问 PyPI、Conda Forge、Docker Hub 和 Azure OpenAI 的网络。

Nacos 自带 Java 运行时的 Docker 镜像，因此宿主机不需要单独安装 Java。克隆整合分支：

```bash
git clone --branch wangyu/a2a-amos-integrated --single-branch \
  https://github.com/yishou1/A2A.git a2a-amos-integrated
cd a2a-amos-integrated
```

运行统一初始化脚本。脚本会创建或更新 Python 3.11 Conda 环境 `a2a`、安装完整的
Commander/算法服务/AMOS 依赖、安装 PyTorch，并编译 AlgoLib。默认安装 CPU 版 PyTorch；
需要本地 GPU 跑 Qwen 时，先指定 CUDA wheel 源：

```bash
./scripts/bootstrap.sh
# 或：
A2A_TORCH_INDEX_URL=https://download.pytorch.org/whl/cu121 ./scripts/bootstrap.sh
```

环境入口文件分别是：

- `environment.yml`：Python、CMake、Ninja 和 C++ 编译器；
- `requirements.txt`：Commander、算法 HTTP 服务和 AMOS 的完整 Python 依赖；
- `scripts/bootstrap.sh`：按正确顺序安装依赖并编译 AlgoLib。

需要手动安装时，执行与脚本等价的命令：

```bash
conda env create -f environment.yml
conda run -n a2a python -m pip install torch torchvision \
  --index-url https://download.pytorch.org/whl/cu121
conda run -n a2a python -m pip install -r requirements.txt
conda run -n a2a cmake -S commander -B commander/build -G Ninja \
  -DALGOLIB_BUILD_TESTS=ON -DALGOLIB_WITH_ONNXRUNTIME=OFF
conda run -n a2a cmake --build commander/build -j2
```

`ALGOLIB_WITH_ONNXRUNTIME=OFF` 只关闭 C++ 进程内 ONNX Runtime；本演示的 Python 算法服务
仍使用 `onnxruntime`，足以完成实际演示，并减少额外 C++ SDK 依赖。

复制环境模板并只在本机填写密钥：

```bash
cp .env.example .env
```

关键配置如下。`.env` 已被 `.gitignore` 排除，脚本不会打印密钥。

```dotenv
LLM_PROFILE=azure
ENABLE_LLM=true
AZURE_OPENAI_ENDPOINT=https://wysengine.openai.azure.com/
AZURE_OPENAI_DEPLOYMENT=4o-mini
AZURE_OPENAI_API_VERSION=2024-12-01-preview
AZURE_OPENAI_API_KEY=<在本机填写>
```

`text-embedding-3-small` 配置仅为后续接入预留，当前代码不会调用 Azure embedding。
Decision/Compliance RAG 默认使用 SQLite 索引和关键词排序；ONNX RAG 开关默认关闭，仓库也
没有提供 `models/rag/embedding.onnx`。TIA 的 SynapseRAG 在收到知识文档时使用
Sentence Transformers 的 `paraphrase-MiniLM-L6-v2`，首次使用可能需要下载模型。

## 启停

不调用 Azure、使用确定性算法规划启动：

```bash
./scripts/start.sh --offline
```

推荐给大多数开发者：使用 Azure/API-hosted GPT-4o-mini 动态选择算法，不需要本地部署大模型。
密钥缺失时启动会直接失败：

```bash
./scripts/start.sh --llm-profile azure --require-llm
```

可选：使用本地 Qwen3-1.7B 动态选择算法，并优先使用 CUDA/GPU；如果 `127.0.0.1:11435`
没有现成 OpenAI-compatible 服务，启动脚本会自动拉起 `scripts/local_qwen_openai_server.py`：

```bash
./scripts/start.sh --llm-profile local-qwen-gpu --require-llm
```

快速切换方式：

- `./scripts/start.sh --llm-profile azure --require-llm`：Azure OpenAI，使用 `.env` 中的 `AZURE_OPENAI_*`；
- `./scripts/start.sh --llm-profile local-qwen-gpu --require-llm`：本地 OpenAI-compatible Qwen，默认 `http://127.0.0.1:11435/v1`；
- `./scripts/start.sh --llm-profile offline` 或 `./scripts/start.sh --offline`：不调用 LLM，使用固定/确定性算法规划。

也可以继续使用环境变量 `LLM_PROFILE=azure|local-qwen-gpu|offline`；命令行
`--llm-profile` 优先级更高。切换 profile 时先执行 `./scripts/stop.sh`，再重新
`./scripts/start.sh`，否则已经运行的 Agent 进程不会重新加载新环境。

Act 阶段是否也使用 LLM 选算法由 `A2A_ACT_AGENT_LLM` 控制。默认跟随全局 LLM 开关；
如需演示速度优先，可在 `.env` 中设为 `false`。

查看状态、停止应用但保留 Nacos，或全部停止：

```bash
./scripts/status.sh
./scripts/stop.sh --keep-nacos
./scripts/stop.sh
```

日志位于 `.runtime/logs/`。启动脚本会先验证并激活算法包，再启动各个独立 Agent。
LLM 模式默认允许单个 Agent 请求执行 180 秒，以覆盖 Azure 调用和首次模型冷启动；可用
`A2A_REQUEST_TIMEOUT` 自行覆盖。

## OODA / F2T2EA 与四检查点

界面中的两套阶段不是两条独立流程，而是同一个任务闭环的两种视图：

| OODA | F2T2EA | 本系统中的工作 |
| --- | --- | --- |
| Observe | FIND + FIX | 汇集当前传感器资料、检测接触、关联身份并固定目标位置 |
| Orient | TRACK | 维持航迹、融合新观测、评估和排序威胁 |
| Decide | TARGET | 分配任务与资源、生成方案、检查交战规则并请求人工授权 |
| Act | ENGAGE + ASSESS | 执行已授权的模拟攻击、监控执行状态并评估效果 |

`FIND` 与 `FIX` 的边界是“发现接触”与“形成可持续引用的目标实体”：只有观测到接触属于
FIND；完成多源关联、定位和身份候选固定后才进入 FIX。OODA 的 Observe 必须等 FIND 和 FIX
都完成才显示完成。

仿真与分析由 Director 同步推进。到达检查点后，Director 冻结一份因果快照并启动当前
OODA 阶段的 BPEL；在后端分析期间，仿真可以继续播放当前阶段的剩余过程，但最多推进到
下一 F2T2EA 阶段边界前。工作流没有真实完成或活动校验失败时绝不会进入下一阶段，等待过久
则在边界前暂停。前端在等待期间保持状态流连接并平滑插值地图标记，不提供独立的“提交分析”
动作，也不会在后端没有结果时显示完成。

打开 AMOS，选择“海上编队护航与要地防空”。正式链路按以下因果检查点运行：

1. `MAR-CP-PERCEPTION`：海空观测融合输入就绪。
2. `MAR-CP-ASSESS`：敌方高速艇与民用渔船识别输入就绪。
3. `MAR-CP-PLAN`：攻击方案、禁射规则和人工复核请求就绪。
4. `MAR-CP-CLOSE`：模拟攻击后的毁伤评估与渔船安全复核就绪。

前三个检查点不会自动开火。前三阶段工作流分别包含 3、3、4 个 BPEL 活动（含外层
`sequence`），最后的 Act 工作流包含 3 个活动。后端返回 `pending_review/review_required`
后，Director 会在 ENGAGE 入口暂停并等待操作员授权。AMOS 服务端仍会
执行三层校验：目标必须被后端确认为敌方、不得属于民用禁射类别、请求必须携带操作员的
明确批准。即使请求标记为批准，渔船仍会被拒绝。

## 一键验收

只检查服务、7 个不同 PID 和 Nacos 注册：

```bash
conda run -n a2a python scripts/verify.py
```

创建一个全新 run，执行四个工作流，并由当前命令的操作员显式批准一次模拟发射：

```bash
conda run -n a2a python scripts/verify.py \
  --run-scenario --authorize-fire --workflow-timeout 240
```

验收默认使用因果检查点快进：仿真引擎仍真实计算并生成各检查点的数据，但不等待检查点之间
的墙钟时间；每到一个检查点仍会等待 Commander、独立 Agent、AlgoLib 和 Azure 的真实调用
完成后才进入下一阶段。它检查四个阶段各自的 3/3/4/3 个 BPEL 活动、两类目标识别、
ENGAGE 人工授权门、武器终态和第四检查点。报告写入 `.runtime/verification-last.json`。
省略 `--authorize-fire` 时，验收会停在授权门并确认系统不会自行进入 ENGAGE；带该参数才会
完成整个剧本。

需要按界面演示相同的 32 倍速逐秒播放时，增加 `--wall-clock`：

```bash
conda run -n a2a python scripts/verify.py \
  --run-scenario --authorize-fire --workflow-timeout 240 --wall-clock
```

默认快速验收的耗时主要取决于真实 Agent 和 Azure 请求，不再包含约 175 秒的检查点间等待。
在 `--require-llm` 模式启动后，可额外传入 `--require-llm-evidence`，强制检查 TIA 的原始
LLM 规划、AlgoLib 活跃目录来源、Track Threat 的 Azure planner 模式以及零 fallback。

## 开发与提交

运行当前整合链路相关测试：

```bash
conda run -n a2a python -m pytest -q amos-platform/tests
conda run -n a2a --cwd commander python -m pytest -q \
  tests/test_commander_gateway.py \
  tests/test_bpel_workflow.py \
  -k 'not test_demo_script_runs_both_workflows'
conda run -n a2a ctest --test-dir commander/build --output-on-failure
```

上述 Commander 命令排除了原仓库的旧沙滩突击 Demo；该用例依赖当前分支未包含的
`artillery_agent`，与海上编队整合链路无关。

日常开发从整合分支创建个人分支，避免直接向共享分支强推：

```bash
git switch wangyu/a2a-amos-integrated
git pull --ff-only
git switch -c <姓名>/<功能名>
```

`.env`、`.runtime/`、数据库、日志、PID、构建目录和本地模型权重已被忽略。提交前仍应运行
`git status --ignored`，确认没有使用 `git add -f` 把密钥或运行产物加入暂存区。

## 常见问题

- `docker info` 失败：在 Docker Desktop 的 Resources > WSL Integration 中启用当前发行版。
- Nacos 已启动但发现不到 Agent：查看 `.runtime/logs/agent-*.log`，然后运行验收脚本检查角色集合。
- `--require-llm` 报密钥为空：确认 `.env` 中 `AZURE_OPENAI_API_KEY` 非空，且没有给值加错误的空格。
- 端口被占用：先运行 `./scripts/stop.sh --keep-nacos`，再检查占用进程后重启。
