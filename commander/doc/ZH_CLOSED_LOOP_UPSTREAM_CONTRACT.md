# zh 闭环与执行控制：近期改动、原因与上游结构化输入预期

本文说明 `zh` 分支上执行控制（EC）与闭环（Closed Loop）近期改造内容、改造原因，以及上游 Agent / Commander 应提供的结构化输入约定。另单独说明图 + polygon 毁伤模式当前是否打通、传图后系统会做什么。

---

## 改造做了什么、为什么做

### 算法库双后端与心跳

Agent 默认仍在进程内跑算法（`local`），需要时可切到算法库 HTTP 调用（`algolib`）。支持直连各包 `/predict`，也支持网关 `/run`。算法库失败时可降级回本地，并在结果里写 warning。EC / CL / recon / artillery / assault 启动改为 `AgentRuntimeSDK`，Nacos 心跳带上 skill 与资源元数据，便于发现与租约。

原因：算法已打包进算法库，Agent 侧需要可切换、可降级的调用方式；心跳原先 EC/CL 只有静态 role/status，远程调度信息不完整。

### 闭环 algolib 编排补强

algolib 闭环补上：无 `targets` 时用 `_build_live_targets` 按 `target_count` 合成目标；按 `cycles` 多轮迭代并 `_apply_action`；输出对齐 Commander 可读字段（`execution_control` / `effect_assessment` / `closed_loop_optimization` / `requirement_report`）；离线 xBD 准确率门槛在 algolib 模式标明未评估，避免和本地训练门禁混为一谈。

原因：beachhead 常见只传 `target_count` + `results`，旧 algolib 路径空跑；结果信封与 Commander 日志字段不一致；单轮编排与本地闭环能力差距过大。

### 执行控制 algolib 契约校验

planner 返回的命令校验 `executor_role`、`action` 等字段，不合格则失败（可降级 local）。

原因：artillery / assault 依赖这些字段，坏契约会导致下游执行异常。

### 毁伤评估可选 images 模式

闭环 algolib 路径对每个 target：若带齐灾前图、灾后图与 polygon，则走 `xbd_damage_assessor` 的 `images` 模式；否则走 `features`。可用环境变量或入参强制模式。

原因：算法库本身支持双入口，但 Agent 侧原先只接了特征表路径。

相关说明还可对照 `doc/ZH_ALGOLIB_HEARTBEAT_CHANGES.md`。

---

## 传入方式总览

闭环有三种常见传入路径，字段最终都落到 Agent 的 `input`（或顶层与 `input` 同名的透传键）上。

| 路径 | 谁发起 | 怎么传 | 典型用途 |
|---|---|---|---|
| beachhead / Commander | Commander 组任务 | 自动填 `target_count` + `cycles` + `results`，**不传 targets** | 演示工作流 |
| 直接调 Closed Loop Agent | 调用方 HTTP/A2A | POST 任务，在 `input` 里放完整字段 | 真目标 / 图评估 |
| 直连算法库毁伤包 | 调用方 HTTP | POST `9016/predict`，`inputs` 为单目标毁伤入参 | 单次毁伤推理 |

执行控制同理：Commander 传 `phase` + `results`；也可直接调 EC Agent，在 `input` 里放相同字段。

Agent 读取规则：优先 `payload.input` 里的字段；若某键只在 payload 顶层出现，也会透传（闭环透传键见下表）。

---

## 闭环 Agent：怎么传、字段长什么样

### 调用方式

闭环 Agent 默认端口 `8016`。用标准 A2A `sendMessage` / 任务协议，核心是带上：

- `command` / `required_skill`：`closed_loop_optimization`
- `output_hint`：`closed_loop_result`
- `input`：业务字段对象（见下）

也可用 local_runtime / Commander remote，本质仍是同一套 `input`。

### `input` 顶层字段

| 字段 | 类型 | 是否必填 | 说明 |
|---|---|---|---|
| `targets` | array\<object\> | 否 | 真实目标列表；有则优先用；无则按 `target_count` 合成 |
| `target_count` | int | 否 | 合成目标数量，默认环境变量或 `50` |
| `cycles` | int | 否 | 闭环轮数，`1`～`8`，默认 `3` |
| `seed` | int | 否 | 合成目标随机种子 |
| `results` | object | 建议 | 上游标准结果块（感知/威胁/EC 等） |
| `previous_results` | array | 否 | 备选上游结果列表，会合并进 results 逻辑 |
| `dataset_paths` | object | 否 | 本地模式训练/加载用 CSV、模型路径 |
| `damage_input_mode` | string | 否 | `auto` / `features` / `images`，默认 `auto` |
| `xbd_input_mode` | string | 否 | 同上别名 |
| `feature_mode` | string | 否 | 任务特征：`hybrid` / `strict` / `fixture` |
| `device` | string | 否 | 图像模式推理设备，如 `cpu` / `cuda` |
| `enforce_min_target_count` | bool | 否 | 默认 `true` 时合成目标不少于 `50` |
| `request_id` | string | 否 | 追踪用 |

