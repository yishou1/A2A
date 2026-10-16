# 613 项目容器化一键启动与 Qwen3 1.7B 集成计划

## 目标与使用边界

满足运行条件的开发者在任意目录克隆项目后，双击 `docker-tools` 目录中的 `启动Docker环境.bat`，即可自动构建并启动项目、下载 `qwen3:1.7b`，然后通过 AMOS 页面运行 A2A 剧本二 `coastal-joint-recon-strike`。根目录原有 `启动项目.bat` 和 `停止项目.bat` 保持本机原生启动逻辑不变。剧本执行时，指定的 Agent 必须向该模型发出真实推理请求；模型不可用时，启动或对应工作流明确报错，不能把固定规划回退显示成 Qwen 执行成功。

运行条件限定为：Windows x64、已安装 Docker Desktop 并能使用 Linux 容器、首次启动可访问 Docker 镜像源、GitHub、Python/Node 依赖源、Ollama 模型仓库及按需使用的模型源，以及足够的内存和磁盘空间。启动器负责在 Docker Desktop 已安装但未运行时尝试启动它。项目不要求开发者另外安装 Conda、Python、Node、Ollama，也不要求手工创建 `.env` 或提供云端模型密钥。默认使用 CPU；GPU 仅作为可选加速配置。实施后根据干净机器实测补充具体资源下限和首次启动耗时。

Ollama 的模型名固定为 `qwen3:1.7b`，不是 `qwen3:latest`。模型在同一 Compose 项目首次启动时下载到 Docker 命名卷，后续启动复用；从另一目录克隆通常会创建另一组项目卷，可能重新下载。现有 WSL 模型缓存只能作为个人可选加速来源，不能成为项目启动依赖。

## 目标架构

| Compose 服务 | 职责 | 就绪条件 |
| --- | --- | --- |
| `nacos` | 服务注册与发现 | Nacos 健康检查通过 |
| `auth-mock` | 现有认证模拟服务 | HTTP 健康检查通过 |
| `ollama` | 提供 Qwen 推理接口 | Ollama API 可访问 |
| `qwen-init` | 检查缓存；缺少模型时执行 `ollama pull qwen3:1.7b`；完成推理探测 | 模型存在且 `/v1/chat/completions` 成功后退出 0 |
| `a2a-core` | Commander、Gateway、Agent 和 AlgoLib 等既有 A2A 服务 | 关键内部服务及网关均就绪，且 `qwen-init` 成功 |
| `amos` | 剧本与前端页面 | 页面及 A2A 上游连接健康 |

`ollama` 使用独立命名卷保存 `/root/.ollama`，默认只在 Compose 内网开放。`a2a-core` 依赖 `qwen-init` 的 `service_completed_successfully`，并对 Nacos 等依赖使用健康检查；启动器只在 AMOS 与 A2A 都健康时报告成功。启动器须显式检查 `qwen-init` 退出码、下载日志与应用健康；分阶段启动或先验证所用 Compose 版本对一次性服务和 `up --wait` 的处理，避免仅凭 `running` 判定成功。首次下载、CPU 冷启动分别设置可配置超时和有界重试。

除模型卷外，还要持久化 AMOS 的 `amos-platform/instance/amos_runs.sqlite3`、Gateway 的 `.a2a_state/commander_gateway`、A2A 的 `.runtime/workflows`、AlgoLib 的 `.runtime/algolib`。容器启动改为幂等初始化，避免现有 `ALGOLIB_RESET_REGISTRY=true` 在重启时删除注册表与执行日志。`docker compose down` 保留这些卷；清除某类状态必须通过单独、明确的重置命令。

现有算法卡大量使用 `127.0.0.1`，第一版先把这些相互依赖的进程放在 `a2a-core` 容器内，使用可管理的前台入口和进程监督；AMOS 独立容器，便于后续修改剧本二并单独重建。内部算法卡可以保留本容器的 `localhost`，但 AMOS 目前会自行请求卡片中的健康地址，必须改为通过 A2A 核心汇总探活或安全地转换地址，否则它会探测到 AMOS 自身。跨容器接口使用 Compose 服务名并监听 `0.0.0.0`；剧本附件 URL 要让 A2A 容器可取，浏览器可见链接则要让 Windows 浏览器可取，必要时拆分内外地址或增加反向代理。检查 Nacos 注册地址、认证地址、媒体附件和 Gateway URL；若设置代理，`NO_PROXY` 至少覆盖 `localhost`、`127.0.0.1`、`ollama`、`nacos`、`auth-mock`、`a2a-core`、`amos`。

