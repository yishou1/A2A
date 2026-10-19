# 算法库实现与可调用性报告（M01–M20）

报告日期：2026-08-16  
项目：A2A-lkf-algorithmrepo  
对照依据：`18 类算法模型清单.md`。该文件名写“18 类”，正文实际列出 M01–M20 共 20 类。

## 1. 结论摘要

当前仓库不存在完全没有候选实现的 M 类，但“存在算法包”不等于“真实实现完成”。按本项目
严格门禁判定：

| 统计项 | 当前结果 |
|---|---:|
| 清单规定模型类别 | 20 类 |
| `COMPLETE` | 14 类（M01–M08、M12–M16、M20） |
| `PARTIAL` | 6 类（M09–M11、M17–M19） |
| `CALLABLE` 但未完成 | 0 类（原 M16 已完成闭环） |
| `MISSING` | 0 类 |
| 仓库内算法包 | 35 个 |
| `validated` 算法包 | 15 个 |
| `draft` 算法包 | 20 个 |
| 后端构成 | 31 个 `python_http_service`、4 个 ONNX |

当前可以得出三个核心结论：

1. M01–M08、M12–M16、M20 已有真实算法或训练产物，能够通过 HTTP/ONNX 与
   `algolib register/activate/run` 严格调用，不依赖 Mock、随机初始化或静默 fallback。
2. M09–M11 已按外部 API 能力建立算法包和服务契约，不要求本仓库训练 LLM、RAG 或
   Agent，但尚缺带真实端点、凭据、超时和错误处理的端到端验收记录，因此严格状态仍是
   `PARTIAL`。
3. M17–M19 均有代码和算法包，但正式权重、业务数据指标或禁止 fallback 的真实门禁尚未
   全部闭环。它们按当前实施顺序暂缓，不应报告为已完成。

## 2. 判定口径

### 2.1 `COMPLETE`

某个 M 类至少有一个实现同时满足：算法卡和 Schema 完整、使用真实算法或已训练产物、
记录数据/脚本/产物 SHA256 和评价指标、真实健康检查通过、HTTP 或 ONNX 推理通过、
`algolib` 注册激活运行通过、算法卡状态为 `validated`。

### 2.2 `PARTIAL`

已有算法包、代码或接口，但真实外部端点、正式 checkpoint、数据集指标、生命周期状态或
严格 real gate 中至少一项未闭环。Mock 能返回、静态检查通过或 fallback 可运行，均不能
单独升级为 `COMPLETE`。

### 2.3 类别状态与算法包状态

M 类状态是类别级结论。例如 M15 由 `multimodal_mamba_fusion` 完成，因此 M15 可以是
`COMPLETE`，但同类候选 `imagebind_multimodal_encoder` 仍可保持 `draft`。不能把“某类已
完成”理解为该类下所有候选包都已完成。

## 3. M01–M20 对照结果

