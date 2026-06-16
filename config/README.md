# 战术情报 Agent 配置

| 文件 | 用途 |
|------|------|
| `default.yaml` | **真实推理**（`use_mock: false`），加载 RT-DETR / Mask2Former / ImageBind 等 |
| `mock.yaml` | **轻量联调**（`use_mock: true`），无 GPU / 无 torch 时使用 |

## 真实推理（default.yaml）

## 验收
cd D:\a2a_project\A2A-main
$env:PYTHONPATH = "."; $env:TIA_PORT = "8016"
.\.venv\Scripts\python.exe scripts\demo_tactical_intelligence_acceptance.py --pause


$env:PYTHONPATH = "."
$env:TIA_CONFIG = "config\default.yaml"
$env:TIA_ALLOW_INLINE_FRAMES = "1"
$env:TIA_NACOS_REGISTER = "0" 
$env:TIA_PORT = "8016"
.\.venv\Scripts\python.exe tactical_intelligence_agent\main.py


```powershell
cd D:\a2a_project\A2A-main

# 1. 基础 + 推理依赖
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe -m pip install -r requirements-real.txt
.\.venv\Scripts\python.exe -m pip install git+https://github.com/facebookresearch/ImageBind.git

# 2. 预下载模型权重（与 default.yaml 中 inference 字段对应）
$env:PYTHONPATH = "."
$env:TIA_CONFIG = "config\default.yaml"
.\.venv\Scripts\python.exe scripts\download_models.py

# 3. 启动 Agent
$env:TIA_NACOS_REGISTER = "0"
$env:TIA_PORT = "8016"
.\.venv\Scripts\python.exe tactical_intelligence_agent\main.py
```

可选环境变量：

| 变量 | 默认 | 说明 |
|------|------|------|
| `TIA_CONFIG` | `config/default.yaml` | 配置文件路径 |
| `TIA_PORT` | `8015` | HTTP 服务端口 |
| `TIA_NACOS_REGISTER` | `1`（main.py） | `0` 跳过 Nacos 注册 |
| `OPENAI_API_KEY` | — | SynapseRAG 使用 `llm_model: gpt-4o-mini` 时需要 |

`default.yaml` 中 `inference.device: auto` 会自动选择 CUDA（若可用）否则 CPU。

## Mock 联调（mock.yaml）

```powershell
$env:TIA_CONFIG = "config\mock.yaml"
.\.venv\Scripts\python.exe tactical_intelligence_agent\main.py
```

仅安装 `requirements.txt` 即可，无需 torch。

## 红蓝态势 + 完整数据处理流程（iron_valley）

场景文件：`scripts/simulation/scenarios/iron_valley_red_blue.yaml`（铁谷谷地红蓝对抗，可编辑单位/位置/阶段叙述）。

### 步骤 1：仅导出红蓝态势（不跑推理）

```powershell
$env:PYTHONPATH = "."
.\.venv\Scripts\python.exe scripts\build_situation.py
```

输出：`data/output/situation/OP-IRON-VALLEY-2026-<时间>/`

### 步骤 2：四阶段仿真 + 三技能流水线 + 验收

```powershell
$env:PYTHONPATH = "."
$env:TIA_CONFIG = "config\mock.yaml"      # 无 GPU 快速联调
# $env:TIA_CONFIG = "config\default.yaml" # 真实神经网络
.\.venv\Scripts\python.exe scripts\run_simulation.py
```

| 阶段 | 红蓝态势 | 传感器数据 | 算法 |
|------|----------|------------|------|
| `01_recon` | 侦察阶段 | EO + SAR | RT-DETR + ImageBind |
| `02_contact` | 接触阶段 | EO + SAR + 前沿报告 | Siamese Mask2Former / SupCon / SynapseRAG |
| `03_bda` | 打击后评估 | 毁伤场景 vs 参考帧 | Siamese Mask2Former |
| `04_jammed` | 强电磁干扰 | EO + 报告 | MARL `fhss_backup` 抗干扰路由 |

输出目录 `data/output/campaign/OP-IRON-VALLEY-2026-<时间>/`：

```
situation/00_red_blue_overview.json   # 红蓝总览
situation/01_recon_situation.json     # 各阶段态势
intelligence/01_recon.json …          # 各阶段情报包
downstream/latest_for_agents.json     # 下游 Agent 直接读取
campaign_manifest.json                # 阶段清单
validation_report.json                # 内置验收结果
```

退出码 `0` 表示四阶段全部验收通过。
