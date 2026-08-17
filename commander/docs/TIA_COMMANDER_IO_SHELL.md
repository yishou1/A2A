# TIA Agent — Commander 统一输入/输出外壳


对齐飞书「Commander → Agent / Agent → Commander」外壳。  
本文只列 **Tactical Intelligence Agent** 自身业务字段，不含具体数据集示例。

约定：
- 输入业务字段写入：`input.agent_request`
- 输出业务结果写入：`output.intelligence_packet`（与 `output_hint` 一致）

---

## 一、Commander → Agent 输入外壳

```json
{
  "schema_version": "1.0",
  "workflow_id": "wf-001",
  "work_item": "wf-001:tactical_intelligence",
  "command": "process_intelligence",
  "required_skill": "tactical_intelligence_analysis",
  "required_skills": ["tactical_intelligence_analysis"],
  "input": {
    "agent_request": {
      "recon_report": "",
      "sector": "",
      "coordinates": {
        "lat": 0.0,
        "lon": 0.0
      }
    }
  },
  "output_hint": "intelligence_packet",
  "context": {
    "jamming_level": 0.0,
    "subscriber_agents": ["commander", "artillery", "evaluator"],
    "knowledge_base": [],
    "battlefield_situation": "",
    "area_of_operations": "",
    "ground_elevation_m": null,
    "sea_surface_elevation_m": null,
    "laser_range_m": null,
    "radar_range_m": null,
    "sensor_telemetry": {
      "altitude_m": null,
      "platform_lat": null,
      "platform_lon": null,
      "heading_deg": null
    },
    "georef": {},
    "output_storage_prefix": ""
  },
  "work_list": [],
  "attachments": [
    {
      "uri": "",
      "kind": "image",
      "checksum": {
        "algorithm": "sha256",
        "value": ""
      },
      "meta": {
        "sensor_id": "",
        "modality": "eo_ir",
        "platform_lat": null,
        "platform_lon": null,
        "altitude_m": null,
        "heading_deg": null,
        "depression_angle_deg": null,
        "gimbal_pitch_deg": null,
        "fov_deg": null,
        "resolution": ""
      }
    }
  ],
  "retry_policy": {
    "max_retries": 1,
    "timeout_seconds": 120,
    "failure_policy": "pause"
  }
}
```

### 输入字段说明

#### 外壳必填（飞书统一）

| 字段 | 说明 |
|------|------|
| `schema_version` | 协议版本 |
| `workflow_id` | 工作流 ID |
| `work_item` | 本步工作项 ID |
| `command` | TIA 命令，默认 `process_intelligence` |
| `required_skill` | 所需技能 |
| `input` | 业务输入容器 |
| `output_hint` | 固定为 `intelligence_packet` |

#### `input.agent_request`（TIA 业务输入）

| 字段 | 类型 | 说明 |
|------|------|------|
| `recon_report` | string | 侦察/态势文本简报 |
| `sector` | string | 作战扇区 |
| `coordinates` | object | 关注坐标，含 `lat` / `lon` |

#### `attachments[]`（传感器图像/文件，对象存储引用）

| 字段 | 说明 |
|------|------|
| `uri` | 对象存储地址 |
| `kind` | `image` / `eo_ir` / `sar` / `radar` / `text` 等 |
| `checksum` | 完整性校验 |
| `meta.sensor_id` | 传感器 ID |
| `meta.modality` | 模态：`eo_ir` / `sar` / `radar` / `text_report` 等 |
| `meta.platform_lat` / `platform_lon` / `altitude_m` / `heading_deg` 等 | 传感器位姿，供地理解算 |

#### `context`（运行上下文）

| 字段 | 说明 |
|------|------|
| `jamming_level` | 干扰等级 |
| `subscriber_agents` | 下游订阅方 |
| `knowledge_base` | 知识库引用 |
| `battlefield_situation` | 战场态势描述 |
| `area_of_operations` | 作战区域 |
| `sensor_telemetry` | 平台遥测 |
| `georef` | 地理参考 |
| `output_storage_prefix` | 产物写回前缀 |

