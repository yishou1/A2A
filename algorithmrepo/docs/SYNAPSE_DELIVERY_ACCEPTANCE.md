# SynapseRAG 独立算法交付验收

验收日期：2026-09-15。部署操作见 [独立部署说明](SYNAPSE_STANDALONE_DELIVERY.md)。

## 实现范围

- `synapse_rag_retriever:2.0.0`：通过 AlgoLib `/run` 调用真实 SynapseRAG 检索，返回证据、引用、索引版本和 `trace_id`。保留旧版 1.0.0。
- `synapse_graph_explorer:1.0.0`：通过同一入口查询实体邻域、两点关联路径和已持久化检索轨迹。
- 两个算法共享 HTTP 后端；源码在算法库中，环境、模型和索引数据独立，不需要启动 Commander。
- A2A 可设置 `SYNAPSERAG_TRANSPORT=algolib` 经算法库检索；默认仍为 direct。

## 真实调用结果

使用既有 Newport 索引的临时副本、本地 Ollama 和当前源码编译的 AlgoLib。测试过程包含注册、校验、激活和 `/run` 调用，没有使用模拟检索响应，也没有修改原始索引。

查询：`What authorization is required?`

| 调用 | 结果 | 本次适配服务耗时 |
| --- | --- | --- |
| retrieve | 3 条证据，含文件名、页码、引用、内容哈希及 trace_id | 1747.001 ms |
| trace | 13 个节点、7 条边、3 条解释路径 | 2.618 ms |
| neighbors | 41 个节点、80 条边，达到边数上限后标记 truncated | 11.183 ms |
| paths | 3 个节点、3 条边、3 条关联路径 | 18.705 ms |

证据来源为 `Newport Rules of Engagement Handbook.pdf`，引用页为 54、96–97、20。返回的索引版本非空，采用索引清单 SHA-256 标识。

以上为单次调用的 `usage.latency_ms`，不代表吞吐量基准或完整 Agent 工作流耗时。图谱路径描述关联关系或检索解释，不代表形式逻辑证明。

完整实测 JSON 位于 `runtime-data/delivery-report.json`，属于本机运行产物，不纳入源码提交。验收结束已停止测试栈及本次启动的 Ollama；临时轨迹数据库随测试目录清理，因此报告中的 trace_id 不能在新启动实例中直接复用。

## 自动化测试

| 测试范围 | 结果 |
| --- | --- |
| 新算法适配及图谱接口：转发、超时、鉴权、参数、索引一致性、路径和历史轨迹 | 2 项测试通过，内含多组断言 |
| A2A SynapseRAG 路由与 LLM client 回归 | 13 项通过 |
| 原文服务、检索轨迹、证据骨架、PDF 定位及高亮等原有接口 | 9 项通过 |
| 当前 AlgoLib CLI 与 HTTP server 编译 | 通过 |
| git diff --check | 通过 |

验收入口为 `scripts/verify_synapse_delivery.py`，启动入口为 `scripts/start_synapse_stack.py`。脚本必须等待两个指定版本均激活后才开始业务调用。

## 未覆盖范围

- 本轮复用已有 Python 环境，尚未在全新环境或离线机器上验证依赖安装；服务依赖列表不是跨平台锁文件。
- 本轮没有重新执行完整 Commander/AMOS 工作流、并发压测或全部仓库回归。
- 真实验收复用了已构建索引，未对新文档首次建库进行完整模型联调。
- 独立部署仍需提供模型服务及知识库数据；不是无外部依赖的一键离线包。