## 实施步骤

### 1. 构建可复现的容器环境

- 在仓库根目录新增 `compose.yaml`、A2A/AMOS Dockerfile、容器入口脚本和 `.dockerignore`。构建上下文明确排除 `.env`、本地模型、`.runtime`、缓存、`outputs/`、`.codex-build/` 及根目录的大型 ZIP 文件。
- 在镜像中安装 Python 3.11、项目 Python 依赖、C++ 构建工具并构建 AlgoLib；使用仓库根目录作为 Python 可编辑安装与 CMake 构建上下文，并固定关键依赖。CMake 的 `FetchContent` 需要访问 GitHub，构建步骤要记录或锁定所取版本。构建 `algorithmrepo/web/dist`，并在运行镜像中保留 AMOS 模板、静态文件及其源码相对路径；验收实际访问首页与 `/algolib/`。
- 建立剧本二运行资产清单与启动前检查：核对被引用但未提交到 Git 的 `commander/models/checkpoints/*.pt` 等权重的路径和来源；对 TIA 检索用的 `paraphrase-MiniLM-L6-v2` 明确采用自动预取加缓存卷，或改为已验证的无下载实现。其他模型只在实际调用链需要时纳入。任何额外模型都不能在首次剧本运行时才意外联网下载。
- 不把 Qwen 权重打进应用镜像；由 Ollama 服务与命名卷管理模型。固定或记录基础镜像、关键依赖和模型版本，避免构建依赖开发者本机环境。
- 容器入口不直接沿用当前会检查 Conda、尝试启动宿主 Docker、后台化进程后退出的启动方式；为容器提供可监督的前台进程及健康检查。

### 2. 让 Qwen 成为默认且必需的模型

- Docker 默认配置设为 `LLM_PROFILE=local-qwen`、`LLM_PROVIDER=openai_compatible`、`TOOL_LLM_URL=http://ollama:11434/v1`、`TOOL_LLM_NAME=qwen3:1.7b`。同时开启 `ENABLE_LLM`、`ALGOLIB_ENABLE_LLM`、`A2A_ACT_AGENT_LLM`、TIA 与 Task Scheduling 的 LLM 规划；Ollama 兼容接口所需的占位密钥由容器配置提供。明确本版必需的调用点为 CUE/Observe 的 TIA 与 PLAN/Decide 的 Task Scheduling；TrackThreat 与启用 LLM 的 Act Agent 同样按严格模式处理。DecisionPlanning/Compliance 当前被单独设置为 `DECISION_AGENT_ALGOLIB_LLM=false`，不把它们计入 Qwen 调用承诺；如需启用须另行补齐回退处理。
- 修改 `scripts/common.sh`、`scripts/start.sh` 和环境模板，使容器传入的配置不会被根目录 `.env` 意外覆盖。Compose 本身也会读取根目录 `.env` 做变量插值，因此一键配置中的必需模型参数要在 Compose 中显式固定，并测试已有 Azure `.env` 与宿主环境变量不会将容器切回 Azure；特殊端口或代理使用独立、可选的覆盖配置。保留 Azure、原生本机和离线模式作为显式选择；一键启动始终使用容器内 Qwen，不再尝试启动本机 Transformers 模型服务或默认要求 CUDA。
- `qwen-init` 对缓存命中也执行真实推理探测。A2A 启动预检再次确认目标模型可调用，并用项目真实规划客户端执行一次结构化 JSON smoke，核对 Qwen 的思考文本、`response_format`、输出解析与重试行为。根据 CPU 冷启动实测设置 `LLM_TIMEOUT_SECONDS`、`ALGOLIB_LLM_TIMEOUT_SECONDS`、A2A 请求超时、最大 token 和并发上限。
- 在剧本二涉及的 LLM 规划路径关闭静默固定规划回退：TIA 与 Task Scheduling 的 `fallback_to_fixed` 当前是配置默认值，需新增可由容器注入的严格配置；TrackThreat 启用 `TOOL_LLM_REQUIRED`；ClosedLoop、Decision/Compliance 及 AlgoLib 规划器的异常修复和本地回退也要审计。所有被声明为必需的 Qwen 规划若请求、响应解析或 JSON 结构失败，应在工作流中报错；其他算法本地回退单独标记，不能冒充完整 A2A 链路。
- 增加轻量调用审计：业务请求生成 `llm_call_id`，沿 Agent 输出和工作流证据传递；日志至少记录 `provider`、`model`、`workflow_id`、`llm_call_id`、Agent/阶段、请求结果和 `fallback_reason`，并区分启动探测与业务调用，不记录密钥及完整敏感输入。单独的模型存在、页面打开、模型启动探测或 `llm_plan.mode=llm` 都不能视为剧本真实调用证据。SynapseRAG 摘要目前使用独立的 `OPENAI_*` 配置，不计入本版 Qwen 调用证据；若将来启用，需单独接到 Ollama。

