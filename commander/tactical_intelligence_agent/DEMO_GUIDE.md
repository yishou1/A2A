# 战术情报 Agent — 分步可视化验收指南

> **接入原理、验收对照与源码说明**：见 [`PROJECT_SYNC.md`](PROJECT_SYNC.md)

---

## 准备

```powershell
cd D:\a2a_project\A2A-main
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe -m pip install -r requirements-real.txt
$env:PYTHONPATH = "."
$env:TIA_CONFIG = "config\default.yaml"
```

---

## 阶段 A：Agent 协议验收（7 步，真实推理）

### 步骤 0 — 启动 Agent（终端 1，保持运行）

```powershell
$env:PYTHONPATH = "."
$env:TIA_CONFIG = "config\default.yaml"
$env:TIA_ALLOW_INLINE_FRAMES = "1"
$env:TIA_NACOS_REGISTER = "0"
$env:TIA_PORT = "8016"
.\.venv\Scripts\python.exe tactical_intelligence_agent\main.py
```

看到 `Uvicorn running on http://0.0.0.0:8016` 即可。首次启动会加载模型，约 20–30 秒。

### 步骤 1–6 — 分步演示（终端 2）

**推荐（逐步暂停，便于录屏）：**

```powershell
$env:PYTHONPATH = "."
$env:TIA_PORT = "8016"
.\.venv\Scripts\python.exe scripts\demo_tactical_intelligence_acceptance.py --pause
```

**或用手动 curl 逐步操作：**

#### 步骤 1 · 代码能跑

```powershell
.\.venv\Scripts\python.exe -c "import tactical_intelligence_agent.main; print('OK')"
```

#### 步骤 2 · Agent 能被发现

```powershell
curl http://127.0.0.1:8016/.well-known/agent-card
```

确认 JSON 中有 `"role": "tactical_intelligence"`。

#### 步骤 3 · 能接收任务 + 鉴权

无 JWT（应 401）：

```powershell
curl -X POST http://127.0.0.1:8016/sendMessage -H "Content-Type: application/json" -d "{\"workflow_id\":\"wf-1\",\"work_item\":\"wf-1:001\",\"command\":\"process_intelligence\"}"
```

带 JWT（应 200）— 用演示脚本更方便，或参考 `scripts/verify_commander_a2a.py` 里的 payload。

#### 步骤 4 · 返回结构

检查上一步 JSON 含：`work_item`、`status`、`role`、`message`。

#### 步骤 5 · 流式 SSE

演示脚本会逐条打印 4 个事件（perception → cognition → communication → Completed）。

#### 步骤 6 · 幂等

对同一 `work_item` 再 POST 一次，响应应与第一次完全相同。

### 步骤 7 · 不破坏控制面（可选）

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s tests -p "test_*.py" -v
```

---

## 阶段 B：红蓝态势 + 数据处理全流程

使用 `iron_valley_red_blue.yaml`（铁谷谷地红蓝对抗）。

### B1 · 仅导出红蓝态势

```powershell
$env:PYTHONPATH = "."
.\.venv\Scripts\python.exe scripts\build_situation.py
```

打开输出目录 `data/output/situation/OP-IRON-VALLEY-2026-<时间>/`，查看：

- `00_red_blue_overview.json` — 红蓝编制总览
- `01_recon_situation.json` … `04_jammed_situation.json` — 四阶段态势

### B2 · 四阶段仿真 + 三技能流水线

```powershell
$env:PYTHONPATH = "."
$env:TIA_CONFIG = "config\default.yaml"
.\.venv\Scripts\python.exe scripts\run_simulation.py
```

逐步观察终端输出：

```
>>> [01_recon] 侦察建立态势
>>> [02_contact] 接触与目标关联
>>> [03_bda] 打击后毁伤评估
>>> [04_jammed] 强电磁干扰下通信
验收通过 — 全部阶段满足目标数与路由要求
```

### B3 · 查看产物

```
data/output/campaign/OP-IRON-VALLEY-2026-<时间>/
├── situation/          ← 红蓝态势 JSON
├── intelligence/       ← 各阶段情报包
├── downstream/latest_for_agents.json   ← 下游 Agent 可读
├── campaign_manifest.json
└── validation_report.json
```

用 VS Code 或浏览器打开 JSON 文件即为**可视化**查看结果。

---

## 阶段 C：真实神经网络（可选）

```powershell
.\.venv\Scripts\python.exe -m pip install -r requirements-real.txt
$env:TIA_CONFIG = "config\default.yaml"
.\.venv\Scripts\python.exe scripts\download_models.py
.\.venv\Scripts\python.exe scripts\run_simulation.py
```

---

## 演示顺序建议（答辩 / 验收现场）

| 顺序 | 做什么 | 给观众看什么 |
|------|--------|--------------|
| 1 | 打开 `iron_valley_red_blue.yaml` | 红蓝编制与四阶段叙述 |
| 2 | `build_situation.py` | 态势 JSON 文件 |
| 3 | 启动 Agent（终端 1） | Uvicorn 日志 |
| 4 | `demo_tactical_intelligence_acceptance.py --pause` | 逐步 HTTP 请求/响应 |
| 5 | `run_simulation.py` | 四阶段情报包输出目录 |
