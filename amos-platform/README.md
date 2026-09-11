# AMOS Simulation Platform

前端现支持选择“海上编队护航与要地防空”“临海多域协同侦察与精确打击”和“空天海航母编队联合对陆打击”剧本。页面查看顺序、资料来源和运行状态说明见 [页面演示说明](PRESENTATION.md)。

AMOS 是一个面向后端联调的因果仿真展示端。它负责剧本编排、轻量运动与传感器观测、当前时刻输入投影，以及后端分析任务的输入、执行过程和输出展示；目标识别与风险判定由 A2A 后端完成。

## 当前范围

- 正式剧本：海上编队护航与要地防空、临海多域协同侦察与精确打击、空天海航母编队联合对陆打击
- 场景契约：`amos.scenario.v2`，统一声明 A1–A6、M01–M16 核心算法、M17–M20 工程模型、28 个功能点、条件检查点、故障注入和预期分支
- 运行方式：联调模式与服务端演示导演模式，固定随机种子可复现
- 正式后端入口：A2A Gateway（默认 `http://127.0.0.1:8030`）
- 诊断入口：可显式切换为 Commander 直连（默认 `http://127.0.0.1:8021`），不作为部署模式
- 实时通道：HTTP + SSE，状态轮询作为断线降级
- 数据边界：浏览器和 A2A 后端只能看到当前仿真时刻及以前产生的观测、历史轨迹和媒体
- 离线地图：本地 Protomaps 参照底图叠加 ETOPO 2022 地形/水深、山体阴影和等高/等深线，运行时零网络请求
- 持久化：仿真状态保存在进程内，验收运行档案写入本地 SQLite

## 目录

```text
src/amos_platform/
  agents/              Gateway/Commander 客户端、输入映射、结果投影、工作流视图
  api/                 Flask 应用及 sim/scenario/context/a2a/status 路由
  data/                当前剧本 builder、能力目录、场景校验和安全摘要
  domain/              观测、资产、目标与可见性规则
  frontend_state/      internal → operator/agent 因果投影
  fusion/              传感器观测关联与融合航迹
  realtime/            SSE 状态流
  runtime/             单进程运行上下文
  sensors/             传感器覆盖与观测生成
  simulation/          时钟、运动、场景装载、导演状态和世界状态
static/                地图、样式、页面脚本和剧本媒体
templates/             仿真联调页面
tests/                 因果边界、仿真、Gateway/Commander 契约测试
docs/                  当前架构、剧本及后端缺陷说明
```

根目录不再保留旧模块的兼容转发文件，也不包含下载缓存、多剧本数据导入器、WebSocket 服务或 TIA/TrackThreat 直连接口。

## 运行（一键启动，推荐）

```bash
./start.sh    # 启动/重启（后台运行，自动处理旧实例）
./stop.sh     # 停止
```

服务默认通过 Waitress 监听 `127.0.0.1`。systemd、容器或远程任务环境可用 `FOREGROUND=1 ./start.sh`，由外部进程管理器保持前台进程；需要局域网访问时显式使用 `HOST=0.0.0.0 ./start.sh`。

浏览器访问 `http://127.0.0.1:5000/`。自定义端口：`PORT=8080 ./start.sh`。
日志写入 `.amos-server.log`。

## 新电脑首次部署

1. 安装 Python 3.10+（Windows/macOS/Linux 均可，无需 conda）
2. 进入项目目录，执行 `./setup.sh`（创建 `.venv`、安装依赖与 `amos-platform` 命令并验证）

   ```bash
   ./setup.sh
   ./start.sh    # 启动
   ```

3. 浏览器访问 `http://127.0.0.1:5000/`

`setup.sh` / `start.sh` / `stop.sh` 均为自包含脚本，无需预先安装任何工具（仅依赖 Python 与 curl）。

## 手动启动（可选）

```bash
cd /home/dministrator/work/amos-simulation-platform
python -m venv .venv
.venv/bin/pip install -e '.[dev]'
A2A_BACKEND_MODE=gateway \
A2A_GATEWAY_URL=http://127.0.0.1:8030 \
.venv/bin/amos-platform --host 127.0.0.1 --port 5000
```

可选配置：

```bash
A2A_BACKEND_MODE=gateway
A2A_GATEWAY_URL=http://127.0.0.1:8030
A2A_GATEWAY_TOKEN=...
A2A_REQUEST_TIMEOUT=30
A2A_WORKFLOW_MODE=bpel
A2A_WORKFLOW_FILE=integrated_system/workflows/integrated_demo_workflow.bpel
AMOS_PUBLIC_BASE_URL=http://127.0.0.1:5000/
HOST=127.0.0.1
PORT=5000
```

仅排查 Gateway 之外的 Commander 问题时使用：

```bash
A2A_BACKEND_MODE=commander A2A_COMMANDER_URL=http://127.0.0.1:8021 ./start.sh
```

## 验证

```bash
.venv/bin/python -m pytest -q
.venv/bin/amos-platform-smoke
```

## 场景与导演接口

```text
GET  /api/v1/scenarios
GET  /api/v1/scenarios/{scenario_id}
POST /api/v1/director/configure
GET  /api/v1/director/state
POST /api/v1/director/action
GET  /api/v1/runs
GET  /api/v1/runs/{run_id}
GET  /api/v1/runs/{run_id}/report?format=json|markdown|html
```

导演动作包括 `start`、`pause`、`step_tick`、`advance_checkpoint`、`start_auto` 和 `stop_auto`。`advance_checkpoint` 通过正常仿真 tick 到达条件检查点，不直接改写时钟；需要分析的检查点通过与手动提交相同的 Gateway 服务提交。Gateway 不可达时记录失败，不生成替代结果。

页面采用地图与任务工作区双栏结构，工作区包含场景态势、Agent 拓扑、算法覆盖、流程执行、证据与结果五个页签。场景声明只表示计划覆盖；只有后端成功 trace 才计入本次运行的算法和功能点验证结果。证据页可按当前 `run_id` 导出 JSON、Markdown 或独立 HTML 验收报告。

运行档案默认保存到 `instance/amos_runs.sqlite3`，可通过 `AMOS_RUN_DB` 指定其他 SQLite 文件。档案记录场景、随机种子、分支、冻结提交、工作流、已验证覆盖、指标、告警和最终状态；尚无运行证据的字段保持“未上报”。

## 关键约束

1. 剧本的完整未来真值只存在于仿真内部。
2. `/api/v1/scenarios/<id>`、`/api/v1/sim/state`、`/api/v1/sim/snapshot` 和事件流都经过时间门禁。
3. 未获得后端评估的接触显示为“待分析”，不得由 AMOS 预标为威胁。
4. 地图只绘制当前位置和已发生的历史轨迹，不绘制剧本未来航路。
5. 后端未返回结构化结果时，页面明确显示不可用或失败，不伪造识别结论。

更多说明见 [架构文档](docs/AMOS_ARCHITECTURE.md)、[场景与导演说明](docs/MULTI_SCENARIO_DIRECTOR.md)、[正式剧本明细](docs/SCENARIO_V2_FORMAL_DEMOS.md) 和 [后端待修改项](docs/COMMANDER_BACKEND_GAPS.md)。