### 3. 提供真正的一键入口

- 在独立的 `docker-tools` 目录新增 Docker 专用启动批处理，调用 `scripts/docker-start.ps1`；保留根目录现有 `启动项目.bat` 和 `停止项目.bat` 原样，不改变原生启动逻辑。
- 启动器检查 Docker CLI 与 Linux daemon；若 Docker Desktop 已安装但未运行，尝试 `docker desktop start` 并等待就绪。随后执行 Compose 构建、拉取及启动，显示首次下载进度或 `qwen-init` 日志，检查其退出码，并在 AMOS、Gateway、Agent 与 AlgoLib 健康检查通过后显示 AMOS 地址。失败时返回非零退出码，并提示对应服务日志。只对本机发布 AMOS 等必要端口，避免 Nacos、认证服务和 Ollama 与宿主机服务冲突。
- 在 `docker-tools` 目录新增 Docker 专用停止批处理，执行 `docker compose down` 并保留模型和运行状态卷。提供等价命令行用法，便于其他系统通过同一 `compose.yaml` 启动。
- 更新 `README.md` 或新增 Docker 使用文档，修正当前过期的克隆分支示例，说明首次联网、代理配置、CPU/GPU 选择、日志查看、数据持久化、模型重置和常见失败处理。普通使用无需编辑 `.env`；代理等特殊设置使用可选配置。所有启动文件与文档须纳入 Git，才能在新机器上克隆得到。

### 4. 补齐剧本二端到端验收

- 为 `coastal-joint-recon-strike` 新增独立验证脚本，复用现有 HTTP 检查即可；现有 `scripts/verify.py` 固定了剧本一的四个 MAR 检查点、目标与授权分支，并依赖宿主 PID 文件，不能仅替换场景 ID。容器模式改用 Compose 状态、Agent 注册和 HTTP 健康检查。
- 每到剧本二的六个检查点，取该检查点提交的 `workflow_id`，独立轮询对应工作流直到终态，再断言结果为 `completed`；这些检查点当前允许仿真先于分析继续前进，CPU 验收可串行推进以避免并发模型请求过载。ENGAGE 的模拟授权与 CLOSE 的人工复核必须由验证流程显式执行。
- 从 `CJR-CP-CUE` 的 Observe/TIA 开始验证：同一 `workflow_id` 下既有成功的 `qwen3:1.7b` 业务请求及匹配的 `llm_call_id`，也有有效的 `raw_llm_plan`、`mode=llm` 和空 `fallback_reason`。PLAN/Decide 至少核对 Task Scheduling 的 Qwen 请求；其他启用模型的阶段也检查 `warnings`、`backend`、`reason` 等字段，确保没有把固定规划或本地回退误判为模型成功。
- 覆盖四类环境测试：①全新克隆、空 Docker 卷、CPU 模式一键构建和下载，目录名包含空格或中文；②保留卷的再次启动不重复下载，所有必需镜像和其他模型缓存齐备时可离线重启；③下载失败阻止 A2A 宣告就绪；④运行中停止 Ollama，工作流明确失败而不伪装成模型规划成功。额外检查 AMOS 首页、`/algolib/`、剧本附件跨容器下载及浏览器可见链接。
- 实测并在文档中写明最低 RAM、空闲磁盘、首次构建/下载耗时、CPU 模式六检查点耗时及相应超时设置；启动器对明显不足的资源给出可理解的提示。通过 `docker compose config`、模型结构化推理、容器健康检查和剧本二端到端验证后，才将一键启动标记为完成。

## 实施记录（更新至 2026-09-28，本地实现与验收完成）

