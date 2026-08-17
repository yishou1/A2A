# A2A 与 SynapseRAG 联动说明

## 运行关系

SynapseRAG 通过 `POST /api/retrieve` 提供纯证据检索。方案生成和规则
Agent 的 LLM 仍只选择对应 core；core 内部完成结构化规则匹配、证据检索和
LR/LSTM/ONNX 推理。

```text
Agent -> decision/compliance core -> structured rules
                              -> SynapseRAG /api/retrieve
                              -> LR/LSTM/ONNX and final result
```

## A2A 配置

```bash
export RAG_BACKEND=synapserag
export SYNAPSERAG_BASE_URL=http://127.0.0.1:8000
export SYNAPSERAG_API_TOKEN=
export SYNAPSERAG_TIMEOUT_SECONDS=60
export SYNAPSERAG_TOP_K_PLANNING=3
export SYNAPSERAG_TOP_K_COMPLIANCE=3
```

- `RAG_BACKEND=local` 保留原本的 A2A 本地知识库。
- `RAG_BACKEND=disabled` 禁用检索；规划继续使用规则基础扣分，合规结果会
  保守转为人工复核，已有 blocking 结论保持不变。
- Token 为空时适合本机联调；SynapseRAG 配置同名 Token 后必须保持一致。

## local 与 algolib

`DECISION_AGENT_BACKEND=local` 时，环境变量设置在两个 Agent 进程中。

`DECISION_AGENT_BACKEND=algolib` 时，环境变量必须设置在
`decision_planning_core` 和 `compliance_authorization_core` 两个 Python HTTP
Service 进程中。Agent 进程本身不代替 core 发起检索。

算法库调用超时应覆盖 RAG 检索时间：

```bash
export ALGOLIB_TIMEOUT_SECONDS=75
```

## 检查

```bash
curl http://127.0.0.1:8000/api/health

curl -X POST http://127.0.0.1:8000/api/retrieve \
  -H 'Content-Type: application/json' \
  -d '{
    "schema_version":"1.0",
    "request_id":"rag-smoke-001",
    "purpose":"compliance",
    "top_k":3,
    "queries":[{
      "query_id":"RULE-BLOCK-001",
      "text":"direct execution authorization restriction"
    }]
  }'
```

Agent/core 输出应包含 `rag_evidence`、`rag_model_profile`、`rag_warnings` 和
`rag_duration_ms`。规划方案还应包含 `base_score`、`rag_rule_adjustment`、
`matched_rule_ids`、`evidence_rule_ids` 和 `final_score`。