| M 序号 | 规定类别 | 当前主要实现 | 实现形式 | 严格调用情况 | 状态 | 结论与缺口 |
|---|---|---|---|---|---|---|
| M01 | 聚类算法 | `clustering_engine` | Python HTTP；K-Means、DBSCAN | 可真实调用 | `COMPLETE` | 确定性实现、Schema、golden case、源码哈希及 HTTP/`algolib` 均闭环；后续补真实数据聚类指标 |
| M02 | 关联算法 | `execution_rule_matcher` | Python HTTP；Apriori 风格规则 | 可真实调用 | `COMPLETE` | 固化 28 条规则，12 条独立留出场景动作和角色准确率均为 1.0；需用真实脱敏记录重新挖掘 |
| M03 | 线性回归模型 | `trajectory_linear_predictor` | Python HTTP；逐请求二维 OLS | 可真实调用 | `COMPLETE` | 160 条合成留出轨迹 MAE 0.519819、RMSE 0.964136；需在真实航迹上分层评估 |
| M04 | 逻辑回归模型 | `decision_plan_recommender_onnx`、`compliance_risk_scorer_onnx` | 原生 ONNX | 可真实调用 | `COMPLETE` | 两模型分别用 1200 条参考数据训练；留出 ROC AUC 0.960621、0.995357，Python/C++ ORT 均通过 |
| M05 | 随机森林模型 | `threat_priority_random_forest` | Python HTTP；sklearn/joblib | 可真实调用 | `COMPLETE` | 训练数据、脚本、模型哈希、留出指标及严格门禁闭环；生产使用前需换真实标注 |
| M06 | 传统神经网络模型 | `supcon_meta_classifier` | Python HTTP；PyTorch/safetensors | 可真实调用 | `COMPLETE` | 99,200 参数 MLP 与类别原型，合成留出 accuracy/macro-F1 均为 1.0；指标不是实际识别精度 |
| M07 | 朴素贝叶斯网络 | `intent_gaussian_naive_bayes` | Python HTTP；GaussianNB/joblib | 可真实调用 | `COMPLETE` | 模型、后验概率、留出指标和哈希闭环；仍需真实数据概率校准和漂移监测 |
| M08 | 生成对抗网络 | `conditional_tabular_gan` | Python HTTP；PyTorch 条件 GAN | 可真实调用 | `COMPLETE` | 已完成对抗训练、条件生成、分布指标和产物固化；合成输出不可冒充真实观测 |
| M09 | 大语言模型 | `llm_rule_explainer` | Python HTTP；外部 LLM API | 配置真实 API 后可调用 | `PARTIAL` | 接口契约存在，本仓库无需训练 LLM；缺真实凭据环境下的端到端成功、超时和错误路径验收 |
| M10 | 检索增强生成模型 | `synapse_rag_retriever`、`knowledge_semantic_comm` | Python HTTP；外部/本地 RAG 能力 | Mock 可调用，真实 RAG 未闭环 | `PARTIAL` | 算法包和调用路径存在；全量 real 核验中 SynapseRAG 真实调用失败，需固定服务端点与凭据 |
| M11 | 智能体模型 | `decision_planning_core` | Python HTTP；外部 A2A Agent/编排 API | 服务包装可调用，外部 Agent 未闭环 | `PARTIAL` | 按 API 边界管理；需要伴随 A2A 仓库或真实 Agent 端点完成计划生成链路验收 |
| M12 | 联邦学习模型 | `federated_fedavg_aggregator` | Python HTTP；FedAvg | 可真实调用 | `COMPLETE` | 通用嵌套权重聚合和三客户端八轮可复现实验已闭环；生产化仍缺安全聚合、认证和掉线恢复 |
| M13 | 强化学习模型 | `marl_ppo_task_scheduler` | Python HTTP；PyTorch/safetensors | 可真实调用 | `COMPLETE` | 39,434 参数参数共享 MARL-PPO；平均奖励 3.500993，优于随机基线 2.066525；`marl_dynamic_router` 仍是补充候选 |
| M14 | 可解释 AI 模型 | `edl_evidential_verifier`、规则证据链 | Python HTTP；EDL/safetensors | 可真实调用 | `COMPLETE` | 290 参数 Dirichlet EDL，留出 accuracy 0.903、macro-F1 0.901410，并返回完整证据与人工复核分流 |
| M15 | 多模态融合模型 | `multimodal_mamba_fusion` | Python HTTP；Mamba 风格嵌入融合/safetensors | 可真实调用 | `COMPLETE` | 412,688 参数；留出 mean cosine 0.771683、RMSE 0.042234，优于均值基线；只完成嵌入融合，不包含原始媒体编码 |
| M16 | 时间序列预测模型 | `target_trend_predictor_onnx` | 原生 ONNX；训练 LSTM | 可真实调用 | `COMPLETE` | 1,425 参数，以 12 步预测未来第 4 步；留出 RMSE 0.051080、R² 0.980410，优于末值基线 RMSE 0.095422 |
| M17 | 实时目标检测模型 | `battlefield_rtdetr_detector` | Python HTTP；RT-DETR/Ultralytics | 接口与 Mock 可调用，严格 real 未通过 | `PARTIAL` | 存在通用 `rtdetr-l.pt`，但战场微调 checkpoint、检测数据指标和正式模型解析尚未闭环 |
| M18 | 差分与变化检测模型 | `siamese_mask2former_damage`、`xbd_damage_assessor` | Python HTTP；Siamese/Mask2Former 路径及 xBD 辅助模型 | 现有真实路径可调用，类别证据未闭环 | `PARTIAL` | xBD 与部分真实路径可运行，但 Siamese 正式产物、变化检测数据集指标及发布状态不完整 |
| M19 | 多目标跟踪与定位模型 | `motr_neural_kalman_tracker` | Python HTTP；MOTR/Neural Kalman 路径 | 接口与 Mock 可调用，严格 real 未通过 | `PARTIAL` | 缺 `motr_tracker` 正式 checkpoint、连续帧数据集指标和真实连续航迹验收 |
| M20 | 图神经网络模型 | `graph_relation_reasoner` | Python HTTP；训练 GNN/safetensors | 可真实调用 | `COMPLETE` | 16,673 参数两层消息传递 GNN；600 个合成留出图 edge F1 0.962788、ROC AUC 0.999212，优于规则基线 F1 0.912402；缺权重或哈希错误拒绝启动，无规则 fallback |