环境变量（影响 Commander 默认组包或 Agent 后端）：

| 变量 | 作用 |
|---|---|
| `CLOSED_LOOP_BACKEND` / `A2A_ALGORITHM_BACKEND` | `local` 或 `algolib` |
| `CLOSED_LOOP_TARGET_COUNT` | Commander 默认 target_count |
| `CLOSED_LOOP_CYCLES` | Commander 默认 cycles |
| `CLOSED_LOOP_DAMAGE_INPUT_MODE` | 毁伤模式偏好 |
| `CLOSED_LOOP_XBD_DAMAGE_CSV` 等 | dataset_paths |
| `ALGOLIB_TRANSPORT` | `direct` / `gateway` |

### beachhead / Commander 实际传入（当前默认）

Commander 自动组的 `input` 形状：

```json
{
  "target_count": 50,
  "cycles": 3,
  "results": {
    "perception_detection": { "output_data": { "...": "..." } },
    "threat_evaluation": { "output_data": { "...": "..." } },
    "execution_control": { "output_data": { "...": "..." } },
    "data_fusion": { "output_data": { "...": "..." } },
    "communication": { "output_data": { "...": "..." } },
    "resource_allocation": { "output_data": { "...": "..." } }
  },
  "dataset_paths": {
    "xbd_damage_csv": "可选，来自环境变量",
    "sc2le_task_csv": "可选，来自环境变量"
  }
}
```

完整任务外壳（示意）：

```json
{
  "workflow_id": "wf-xxx",
  "work_item": "wf-xxx:N:closed_loop",
  "command": "closed_loop_optimization",
  "required_skill": "closed_loop_optimization",
  "output_hint": "closed_loop_result",
  "input": {
    "target_count": 50,
    "cycles": 3,
    "results": {}
  }
}
```

当前 **不会** 自动传 `targets`，也 **不会** 自动传图 + polygon。

### 直接调闭环：特征毁伤（手写 features）

把真实目标放进 `input.targets`：

```json
{
  "command": "closed_loop_optimization",
  "required_skill": "closed_loop_optimization",
  "output_hint": "closed_loop_result",
  "input": {
    "cycles": 2,
    "seed": 20260412,
    "enforce_min_target_count": false,
    "damage_input_mode": "features",
    "feature_mode": "hybrid",
    "results": {
      "threat_evaluation": {
        "output_data": { "priority_score": 0.75 }
      },
      "execution_control": {
        "output_data": { "latency_ms": 180.0, "phase": "assault" }
      }
    },
    "targets": [
      {
        "target_id": "building-0",
        "sample_id": "guatemala-volcano_00000000",
        "pre_area": 0.007,
        "spectral_delta": 0.11,
        "texture_delta": 0.04,
        "heat_signature": 0.10,
        "crater_density": 0.29,
        "std_spectral": 0.09,
        "max_spectral": 0.61,
        "high_change_ratio": 0.21,
        "severe_damage_ratio": 0.06,
        "collapse_ratio": 0.17,
        "post_brightness": 0.65,
        "brightness_drop": 0.06,
        "normalized_distance": 0.41,
        "detection_confidence": 1.0,
        "threat_score": 0.7,
        "uncertainty": 0.2,
        "velocity_norm": 0.3,
        "ammo_need": 0.5
      }
    ]
  }
}
```

单个 target 常用字段：

| 字段 | 类型 | 说明 |
|---|---|---|
| `target_id` | string | 目标 ID |
| `sample_id` | string | 样本/灾种 ID，影响部分特征桶 |
| `pre_area` … `brightness_drop` | number | xBD 手工特征（features 模式） |
| `detection_confidence` | number | 检测置信度 |
| `threat_score` | number | 威胁分数 |
| `uncertainty` | number | 不确定性 |
| `velocity_norm` / `ammo_need` | number | 态势相关 |
| `cnn_embedding` | array\<number\> | 可选，1536 维；features 模式可附带 |
| `handcrafted_features` | object | 可选，嵌套放手工特征，与扁平行等价 |

