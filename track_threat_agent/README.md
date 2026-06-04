# Track Threat Agent

`track_threat_agent` 是一个近真实 A2A/Nacos 下游态势分析 Agent Demo。它接收上游感知/融合模块输出的结构化 `perception_result`，维护连续航迹，预测未来短时航线，识别疑似编队/编组，分析保护资产影响，并输出统一关注排序和可供 AMOS/A2A 消费的事件。

安全边界：

- 只做仿真态势分析、航迹预测、编组识别和关注优先级排序。
- 不实现真实武器控制。
- 不生成攻击建议。
- 不做制导、火控、打击或交战决策。
- `threat` / `risk` / `impact` 在本 Demo 中只表示态势关注优先级。

## 1. A2A 角色

推荐注册角色：

```text
SERVICE_NAME=A2A-Agent
AGENT_ROLE=track_threat
AGENT_STATUS=idle
```

Commander / Gateway 可通过 Nacos 查询：

```text
serviceName=A2A-Agent
metadata.role=track_threat
metadata.status=idle
```

## 2. 当前能力

- 多目标航迹跟踪。
- 未来 10 / 20 / 30 秒航线预测。
- 自适应运动模型：匀速、加速度、CTRA 转弯。
- ST-GNN-inspired 图关系预测修正。
- DBN-inspired 动态风险状态平滑。
- 疑似空中编队和海上编组识别。
- 己方保护资产影响分析。
- 单体、群体、资产影响统一关注排序。
- A2A `sendMessage` / `sendMessageStream`。
- `workflow_id` / `work_item` / `work_list` 工作流字段。
- `work_item` 幂等缓存。
- `GET /workflows/{workflow_id}/work-list`。
- Nacos role/status metadata。
- `AlgorithmProvider` 预留接口。

当前算法以内置 Demo 实现为主，暂不依赖公共算法库。后续公共算法库或 A100 训练版 GNN/ST-GNN 完成后，可替换 `app/algorithm_provider.py` 中的 provider，不改变 A2A/Nacos 对外协议。

## 3. 启动

```bash
cd track_threat_agent
uv run --with-requirements requirements.txt uvicorn app.main:app --host 0.0.0.0 --port 8102
```

健康检查：

```bash
curl http://127.0.0.1:8102/health
```

Agent Card：

```bash
curl http://127.0.0.1:8102/.well-known/agent-card
```

## 4. Nacos 配置

默认不强依赖 Nacos，便于本地单独运行。接入 A2A Commander 时可启用：

```bash
export NACOS_ENABLED=true
export NACOS_SERVER=127.0.0.1:8848
export NACOS_NAMESPACE=public
export SERVICE_NAME=A2A-Agent
export SERVICE_IP=127.0.0.1
export SERVICE_PORT=8102
export AGENT_ID=track-threat-group-agent-01
export AGENT_ROLE=track_threat
export AGENT_STATUS=idle
export HEARTBEAT_INTERVAL=5
```

完整示例见 `.env.example`。

## 5. A2A 调用

### 5.1 工作流入口

```http
POST /sendMessage
Authorization: Bearer <token>
Content-Type: application/json
```

示例：

```json
{
  "workflow_id": "wf-demo-001",
  "work_item": "track-threat-step-001",
  "command": "analyze_perception_result",
  "role": "track_threat",
  "work_list": [
    {"activity": "perception_fusion", "role": "recon"},
    {"activity": "track_threat_analysis", "role": "track_threat"},
    {"activity": "situation_display", "role": "commander"}
  ],
  "payload": {
    "task_id": "task-001",
    "message_type": "perception_result",
    "algorithm_level": "medium",
    "scene": {
      "protected_zone_lat": 31.2304,
      "protected_zone_lon": 121.4737,
      "protected_radius_m": 30000,
      "protected_assets": []
    },
    "detections": []
  }
}
```

`work_item` 是幂等键。同一个 `work_item` 重试时，Agent 会返回缓存 artifact，不会重复推进航迹历史、DBN 状态或编组状态。

### 5.2 SSE 进度流

```http
POST /sendMessageStream
Authorization: Bearer <token>
Content-Type: application/json
```

返回 `text/event-stream`，包含 `Working`、`Artifact`、`Completed` 等事件。

### 5.3 查询 workflow work list

```bash
curl http://127.0.0.1:8102/workflows/wf-demo-001/work-list
```

## 6. 直接调试入口

保留直接态势输入接口，便于 curl 和本地联调：

```bash
curl -X POST http://127.0.0.1:8102/a2a/perception-result \
  -H "Content-Type: application/json" \
  --data @sample_data/group_scene.json
```

## 7. 输出

返回 `track_threat_group_artifact`，包含：

- `tracks`：连续航迹、历史路径、预测路径。
- `threats`：单体关注排序。
- `groups`：疑似编队/编组。
- `asset_impacts`：保护资产影响分析。
- `unified_threat_ranking`：单体、群体、资产影响统一排序。
- `events`：`track.updated`、`threat.updated`、`track.group.updated`、`threat.group.updated`、`threat.ranking.updated`、`asset.impact.updated` 等事件。

## 8. 测试

```bash
cd track_threat_agent
uv run --with-requirements requirements.txt pytest -q
```

当前测试覆盖：

- 多目标跟踪。
- 历史路径长度控制。
- 威胁排序。
- 编队/编组识别。
- 保护资产影响分析。
- AMOS/A2A 事件适配。
- ST-GNN-inspired 图关系修正。
- DBN-inspired 风险状态平滑。
- `work_item` 幂等。
- `work_list` 查询。

## 9. 当前限制

- 当前是 Demo，不是生产系统。
- ST-GNN 是规则式 inspired 原型，不是训练好的深度 GNN。
- 训练版 GNN/ST-GNN 计划后续在实验室 A100 上完成。
- 状态目前保存在内存中，服务重启后不恢复历史航迹。
- 单实例串行处理，多并发建议通过多 Agent 实例水平扩展。
