# SynapseRAG 独立算法交付

## 交付内容

只需交付 algorithmrepo 目录。两个新算法通过 HTTP 调用其中的完整 SynapseRAG，
不导入 Commander、decision_support 或 Agent SDK。模型服务和知识库数据另行配置。

| 算法 ID | 版本 | 端口 | 功能 |
|---|---|---|---|
| synapse_rag_retriever | 2.0.0 | 9048 | 图增强检索、证据、引用、trace_id |
| synapse_graph_explorer | 1.0.0 | 9049 | neighbors、paths、trace |

原 synapse_rag_retriever:1.0.0 的 TIA 协议继续保留。
两个新包共享 8000 端口的 SynapseRAG，统一通过 8088 的 /run 调用。

## 部署命令

在独立 algorithmrepo 根目录运行，建议 Python 3.11：

```bash
python3 -m venv .venv-synapse
.venv-synapse/bin/python -m pip install -r services/synapserag/requirements-service.txt
cmake -S . -B build
cmake --build build --target algolib_cli algolib_server -j2

export SYNAPSERAG_SAVE_DIR="$PWD/runtime-data/synapserag"
export SYNAPSERAG_INDEX_ID=customer-kb-v1
export SYNAPSERAG_EMBEDDING_MODEL=qwen3-embedding:0.6b
export SYNAPSERAG_EMBEDDING_BASE_URL=http://127.0.0.1:11434/v1
export SYNAPSERAG_QA_MODEL=qwen3:1.7b
export SYNAPSERAG_QA_BASE_URL=http://127.0.0.1:11434/v1
export SYNAPSERAG_OPENIE_MODEL=qwen3:1.7b
export SYNAPSERAG_OPENIE_BASE_URL=http://127.0.0.1:11434/v1
export SYNAPSERAG_OPENIE_API_KEY=EMPTY

.venv-synapse/bin/python scripts/start_synapse_stack.py
```

提前启动 Ollama 并准备指定模型，也可改为兼容 API 地址。
首次 CMake 配置下载 C++ 依赖；离线交付需要提前准备依赖或编译产物。
requirements-service.txt 是 HTTP 模型部署依赖范围，尚不是跨平台锁定环境。
启动器遇到端口冲突直接报错；Ctrl+C 停止本次启动的四个进程。
环境、数据和部署注册表放在 runtime-data 或指定的外部目录。

## 首次索引与注册

访问 http://127.0.0.1:8000/docs 使用文档上传、索引构建和任务状态管理接口。
最小文本导入：

```bash
curl --fail-with-body http://127.0.0.1:8000/api/index -H 'Content-Type: application/json' -d '{"docs":[{"title":"Demo","text":"Acme was founded by Alice. Alice lives in Paris."}]}'
```

启动器等待索引就绪，然后执行真实检索、获取 trace_id、生成部署专属 golden
request，再注册和激活两个算法包。因为注册器会实际调用 /predict，不能用任意
客户知识库中不存在的固定实体 ID 作为验收请求。
生成的验收包位于 runtime-data/packages，源码中的样例不被改写。
空知识库不报告可用。当前实例仅有一个激活索引，错误 index_id 返回 INDEX_MISMATCH。

## 检索调用

```bash
curl --fail-with-body http://127.0.0.1:8088/run -H 'Content-Type: application/json' -d '{"request_id":"demo-1","algorithm_id":"synapse_rag_retriever","version":"2.0.0","backend_type":"python_http_service","inputs":{"queries":[{"query_id":"q1","text":"Who founded Acme?"}],"top_k":3,"explain":{"enabled":true,"level":"summary"}},"params":{}}'
```

outputs.results 按 query_id 返回 evidence；证据包含 text、source、document_id、
page_start/page_end、citation、score。outputs.trace_id 在轨迹启用且保存成功时返回。
retrieval_profile 包含 index_id、index_version 和 duration_ms。
purpose 可为 general、planning、compliance，默认 general。

## 图谱调用

同样调用 /run，algorithm_id=synapse_graph_explorer、version=1.0.0。

| operation | 必需字段 | 可选约束 |
|---|---|---|
| neighbors | entity_id | index_id、max_hops、max_nodes、max_edges |
| paths | source_id、target_id | index_id、max_hops、max_paths、max_nodes、max_edges |
| trace | trace_id | index_id、query_trace_id、evidence_node_key、view、max_paths、max_nodes、max_edges |

例如 inputs={"operation":"trace","trace_id":"检索返回的 trace_id"}。
实体 ID 可从 trace 输出的 nodes[].node_key 获取。输出统一包含 nodes、edges、
paths、truncated、index_id、index_version、duration_ms。
neighbors/paths 最多检查 20000 条边，数量限制导致截断时标记 truncated。
paths 是无向图关联路径，不表示严格逻辑蕴含。trace 是检索后的解释子图，
读取保存的图快照，不重新使用当前图谱解释历史请求。
旧索引缺少 job_id 时用索引清单 SHA-256 作为版本标识。

## 错误与认证

适配器返回 ok、outputs、error、usage.latency_ms。
常见错误：INDEX_NOT_READY、INDEX_MISMATCH、ENTITY_NOT_FOUND、TRACE_NOT_FOUND、
TRACE_QUERY_NOT_FOUND、RAG_TIMEOUT、RAG_SERVICE_UNAVAILABLE、INVALID_UPSTREAM_RESPONSE。
输入 422 映射为 ALGORITHM_INPUT_ERROR；算法库可能继续包装远端错误，查看 error/message。
适配器没有 mock 回退，底层检索自身的重排降级保留在检索轨迹中。
SYNAPSERAG_API_TOKEN 用于适配器到 SynapseRAG 的认证，不是网关认证凭据。
默认仅监听本机；对外访问控制由部署方配置。

## A2A 路由

```bash
export RAG_BACKEND=synapserag
export SYNAPSERAG_TRANSPORT=algolib
export ALGOLIB_BASE_URL=http://127.0.0.1:8088
```

两个 Agent 和 core 可由上述配置通过算法库检索。direct 模式保持直接 /api/retrieve，
algolib 模式调用 2.0.0。trace_id 保留在 rag_model_profile 中。
AMOS 后续可用 graph explorer 的 trace 操作展示，本轮未修改前端。

## 验收命令

```bash
PYTHONPATH=services:services/synapserag:services/synapserag/src .venv-synapse/bin/python tests/python/test_synapse_delivery.py -v
.venv-synapse/bin/python scripts/verify_synapse_delivery.py --seed-data /path/to/legacy-index-data --index-id your-index-id --query "What authorization is required?"
```

真实验收需要模型服务已运行、四个端口空闲。脚本使用临时知识库副本，结束后停止
测试服务，结果写入 runtime-data/delivery-report.json。带绝对 active pointer 的托管
索引须先迁移，不能直接作为此脚本输入；新客户通过文档管理接口重新构建索引。
