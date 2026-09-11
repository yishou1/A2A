# 当前算法库实现状态与 M01–M20 对照报告

报告日期：2026-08-18
对照依据：《18 类算法模型清单.md》正文（正文实际定义 M01–M20 共 20 类）  
报告范围：算法类别是否交付、主要算法包、实现形式和实际调用条件。

## 1. 状态口径

本报告使用以下类别交付状态：

- `COMPLETE-LOCAL`：本仓库内已有真实算法、训练模型或确定性实现，并已形成 Algorithm Card、输入输出 Schema 和运行入口。
- `COMPLETE-EXTERNAL`：模型能力由已部署的外部 API 提供；本仓库负责 Algorithm Card、参数校验、请求封装、结果 Schema 统一和 A2A 调用。该状态不表示仓库内保存或训练了对应大模型。
- `PARTIAL`：已有算法包、接口或部分实现，但正式模型权重、业务指标或严格真实调用链尚未闭环。

M09–M11 按当前项目边界和已确认事实，采用 `COMPLETE-EXTERNAL`：LLM、RAG 和 Agent 本体由外部服务提供，本算法库通过 API 调用，不再要求在本仓库内训练或保存这些模型。

Algorithm Card 中的 `draft/validated` 是算法包生命周期字段，与本报告的“算法类别交付状态”不是同一维度。M09–M11 当前卡片仍为 `draft`，后续可在目标部署环境补充一次带真实凭据的验收记录后同步为 `validated`；这不影响本报告按既定 API 集成范围将其判定为已完成。

## 2. 总体结论

| 项目 | 数量 | 对应 M 序号 |
|---|---:|---|
| 清单定义的算法类别 | 20 | M01–M20 |
| 已完成（本地算法/模型） | 14 | M01–M08、M12–M16、M20 |
| 已完成（外部 API 能力） | 3 | M09–M11 |
| 已完成合计 | 17 | M01–M16、M20 |
| 部分完成/待补齐 | 3 | M17–M19 |
| 完全缺失 | 0 | 无 |

按本报告口径，当前完成率为 **17/20，即 85%**。M01–M16 以及 M20 已完成；下一阶段只需要集中补齐 M17、M18、M19。

## 3. M01–M20 对照清单

