# Docker Desktop 一键运行 613 A2A

## 快速开始

适用环境：Windows x64、Docker Desktop 的 Linux 容器模式、首次启动可访问 Docker Hub、PyPI、npm、GitHub、Hugging Face 与 Ollama 模型仓库。双击 `docker-tools` 目录中的 `启动Docker环境.bat`，等待命令窗口显示 AMOS 的 `Ready` 地址（默认 `http://127.0.0.1:5000/`）。首次构建和模型下载可能较久，窗口会打印进度。停止时双击 `docker-tools\停止Docker环境.bat`。根目录的 `启动项目.bat` 与 `停止项目.bat` 保留原有本机启动逻辑。

```powershell
git clone --branch integration/maritime-algolib --single-branch https://github.com/yishou1/A2A.git 613
cd 613
.\docker-tools\启动Docker环境.bat
```

项目启动不需要宿主 Python、Node、Conda、Java、Ollama 或 `.env`。非 Windows 宿主可按以下顺序启动，并检查模型下载和推理探测的退出码：

```bash
docker compose build
docker compose up -d ollama nacos auth-mock
docker compose up --force-recreate --no-deps --exit-code-from qwen-init qwen-init
docker compose up -d --wait a2a-core amos
docker compose exec -T a2a-core python scripts/docker/healthcheck.py full
```

需要验收剧本二时，在运行中的容器内执行以下命令。它会创建新的剧本运行，并显式授权一次模拟攻击：

```powershell
powershell -File scripts/docker-compose.ps1 exec -e AMOS_BASE_URL=http://amos:5000 a2a-core python scripts/verify_coastal.py
```

宿主已安装 Python 3.11 或更新版本时，也可运行 `python .\scripts\verify_coastal.py`；修改宿主端口后，需同步设置 `AMOS_BASE_URL` 和 `A2A_GATEWAY_URL`。

## 服务与数据

| 服务 | 用途 | 宿主访问 |
| --- | --- | --- |
| `amos` | 剧本及网页 | <http://127.0.0.1:5000/> 与 `/algolib/` |
| `a2a-core` | Commander、Gateway、七个 Agent、AlgoLib | <http://127.0.0.1:8030/gateway/v1/health> |
| `ollama`、`qwen-init` | 模型服务、按需拉取并执行 JSON 推理探测 | 仅容器内网 |
| `nacos`、`auth-mock` | 注册发现和认证模拟 | 仅容器内网 |

`ollama_models` 命名卷保存 `qwen3:1.7b`。`a2a_runtime`、`a2a_state`、`amos_instance` 保存算法注册与执行日志、工作流和剧本运行记录；`hf_cache` 保存 TIA 检索嵌入模型。再次启动会复用卷并仍执行 Qwen 推理探测。停止脚本不会删除卷。Windows 启动器根据克隆目录的绝对路径生成稳定的 ASCII Compose 项目名，因此目录包含空格或中文也可用；不同目录拥有独立卷。手动查看或操作容器时请使用 `scripts/docker-compose.ps1`，以便选中同一项目。

Qwen 在 Compose 栈运行期间保持加载。CPU 冷加载在本机约需 28 秒，并可能在 A2A 已占用大量内存后被系统终止；启动器因此先停止旧的 AMOS/A2A 应用容器（卷保留），完成模型加载与真实推理探测，再启动应用，并避免空闲期间卸载模型。AlgoLib 的模型客户端会对短暂的连接或 Docker DNS 失败做两次短间隔重试；HTTP 错误、模型错误和非法 JSON 不会被当成成功。

默认 CPU 模式采用 Ollama 官方 `0.34.1-rocm` 镜像，它比同版本包含 CUDA 库的标准镜像小；没有可用 GPU 时由 Ollama 选择 CPU 推理。`-Gpu` 改用官方 `ollama/ollama:0.34.1` 镜像。两种模式使用同一 Compose 模型卷，且都必须通过实际 JSON 推理探测。

一键入口明确设置 `local-qwen`、Ollama 兼容 API、`qwen3:1.7b` 和严格 LLM 模式，因此本机已有的 Azure `.env` 不会覆盖它。CUE 的 Tactical Intelligence 与 PLAN 的 Task Scheduling 必须产生真实 Qwen 调用和可关联的 `llm_call_id`；Track Threat 及启用模型的 Act 阶段也要求模型请求成功。Decision Planning 与 Compliance 目前不启用 LLM 算法选择。模型不可用或响应不合法时，业务工作流失败，不标为模型规划成功。审计日志保存在核心容器的 `/app/.runtime/llm_audit.jsonl`，只记录模型、Agent、工作流、调用标识和结果，不记录提示词、密钥或完整响应。

本镜像默认 `TIA_USE_MOCK=1`，因为仓库没有提交 `commander/models/checkpoints/*.pt` 等专用感知权重。它让剧本所需的部分感知算法按现有模拟实现运行；**Qwen 规划请求仍是真实推理**。这套环境适合调试 A2A 链路与剧本逻辑，不能用来声称未提供权重的感知模型达到真实精度。`paraphrase-MiniLM-L6-v2` 在 A2A 启动前预取到缓存卷；断网重启需先完成一次在线启动。

