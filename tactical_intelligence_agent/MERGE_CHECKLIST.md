# 合并 PR 验收清单（战术情报 Agent）

对应《项目同步说明》第七节。**推荐分步可视化验收**，见 [`DEMO_GUIDE.md`](DEMO_GUIDE.md)。

| # | 验收项 | 可视化验证方式 |
|---|--------|----------------|
| 1 | 代码能跑 | 演示脚本步骤 1 / `import tactical_intelligence_agent.main` |
| 2 | Agent 能被发现 | 步骤 2：`curl /.well-known/agent-card` |
| 3 | 能正常接收任务 | 步骤 3：无 JWT→401，有 JWT→200 |
| 4 | 返回结构符合协议 | 步骤 4：打印 JSON 含 work_item/status/role/message |
| 5 | 流式输出正常 | 步骤 5：SSE 逐条打印 4 阶段 |
| 6 | 恢复后能继续工作 | 步骤 6：同一 work_item 重复调用对比 |
| 7 | 不破坏已有测试 | 步骤 7：`unittest discover -s tests` |

## 一键分步演示（推荐）

```powershell
# 终端 1：启动 Agent
$env:PYTHONPATH="."; $env:TIA_CONFIG="config\mock.yaml"
$env:TIA_NACOS_REGISTER="0"; $env:TIA_PORT="8016"
.\.venv\Scripts\python.exe tactical_intelligence_agent\main.py

# 终端 2：逐步验收（--pause 每步暂停便于截图）
$env:PYTHONPATH="."; $env:TIA_PORT="8016"
.\.venv\Scripts\python.exe scripts\demo_tactical_intelligence_acceptance.py --pause
```

## 状态说明（第 6 项）

- **工作流 checkpoint** 由 Commander / `workflow_state_store.py` 负责，本 Agent **不**持久化 workflow 状态。
- **Agent 恢复语义**：Commander resume 后若重复下发同一 `work_item`，本 Agent 返回**缓存结果**（`sendMessage` + `sendMessageStream` 均可重放），避免重复副作用。

## 真实推理（default.yaml）

完整步骤见 [`config/README.md`](../config/README.md)。

```powershell
cd D:\a2a_project\A2A-main
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe -m pip install -r requirements-real.txt
$env:PYTHONPATH="."; $env:TIA_CONFIG="config\default.yaml"
.\.venv\Scripts\python.exe scripts\download_models.py
.\.venv\Scripts\python.exe tactical_intelligence_agent\main.py
```

## 一键验收（mock，CI/PR 用）

见 [`DEMO_GUIDE.md`](DEMO_GUIDE.md) 分步演示；不再使用 `test_merge_acceptance.py`。

## 能否直接用 A2A-main 自带接口合并？

**可以。** 已在 `d:\a2a_project\A2A-main` 实测：

- `tactical_intelligence_agent/service.py` 继承 **`a2a_protocol.server.A2ABaseAgent`**（与 recon/artillery 相同基类）
- A2A-main 原有测试 + 新增 8 项 merge 验收 **全部通过**

| A2A-main 自带接口 | 本 Agent |
|-------------------|----------|
| `A2ABaseAgent` | ✅ 直接继承 |
| `GET /.well-known/agent-card` | ✅ |
| `POST /sendMessage` + JWT | ✅ |
| `POST /sendMessageStream` | ✅ |
| `registry/nacos_manager.py` | ✅ `main.py` 注册 |
| `workflow_payloads.py` | ✅ 共用主项目文件 |

**无需改** `a2a_protocol/`、`registry/` 即可独立运行 Agent。

### 一键同步到 A2A-main（已验证）

```powershell
powershell -File scripts\prepare_a2a_branch.ps1 -TargetRoot D:\a2a_project\A2A-main
# 或手动 robocopy tactical_intelligence_agent + agent + config + tests
```

### 在 A2A-main 验收

```powershell
cd D:\a2a_project\A2A-main
$env:TIA_CONFIG = "config\mock.yaml"
$env:PYTHONPATH = "."
python -m unittest discover -s tests -p "test_*.py" -v   # 原有 + 新增全部通过
python tactical_intelligence_agent\main.py                  # 端口 8015
```

```powershell
cd d:\a2a_project\TacticalIntelligenceAgent
git init
git checkout -b feature/<你的名字>-tactical-intelligence
git add .
git commit -m "feat: tactical intelligence agent ready for A2A-main merge"
git remote add a2a <你的 A2A-main fork URL>
git push -u a2a feature/<你的名字>-tactical-intelligence
```

## 合并到 A2A-main 后（负责人 + 你联调）

1. 按 `INTEGRATION_SYNC.md` 第 4 节改 Commander / BPEL / `local_runtime.py`（由负责人确认）。
2. 在 A2A-main 根目录：

```bash
python -m unittest discover -s tests -p "test_*.py" -v
python scripts/verify_commander_a2a.py   # 需先启动 tactical_intelligence_agent/main.py
```

3. PR 描述中附上本清单 + `run_merge_acceptance.py` 输出截图。

## PR 描述模板

```markdown
## Summary
- 新增 `tactical_intelligence_agent`（单体 Agent：感知→认知→通信）
- 对齐 A2A Commander：`agent-card` / `sendMessage` / `sendMessageStream`
- 同一 work_item 幂等，支持 Commander resume 重放

## Test plan
- [x] `python scripts/run_merge_acceptance.py`（TIA 仓库）
- [ ] A2A-main 全量 `unittest discover`（合并后）
- [ ] Nacos remote 模式联调（可选）

## Commander 接入
- 需 BPEL 增加 recon → tactical_intelligence → artillery 步骤（见 INTEGRATION_SYNC.md）
```