### 直接调闭环：图 + polygon 毁伤

需 `CLOSED_LOOP_BACKEND=algolib`，且 `xbd_damage_assessor`（默认 `9016`）已启动。

```json
{
  "command": "closed_loop_optimization",
  "required_skill": "closed_loop_optimization",
  "output_hint": "closed_loop_result",
  "input": {
    "cycles": 1,
    "enforce_min_target_count": false,
    "damage_input_mode": "auto",
    "device": "cpu",
    "results": {
      "threat_evaluation": {
        "output_data": { "priority_score": 0.7 }
      }
    },
    "targets": [
      {
        "target_id": "building-0",
        "sample_id": "guatemala-volcano_00000000",
        "pre_image": {
          "path": "D:/data/xbd/train/train/images/xxx_pre_disaster.png"
        },
        "post_image": {
          "path": "D:/data/xbd/train/train/images/xxx_post_disaster.png"
        },
        "polygon": [
          [10.0, 10.0],
          [80.0, 10.0],
          [80.0, 80.0],
          [10.0, 80.0]
        ],
        "threat_score": 0.7,
        "uncertainty": 0.2
      }
    ]
  }
}
```

图像字段允许的写法：

| 写法 | 示例 |
|---|---|
| path 对象 | `"pre_image": { "path": "C:/.../pre.png" }` |
| base64 字符串 | `"pre_image": "<base64 或 data:image/png;base64,...>"` |
| base64 对象 | `"pre_image": { "base64": "..." }` |
| 嵌套 image_pair | `"image_pair": { "pre": "...", "post": "..." }` |
| polygon 点列 | `"polygon": [[x,y], [x,y], [x,y], ...]`（至少 3 点） |
| polygon 嵌套 | `"geometry": { "polygon": [[...], ...] }` |
| WKT 字符串 | `"polygon": "POLYGON ((x y, x y, ...))"` |

`path` 必须是 **毁伤算法服务进程能访问到的路径**（不是仅 Agent 本机私有盘也不行）。

### 直连算法库毁伤包（单目标，不经闭环编排）

```http
POST http://127.0.0.1:9016/predict
```

features：

```json
{
  "algorithm_id": "xbd_damage_assessor",
  "version": "1.0.0",
  "inputs": {
    "input_mode": "features",
    "sample_id": "guatemala-volcano_00000000",
    "handcrafted_features": {
      "pre_area": 0.007,
      "spectral_delta": 0.11,
      "texture_delta": 0.04,
      "heat_signature": 0.10,
      "crater_density": 0.29,
      "std_spectral": 0.09,
      "max_spectral": 0.61,
      "high_change_ratio": 0.21,
      "severe_damage_ratio": 0.06,
      "collapse_ratio": 0.17,
      "post_brightness": 0.65,
      "brightness_drop": 0.06,
      "normalized_distance": 0.41,
      "detection_confidence": 1.0,
      "threat_score": 0.5
    }
  },
  "params": {}
}
```

images：

```json
{
  "algorithm_id": "xbd_damage_assessor",
  "version": "1.0.0",
  "inputs": {
    "input_mode": "images",
    "sample_id": "guatemala-volcano_00000000",
    "pre_image": { "path": "D:/data/.../pre.png" },
    "post_image": { "path": "D:/data/.../post.png" },
    "polygon": [[10, 10], [80, 10], [80, 80], [10, 80]]
  },
  "params": { "device": "cpu" }
}
```

成功时 `outputs` 含 `damage_probability`、`damage_label`、`assessment_status` 等。

---

## 执行控制 Agent：怎么传、字段长什么样

### 调用方式

EC 默认端口 `8017`。`command` / `required_skill` 常用：

- `plan_strike_control`（火力阶段）
- `plan_assault_control`（突击阶段）
- `generate_execution_commands`（通用，靠 `input.phase`）

`output_hint`：`execution_control_result`。

### `input` 顶层字段

| 字段 | 类型 | 是否必填 | 说明 |
|---|---|---|---|
| `phase` | string | 是（或由 command 隐含） | `strike` / `assault` |
| `results` | object | 建议 | 上游标准结果块 |
| `context` | object | 否 | 额外上下文 |
| `control_phase` | string | 否 | phase 别名 |

### 完整示例

