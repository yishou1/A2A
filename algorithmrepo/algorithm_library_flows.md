# 动态算法库流程说明 v2

> 范围：只说明算法接入、校验、启用、执行、禁用、删除、Agent 卡片查询流程。  
> 不包含 Pipeline 编排，不包含自动后端选择，不包含 fallback。

---

## 1. 总体运行角色

```text
用户 / 上层系统
  ↓
C++ Algorithm Library
  ├── Algorithm Registry
  ├── Algorithm Card Store
  ├── Schema Validator
  ├── ONNX Runner
  └── Python HTTP Service Runner
        ↓
      Python HTTP Service
```

两类算法：

```text
ONNX Algorithm:
  C++ 本地加载 .onnx 模型文件。

Python HTTP Service Algorithm:
  C++ 通过 HTTP 调用已经启动好的 Python 服务。
```

---

## 2. ONNX 算法接入流程

### 2.1 输入材料

用户需要提供一个 ONNX 算法包：

```text
onnx_algorithms/
  text_entity_extractor/
    1.0.0/
      algorithm_card.yaml
      model.onnx
      input.schema.json
      output.schema.json
      preprocess.yaml
      postprocess.yaml
      tokenizer.json
      label_map.json
      golden_cases/
        case_001_input.json
        case_001_expected.json
      README.md
```

### 2.2 注册流程

```text
用户提交 package_uri
  ↓
系统读取 algorithm_card.yaml
  ↓
检查 backend_type = onnx
  ↓
检查 model.onnx 是否存在
  ↓
检查 input.schema.json / output.schema.json
  ↓
检查 preprocess.yaml / postprocess.yaml
  ↓
尝试加载 ONNX 模型
  ↓
执行 golden case
  ↓
输出 schema 校验
  ↓
注册为 validated
```

### 2.3 激活流程

```text
用户显式 activate
  ↓
系统检查状态是 validated
  ↓
状态改为 active
  ↓
该算法可以被 run 调用
  ↓
该算法卡片可以出现在 Agent card list
```

### 2.4 执行流程

```text
用户调用 /algorithms/run
  ↓
指定 algorithm_id + version + backend_type = onnx
  ↓
Registry 查找 active 算法
  ↓
input.schema 校验 inputs
  ↓
ONNX Runner 加载或复用模型 session
  ↓
preprocess/tokenizer 生成 tensor
  ↓
ONNX Runtime 执行推理
  ↓
postprocess 生成 outputs JSON
  ↓
output.schema 校验 outputs
  ↓
返回 AlgorithmResult
  ↓
记录审计日志
```

### 2.5 ONNX 执行失败

失败直接返回，不 fallback。

常见错误：

```text
ONNX_MODEL_NOT_FOUND
ONNX_LOAD_FAILED
ONNX_RUNTIME_ERROR
ONNX_INPUT_TENSOR_MISMATCH
PREPROCESS_FAILED
POSTPROCESS_FAILED
OUTPUT_SCHEMA_INVALID
```

---

## 3. Python HTTP Service 算法接入流程

### 3.1 输入材料

用户需要提供服务算法登记包：

```text
service_algorithms/
  llm_rule_explainer/
    1.0.0/
      algorithm_card.yaml
      input.schema.json
      output.schema.json
      service_contract.md
      golden_cases/
        case_001_request.json
        case_001_response.json
      README.md
```

同时用户或部署系统需要保证 Python 服务已经启动，且提供：

```text
GET  /health
GET  /metadata
POST /predict
```

### 3.2 服务启动流程

推荐方式：

```text
启动 Python 服务
  ↓
加载 tokenizer / model / index / config
  ↓
执行 warmup
  ↓
/health 返回 ready
  ↓
等待 C++ 调用
```

注意：

```text
算法注册不会自动启动服务。
算法注册不会自动加载模型。
只有 Python 服务实际启动并加载模型后才占用资源。
```

### 3.3 注册流程

```text
用户提交 algorithm_card.yaml
  ↓
系统读取算法卡片
  ↓
检查 backend_type = python_http_service
  ↓
读取 endpoint / health_endpoint / metadata_endpoint
  ↓
调用 /health
  ↓
若 status != ready，则注册可失败或标记 service_not_ready
  ↓
调用 /metadata
  ↓
检查 metadata 与算法卡片一致
  ↓
用 golden request 调 /predict
  ↓
校验 response 与 output.schema
  ↓
注册为 validated
```

### 3.4 激活流程

```text
用户显式 activate
  ↓
系统检查卡片与 schema 已校验
  ↓
可选再次检查 /health
  ↓
状态改为 active
```

### 3.5 执行流程

```text
用户调用 /algorithms/run
  ↓
指定 algorithm_id + version + backend_type = python_http_service
  ↓
Registry 查找 active 算法
  ↓
input.schema 校验 inputs
  ↓
PythonHttpRunner 调 /health
  ↓
若 ready，组装 POST /predict 请求
  ↓
发送 HTTP JSON
  ↓
解析 HTTP response
  ↓
检查 HTTP status
  ↓
检查 ok 字段
  ↓
output.schema 校验 outputs
  ↓
返回 AlgorithmResult
  ↓
记录审计日志
```

### 3.6 Python Service 执行失败

失败直接返回，不 fallback。

