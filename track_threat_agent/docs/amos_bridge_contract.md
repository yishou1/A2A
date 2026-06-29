# AMOS Bridge 回写协议

本文说明 `track-threat-group-agent-demo` 如何把航迹预测、编组包络、保护资产影响和威胁关注排序回写给 AMOS。当前项目本身不直接控制 AMOS 前端，推荐由 AMOS Bridge 或 A2A Gateway 消费本 Agent 的 `artifact.events[]`。

## 1. 回写链路

```text
上游感知 / A2A Commander / Gateway
  -> 调用 TrackThreatAgent
  -> Agent 输出 track_threat_group_artifact
  -> Bridge 读取 artifact.events[]
  -> 写入 AMOS Event Bus / AMOS 后端状态 / AMOS 前端地图图层
```

## 2. 事件映射

| Agent 事件 | AMOS 用途 |
|---|---|
| `track.updated` | 更新目标 marker、当前位置、历史航迹线、预测航线 |
| `asset.updated` | 将 track/group 作为 AMOS 可显示资产 upsert |
| `track.group.updated` | 绘制疑似编队/编组包络、中心点、中心预测路线 |
| `threat.updated` | 更新单体目标关注等级、分数、证据链 |
| `threat.group.updated` | 更新群体关注等级和分数 |
| `threat.ranking.updated` | 更新右侧统一威胁/风险关注排序列表 |
| `protected.asset.updated` | 显示己方保护资产、保护半径、重要度 |
| `asset.impact.updated` | 显示目标对保护资产的仿真影响关注告警 |
| `asset.relationship.updated` | 显示 group 与成员 track 的关系 |

## 3. 地图图层字段

### 3.1 单体目标

来自 `track.updated`：

```json
{
  "event_type": "track.updated",
  "track_id": "trk-xxx",
  "object_type": "aircraft",
  "current_position": {"lat": 31.3, "lon": 121.4, "alt": 7800},
  "history_path": [],
  "predicted_path": [],
  "track_quality": 0.91,
  "metadata": {}
}
```

AMOS 地图建议：

- `current_position`：目标 marker。
- `history_path`：实线历史航迹。
- `predicted_path`：虚线预测航线。
- `predicted_path[].uncertainty_radius_m`：预测误差圈或 popup 字段。
- `predicted_path[].graph_influence`：图关系预测影响程度。
- `metadata.st_gnn_inspired`：ST-GNN-inspired 图关系修正说明。

### 3.2 编组/编队

来自 `track.group.updated`：

```json
{
  "event_type": "track.group.updated",
  "group_id": "grp-xxx",
  "group_type": "air_formation",
  "members": ["trk-a", "trk-b"],
  "centroid": {},
  "envelope": {},
  "centroid_prediction": [],
  "predicted_envelope": {},
  "cohesion_score": 0.82
}
```

AMOS 地图建议：

- `envelope`：半透明矩形或多边形包络。
- `centroid`：群体中心 marker。
- `centroid_prediction`：群体中心虚线预测路线。
- `members`：在 popup 中显示成员 track。

### 3.3 保护资产

来自 `protected.asset.updated`：

```json
{
  "event_type": "protected.asset.updated",
  "asset_id": "blue-c2-node",
  "asset_name": "Blue C2 Node",
  "asset_type": "command_post",
  "position": {"lat": 31.2304, "lon": 121.4737, "alt": 30},
  "protection_radius_m": 9000,
  "criticality": 0.95
}
```

AMOS 地图建议：

- `position`：己方保护资产 marker。
- `protection_radius_m`：保护圈。
- `criticality`：popup 或列表字段。

## 4. 右侧排序列表

来自 `threat.ranking.updated`：

```json
{
  "event_type": "threat.ranking.updated",
  "ranking": [
    {
      "rank": 1,
      "item_type": "track",
      "item_id": "trk-xxx",
      "score": 0.62,
      "level": "medium"
    }
  ]
}
```

AMOS 前端建议显示：

- `rank`
- `item_type`
- `item_id`
- `score`
- `level`
- `protected_asset_name`，如果 item 是 asset impact

点击列表项时：

- `track`：定位到 track marker。
- `group`：定位到 group centroid。
- `asset_impact`：定位到 protected asset。

## 5. XAI 证据链

`threat.updated.metadata.xai` 包含：

```json
{
  "evidence_chain": [],
  "factor_contributions": {},
  "model_trace": [],
  "safety_note": "simulation-only attention priority; no weapon control, targeting, or engagement advice"
}
```

AMOS 可在详情面板中显示这些解释：

- 为什么分数高。
- 哪些因素贡献最大。
- 使用了哪些模型阶段。
- 安全边界说明。

## 6. 安全边界

AMOS 页面即使显示 `threat` 字段，也应解释为“态势关注优先级”。本 Agent 不输出武器控制、攻击建议、制导指令、火力分配或交战决策。
