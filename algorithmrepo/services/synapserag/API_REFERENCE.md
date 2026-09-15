# A2A 纯检索接口

`POST /api/retrieve` 只执行 `SynapseRAG.retrieve()`，不会调用 QA LLM，也不
产生批准、阻断或方案推荐结论。

```bash
curl -X POST http://127.0.0.1:8000/api/retrieve \
  -H 'Content-Type: application/json' \
  -H 'Authorization: Bearer optional-token' \
  -d '{
    "schema_version":"1.0",
    "request_id":"dp-001",
    "purpose":"planning",
    "top_k":3,
    "context":{
      "workflow_id":"workflow-001",
      "task_id":"task-001",
      "work_item_id":"work-item-001",
      "agent_id":"decision_planning_agent"
    },
    "explain":{"enabled":true,"level":"detailed"},
    "queries":[{
      "query_id":"RULE-BLOCK-001",
      "text":"rule and candidate-plan context"
    }]
  }'
```

约束：每次 1 至 20 个 query，`top_k` 为 1 至 10，每条 query 最长 4000
字符。返回证据包含文件名、页码、标题路径、稳定引用和内容 SHA-256。

`context` 和 `explain` 均为可选字段。启用解释后，响应增加 `trace_id`，
每条 query 增加 `query_trace_id`。轨迹按请求保存事实重排、PPR 种子、
Graph/Dense/Lexical 召回、融合贡献、最终证据、图谱覆盖层和阶段耗时。

轨迹查询接口：

```text
GET /api/retrieval-traces?task_id=task-001
GET /api/retrieval-traces?workflow_id=workflow-001
GET /api/retrieval-traces/{trace_id}
GET /api/retrieval-traces/{trace_id}/graph?view=skeleton
GET /api/retrieval-traces/{trace_id}/graph?view=candidates
GET /api/retrieval-traces/{trace_id}/graph?view=skeleton&evidence_node_key=chunk-...&max_paths=1
```

列表接口只返回轻量摘要，详情接口返回有大小限制的完整轨迹，`/graph`
只返回可叠加到全局知识图谱的稳定 `entity-*`/`chunk-*` 节点键和边。
`skeleton` 返回种子到最终证据的精简路径，保留低分但必要的连接节点；
`candidates` 只返回召回节点，不返回边。可用 `query_trace_id` 选择批量请求中的
某个 Query，用 `evidence_node_key` 下钻到单条证据。
任务列表仍由 A2A/Commander 管理，SynapseRAG 只根据外部 `task_id`
保存和查询检索记录。

可选鉴权：

```bash
export SYNAPSERAG_API_TOKEN=optional-token
```

Token 为空时不鉴权；配置后保护 `/api/retrieve` 和所有轨迹查询接口，
`/api/health` 仍公开。
无活动索引返回 `503 INDEX_NOT_READY`，检索异常返回
`500 RETRIEVAL_ERROR`。开启解释时，失败响应也包含 `trace_id`，用于查看
失败阶段和降级原因。

轨迹配置：

```bash
export SYNAPSERAG_TRACE_ENABLED=true
export SYNAPSERAG_TRACE_RETENTION_DAYS=7
export SYNAPSERAG_TRACE_MAX_CHANNEL_RESULTS=20
export SYNAPSERAG_TRACE_MAX_PPR_NODES=30
export SYNAPSERAG_TRACE_MAX_NODES=25
export SYNAPSERAG_TRACE_MAX_EDGES=20
export SYNAPSERAG_TRACE_MAX_PATHS=3
export SYNAPSERAG_TRACE_MAX_HOPS=4
```

轨迹保存在 `outputs/api_server_data/retrieval_traces.sqlite3`，与文档索引
任务数据库分离。`summary` 级别会进一步限制通道结果和 PPR 节点数量。

## 图谱展示消息协议

`scripts/analysis/graph_pickle_to_html.py` 生成的图谱页面支持同源父页面通过
`postMessage` 控制检索覆盖层：

```javascript
iframe.contentWindow.postMessage({
  type: "synapserag:apply-overlay",
  overlay: {nodes: traceNodes, edges: traceEdges},
  traceId: "trace-001"
}, window.location.origin);

iframe.contentWindow.postMessage({
  type: "synapserag:focus-node",
  nodeKey: "chunk-..."
}, window.location.origin);

iframe.contentWindow.postMessage({
  type: "synapserag:clear-overlay"
}, window.location.origin);
```

图谱加载完成后发送 `synapserag:ready`；用户点击节点时发送
`synapserag:node-select`。覆盖层按 `ppr_seed`、`ppr_recall`、
`graph_recall`、`fusion_result` 和 `final_evidence` 角色着色。

## 原文定位与高亮

证据返回的逻辑 `chunk_id` 可继续追溯到活动索引对应的原始文件：

```text
GET /api/evidence/{chunk_id}/location
GET /api/evidence/{chunk_id}/preview
GET /api/documents/{document_id}/original
```

`location` 对文本返回字符区间，对文本型 PDF 返回页码和矩形坐标。
`preview` 返回带高亮标注的 PDF 内存副本，或使用 `<mark>` 标记证据的安全
HTML；原文件不会被修改。旧索引通过 `source_map` 动态定位，新建索引还会
在 chunk 中记录 `source_segments`。扫描 PDF 仍需先经过 OCR。

三个接口与检索轨迹接口使用同一个可选 Bearer Token。原文件只能根据活动
manifest 中的 `document_id` 访问，接口不接受磁盘路径。
