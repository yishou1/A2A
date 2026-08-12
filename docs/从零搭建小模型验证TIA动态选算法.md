# 从零搭建小模型并验证 TIA 动态选算法

面向：**本机还没有小模型**，希望尽快验证「小模型 → 动态选算法库算法」是否可行。  
推荐路径：**Ollama（本机）+ 开源小模型**，无需云 API Key。

---

## 一、整体链路（你要验证的是什么）

```text
你安装的小模型 (Ollama)
        │  OpenAI 兼容接口 /v1/chat/completions
        ▼
TIA Planner (ENABLE_LLM + TIA_ALGORITHM_PLANNER=llm)
        │  GET /algorithms（可选）
        ▼
算法库 Gateway :8088  POST /run
        │
        ▼
各算法 /predict (9020–9030)
```

验证目标：小模型返回一份 `algorithm_calls` 计划，且必含检测/EDL/跟踪。

---

## 二、从无到有：安装小模型（约 10～20 分钟）

### 步骤 1 — 安装 Ollama（Windows）

1. 打开官网下载页：https://ollama.com/download  
2. 安装 Windows 版 Ollama  
3. 安装完成后，开始菜单应能看到 Ollama；或新开 PowerShell 执行：

```powershell
ollama --version
```

能输出版本号即安装成功。

> 也可用 winget（若可用）：  
> `winget install Ollama.Ollama`

### 步骤 2 — 拉取一个小模型

首次建议用体积较小、对指令跟随还可以的模型（任选其一）：

| 模型 | 命令 | 说明 |
|------|------|------|
| **推荐入门** `qwen2.5:3b` | `ollama pull qwen2.5:3b` | 体积较小，适合本机验证 |
| 更强一点 `qwen2.5:7b` | `ollama pull qwen2.5:7b` | 效果更好，显存/内存要求更高 |
| 与 lzh 测试接近 `qwen3:1.7b` | `ollama pull qwen3:1.7b` | lzh 单测里用过的名字 |

```powershell
ollama pull qwen2.5:3b
ollama list
```

### 步骤 3 — 确认服务在跑

Ollama 安装后一般会常驻。探测：

```powershell
curl http://127.0.0.1:11434/api/tags
```

应返回 JSON，里面的 `models` 含你刚 pull 的名字。

手动启动（若没起来）：

```powershell
ollama serve
```

### 步骤 4 — 先做一次「裸聊」确认模型可用

```powershell
ollama run qwen2.5:3b "用一句话介绍你自己"
```

能正常回复即可进入 TIA 验证。

---

## 三、配置环境变量（给 TIA 用）

在**同一个** PowerShell 窗口里设置（与模型名保持一致）：

```powershell
cd D:\a2a_project\A2A-main

$env:ENABLE_LLM="true"
$env:TIA_ALGORITHM_PLANNER="llm"
$env:LLM_PROVIDER="openai_compatible"
$env:TOOL_LLM_URL="http://127.0.0.1:11434/v1"
$env:TOOL_LLM_NAME="qwen2.5:3b"          # 必须与 ollama list 里的名字一致
$env:API_KEY="ollama"
$env:LLM_TEMPERATURE="0.1"
$env:LLM_MAX_TOKENS="1024"
$env:LLM_STRIP_THINKING="true"
$env:LLM_JSON_RETRY_COUNT="1"
$env:ALGOLIB_BASE_URL="http://127.0.0.1:8088"
$env:TIA_ALGOLIB_CALL_MODE="run"
```

换模型时只改 `TOOL_LLM_NAME`（例如改成 `qwen2.5:7b`）。

---

## 四、验证步骤（由易到难）

### 验证 A — 只测「小模型能否产出选算法计划」（推荐先做）

不要求算法服务全开；网关没有时会回退本地目录。

```powershell
.\.venv\Scripts\python.exe scripts\verify_tia_llm_lzh_ollama.py
```

脚本默认会读上面的环境变量。若你用的是 `qwen2.5:3b`，请确保已设置 `$env:TOOL_LLM_NAME="qwen2.5:3b"`。

**通过标准：**

- 打印 `[plan] mode=llm`
- `selected` 里至少有：  
  `battlefield_rtdetr_detector`、`edl_evidential_verifier`、`motr_neural_kalman_tracker`
- 生成文件：`data/output/verify_tia_llm_lzh/llm_plan.json`

### 验证 B — FakeLLM（不装模型也能测接线）

若暂时装不了 Ollama，可先证明编排逻辑通：

```powershell
$env:TIA_LLM_SMOKE_FAKE="1"
$env:TIA_ALGORITHM_PLANNER="llm"
.\.venv\Scripts\python.exe scripts\smoke_tia_llm_planner.py
```

这只能证明「按 plan 跳过算法」通，**不能**证明真模型可用。

### 验证 C — 完整链路（小模型 + 算法库网关）

```powershell
# 终端 1：算法包 + 网关
.\scripts\start_a2a_algorithm_services.ps1 -TiaOnly

# 终端 2：同样设置第三节环境变量后
.\.venv\Scripts\python.exe scripts\verify_tia_llm_lzh_ollama.py
# 期望 catalog_source=algolib:/algorithms
```

再进一步可跑端到端 smoke（需图像与推理环境）：

```powershell
$env:TIA_LLM_SMOKE_FAKE="0"
$env:TIA_ALLOW_LOCAL_FILE="1"
$env:TIA_EXECUTION_MODE="algorithm_library"
.\.venv\Scripts\python.exe scripts\smoke_tia_llm_planner.py
```

---

## 五、常见问题

| 现象 | 处理 |
|------|------|
| `无法连接到远程服务器` / 11434 | 先 `ollama serve`，再 `curl http://127.0.0.1:11434/api/tags` |
| `model not found` | `ollama pull <名字>`，`TOOL_LLM_NAME` 与 `ollama list` 完全一致 |
| 返回不是 JSON / planner 失败 | 加大 `LLM_JSON_RETRY_COUNT=2`；换 `qwen2.5:7b`；确认 `LLM_STRIP_THINKING=true` |
| 计划缺必选算法 | 正常情况下会自动补齐 detector/edl/motr；若仍失败看 `llm_plan.json` |
| 只有 CPU、3b 很慢 | 正常；首次推理更慢，可先用验证 A |

---

## 六、一键检查清单

- [ ] `ollama --version` 成功  
- [ ] `ollama list` 能看到模型  
- [ ] `curl http://127.0.0.1:11434/api/tags` 成功  
- [ ] 环境变量 `TOOL_LLM_URL` / `TOOL_LLM_NAME` / `ENABLE_LLM` / `TIA_ALGORITHM_PLANNER` 已设  
- [ ] `verify_tia_llm_lzh_ollama.py` 输出 `mode=llm` 且含三个必选算法  

做到以上，即可证明：**在「从无到有」装好小模型后，TIA 能用小模型动态选择算法库内算法。**