## 4. 当前 35 个算法包清单

### 4.1 已严格闭环的 15 个 `validated` 包

| algorithm_id | 对应类别 | 后端/产物 | 当前调用结论 |
|---|---|---|---|
| `clustering_engine` | M01 | Python HTTP / 源码算法 | 真实 HTTP + `algolib` 通过 |
| `execution_rule_matcher` | M02、M14 辅助 | Python HTTP / 固化规则 JSON | 真实 HTTP + `algolib` 通过 |
| `trajectory_linear_predictor` | M03 | Python HTTP / OLS | 真实 HTTP + `algolib` 通过 |
| `decision_plan_recommender_onnx` | M04 | ONNX | Python ORT + C++ ORT 通过 |
| `compliance_risk_scorer_onnx` | M04 | ONNX | Python ORT + C++ ORT 通过 |
| `threat_priority_random_forest` | M05 | Python HTTP / joblib | 真实 HTTP + `algolib` 通过 |
| `supcon_meta_classifier` | M06 | Python HTTP / safetensors | 真实 HTTP + `algolib` 通过 |
| `intent_gaussian_naive_bayes` | M07 | Python HTTP / joblib | 真实 HTTP + `algolib` 通过 |
| `conditional_tabular_gan` | M08 | Python HTTP / PyTorch `.pt` | 真实 HTTP + `algolib` 通过 |
| `federated_fedavg_aggregator` | M12 | Python HTTP / FedAvg | 真实 HTTP + `algolib` 通过 |
| `marl_ppo_task_scheduler` | M13 | Python HTTP / safetensors | 真实 HTTP + `algolib` 通过 |
| `edl_evidential_verifier` | M14 | Python HTTP / safetensors | 真实 HTTP + `algolib` 通过 |
| `multimodal_mamba_fusion` | M15 | Python HTTP / safetensors | 真实 HTTP + `algolib` 通过 |
| `target_trend_predictor_onnx` | M16 | ONNX | Python ORT + C++ ORT 通过 |
| `graph_relation_reasoner` | M20 | Python HTTP / safetensors | 真实 HTTP + `algolib` 通过 |

### 4.2 仍为 `draft` 的 20 个包

| algorithm_id | 对应类别/用途 | 当前情况 |
|---|---|---|
| `llm_rule_explainer` | M09 | 外部 LLM API 包；需真实端点验收 |
| `synapse_rag_retriever` | M10 | Mock 可用；真实 RAG 调用未闭环 |
| `knowledge_semantic_comm` | M10 辅助 | 真实核验路径可运行，但卡片和独立指标未闭环 |
| `decision_planning_core` | M11 | 外部/伴随 A2A Agent 服务包装 |
| `compliance_authorization_core` | 流程服务、M11/M14 辅助 | 服务包装存在；外部链路与生命周期未闭环 |
| `marl_dynamic_router` | M13 补充 | 缺正式 `marl_policy` checkpoint；不影响 M13 已由 PPO 调度器完成 |
| `imagebind_multimodal_encoder` | M15 补充 | 缺离线 ImageBind/CLIP 资源；不影响 M15 嵌入融合已完成 |
| `multimodal_feature_fuser` | M15 辅助 | 可调用的工程融合服务，尚无独立训练闭环 |
| `trajectory_predictor` | M16 辅助 | 可调用；正式数据指标和生命周期未闭环 |
| `battlefield_rtdetr_detector` | M17 | 缺战场微调权重与检测指标 |
| `siamese_mask2former_damage` | M18 | 真实路径可运行，正式产物和变化检测指标不完整 |
| `xbd_damage_assessor` | M18 辅助 | xBD 路径可运行，算法卡仍为 draft |
| `motr_neural_kalman_tracker` | M19 | 缺正式跟踪 checkpoint |
| `track_state_updater` | M19/M16 辅助 | 工程航迹更新服务；尚未独立闭环 |
| `target_type_classifier` | M06 辅助 | 可调用分类服务；未形成独立训练证据链 |
| `execution_control_planner` | 流程编排辅助 | 组合规则、运动预测和指令生成，非独立 M 类完成项 |
| `mission_feature_adapter` | 数据适配辅助 | 特征适配服务，非模型类别 |
| `mission_completion_scorer` | M05/任务评估辅助 | 代理随机森林可调用，算法卡仍为 draft |
| `closed_loop_decision_advisor` | 闭环流程辅助 | 规则式建议服务，非独立 M 类完成项 |
| `onnx_text_classifier` | ONNX 契约夹具 | 常量 logits 测试模型，不计作 M09 业务大模型 |