常见错误：

```text
SERVICE_NOT_READY
SERVICE_UNAVAILABLE
SERVICE_TIMEOUT
SERVICE_HTTP_ERROR
SERVICE_RESPONSE_INVALID
SERVICE_OUTPUT_SCHEMA_INVALID
```

---

## 4. 算法卡片查询流程

后续 Agent 会读取算法卡片来理解可用算法，但第一版不让 Agent 自动执行算法。

### 4.1 查询流程

```text
Agent 或上层系统请求 /algorithms/cards
  ↓
可选过滤 task_family / backend_type / modality / capability
  ↓
Registry 返回 active 算法
  ↓
生成脱敏 Agent 视图
  ↓
返回算法卡片列表
```

### 4.2 Agent 视图字段

返回：

```text
algorithm_id
version
backend_type
task_family
modalities
capabilities
agent_card.summary
agent_card.when_to_use
agent_card.when_not_to_use
agent_card.input_description
agent_card.output_description
constraints
safety
input_schema 摘要
output_schema 摘要
```

不返回：

```text
内部鉴权 token
绝对路径
管理员字段
私有部署细节
敏感 endpoint 凭证
```

---

## 5. 禁用和删除流程

### 5.1 禁用

```text
用户调用 disable
  ↓
状态 active/validated → disabled
  ↓
新请求不能调用
  ↓
历史记录保留
  ↓
默认不出现在 Agent card list
```

### 5.2 逻辑删除

```text
用户调用 delete
  ↓
状态 → deleted
  ↓
不允许新请求调用
  ↓
不出现在算法列表
  ↓
保留审计引用
```

### 5.3 物理清理

```text
管理员调用 purge
  ↓
检查是否允许物理删除
  ↓
删除本地包或 registry 记录
```

第一版可以不实现 purge。

---

## 6. 执行请求与响应

### 6.1 请求

```json
{
  "request_id": "req_001",
  "trace_id": "trace_001",
  "algorithm_id": "text_entity_extractor",
  "version": "1.0.0",
  "backend_type": "onnx",
  "inputs": {
    "text": "张三于2024年5月到北京参加会议。"
  },
  "params": {}
}
```

### 6.2 成功响应

```json
{
  "ok": true,
  "request_id": "req_001",
  "trace_id": "trace_001",
  "algorithm_id": "text_entity_extractor",
  "version": "1.0.0",
  "backend_type": "onnx",
  "outputs": {
    "entities": []
  },
  "usage": {
    "latency_ms": 52
  },
  "error": null
}
```

### 6.3 失败响应

```json
{
  "ok": false,
  "request_id": "req_001",
  "trace_id": "trace_001",
  "algorithm_id": "text_entity_extractor",
  "version": "1.0.0",
  "backend_type": "onnx",
  "outputs": {},
  "usage": {
    "latency_ms": 30
  },
  "error": {
    "code": "OUTPUT_SCHEMA_INVALID",
    "message": "The model output does not match output.schema.json."
  }
}
```

---

## 7. Python HTTP Service 接口流程

### 7.1 /health

```text
C++ → GET /health
Python Service → ready/loading/error
```

ready 示例：

```json
{
  "ok": true,
  "status": "ready",
  "algorithm_id": "llm_rule_explainer",
  "version": "1.0.0",
  "model_loaded": true
}
```

### 7.2 /metadata

```text
C++ → GET /metadata
Python Service → 服务元信息
```

示例：

```json
{
  "algorithm_id": "llm_rule_explainer",
  "version": "1.0.0",
  "backend_type": "python_http_service",
  "task_family": "generation",
  "batch_supported": false,
  "streaming_supported": false,
  "max_input_chars": 20000
}
```

### 7.3 /predict

```text
C++ → POST /predict
Python Service → outputs
```

---

## 8. 数据传输约束

### 8.1 小数据

文本、结构化字段、参数直接放 JSON：

```json
{
  "inputs": {
    "text": "hello",
    "language": "zh"
  }
}
```

### 8.2 大文件

图像、视频、长文档使用 `media_refs`：

```json
{
  "inputs": {
    "media_refs": [
      {
        "name": "input_image",
        "uri": "file:///data/images/a.jpg",
        "media_type": "image",
        "format": "jpg"
      }
    ]
  }
}
```

第一版不支持 multipart 上传，不建议 base64 大文件。

---

## 9. 日志流程

每次执行后写一条审计日志：

```json
{
  "request_id": "req_001",
  "trace_id": "trace_001",
  "algorithm_id": "text_entity_extractor",
  "version": "1.0.0",
  "backend_type": "onnx",
  "status": "success",
  "latency_ms": 52,
  "input_hash": "sha256:...",
  "output_hash": "sha256:..."
}
```

---

## 10. 推荐第一阶段验收

第一阶段完成后，应能做到：

```text
1. 注册一个 ONNX 算法包。
2. 注册一个 Python HTTP Service 算法。
3. 查询算法列表。
4. 查询 Agent 算法卡片。
5. 激活 / 禁用 / 逻辑删除算法。
6. 调用指定 ONNX 算法。
7. 调用指定 Python HTTP Service 算法。
8. 失败时返回明确错误码。
9. 不做 fallback。
10. 不做自动 backend 选择。
```