| M 序号 | 规定算法模型 | 当前主要算法包/实现 | 实现形式 | 当前可调用情况 | 类别状态 | 说明 |
|---|---|---|---|---|---|---|
| M01 | 聚类算法 | `clustering_engine` | Python HTTP；K-Means、DBSCAN | 可真实调用 | `COMPLETE-LOCAL` | 已完成算法卡、Schema、golden case 和严格调用验收。 |
| M02 | 关联算法 | `execution_rule_matcher` | Python HTTP；Apriori 风格固化规则 | 可真实调用 | `COMPLETE-LOCAL` | 已完成规则挖掘产物、独立留出评估及 HTTP/`algolib` 调用。 |
| M03 | 线性回归模型 | `trajectory_linear_predictor` | Python HTTP；二维 OLS | 可真实调用 | `COMPLETE-LOCAL` | 逐请求拟合并输出预测、回归系数和评价信息。 |
| M04 | 逻辑回归模型 | `decision_plan_recommender_onnx`、`compliance_risk_scorer_onnx` | 原生 ONNX | 可真实调用 | `COMPLETE-LOCAL` | 已训练、导出并通过 Python/C++ ONNX Runtime 验收。 |
| M05 | 随机森林模型 | `threat_priority_random_forest`、`threat_priority_random_forest_onnx` | Python HTTP + 原生 ONNX | 可真实调用 | `COMPLETE-LOCAL` | 同时提供完整服务版与无Python依赖的ONNX轻量版。 |
| M06 | 传统神经网络模型 | `supcon_meta_classifier`、`supcon_meta_classifier_onnx` | Python HTTP + 原生 ONNX | 可真实调用 | `COMPLETE-LOCAL` | 完整版支持动态 support-shot；固定原型 ONNX 轻量版通过 Python/C++ ORT 真实验收。 |
| M07 | 朴素贝叶斯网络 | `intent_gaussian_naive_bayes`、`intent_gaussian_naive_bayes_onnx` | Python HTTP + 原生 ONNX | 可真实调用 | `COMPLETE-LOCAL` | 同时提供完整服务版与无Python依赖的ONNX轻量版。 |
| M08 | 生成对抗网络 | `conditional_tabular_gan`、`conditional_tabular_gan_onnx` | Python HTTP + 原生 ONNX | 可真实调用 | `COMPLETE-LOCAL` | ONNX 版执行显式噪声和 one-hot 条件生成；完整版额外处理种子、温度和命名字段。 |
| M09 | 大语言模型 | `llm_rule_explainer`（目录 `python_http_service_llm_explainer`） | Python HTTP；外部 LLM API | 外部服务可用并配置后真实调用 | `COMPLETE-EXTERNAL` | 已按项目边界完成 API 封装；不在本仓库训练或保存 LLM。 |
| M10 | 检索增强生成模型 | `synapse_rag_retriever`，`knowledge_semantic_comm` 为辅助能力 | Python HTTP；外部 RAG API | 外部服务可用并配置后真实调用 | `COMPLETE-EXTERNAL` | 已完成检索请求和统一结果契约；RAG 索引与生成模型由外部系统维护。 |
| M11 | 智能体模型 | `decision_planning_core` | Python HTTP；外部 A2A Agent/编排 API | 外部服务可用并配置后真实调用 | `COMPLETE-EXTERNAL` | 已完成 Agent 能力服务封装；Agent 本体属于外部/伴随服务。 |
| M12 | 联邦学习模型 | `federated_fedavg_aggregator` | Python HTTP；FedAvg | 可真实调用 | `COMPLETE-LOCAL` | 已实现嵌套权重聚合、客户端更新协议和可复现实验。 |
| M13 | 强化学习模型 | `marl_ppo_task_scheduler`、`marl_ppo_task_scheduler_onnx` | Python HTTP + 原生 ONNX | 可真实调用 | `COMPLETE-LOCAL` | ONNX 版执行掩码策略与价值估计；完整版额外处理战场对象、观测构造及跨智能体分配。 |
| M14 | 可解释 AI 模型 | `edl_evidential_verifier`、`edl_evidential_verifier_onnx`、规则证据链 | Python HTTP + 原生 ONNX | 可真实调用 | `COMPLETE-LOCAL` | ONNX 版返回核心证据张量；完整版额外提供偶然不确定性、阈值解释和人工复核分流。 |
| M15 | 多模态融合模型 | `multimodal_mamba_fusion`、`multimodal_mamba_fusion_onnx` | Python HTTP + 原生 ONNX | 可真实调用 | `COMPLETE-LOCAL` | ONNX 版执行四模态掩码融合；完整版额外支持不等长输入、传感器关联和目标 ID 回填。 |
| M16 | 时间序列预测模型 | `target_trend_predictor_onnx` | 原生 ONNX；训练 LSTM | 可真实调用 | `COMPLETE-LOCAL` | 已训练、导出并通过 Python/C++ ONNX Runtime 调用。 |
| M17 | 实时目标检测模型 | `battlefield_rtdetr_detector` | Python HTTP；RT-DETR/Ultralytics 路径 | 接口/Mock 可用，严格 real gate 未通过 | `PARTIAL` | 缺战场微调 checkpoint、检测数据集指标及正式权重验收。 |
| M18 | 差分检测与变化检测模型 | `siamese_mask2former_damage`，`xbd_damage_assessor` 为辅助实现 | Python HTTP；Siamese/Mask2Former 路径 | 部分真实路径可运行，类别证据未闭环 | `PARTIAL` | 缺正式 Siamese 产物、变化检测数据集指标及完整发布状态。 |
| M19 | 多目标跟踪与定位模型 | `motr_neural_kalman_tracker` | Python HTTP；MOTR/Neural Kalman 路径 | 接口/Mock 可用，严格 real gate 未通过 | `PARTIAL` | 缺正式跟踪 checkpoint、连续帧数据集指标及真实航迹验收。 |
| M20 | 图神经网络模型 | `graph_relation_reasoner`、`graph_relation_reasoner_onnx` | Python HTTP + 原生 ONNX | 可真实调用 | `COMPLETE-LOCAL` | ONNX 版输出十节点关系矩阵；完整版额外完成航迹特征构造、阈值判断、关系标签和编组。 |