---

## 二、Agent → Commander 输出外壳

```json
{
  "schema_version": "1.0",
  "workflow_id": "wf-001",
  "work_item": "wf-001:tactical_intelligence",
  "agent": "tactical_intelligence_agent",
  "role": "tactical_intelligence",
  "command": "process_intelligence",
  "status": "completed",
  "output": {
    "intelligence_packet": {
      "schema_version": "1.0",
      "packet_id": "",
      "mission_id": "",
      "created_at": "",
      "summary": "",
      "tracks": [
        {
          "track_id": "T-0001",
          "object_type": "ship",
          "class_name": "ship",
          "timestamp": 1718000050.0,
          "lat": 31.24,
          "lon": 121.51,
          "alt": 0.0,
          "speed": 12.5,
          "heading": 130.0,
          "confidence": 0.88,
          "geo": {
            "lat": 31.24,
            "lon": 121.51,
            "alt_m": 0.0
          },
          "history_path": [
            {
              "timestamp": 1718000000.0,
              "lat": 31.23,
              "lon": 121.47,
              "alt": 0.0,
              "speed": 12.0,
              "heading": 128.0,
              "confidence": 0.85
            }
          ]
        }
      ],
      "targets": [
        {
          "track_id": "",
          "class": "",
          "label": "",
          "affiliation": "",
          "threat_level": "",
          "threat_score": 0.0,
          "confidence": 0.0,
          "damage_score": null,
          "geo": {
            "lat": 0.0,
            "lon": 0.0,
            "alt_m": 0.0,
            "slant_range_m": null,
            "domain": null,
            "geo_method": null
          },
          "bbox": null,
          "knowledge_ref": null,
          "sensor_id": null
        }
      ],
      "semantic_vector": [],
      "knowledge_graph": {},
      "routing": {},
      "provenance": {},
      "raw_compression_ratio": 1.0,
      "task_schedule": {
        "sensor_assignments": [
          {
            "sensor_id": "",
            "target_id": null,
            "task": "surveillance",
            "priority": "normal",
            "rationale": ""
          }
        ],
        "reattack_plan": [
          {
            "asset_id": "",
            "target_id": "",
            "task": "reattack",
            "priority": "critical",
            "expected_damage": null,
            "rationale": ""
          }
        ],
        "covered_targets": [],
        "reattack_targets": [],
        "algorithm": ""
      },
      "output_attachments": [
        {
          "uri": "",
          "kind": "image",
          "meta": {}
        }
      ],
      "consumer_guide": {
        "schema_version": "1.0",
        "sections": {
          "tracks": {
            "consumers": ["trajectory_predictor", "track_state_updater", "graph_relation_reasoner"],
            "description": "机器可读航迹层"
          },
          "targets": {
            "consumers": ["decision_planning", "artillery", "evaluator"],
            "description": "语义目标层"
          },
          "task_schedule": {
            "consumers": ["sensor_scheduler", "commander"],
            "description": "传感器调度"
          },
          "output_attachments": {
            "consumers": ["evaluator", "bda", "visualization"],
            "description": "产物 URI"
          }
        }
      }
    },
    "schema_version": "1.0",
    "track_count": 0,
    "target_count": 0,
    "summary": "",
    "output_attachments": [],
    "consumer_guide": {},
    "resource_allocation": {}
  },
  "metrics": {
    "duration_ms": 0.0
  },
  "error": null,
  "message": "completed",
  "attempts": 1,
  "cached": false
}
```

### 输出字段说明

#### 外壳字段（飞书统一）

| 字段 | 说明 |
|------|------|
| `agent` | `tactical_intelligence_agent` |
| `role` | `tactical_intelligence` |
| `status` | `completed` / `failed` 等 |
| `output` | 业务输出容器 |
| `metrics.duration_ms` | 耗时 |
| `error` / `message` / `attempts` / `cached` | 状态与诊断 |

#### `output.intelligence_packet`（TIA 核心业务输出）