## 5. 验收和可调用性证据

截至报告日期，最近一次全量结果为：

- 35 个算法包静态验收：35/35 通过；其中 20 个 draft 包产生 27 条生命周期/资料警告。
- Python 自动化测试：115 passed，另有 5 个子测试通过。
- C++ CTest：1/1 通过。
- TIA Mock：11/11 可用。
- TIA Real：6/11 可用；M15 `multimodal_mamba_fusion` 已进入真实通过集合，其余失败项主要是
  checkpoint、离线预训练资源或真实 RAG 端点问题。
- M01–M08、M12–M16 均存在独立严格批次报告；M16 最近报告为
  `build-smoke-offline/acceptance-batches/m16-time-series-20260816-202404/acceptance_report.md`。
- M20 图神经网络严格报告为
  `build-smoke-offline/acceptance-batches/m20-graph-neural-network-20260816-211202/acceptance_report.md`。

注意：`static-all` 的 PASS 只说明包结构合法。判断能否真实调用，应查看对应 M 分组的
runtime/`algolib` 报告或 TIA real 报告。

## 6. 调用方式

### 6.1 Python HTTP 服务算法

服务启动后通过 `/health`、`/metadata`、`/predict` 调用。严格批次脚本会自动启动目标服务、
检查 `model_loaded`、执行 golden case，再通过 `algolib` 调用。例如：

```powershell
.\scripts\run_algorithm_batch_acceptance.ps1 `
  -Group m15-multimodal-fusion `
  -Python D:\software\Miniconda3\envs\algorithm_repo\python.exe `
  -Algolib .\build-smoke-offline\Debug\algolib.exe `
  -RealGate
```

### 6.2 ONNX 算法

M04 和 M16 的业务 ONNX 包由 C++ ONNX Runtime 后端直接运行，无需 Python HTTP 服务：

```powershell
.\build-smoke-offline\Debug\algolib.exe register .\examples\target_trend_predictor_onnx\1.0.0
.\build-smoke-offline\Debug\algolib.exe activate target_trend_predictor_onnx 1.0.0 onnx
.\build-smoke-offline\Debug\algolib.exe run .\examples\target_trend_predictor_onnx\1.0.0\golden_cases\case_001_input.json
```

### 6.3 外部 API 算法

M09–M11 的算法库职责是参数校验、请求封装、超时/错误处理、结果 Schema 统一和 A2A 调用，
不是在本仓库保存大模型本体。能否真实调用取决于实际部署环境是否提供端点、凭据和配套服务。

## 7. 当前限制与风险

1. 多数已训练模型使用确定性合成或参考数据，指标证明工程闭环和可复现性，不代表真实业务精度。
2. M15 完成的是嵌入级融合，原始图像、SAR、雷达和文本编码仍依赖 ImageBind/CLIP 等上游模型。
3. M09–M11 的 API 边界已明确，但缺当前环境下的真实端到端验收证据。
4. M17–M19 的主要问题分别是检测微调权重、变化检测产物和跟踪 checkpoint；M20 已关闭 GNN 模型与 fallback 缺口。
5. 当前存在两个端口冲突：`9020` 同时用于目标检测和决策核心，`9021` 同时用于合规核心和
   Siamese 变化检测。分组验收不受影响，但在同一主机同时启动全部服务前必须改为唯一端口或
   使用统一网关路由。

## 8. 建议的后续顺序

1. 若 M09–M11 的外部服务已经部署，补充真实端点配置并生成 API real-gate 报告，将“业务上
   已可用”转化为可审计证据。
2. 在暂缓 M17–M19 期间，先固化 35 个算法包版本、产物清单、SHA256 和统一一键验收入口。
3. 获取真实脱敏数据后，优先替换 M01–M08、M12–M16 的合成参考数据并进行独立时间/场景切分评估。
4. 恢复 M17–M19 工作时，按 M17 检测、M18 变化检测、M19 跟踪的顺序逐类关闭
   checkpoint、指标和严格平台调用缺口。

## 9. 最终判定

当前算法库已经形成完整的 M01–M20 包覆盖，且 M01–M08、M12–M16、M20 共 14 类具备真实、可复现、
可由平台调用的闭环实现。M09–M11 属于外部 API 集成项，M17–M19 属于仍待模型资源与严格验收
的工程模型。对外汇报时应使用“14 类严格完成、6 类已有实现但待闭环、0 类完全缺失”，不宜表述
为“20 类全部真实实现完成”。