## 4. 已完成算法的调用边界

### 4.1 本地算法与模型

M01–M08、M12–M16、M20 的算法能力由仓库内的源代码、ONNX、joblib、PyTorch 或 safetensors 产物提供。Python HTTP 算法通过 `/health`、`/metadata`、`/predict` 暴露服务，ONNX 算法由 `algolib` 的 ONNX Runtime 后端运行；算法包注册、激活后可通过统一 `/run` 路径调用。

### 4.2 外部 API 算法

M09–M11 已完成的是算法库一侧的集成职责：

1. 使用 Algorithm Card 描述能力、输入输出和服务端点。
2. 校验调用参数并封装外部 API 请求。
3. 将 LLM、RAG 或 Agent 返回值转换为稳定的输出 Schema。
4. 通过算法库统一注册、发现和运行入口向上层 Agent 提供能力。

实际运行时仍需目标环境中的外部服务处于可用状态，并提供正确的 endpoint、凭据和网络连通性。这属于部署依赖，不属于本算法库重新实现 LLM、RAG 或 Agent 模型的范围。

## 5. Algorithm Card 与 Agent 调用现状

算法库平台已经具备基于 Algorithm Card 的能力发现和统一调用基础：可列出已注册算法，并通过统一 `POST /run` 执行选中的算法。每个算法包均提供或预留能力描述和输入输出 Schema。

需要区分两个层次：

- **算法库可调用性**：17 个已完成 M 类均有对应实现或外部 API 适配路径，可以被平台调用。
- **Agent 自动选型**：当前 `TacticalIntelligenceAgent` 仍有直接实例化固定 skill 的路径，尚未完全改造成“读取 Algorithm Card capabilities → 匹配输入 Schema → 动态选择算法 → 调用 `/run` → 校验输出”的统一适配流程。

因此，当前结论是：算法卡片和调用底座已经具备，但若目标是让 Agent 完全根据任务语义自主选取任意算法包，还需要补一个通用的 Card 驱动型 Agent Adapter。这项工作与 M01–M20 算法模型是否完成是两个不同维度。

## 6. 当前验收依据

- 仓库共有 43 个算法包；新增 M08/M13 两个 ONNX 包严格静态、Python ORT 与 C++ ORT 验收 2/2 通过。
- 当前严格闭环的本地算法包为 23 个，对应 M01–M08、M12–M16、M20 中的主要实现与轻量变体。
- M05–M08、M13–M16、M20 源模型、导出一致性及全部既有 ONNX 回归：43 passed，另有 5 个子测试通过。
- 全量 `tests/python` 当前因合并后两个 `decision_agents` 模块缺失而阻塞于收集阶段，与本次新增 ONNX 包无关。
- C++ CTest：1/1 通过。
- M20 严格端到端验收：1/1 通过，0 warning。
- M09–M11 的完成结论基于已经确认的外部 API 交付边界；其可用性依赖部署环境中的外部服务。

静态验收通过只表示算法包结构和资料合法，不能单独证明真实模型调用。对于本地模型应结合各 M 分组 real gate；对于 M09–M11 应结合外部服务健康状态和部署环境联调结果判断运行可用性。

## 7. 后续待办

1. 补齐 M17 的战场目标检测正式 checkpoint、数据集指标和严格 real gate。
2. 补齐 M18 的 Siamese 变化检测正式产物、指标和端到端验收。
3. 补齐 M19 的跟踪 checkpoint、连续帧评估指标和真实航迹验收。
4. 在目标部署环境为 M09–M11 固化 API endpoint、凭据注入、超时/错误路径和验收报告，并同步 Algorithm Card 生命周期状态。
5. 实现 Card 驱动型 Agent Adapter，使 Agent 真正按照 capabilities 和 Schema 动态选型及调用。

## 8. 最终判定

按确认后的项目范围，M09 大语言模型、M10 RAG 和 M11 Agent 以外部 API 能力接入，均报告为已完成。因此当前算法库的 M 类别交付状态为：

- **已完成 17 类：M01–M16、M20。**
- **部分完成 3 类：M17–M19。**
- **完全缺失 0 类。**

下一步的算法补齐工作应集中在 M17、M18、M19；Agent 依据算法卡片动态选择和调用算法则应作为独立的平台集成任务推进。
