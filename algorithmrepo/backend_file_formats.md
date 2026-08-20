# 后端接入文件格式说明

这份文档说明当前仓库里两条后端路径实际接受的文件、接口和格式要求：

- `onnx`
- `python_http_service`

它对应的是当前代码已经实现并验证过的能力，不等于“任意模型/任意服务都能零改动接入”。

## 1. ONNX 路径

### 1.1 目录结构

推荐目录结构如下：

```text
your_algorithm/
  algorithm_card.yaml
  input.schema.json
  output.schema.json
  model.onnx
  preprocess.yaml
  postprocess.yaml
  tokenizer.json              # 可选
  label_map.json              # 可选
  golden_cases/
    case_001_input.json
    case_001_expected.json
```

示例可参考：

- `examples/onnx_text_classifier/1.0.0`

### 1.2 必需文件

- `algorithm_card.yaml`
  - YAML
  - 描述算法身份、后端类型、schema、模型位置、预处理/后处理配置
- `input.schema.json`
  - JSON
  - 用于校验 `request.inputs`
- `output.schema.json`
  - JSON
  - 用于校验推理输出
- `model.onnx`
  - ONNX 二进制模型文件
- `preprocess.yaml`
  - YAML
  - 描述输入如何转换成 ONNX tensor
- `postprocess.yaml`
  - YAML
  - 描述 ONNX 输出如何转换回结构化 JSON
- `golden_cases/*_input.json`
  - JSON
  - 示例输入，用于 package 校验
- `golden_cases/*_expected.json`
  - JSON
  - 示例期望输出，用于 package 校验

### 1.3 可选文件

- `tokenizer.json`
  - JSON
  - 当 `text_tokenization` 预处理需要外部分词器配置时使用
- `label_map.json`
  - JSON object
  - 当 `classification_postprocess` 需要标签映射时使用
- `README.md`
  - 说明文档，不参与强校验

### 1.4 algorithm_card.yaml 关键字段

至少需要正确配置这些字段：

```yaml
algorithm_id: onnx_text_classifier
version: 1.0.0
backend_type: onnx

machine_spec:
  input_schema_ref: input.schema.json
  output_schema_ref: output.schema.json
  runtime:
    backend_type: onnx
    model_uri: model.onnx
    execution_provider: cpu
  preprocess:
    config_uri: preprocess.yaml
  postprocess:
    config_uri: postprocess.yaml
```

### 1.5 当前支持的 preprocess / postprocess 子集

当前不是任意预处理/后处理都支持，只支持已实现的几种：

`preprocess.yaml` 中的 `type` 目前支持：

- `no_op`
- `tensor_from_json`
- `text_tokenization`

`postprocess.yaml` 中的 `type` 目前支持：

- `no_op`
- `classification_postprocess`

### 1.6 这句“不是任意模型都能零改动接入”是什么意思

它的意思是：现在系统已经能跑真实 ONNX Runtime，但它并不是“随便给一个 `.onnx` 文件就自动知道如何喂输入、如何解释输出”。

当前 ONNX 接入依赖一套固定契约：

- `request.inputs` 必须能被当前 `preprocess.yaml` 转成模型所需 tensor
- 模型真实的输入 tensor 名称，要和预处理推导出的名称一致
- 模型真实的输出 tensor 名称，要和后处理期望的名称一致
- 模型输出的数据类型/形状，要能被当前 `postprocess.yaml` 解释

例如：

- `text_tokenization` 默认会产出 `input_ids`、`attention_mask`
- `classification_postprocess` 默认更适合消费分类类输出，比如 `logits`

所以如果你的 ONNX 模型：

- 输入 tensor 名字不同
- 输出 tensor 名字不同
- 输入格式不是当前支持的 JSON / 文本形式
- 输出格式不是当前支持的分类或直通格式

那就需要补新的 preprocess/postprocess 适配，而不是直接零改动接入。

### 1.7 当前 ONNX 运行边界

- 只支持 `execution_provider: cpu`
- 当前张量类型主要覆盖：
  - `float32`
  - `int64`
  - `string`
- 输入/输出 schema 只支持当前实现的 JSON Schema 子集

## 2. Python HTTP Service 路径

### 2.1 目录结构

推荐目录结构如下：

```text
your_algorithm/
  algorithm_card.yaml
  input.schema.json
  output.schema.json
  golden_cases/
    case_001_request.json
    case_001_response.json   # 可选但推荐
  README.md                  # 可选
  service_contract.md        # 可选
```