## 验收剧本二

启动完成后运行前述容器内验收命令，或使用宿主 Python 执行 `python scripts/verify_coastal.py`。脚本逐个推进 `CJR-CP-CUE`、`IDENTIFY`、`FUSION`、`PLAN`、`ENGAGE`、`CLOSE`，分别等待对应 `workflow_id` 到 `completed`，检查工作流、算法证据与 CUE/PLAN 的 Qwen 调用标识。ENGAGE 必须显式授权模拟攻击；CLOSE 必须执行 `review_checkpoint` 人工复核。脚本会配置一个新的 `standard` 剧本运行并改变 AMOS 当前状态，因此请在没有其他操作者使用页面时运行。

```powershell
powershell -File scripts/docker-compose.ps1 ps
powershell -File scripts/docker-compose.ps1 logs --tail=100 a2a-core amos qwen-init ollama
powershell -File scripts/docker-compose.ps1 exec a2a-core sh -c 'tail -n 20 /app/.runtime/llm_audit.jsonl'
```

失败时启动器返回非零状态。先看 `qwen-init`、`ollama` 日志确定模型下载或探测是否完成；再看 `a2a-core` 日志及 `/app/.runtime/logs/` 下对应 Agent 日志。只打开 AMOS 页面或在 Ollama 中看到模型，均不代表业务调用已成功。

运行中模型故障的专项验收会主动停止 Ollama，并创建一个新的 CUE 工作流；请只在没有其他操作者使用页面时执行。脚本断言工作流明确失败、没有伪造 Qwen 成功记录，并在退出前重新启动 Ollama：

```powershell
python scripts/verify_ollama_failure.py
```

## 资源与网络

默认只用 CPU，不依赖 NVIDIA 驱动。首次启动会下载基础镜像、Python/Node 依赖、约 1.36 GB 的 Qwen 模型及 TIA 检索模型。建议宿主机至少 16 GB 内存、Docker Desktop 的 Linux 环境分配至少 8 GiB 内存，并在 Docker 数据盘预留 40 GB 空间；启动器在低于 7.5 GiB 时拒绝启动。本机 Docker 分配 7.7 GiB 时，常驻模型的验收样本约为 A2A 核心 4.0–4.6 GiB、Ollama 1.8–2.0 GiB、Nacos 0.42 GiB、AMOS 0.05–0.35 GiB。多次构建后镜像、命名卷与构建缓存约占 29 GB。

2026-09-28 在本机 CPU 环境进行的实测结果：无 `.git` 的独立项目副本放在含中文和空格的目录，使用全新 Compose 项目名和空模型卷，从启动到 `Ready` 共 1780 秒（29 分 40 秒），期间模型分块下载两次断流后续传成功；同一主项目缓存启动复用了模型，没有重复下载。剧本二最终运行 `run-5cc66665a7ad` 从配置到六检查点、授权、CLOSE 人工复核及导演完成共 602 秒（10 分 02 秒）。这些数值是本机与当时网络的实测值，不是其他机器的性能承诺。CPU 上模型推理可能耗时数分钟；配置中的 LLM 单次超时为 180 秒、工作流验收默认等待 900 秒。慢机器可通过启动器参数 `-StartupTimeoutSeconds` 延长 Compose 健康等待。
模型下载默认总时限为 3600 秒，单次连续 180 秒无进度会重新连接，最多重试 3 次，并保留 Ollama 命名卷中的已完成分块。网络慢时可在启动前设置 `QWEN_DOWNLOAD_TIMEOUT_SECONDS`、`QWEN_STALL_TIMEOUT_SECONDS` 和 `QWEN_PULL_ATTEMPTS`；下载时限内仍无法取得完整模型时，A2A 不会启动为健康状态。

有 NVIDIA GPU 且 Docker Desktop 已启用 GPU 容器支持时，可执行 `powershell -File scripts/docker-start.ps1 -Gpu`，它会加载 `compose.gpu.yaml`。普通双击入口始终按 CPU 模式启动。

如果 Docker Hub、PyPI、npm、GitHub、Hugging Face 或 Ollama 仓库不可达，应先配置 Docker Desktop 和网络代理，再重新运行一键脚本。Compose 仅使用固定服务名在容器间访问，代理的 `NO_PROXY` 覆盖本项目服务。已有 `.env` 中的 Azure 密钥不会传入默认一键栈。

若宿主端口被占用，可在启动前设置 `AMOS_HOST_PORT` 和 `A2A_GATEWAY_HOST_PORT`（默认分别为 `5000`、`8030`）；启动器会显示实际 AMOS 地址。修改端口后运行宿主验收脚本时，同时设置 `AMOS_BASE_URL` 和 `A2A_GATEWAY_URL`；容器内验收不受宿主端口影响。

模型与运行数据删除是显式操作：`powershell -File scripts/docker-reset.ps1 -Target Model` 只清模型卷，`-Target Runtime` 清工作流与剧本状态卷，`-Target All` 清全部卷；脚本会先停止本 Compose 项目。重新启动后，模型卷缺失会触发再次下载。