- 已新增根目录 Compose 栈、CPU/GPU 配置、镜像构建、`docker-tools` 下的 Windows 专用一键启停入口、分类重置脚本、持久化卷、Qwen 下载和真实推理探测、容器健康检查及使用文档。根目录既有原生启动/停止批处理保持原样。Docker 启动不依赖宿主 Python、Node、Conda、Ollama 或 `.env`。
- 已用一个无 `.git` 的独立项目副本执行空卷验收。副本目录同时包含中文和空格，Compose 项目名与主项目隔离；BuildKit 的 Windows 非 ASCII 路径限制通过临时 ASCII NTFS junction 规避。全新模型卷下载 `qwen3:1.7b`、SHA 校验、manifest 写入、JSON 推理探测以及全栈健康检查全部通过，从启动到 `Ready` 实测 1780 秒。临时容器、卷和副本目录验收后均已清理。
- 缓存启动确认复用 `qwen3:1.7b`，没有重复下载；镜像和 Hugging Face 缓存齐备后，使用内部网络及离线环境变量重启仍全部健康。模型下载失败测试确认 `qwen-init` 非零退出，`a2a-core` 不会启动并宣告就绪。
- 运行中停止 Ollama 的专项测试确认 CUE 工作流进入暂停/失败状态，trace 记录 Tactical Intelligence 调用失败，且没有 Qwen 成功声明；测试脚本随后恢复 Ollama。短暂 Docker DNS/连接失败现在由 AlgoLib 模型客户端做两次短间隔连接重试，业务 HTTP 错误、模型错误和非法输出仍严格失败。
- Qwen 由启动器在 A2A 之前完成冷加载和推理探测，并在 Compose 运行期间保持驻留。再次启动时会先停止旧 AMOS/A2A 容器但保留卷，再准备模型；这样避免本机 7.7 GiB Docker 内存下，A2A 已占用约 4 GiB 后再冷加载模型导致 llama runner 被系统终止。镜像内 AlgoLib 桥接测试为 18 passed、3 subtests passed。
- 剧本二最终验收运行 `run-5cc66665a7ad` 已按顺序完成 CUE、IDENTIFY、FUSION、PLAN、ENGAGE、CLOSE。六个工作流活动数分别为 3、3、3、4、3、3，验证算法数分别为 4、2、3、6、10、10。CUE 的 Tactical Intelligence Qwen 调用为 `llm-4fa1f0f3d08142ffb270d1686fd60827`，PLAN 的 Task Scheduling Qwen 调用为 `llm-3cc9ba58407e47d297c9e3b7c2a18910`；二者均为 `qwen3:1.7b` 严格模式成功证据。未授权攻击返回 HTTP 409，授权后产生武器记录；CLOSE 人工复核和导演 `completed` 终态均已确认。从配置到最终完成实测 602 秒。
- AMOS 首页、`/algolib/`、AlgoLib API 和 Gateway 宿主端口均返回 HTTP 200。浏览器工作流视图把内部 `http://amos:5000/` 媒体地址改写为宿主 `127.0.0.1` 地址；实际场景图片可由 Windows 访问。A2A 容器通过 `http://amos:5000/` 下载生成的 SVG 证据，MIME 和 SHA-256 均与清单一致。
- 本机 Docker Desktop 分配 7.7 GiB，常驻运行样本约为 A2A 核心 4.0–4.6 GiB、Ollama 1.8–2.0 GiB、Nacos 0.42 GiB、AMOS 0.05–0.35 GiB；建议至少分配 8 GiB。多次构建后的 Docker 镜像、卷与缓存约占 29 GB，文档建议预留 40 GB。这些是本机实测，不是其他机器的性能保证。
- 仓库未包含 `commander/models/checkpoints/*.pt` 等部分感知权重；容器默认 `TIA_USE_MOCK=1`，仅 Qwen 规划要求真实推理。部分非剧本二算法卡也引用了未提交权重；这套容器环境用于调试 A2A 链路与剧本逻辑，不代表缺失权重的感知模型达到真实精度。
- 本地文件和验收均已完成。要让其他开发者通过远程仓库拉取得到这些文件，还需将当前工作区变更提交并推送到目标分支；本计划未自动执行提交或推送。

## 参考资料

- [Ollama `qwen3:1.7b` 模型页](https://ollama.com/library/qwen3:1.7b)
- [Ollama Docker 部署](https://docs.ollama.com/docker)
- [Ollama OpenAI 兼容接口](https://docs.ollama.com/api/openai-compatibility)
- [Docker Compose 服务启动顺序](https://docs.docker.com/compose/how-tos/startup-order/)
- [Docker Compose `up --wait`](https://docs.docker.com/reference/cli/docker/compose/up/)
- [Docker Compose 环境变量插值](https://docs.docker.com/compose/how-tos/environment-variables/variable-interpolation/)
- [Docker Compose 命名卷](https://docs.docker.com/reference/compose-file/volumes/)
- [Docker Desktop CLI 启动命令](https://docs.docker.com/reference/cli/docker/desktop/start/)