示例可参考：

- `examples/python_http_service_llm_explainer/1.0.0`

### 2.2 必需文件

- `algorithm_card.yaml`
  - YAML
  - 描述 service 地址、health/metadata/predict 接口位置、超时等
- `input.schema.json`
  - JSON
  - 用于校验 `request.inputs`
- `output.schema.json`
  - JSON
  - 用于校验 `response.outputs`
- `golden_cases/*_request.json`
  - JSON
  - 注册和 `validate` 阶段会拿它去调用远端 `/predict`

### 2.3 可选文件

- `golden_cases/*_response.json`
  - JSON
  - 主要作为示例/回归参考，不是当前注册阶段的硬依赖
- `README.md`
  - 文档
- `service_contract.md`
  - 对服务协议的补充说明

### 2.4 algorithm_card.yaml 关键字段

至少需要正确配置这些字段：

```yaml
algorithm_id: llm_rule_explainer
version: 1.0.0
backend_type: python_http_service

machine_spec:
  input_schema_ref: input.schema.json
  output_schema_ref: output.schema.json
  runtime:
    backend_type: python_http_service
    endpoint: http://127.0.0.1:8080/predict
    health_endpoint: http://127.0.0.1:8080/health
    metadata_endpoint: http://127.0.0.1:8080/metadata
    timeout_ms: 10000
```

### 2.5 当前支持的 URL / 传输协议

当前只支持：

- `http://`

当前不支持：

- `https://`

### 2.6 服务必须提供的接口

服务必须暴露这 3 个接口：

- `GET /health`
- `GET /metadata`
- `POST /predict`

### 2.7 /health 返回格式

必须返回 JSON object，至少包含：

```json
{
  "ok": true,
  "status": "ready",
  "algorithm_id": "llm_rule_explainer",
  "version": "1.0.0",
  "model_loaded": true
}
```

当前代码会检查：

- `ok` 是 `boolean`
- `status` 是 `string`
- `algorithm_id` / `version` 与算法卡一致
- `model_loaded` 是 `true`
- `status` 必须是 `ready`

### 2.8 /metadata 返回格式

必须返回 JSON object，至少包含：

```json
{
  "algorithm_id": "llm_rule_explainer",
  "version": "1.0.0",
  "backend_type": "python_http_service"
}
```

当前代码会检查：

- `algorithm_id`
- `version`
- `backend_type`

### 2.9 /predict 请求格式

请求体是统一的 `AlgorithmRequest` JSON，大致如下：

```json
{
  "request_id": "req_001",
  "trace_id": "trace_001",
  "algorithm_id": "llm_rule_explainer",
  "version": "1.0.0",
  "backend_type": "python_http_service",
  "inputs": {
    "task_text": "Check whether the task follows the rule.",
    "entities": []
  },
  "params": {
    "max_tokens": 256,
    "temperature": 0.2
  }
}
```

### 2.10 /predict 响应格式

响应体必须是 JSON object。成功时至少包含：

```json
{
  "ok": true,
  "request_id": "req_001",
  "trace_id": "trace_001",
  "algorithm_id": "llm_rule_explainer",
  "version": "1.0.0",
  "outputs": {
    "explanation": "Required fields are missing and manual review is recommended.",
    "confidence": 0.82
  },
  "usage": {
    "latency_ms": 1200
  },
  "error": null
}
```

当前代码会检查：

- `ok` 是 `boolean`
- `algorithm_id` / `version` 与算法卡一致
- `outputs` 字段存在
- `outputs` 满足 `output.schema.json`

### 2.11 当前 Python service 运行边界

- 真正的推理发生在外部 Python 服务里
- 本仓库负责：
  - 注册
  - 远端联调校验
  - 运行前输入 schema 校验
  - 调 `/health`
  - 调 `/predict`
  - 运行后输出 schema 校验
  - 审计日志记录
- 当前不会自动启动 Python 服务
- 当前不会自动 fallback 到 ONNX

## 3. 当前已实现的 Schema 子集

两条路径共用当前的基础 JSON Schema 子集，主要包括：

- `type`
- `required`
- `properties`
- `additionalProperties`
- `items`
- `enum`
- `minLength`
- `maxLength`
- `minItems`
- `maxItems`
- `minimum`
- `maximum`

如果 schema 用到了这套子集之外的能力，就可能在注册阶段被拒绝。