```json
{
  "command": "plan_strike_control",
  "required_skill": "plan_strike_control",
  "output_hint": "execution_control_result",
  "input": {
    "phase": "strike",
    "results": {
      "perception_detection": {
        "output_data": {
          "detections": [{ "conf": 0.9 }]
        }
      },
      "threat_evaluation": {
        "output_data": { "priority_score": 0.75 }
      },
      "resource_allocation": {
        "output_data": { "readiness": 0.85 }
      },
      "communication": {
        "output_data": { "delivery_rate": 0.9 }
      },
      "data_fusion": {
        "output_data": {
          "track_history": [
            {
              "track_id": "T-001",
              "history": [
                { "t": 0.0, "x": 10.0, "y": 18.0 },
                { "t": 0.4, "x": 11.8, "y": 20.2 }
              ],
              "weapon_prep_sec": 2.0,
              "flight_time_sec": 4.0
            }
          ]
        }
      }
    }
  }
}
```

期望输出命令至少含：`executor_role`、`action`；建议含 `aim_point`、`target_id`、`priority`。

---

## 上游应提供的预期结构化输出

下列约定是闭环 / 执行控制「真正吃得到、且符合预期」的形状。字段放在各 Agent 结果的 `output_data`（或经 Commander 映射后的 `results.<block>.output_data`）中。

### 感知 / 侦察 → `perception_detection`

```json
{
  "output_data": {
    "frame_id": "frame-001",
    "detections": [
      { "track_id": "T-001", "conf": 0.9, "bbox": [x1, y1, x2, y2] }
    ],
    "report_text": "optional free text"
  }
}
```

预期用途：检测置信度、合成目标基准、任务特征里的情报置信度。仅文本报告时只能退化成弱默认置信度。

### 威胁评估 → `threat_evaluation`

```json
{
  "output_data": {
    "priority_score": 0.75,
    "ranked_targets": [
      { "target_id": "T-001", "score": 0.8 }
    ]
  }
}
```

预期用途：威胁压力、合成目标的 `threat_score`。只有一个标量分数也可以，信息量会少一些。

### 执行控制 → `execution_control`（EC 自身输出最完整）

```json
{
  "output_data": {
    "phase": "strike",
    "latency_ms": 120.5,
    "commands": [
      {
        "command_id": "CMD-001",
        "executor_role": "artillery",
        "action": "precision_strike",
        "target_id": "T-001",
        "aim_point": { "x": 12.3, "y": 45.6 },
        "priority": 0.9
      }
    ],
    "tracks": [],
    "coordination": { "groups": [] },
    "matched_rules": [],
    "prediction_details": []
  }
}
```

预期用途：控制时效、指令摘要、轨迹预测结果。`executor_role` / `action` / `aim_point` 是火力与突击 Agent 的硬依赖。

### 数据融合轨迹 → `data_fusion`（供 EC 运动预测）

```json
{
  "output_data": {
    "track_history": [
      {
        "track_id": "T-001",
        "history": [
          { "t": 0.0, "x": 10.0, "y": 18.0 },
          { "t": 0.4, "x": 11.8, "y": 20.2 }
        ],
        "weapon_prep_sec": 2.0,
        "flight_time_sec": 4.0
      }
    ]
  }
}
```

缺省时 EC / 映射层可能退回 fixture 轨迹，演示能跑，但不代表真实感知轨迹。

### 资源与通信（任务特征用）

```json
{
  "resource_allocation": {
    "output_data": {
      "readiness": 0.8,
      "supply_pressure": 0.45
    }
  },
  "communication": {
    "output_data": {
      "delivery_rate": 0.92
    }
  }
}
```

beachhead 里若没有对应 Agent，当前多用默认值填充。

### 毁伤确认（可选，提升任务特征真实性）

```json
{
  "damage_confirmation": {
    "output_data": {
      "engaged_targets": 40,
      "confirmed_destroyed": 28
    }
  }
}
```

### 希望走真实毁伤 / 图评估时：直接传 `targets`

特征模式：

```json
{
  "targets": [
    {
      "target_id": "building-0",
      "sample_id": "guatemala-volcano_00000000",
      "pre_area": 0.007,
      "spectral_delta": 0.11,
      "texture_delta": 0.04,
      "heat_signature": 0.10,
      "crater_density": 0.29,
      "std_spectral": 0.09,
      "max_spectral": 0.61,
      "high_change_ratio": 0.21,
      "severe_damage_ratio": 0.06,
      "collapse_ratio": 0.17,
      "post_brightness": 0.65,
      "brightness_drop": 0.06,
      "normalized_distance": 0.41,
      "detection_confidence": 1.0,
      "threat_score": 0.7,
      "uncertainty": 0.2
    }
  ]
}
```

