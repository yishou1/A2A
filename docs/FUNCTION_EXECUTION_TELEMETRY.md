# 功能点执行轨迹使用说明

算法库使用 `KC-01` 到 `KC-28` 表示 28 个标准功能点。完整目录见
`config/operational_function_catalog.yaml`。

该能力是增量能力：原有 `/run` 请求、算法输入 Schema 和算法输出 `outputs`
均不需要修改。功能点信息作为平台级遥测单独返回。

## 1. 更新已有注册表

注册表会保存 Algorithm Card 的快照。拉取新代码后，对已经注册的算法执行一次
`validate`，即可把卡片中的 `operational_functions` 刷新到注册表，同时保持原来的
生命周期状态：

```powershell
.\build\Debug\algolib.exe validate `
  edl_evidential_verifier 1.0.0 python_http_service
```

尚未注册的算法仍按原来的 `register -> activate` 流程处理。

## 2. 按功能点发现算法

前端可以先读取完整的 28 项目录：

```powershell
$base = "http://127.0.0.1:8088"
Invoke-RestMethod -Uri "$base/operational-functions"
```

```powershell
$base = "http://127.0.0.1:8088"

Invoke-RestMethod `
  -Uri "$base/algorithms?function_code=validate_detection&active_only=true"
```

也可以使用功能点编号：

```powershell
Invoke-RestMethod `
  -Uri "$base/algorithms?function_id=KC-09&active_only=true"
```

## 3. 调用算法并记录功能点

`function_context` 是可选字段。建议 Agent 同一条任务链上的调用共用一个
`trace_id`，并为每一步提供不同的 `step_instance_id`。

```json
{
  "request_id": "request-009",
  "trace_id": "mission-001",
  "algorithm_id": "edl_evidential_verifier",
  "version": "1.0.0",
  "backend_type": "python_http_service",
  "function_context": {
    "function_id": "KC-09",
    "function_code": "validate_detection",
    "workflow_instance_id": "mission-001",
    "step_instance_id": "step-009"
  },
  "inputs": {},
  "params": {}
}
```

响应会增加：

```json
{
  "function_execution": {
    "function_id": "KC-09",
    "function_code": "validate_detection",
    "function_name": "Validate Detection",
    "role": "primary",
    "coverage_level": "full",
    "mapping_source": "request",
    "matched": true,
    "execution_status": "succeeded",
    "workflow_instance_id": "mission-001",
    "step_instance_id": "step-009"
  }
}
```

如果请求不提供 `function_context`，算法库会使用算法卡中 `role: primary` 的映射，
并返回 `mapping_source: algorithm_card_default`。如果显式功能点没有匹配算法卡，算法
仍会照常执行，但返回 `matched: false`，不会破坏旧调用链。

## 4. 前端查询完整流程

```powershell
Invoke-RestMethod `
  -Uri "$base/traces/mission-001/function-executions"
```

响应中的 `function_executions` 按执行日志顺序排列，每项包含：

- `sequence`
- 功能点编号、编码和名称
- 实际使用的 `algorithm_id`、版本和后端
- 成功或失败状态
- 延迟、错误码和记录时间
- 工作流实例与步骤实例编号

前端可以直接按 `sequence` 渲染时间线。接口只读取现有
`execution_audit.jsonl`，不依赖额外数据库。

## 5. 兼容性说明

- 旧 Algorithm Card 不包含 `operational_functions` 时仍然有效。
- 旧 `/run` JSON 不包含 `function_context` 时仍然有效。
- 原有 `outputs` 内容不变。
- 第一版不启用功能点强校验，映射不匹配只记录，不拒绝运行。
- KC-23 使用 `external_handoff`，表示算法库只生成外部执行交接信息。