| 字段 | 类型 | 谁读 | 说明 |
|------|------|------|------|
| `schema_version` | string | 全体下游 | 契约版本，当前 `1.0` |
| `packet_id` | string | 溯源 | 情报包 ID |
| `mission_id` | string | 溯源 | 任务 ID（通常取 workflow_id） |
| `created_at` | string | 溯源 | 生成时间 |
| `summary` | string | Commander / 决策 | 态势摘要 |
| **`tracks[]`** | array | **航迹预测 / 跟踪 / 图关系** | 机器层：含 `history_path` |
| **`targets[]`** | array | **决策 / 评估 / 火力** | 语义层：威胁、敌我、类别 |
| `task_schedule` | object | 调度 / Commander | 传感器分配与再攻击规划 |
| `output_attachments[]` | array | 可视化 / BDA | 标注图等产物 URI |
| `consumer_guide` | object | 全体下游 | 各字段消费方说明 |
| `semantic_vector` | array | 语义通信 | 压缩语义向量 |
| `knowledge_graph` | object | 认知下游 | 知识图 |
| `routing` | object | Commander | 下游路由 |
| `provenance` | object | 诊断 | 算法溯源 |

#### `tracks[]` 单项（给航迹预测）

| 字段 | 说明 |
|------|------|
| `track_id` | 航迹 ID |
| `object_type` | `ship` / `aircraft` / `ground` / …（对齐 trajectory_predictor） |
| `timestamp` | Unix 秒 |
| `lat` / `lon` / `alt` | 当前位置 |
| `speed` / `heading` | 由 history 推算或感知给出 |
| `confidence` | 置信度 |
| `history_path[]` | 历史点序列（至少 1 点；跨帧累积） |
| `geo` | 原始地理解算细节（可选） |

#### `targets[]` 单项（给决策/评估）

| 字段 | 说明 |
|------|------|
| `track_id` | 航迹/目标 ID |
| `class` | 类别 |
| `label` | 标签 |
| `affiliation` | 敌我属性 |
| `threat_level` / `threat_score` | 威胁等级与分数 |
| `confidence` | 置信度 |
| `damage_score` | 毁伤评分 |
| `geo` | 地理位置 |
| `bbox` | 检测框 |
| `knowledge_ref` | 知识库引用 |
| `sensor_id` | 来源传感器 |

#### `output` 同级附加字段（TIA 一并返回）

| 字段 | 说明 |
|------|------|
| `schema_version` | 与 packet 对齐 |
| `track_count` | 航迹数量 |
| `target_count` | 目标数量 |
| `summary` | 摘要（与 packet.summary 对齐） |
| `output_attachments` | 产物附件列表 |
| `consumer_guide` | 消费指引（与 packet 内一致） |
| `resource_allocation` | 由 `task_schedule` 转换的资源分配（有调度结果时） |

### 下游怎么取数

| 下游 Agent | 读取路径 |
|------------|----------|
| 航迹预测 / Track Threat | `output.intelligence_packet.tracks`（可用 `downstream_adapter.to_trajectory_predictor_input`） |
| 决策规划 / 火力 / 评估 | `output.intelligence_packet.targets` |
| 传感器调度 | `output.intelligence_packet.task_schedule` |
| 可视化 / BDA | `output.intelligence_packet.output_attachments` |

Commander 落库键：`context.intelligence_packet`（role=`tactical_intelligence`）。

### 真实演练约束

- **无 mock 兜底**：必须提供 `attachments[]` 传感器引用，或至少 `recon_report` / `sector` / `coordinates` 之一；否则 TIA 直接报错。
- 配置默认 `use_mock: false`（见 `config/default.yaml`），走真实算法库 / 权重推理。

---

## 三、与飞书外壳的对应关系

| 飞书位置 | TIA 填入 |
|----------|----------|
| `input.agent_request` | `recon_report` / `sector` / `coordinates` |
| `output_hint` | `intelligence_packet` |
| `output.<output_hint>` | `output.intelligence_packet` |
| `attachments` | 传感器图像对象存储引用 |
| `context` | 干扰、遥测、下游订阅等运行上下文 |
