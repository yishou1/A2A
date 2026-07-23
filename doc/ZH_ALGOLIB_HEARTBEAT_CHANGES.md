# zh 分支修改说明：算法库对接与 Agent 心跳

> 更新日期：2026-07-23  
> 分支：`zh`  
> 范围：仅修改 A2A 仓库中的 Agent / SDK / 本地运行时，不改动算法库仓库

本文档说明本轮在 `zh` 上完成的具体改动、涉及文件、行为变化与使用方式。

---

## 1. 改动目标

1. 让 `execution_control`、`closed_loop` 支持在**本地内联算法**与**算法库 HTTP 调用**之间切换。  
2. 算法库调用同时支持两种传输：
   - 直连各算法服务的 `/predict`
   - 统一网关的 `/run`
3. 算法库调用失败时，可自动降级回本地算法，并在结果中记录警告。  
4. 用统一的 `AgentRuntimeSDK` 注册 Nacos，补齐技能与资源心跳元数据。  
5. 保持默认行为与改造前一致：不设环境变量时仍走本地算法。

---

## 2. 新增模块

### 2.1 `algolib_bridge/`（共享算法库桥接）

| 文件 | 作用 |
|---|---|
| `algolib_bridge/__init__.py` | 对外导出客户端与配置入口 |
| `algolib_bridge/config.py` | 读取环境变量，解析 backend / transport / 端点 |
| `algolib_bridge/client.py` | HTTP 客户端：网关模式与直连模式 |

**配置优先级（后端选择）：**

```text
单 Agent 环境变量
  EXECUTION_CONTROL_BACKEND / CLOSED_LOOP_BACKEND
    ↓ 未设置
全局 A2A_ALGORITHM_BACKEND
    ↓ 未设置
默认 local
```

**传输模式：**

| `ALGOLIB_TRANSPORT` | 行为 |
|---|---|
| `direct`（默认） | `POST http://127.0.0.1:901x/predict` |
| `gateway` | `GET {ALGOLIB_BASE_URL}/algorithms`，`POST {ALGOLIB_BASE_URL}/run` |

**默认直连端点：**

| algorithm_id | 默认 URL |
|---|---|
| `execution_control_planner` | `http://127.0.0.1:9012/predict` |
| `mission_feature_adapter` | `http://127.0.0.1:9013/predict` |
| `mission_completion_scorer` | `http://127.0.0.1:9014/predict` |
| `closed_loop_decision_advisor` | `http://127.0.0.1:9015/predict` |
| `xbd_damage_assessor` | `http://127.0.0.1:9016/predict` |

可用 `ALGOLIB_ENDPOINT_<ALGORITHM_ID>` 覆盖单个端点，例如：

```powershell
$env:ALGOLIB_ENDPOINT_EXECUTION_CONTROL_PLANNER="http://127.0.0.1:9012/predict"
```

**其他环境变量：**

| 变量 | 默认 | 说明 |
|---|---|---|
| `ALGOLIB_BASE_URL` | `http://127.0.0.1:8088` | 仅 gateway 模式使用 |
| `ALGOLIB_TIMEOUT_SECONDS` | `15` | HTTP 超时 |
| `ALGOLIB_FALLBACK_LOCAL` | `true` | 算法库失败是否降级本地 |
| `ALGOLIB_DEFAULT_VERSION` | `1.0.0` | 请求里的 version |
| `ALGOLIB_BACKEND_TYPE` | `python_http_service` | 请求里的 backend_type |

### 2.2 `execution_control_agent/algolib_runtime.py`

- `run_execution_control_with_backend(arguments)`：统一入口  
- `local`：调用原有 `run_execution_control`  
- `algolib`：调用算法包 `execution_control_planner`（整包）  
- 成功时把算法库 `outputs` 包装回 Agent 原有结果信封：

```text
{
  task_type, input_data, output_data, accuracy, latency
}
```

- `output_data.backend` 标记：
  - `local`
  - `algolib`
  - `local_fallback`（算法库失败后降级）

### 2.3 `closed_loop_agent/algolib_runtime.py`

- `run_closed_loop_with_backend(arguments)`：统一入口  
- `local`：调用原有 `_closed_loop_optimization`  
- `algolib`：按顺序分调四个服务：

```text
mission_feature_adapter
  → mission_completion_scorer
  → xbd_damage_assessor（按目标 features 模式）
  → closed_loop_decision_advisor（按目标）
```

- 组装闭环结果信封，包含：
  - `mission_assessment`
  - `feature_bundle`
  - `assessments` / `commands`
  - `mission_completion_*`
  - `mean_damage_probability`
  - `meets_requirements`
  - `backend` / `algorithms` / `warnings` / `transport`
- 说明：当前 algolib 路径是**单轮服务编排**；完整多 cycle 训练与本地 `_closed_loop_optimization` 仍走 `local` 模式。

---

## 3. 修改的现有文件

### 3.1 Agent 入口改为走 backend 统一入口

| 文件 | 变化 |
|---|---|
| `execution_control_agent/main.py` | `execute_task` 改为调用 `run_execution_control_with_backend`；启动改为 `AgentRuntimeSDK` |
| `closed_loop_agent/main.py` | `execute_task` 改为调用 `run_closed_loop_with_backend`；启动改为 `AgentRuntimeSDK` |
| `local_runtime.py` | local BPEL 路径同样走上述两个 `*_with_backend`，保证 Commander local 模式与 HTTP Agent 行为一致 |