图像模式（见下一节）：

```json
{
  "targets": [
    {
      "target_id": "building-0",
      "sample_id": "guatemala-volcano_00000000",
      "pre_image": { "path": "D:/data/xbd/.../xxx_pre_disaster.png" },
      "post_image": { "path": "D:/data/xbd/.../xxx_post_disaster.png" },
      "polygon": [[10, 10], [80, 10], [80, 80], [10, 80]],
      "threat_score": 0.7,
      "uncertainty": 0.2
    }
  ],
  "damage_input_mode": "auto"
}
```

`pre_image` / `post_image` 也可用 base64 字符串，或：

```json
"image_pair": { "pre": "...", "post": "..." },
"geometry": { "polygon": [[...], [...], [...]] }
```

并打开闭环算法库后端，例如：

```powershell
$env:CLOSED_LOOP_BACKEND="algolib"
$env:ALGOLIB_TRANSPORT="direct"
```

同时保证 `xbd_damage_assessor`（默认 `9016`）已启动。

---

## 图 + polygon 模式现在通不通？传了之后会不会自动抽特征再评估？

### 结论

Agent → 算法库这条链路 **已接通（仅 algolib 后端）**。  
你在某个 target 上带齐 **灾前图 + 灾后图 + polygon** 后，闭环会自动选 `images` 模式调用 `xbd_damage_assessor`；**服务内部会自动做 ROI 裁剪、手工特征抽取、ResNet18 embedding，再送入冻结分类器**，你不需要自己先算 spectral_delta 等字段。

默认 beachhead **没有**把图传进闭环，所以「演示一键跑通出图评估」目前还不通，需要调用方显式传 `targets`（含图）并启用 `CLOSED_LOOP_BACKEND=algolib`。

### 传图之后系统实际步骤

```text
Closed Loop（algolib）发现 target 含 pre/post/polygon
  → 组装 input_mode=images 请求
  → POST xbd_damage_assessor /predict
      → 解码图像
      → 用 polygon 裁 ROI（不做整图 bbox 降级）
      → 在 ROI 上算手工特征（spectral/texture/...）
      → 算 ResNet18 pre/post/diff embedding（若模型启用 CNN）
      → 拼成向量，过冻结 LR
      → 返回 damage_probability / damage_label
  → 闭环再用该概率做态势与动作建议
```

因此：**是的——传了图 + polygon 后，特征抽取在算法服务内自动完成，再做评估。**  
你不必也不应该再手动填一整套手工特征（有的话只会在 images 不完整回退 features 时用到）。

### 使用条件与注意点

- 必须 `CLOSED_LOOP_BACKEND=algolib`（或全局 `A2A_ALGORITHM_BACKEND=algolib`）。本地 `local` 闭环路径仍走进程内模型 / 合成特征，**不会**走算法库 images。
- polygon 必填且至少三个点；缺 polygon 不会静默用整图。
- 图像可用本地 `path` 或 base64；path 必须是 **算法服务进程能读到的路径**。
- images 不完整或服务返回 `insufficient_data` 时，会尝试回退到该 target 上的手工特征（若有）。
- 默认 `damage_input_mode=auto`：有齐图+polygon 用 images，否则 features。

### 和「自动造 targets」的关系

`target_count` 合成出来的目标 **不含** 真实图像与 polygon，只会带仿真手工特征。  
要用图评估，必须由上游提供真实 `targets`（含图+polygon），不能指望合成逻辑自动生成图像。

---

## 一句话对照

- 改动：算法库可切换、心跳补齐、闭环 algolib 能合成目标/多轮/对齐信封、EC 校验命令、毁伤支持可选 images。  
- 原因：接算法库、修 beachhead 空跑、让 Commander 读得懂结果、打通图评估入口。  
- 传入：见上文「传入方式总览」与闭环/EC 完整 JSON 示例；beachhead 默认只传 `target_count`+`results`，真目标/图需直接写进 `input.targets`。  
- 上游预期：按各 `output_data` 块吐结构化结果。  
- 图模式：algolib 路径已通；传齐图+polygon 会在服务内自动抽特征并评估；默认 beachhead 尚未传入这些字段。
