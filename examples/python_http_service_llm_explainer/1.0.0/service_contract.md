# Python HTTP Service 合约示例

这个示例服务预期提供以下接口：

- `GET /health`
- `GET /metadata`
- `POST /predict`

## `GET /health`

返回 JSON object，至少包含：

```json
{
  "ok": true,
  "status": "ready",
  "algorithm_id": "llm_rule_explainer",
  "version": "1.0.0",
  "model_loaded": true
}
```

## `GET /metadata`

返回 JSON object，至少包含：

```json
{
  "algorithm_id": "llm_rule_explainer",
  "version": "1.0.0",
  "backend_type": "python_http_service"
}
```

## `POST /predict`

请求体使用统一 `AlgorithmRequest` JSON，响应体使用统一 `AlgorithmResult` JSON 风格。

一个最小成功响应示例如下：

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

更完整的文件格式与接口要求见：

- [backend_file_formats.md](</c:/Users/liu/Desktop/algorithm repo1/backend_file_formats.md>)