### 3.2 心跳 / Nacos 注册统一到 SDK

| 文件 | 变化 |
|---|---|
| `a2a_sdk/agent_sdk.py` | 新增 `AgentRuntimeSDK.from_agent(...)`，可包装已有业务 Agent |
| `execution_control_agent/main.py` | 注册时带 skill + heartbeat 元数据，并设置 `metadata_provider` |
| `closed_loop_agent/main.py` | 同上 |
| `recon_agent/main.py` | 由手写 `NacosRegistry.register_service` 改为 SDK |
| `artillery_agent/main.py` | 同上；`firepower=heavy` 放入 `extra_metadata` |
| `assault_agent/main.py` | 同上 |

改造前 EC/CL 注册元数据只有：

```text
{ role, status: idle }
```

改造后注册元数据包含：

```text
role / status
skill_ids / skills
资源与模型心跳字段（CPU/GPU/内存、忙闲、任务槽位、质量指标等）
extra_metadata（如 capability）
```

并在心跳周期内通过 `metadata_provider=agent.heartbeat_metadata` 动态刷新。

### 3.3 测试

| 文件 | 变化 |
|---|---|
| `tests/test_algolib_bridge.py` | 新增：配置优先级、本地 backend、失败降级、直连客户端、SDK 元数据 |
| `tests/test_closed_loop_integration.py` | mock 目标从 `_closed_loop_optimization` 改为 `run_closed_loop_with_backend` |

---

## 4. 行为对照（改造前 vs 改造后）

| 场景 | 改造前 | 改造后 |
|---|---|---|
| 默认启动 EC/CL | 本地内联算法 | 仍然本地内联（默认不变） |
| 打开 algolib | 不支持 | EC 调 planner；CL 调四个服务 |
| 算法库不可用 | — | 默认降级本地，并写 `algolib_fallback:...` |
| Nacos 心跳 | EC/CL 静态 role/status | skill + 资源/忙闲动态刷新 |
| local_runtime | 固定本地 core | 跟随同一套 backend 开关 |

---

## 5. 使用示例

### 5.1 默认（本地算法）

```powershell
python execution_control_agent/main.py
python closed_loop_agent/main.py
```

### 5.2 直连算法库

先启动算法库服务（端口 9012–9016），再：

```powershell
$env:A2A_ALGORITHM_BACKEND="algolib"
$env:ALGOLIB_TRANSPORT="direct"
python execution_control_agent/main.py
python closed_loop_agent/main.py
```

只对某一个 Agent 打开：

```powershell
$env:EXECUTION_CONTROL_BACKEND="algolib"
$env:CLOSED_LOOP_BACKEND="local"
```

### 5.3 网关模式

```powershell
$env:A2A_ALGORITHM_BACKEND="algolib"
$env:ALGOLIB_TRANSPORT="gateway"
$env:ALGOLIB_BASE_URL="http://127.0.0.1:8088"
```

### 5.4 关闭自动降级（算法库失败直接报错）

```powershell
$env:ALGOLIB_FALLBACK_LOCAL="false"
```

---

## 6. 结果字段约定

成功走算法库时，可在业务 `output_data` 中看到：

```json
{
  "backend": "algolib",
  "algorithm_id": "execution_control_planner"
}
```

或闭环：

```json
{
  "backend": "algolib",
  "algorithms": [
    "mission_feature_adapter",
    "mission_completion_scorer",
    "xbd_damage_assessor",
    "closed_loop_decision_advisor"
  ],
  "transport": "direct",
  "warnings": []
}
```

降级时：

```json
{
  "backend": "local_fallback",
  "warnings": ["algolib_fallback:<error message>"]
}
```

---

## 7. 验证

本轮本地已跑通：

```powershell
python -m unittest tests.test_algolib_bridge tests.test_closed_loop_integration tests.test_execution_control_integration -q
```

覆盖点：

- 默认仍为 local  
- Agent 级环境变量覆盖全局  
- algolib 失败自动降级  
- 直连 `/predict` 请求形状  
- SDK 注册元数据含 `skill_ids`  
- 原有 EC / Closed Loop 集成测试仍通过  

---

## 8. 已知限制 / 后续可做

1. Closed Loop 的 algolib 路径目前是单轮编排，不含本地模式里的完整多 cycle 训练与聚类过程。  
2. ~~毁伤评估仅 features~~ **已支持可选 images**：默认 `auto`——target 带齐 `pre_image`/`post_image`/`polygon` 时走 images，否则走 features；可用 `damage_input_mode` 或 `CLOSED_LOOP_DAMAGE_INPUT_MODE=auto|features|images` 强制。  
3. 网关模式依赖本机 algolib 网关可用；直连模式依赖各 `python_http_service` 已启动。  
4. 可继续把更多子步骤做成可观测 metrics（每个算法包耗时分别上报）。

### 8.1 毁伤 images 可选接口（target 字段）

任选一种写法即可：

```json
{
  "target_id": "T-001",
  "pre_image": {"path": "data/xbd/.../pre.png"},
  "post_image": {"path": "data/xbd/.../post.png"},
  "polygon": [[10, 10], [50, 10], [50, 50], [10, 50]]
}
```

或：

```json
{
  "image_pair": {"pre": "<base64>", "post": "<base64>"},
  "geometry": {"polygon": [[...], [...], [...]]}
}
```

images 不完整时自动回退 features（若有手工特征）；服务端 images 返回 `insufficient_data` 时也会再试 features。
